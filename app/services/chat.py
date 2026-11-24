"""One conversational turn: load context → companion (with tools) → persist both messages.

Messages are written only once the model has replied, so a failed turn leaves no
half-saved exchange and the client can retry. The read transaction is closed before
the (slow) model call so no DB connection is held meanwhile.

Tool calls are the exception: an executed action (e.g. a created reminder) is
committed when it runs, and is recorded in ``tool_executions``. If the model then
fails to produce its final reply, the action still stands.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AgentReply, CompanionAgent, CompanionTurn, PreparedTurn, ToolFinished
from app.ai.agent import ToolStarted as AgentToolStarted
from app.ai.llm import TextDelta
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.models.conversation import Conversation, Message
from app.models.enums import MessageRole, MessageType
from app.models.user import User, UserPreferences, UserProfile
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.users import UserRepository
from app.services.memory_pipeline import MemoryCoordinator
from app.tools.confirmations import ConfirmationService
from app.tools.executor import ToolActivity, ToolExecutor, build_tool_context
from app.tools.registry import ToolRegistry
from app.tools.timeparse import format_local

logger = logging.getLogger(__name__)

_TITLE_MAX = 60


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    conversation: Conversation
    user_message: Message
    assistant_message: Message
    actions: list[ToolActivity] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StreamStarted:
    conversation_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class StreamDelta:
    text: str


@dataclass(frozen=True, slots=True)
class StreamToolStarted:
    tool: str


@dataclass(frozen=True, slots=True)
class StreamToolFinished:
    activity: ToolActivity


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    result: ChatTurnResult


@dataclass(frozen=True, slots=True)
class StreamFailed:
    code: str
    message: str


ChatStreamEvent = (
    StreamStarted
    | StreamDelta
    | StreamToolStarted
    | StreamToolFinished
    | StreamCompleted
    | StreamFailed
)


@dataclass(slots=True)
class TurnHandle:
    """A loaded, not-yet-persisted turn (see ``begin_turn``)."""

    conversation_id: uuid.UUID
    is_new: bool
    profile: UserProfile
    preferences: UserPreferences
    prepared: PreparedTurn
    memory_enabled: bool
    memories_recalled: int
    title: str
    conversation_created: bool = False  # set when a tool call forced early creation


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        agent: CompanionAgent,
        settings: Settings,
        memory: MemoryCoordinator | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._session = session
        self._agent = agent
        self._settings = settings
        self._memory = memory
        self._tools = tools if tools is not None and len(tools) else None
        self._users = UserRepository(session)
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)

    async def send(
        self,
        user: User,
        text: str,
        conversation_id: uuid.UUID | None = None,
        message_type: MessageType = MessageType.TEXT,
    ) -> ChatTurnResult:
        loaded = await self.begin_turn(user, text, conversation_id, message_type)
        reply = await self._agent.run(loaded.prepared, self._executor(user, text, loaded))
        return await self._save(user, text, loaded, reply, message_type)

    async def stream(
        self,
        user: User,
        text: str,
        conversation_id: uuid.UUID | None = None,
        message_type: MessageType = MessageType.TEXT,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Yields StreamStarted, then StreamDelta / StreamTool* events, then StreamCompleted
        or StreamFailed.

        Errors before the stream starts (e.g. unknown conversation) are raised so the
        API can return a normal HTTP error. Once streaming, failures become StreamFailed.
        """
        loaded = await self.begin_turn(user, text, conversation_id, message_type)
        return self.stream_turn(user, text, loaded, message_type)

    async def stream_turn(
        self, user: User, text: str, loaded: TurnHandle, message_type: MessageType
    ) -> AsyncIterator[ChatStreamEvent]:
        yield StreamStarted(loaded.conversation_id)
        try:
            reply: AgentReply | None = None
            executor = self._executor(user, text, loaded)
            async for event in self._agent.run_stream(loaded.prepared, executor):
                match event:
                    case TextDelta(text=delta):
                        yield StreamDelta(delta)
                    case AgentToolStarted(tool=tool):
                        yield StreamToolStarted(tool)
                    case ToolFinished(activity=activity):
                        yield StreamToolFinished(activity)
                    case AgentReply():
                        reply = event
            if reply is None:
                raise AppError()
            result = await self._save(user, text, loaded, reply, message_type)
        except AppError as exc:
            yield StreamFailed(exc.code, exc.message)
            return
        except Exception:
            logger.exception("chat stream failed")
            yield StreamFailed(AppError.code, AppError.message)
            return
        yield StreamCompleted(result)

    # --- internals --------------------------------------------------------------

    async def begin_turn(
        self,
        user: User,
        text: str,
        conversation_id: uuid.UUID | None,
        message_type: MessageType,
        *,
        spoken_reply: bool | None = None,
    ) -> TurnHandle:
        """Load context and prepare the model request. Nothing is persisted yet.

        ``spoken_reply`` forces voice-style replies (short, speakable) even for typed
        input, e.g. text sent inside a voice session. It defaults to message_type == voice.
        """
        profile = await self._users.get_profile(user.id)
        preferences = await self._users.get_preferences(user.id)
        if profile is None or preferences is None:
            raise NotFoundError("User profile not found.")

        history: list[Message] = []
        summary = None
        pending: list[str] = []
        if conversation_id is not None:
            conversation = await self._conversations.get_for_user(conversation_id, user.id)
            if conversation is None:
                raise NotFoundError("Conversation not found.")
            summary = conversation.summary
            history = await self._messages.recent(
                conversation.id, self._settings.chat_context_max_messages
            )
            if self._tools is not None:
                pending = await self._pending_confirmations(user, conversation.id, profile)

        memory_enabled = self._memory is not None and self._memory.enabled_for(preferences)
        recalled = await self._memory.recall(user.id, text) if memory_enabled else []  # type: ignore[union-attr]
        prepared = self._agent.prepare(
            CompanionTurn(
                user_id=user.id,
                profile=profile,
                preferences=preferences,
                history=history,
                user_text=text,
                conversation_summary=summary,
                voice_mode=spoken_reply
                if spoken_reply is not None
                else message_type is MessageType.VOICE,
                memory_enabled=memory_enabled,
                memories=[m.content for m in recalled],
                tools=self._tools.specs() if self._tools is not None else (),
                pending_confirmations=pending,
            )
        )
        # End the read transaction so no pooled connection is held during the model call.
        await self._session.commit()
        return TurnHandle(
            conversation_id=conversation_id or uuid.uuid4(),
            is_new=conversation_id is None,
            profile=profile,
            preferences=preferences,
            prepared=prepared,
            memory_enabled=memory_enabled,
            memories_recalled=len(recalled),
            title=make_title(text),
        )

    async def _pending_confirmations(
        self, user: User, conversation_id: uuid.UUID, profile: UserProfile
    ) -> list[str]:
        service = ConfirmationService(self._session, self._tools, self._settings)  # type: ignore[arg-type]
        return [
            f"confirmation_id={p.id}: {p.confirmation_summary} "
            f"(expires {format_local(p.expires_at, profile.timezone)})"
            for p in await service.pending(user.id)
            if p.conversation_id == conversation_id and p.expires_at
        ]

    def _executor(self, user: User, text: str, loaded: TurnHandle) -> ToolExecutor | None:
        if self._tools is None:
            return None

        async def ensure_conversation() -> None:
            # Tool executions reference the conversation, so a brand-new one must exist first.
            if loaded.is_new and not loaded.conversation_created:
                await self._conversations.add(
                    Conversation(id=loaded.conversation_id, user_id=user.id, title=loaded.title)
                )
                await self._session.commit()
                loaded.conversation_created = True

        ctx = build_tool_context(
            session=self._session,
            settings=self._settings,
            user=user,
            profile=loaded.profile,
            conversation_id=loaded.conversation_id,
            user_message=text,
            now=datetime.now(UTC),
        )
        return ToolExecutor(
            self._tools, ctx, ensure_conversation=ensure_conversation, settings=self._settings
        )

    async def _save(
        self,
        user: User,
        text: str,
        loaded: TurnHandle,
        agent_reply: AgentReply,
        message_type: MessageType,
    ) -> ChatTurnResult:
        reply = agent_reply.response
        metadata = {
            "model": reply.model,
            "input_tokens": reply.usage.input_tokens if reply.usage else None,
            "output_tokens": reply.usage.output_tokens if reply.usage else None,
            "truncated": reply.truncated or None,
            "latency_ms": reply.latency_ms,
        }
        return await self._persist(
            user, text, loaded, reply.text, metadata, list(agent_reply.activities), message_type
        )

    async def save_interrupted(
        self,
        user: User,
        text: str,
        loaded: TurnHandle,
        partial_reply: str,
        actions: list[ToolActivity],
        message_type: MessageType,
    ) -> ChatTurnResult:
        """Persist a turn the user cut off (barge-in) with whatever was generated so far."""
        return await self._persist(
            user, text, loaded, partial_reply.strip(), {"interrupted": True}, actions, message_type
        )

    async def _persist(
        self,
        user: User,
        text: str,
        loaded: TurnHandle,
        reply_text: str,
        reply_metadata: dict[str, object],
        actions: list[ToolActivity],
        message_type: MessageType,
    ) -> ChatTurnResult:
        if loaded.is_new and not loaded.conversation_created:
            conversation = await self._conversations.add(
                Conversation(id=loaded.conversation_id, user_id=user.id, title=loaded.title)
            )
        else:
            conversation = await self._conversations.get_for_user(loaded.conversation_id, user.id)
            if conversation is None:  # deleted while the model was replying
                raise NotFoundError("Conversation not found.")
            if conversation.title is None:
                conversation.title = loaded.title

        user_message = await self._messages.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=text,
                message_type=message_type,
                message_metadata=_compact({"language": loaded.prepared.detected_language}),
            )
        )
        assistant_message = await self._messages.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=reply_text,
                message_type=message_type,
                message_metadata=_compact(
                    {
                        **reply_metadata,
                        "context_messages": loaded.prepared.context_messages,
                        "memories_recalled": loaded.memories_recalled or None,
                        "tools": [
                            {
                                "tool": a.tool,
                                "status": a.status.value,
                                "execution_id": str(a.execution_id),
                            }
                            for a in actions
                        ]
                        or None,
                    }
                ),
            )
        )
        await self._conversations.touch(conversation)
        await self._session.commit()
        if self._memory is not None:
            self._memory.schedule_after_turn(
                user.id, conversation.id, text, reply_text, loaded.memory_enabled
            )
        return ChatTurnResult(conversation, user_message, assistant_message, actions)


def make_title(text: str) -> str:
    """First line of the opening message, cut at a word boundary."""
    first_line = " ".join(text.split())
    if len(first_line) <= _TITLE_MAX:
        return first_line
    cut = first_line[:_TITLE_MAX].rsplit(" ", 1)[0] or first_line[:_TITLE_MAX]
    return cut.rstrip(",.;:!?") + "…"


def _compact(data: dict[str, object]) -> dict[str, object]:
    return {k: (str(v) if hasattr(v, "value") else v) for k, v in data.items() if v is not None}
