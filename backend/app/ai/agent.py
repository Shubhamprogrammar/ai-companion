"""Companion agent: turns one user message (plus context) into a model request and reply.

Kept free of HTTP and persistence concerns; ``ChatService`` loads data and saves
results. Tool calls are delegated to a ``ToolRunner`` (the backend executor), so the
agent decides *when* to loop while the executor decides *whether* a call is allowed.
"""

import hashlib
import hmac
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.context import build_context_window
from app.ai.language import detect_language
from app.ai.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    TextDelta,
    ToolCall,
    ToolResultItem,
    ToolRound,
    ToolSpec,
)
from app.ai.personality import get_personality
from app.ai.prompts import PromptInputs, UserContext, build_system_prompt
from app.core.config import Settings
from app.core.exceptions import AIUnavailableError
from app.models.conversation import Message
from app.models.enums import Language
from app.models.user import UserPreferences, UserProfile

CAPABILITIES = (
    "have natural conversations about anything: the user's day, feelings, plans and ideas",
    "give guidance on careers, learning, goals and productivity",
    "answer general-knowledge questions from what you already know",
    "remember what was said earlier in this conversation",
)
MEMORY_CAPABILITY = "remember important things the user shares, across conversations"
# Capability descriptions for tool groups, keyed by a tool that signals the group.
TOOL_CAPABILITIES = {
    "create_reminder": "save, list, change and delete reminders (they appear in the user's "
    "reminder list)",
    "create_task": "manage the user's personal to-do list",
    "update_preferences": "change their app settings (voice, speech speed, language, memory)",
    "find_memories": "look up or forget specific things you remember about them",
    "search_web": "search the web for current information",
}
UNAVAILABLE = (
    "ring alarms, send notifications, make calls, send messages, or act outside this app",
)
NO_REMINDERS = "set reminders or manage tasks"
NO_BROWSING = "browse the internet or look up live information (news, weather, prices)"
MEMORY_OFF_LIMITATION = (
    "remember things between separate conversations (the user turned long-term memory off)"
)


@dataclass(frozen=True, slots=True)
class CompanionTurn:
    user_id: uuid.UUID
    profile: UserProfile
    preferences: UserPreferences
    history: Sequence[Message]
    user_text: str
    conversation_summary: str | None = None
    voice_mode: bool = False
    memory_enabled: bool = False
    memories: Sequence[str] = ()
    tools: Sequence[ToolSpec] = ()
    pending_confirmations: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    request: LLMRequest
    detected_language: Language | None
    context_messages: int
    context_dropped: int


class ToolRunResultLike(Protocol):
    result: ToolResultItem
    activity: Any


class ToolRunner(Protocol):
    async def run(self, call: ToolCall) -> ToolRunResultLike: ...


@dataclass(frozen=True, slots=True)
class ToolStarted:
    tool: str


@dataclass(frozen=True, slots=True)
class ToolFinished:
    activity: Any


@dataclass(frozen=True, slots=True)
class AgentReply:
    response: LLMResponse
    activities: list[Any] = field(default_factory=list)


AgentStreamEvent = TextDelta | ToolStarted | ToolFinished | AgentReply


class CompanionAgent:
    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    def prepare(self, turn: CompanionTurn, *, now: datetime | None = None) -> PreparedTurn:
        detected = detect_language(turn.user_text)
        if (
            turn.voice_mode
            and detected is Language.HINDI
            and turn.profile.preferred_language is not Language.HINDI
        ):
            # Speech recognition writes spoken Hinglish in Devanagari; the script says
            # nothing about how the user talks. Reply in Roman-script Hinglish instead.
            detected = Language.HINGLISH
        capabilities, unavailable = _capabilities(turn)
        window = build_context_window(
            turn.history,
            turn.user_text,
            max_messages=self._settings.chat_context_max_messages,
            max_chars=self._settings.chat_context_max_chars,
        )
        instructions = build_system_prompt(
            PromptInputs(
                personality=get_personality(turn.preferences.personality),
                companion_name=self._settings.companion_name,
                user=UserContext(
                    display_name=turn.profile.display_name,
                    preferred_language=turn.profile.preferred_language,
                    timezone=turn.profile.timezone,
                    local_time=_local_time(turn.profile.timezone, now),
                ),
                detected_language=detected,
                conversation_summary=turn.conversation_summary,
                capabilities=capabilities,
                unavailable=unavailable,
                voice_mode=turn.voice_mode,
                memory_enabled=turn.memory_enabled,
                memories=turn.memories,
                tools_available=bool(turn.tools),
                pending_confirmations=turn.pending_confirmations,
            )
        )
        request = LLMRequest(
            instructions=instructions,
            messages=window.messages,
            safety_identifier=self.safety_identifier(turn.user_id),
            tools=tuple(turn.tools),
        )
        return PreparedTurn(request, detected, len(window.messages), window.dropped)

    async def reply(self, prepared: PreparedTurn) -> LLMResponse:
        return await self._llm.generate(prepared.request)

    def stream_reply(self, prepared: PreparedTurn) -> AsyncIterator[LLMStreamEvent]:
        return self._llm.stream(prepared.request)

    async def run(self, prepared: PreparedTurn, tools: ToolRunner | None) -> AgentReply:
        """Generate a reply, letting the model call tools for up to TOOL_MAX_ROUNDS rounds."""
        request, activities = prepared.request, []
        for round_number in range(self._settings.tool_max_rounds + 1):
            response = await self._llm.generate(request)
            if not response.tool_calls or tools is None:
                return AgentReply(self._require_text(response), activities)
            request, finished = await self._run_tools(request, response, tools, round_number)
            activities.extend(finished)
        raise AIUnavailableError()  # unreachable: the last round forces tool_choice="none"

    async def run_stream(
        self, prepared: PreparedTurn, tools: ToolRunner | None
    ) -> AsyncIterator[AgentStreamEvent]:
        request, activities = prepared.request, []
        for round_number in range(self._settings.tool_max_rounds + 1):
            final: LLMResponse | None = None
            async for event in self._llm.stream(request):
                if isinstance(event, TextDelta):
                    yield event
                else:
                    final = event
            if final is None:
                raise AIUnavailableError()
            if not final.tool_calls or tools is None:
                yield AgentReply(self._require_text(final), activities)
                return
            for call in final.tool_calls:
                yield ToolStarted(call.name)
            request, finished = await self._run_tools(request, final, tools, round_number)
            for activity in finished:
                yield ToolFinished(activity)
            activities.extend(finished)
        raise AIUnavailableError()

    async def _run_tools(
        self, request: LLMRequest, response: LLMResponse, tools: ToolRunner, round_number: int
    ) -> tuple[LLMRequest, list[Any]]:
        results, activities = [], []
        for call in response.tool_calls:
            outcome = await tools.run(call)
            results.append(outcome.result)
            activities.append(outcome.activity)
        request = replace(
            request,
            tool_rounds=(*request.tool_rounds, ToolRound(response.provider_items, tuple(results))),
        )
        if round_number + 1 >= self._settings.tool_max_rounds:
            request = replace(request, tool_choice="none")  # next call must answer in text
        return request, activities

    @staticmethod
    def _require_text(response: LLMResponse) -> LLMResponse:
        if not response.text:
            raise AIUnavailableError()
        return response

    def safety_identifier(self, user_id: uuid.UUID) -> str:
        """Keyed hash of the user id: stable per user, but reveals nothing to the provider."""
        key = self._settings.secret_key.get_secret_value().encode()
        return hmac.new(key, str(user_id).encode(), hashlib.sha256).hexdigest()[:32]


def _capabilities(turn: CompanionTurn) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names = {t.name for t in turn.tools}
    can = [*CAPABILITIES]
    if turn.memory_enabled:
        can.append(MEMORY_CAPABILITY)
    can += [text for tool, text in TOOL_CAPABILITIES.items() if tool in names]
    cannot = [*UNAVAILABLE]
    if "create_reminder" not in names:
        cannot.append(NO_REMINDERS)
    if "search_web" not in names:
        cannot.append(NO_BROWSING)
    if not turn.memory_enabled:
        cannot.append(MEMORY_OFF_LIMITATION)
    return tuple(can), tuple(cannot)


def _local_time(timezone: str, now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    try:
        return now.astimezone(ZoneInfo(timezone))
    except ZoneInfoNotFoundError:
        return now.astimezone(UTC)
