"""Real-time voice session (one WebSocket connection).

Turn pipeline, per utterance::

    speech.start → binary audio frames → speech.end
      → STT (transcript deltas streamed as recognised)
      → companion reply streamed token by token (response.text.delta, tool events)
      → each complete sentence is sent to TTS immediately; audio bytes stream to the
        client as binary frames while later sentences are still being generated
      → response.done (persisted turn + timings)

Barge-in: ``speech.start``, ``text.message`` or ``response.cancel`` during a reply
cancels generation and speech at once. The partial reply is saved with
``interrupted: true``, and ``response.cancelled`` is sent.

Every turn uses its own DB session, so cancelling one can't corrupt shared state.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import CompanionAgent
from app.ai.llm import LLMProvider
from app.audio.audio_utils import OUTPUT_MIME, OutputAudioFormat, prepare_text_for_speech
from app.audio.storage import AudioStore
from app.audio.streaming import SentenceChunker, UtteranceBuffer
from app.audio.stt import SpeechToTextProvider, Transcript
from app.audio.tts import SpeechRequest, TextToSpeechProvider
from app.core.background import TaskRunner
from app.core.config import Settings
from app.core.exceptions import AppError, NoSpeechError, NotFoundError, RateLimitedError
from app.core.rate_limit import RateLimiter
from app.core.redis import RedisClient
from app.core.security import PasswordHasher, TokenService
from app.memory.embeddings import EmbeddingProvider
from app.models.enums import MessageType
from app.models.user import User
from app.repositories.conversations import ConversationRepository
from app.repositories.revoked_tokens import RevokedAccessTokenStore
from app.schemas.conversations import ConversationResponse, MessageResponse
from app.schemas.realtime import (
    ConfirmationRespond,
    ResponseCancel,
    SessionEnd,
    SessionStart,
    SpeechEnd,
    SpeechStart,
    TextMessage,
)
from app.schemas.tools import ToolActionResponse, ToolExecutionResponse
from app.services.auth import AuthService
from app.services.chat import (
    ChatService,
    ChatTurnResult,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamToolFinished,
    StreamToolStarted,
)
from app.services.memory_pipeline import MemoryCoordinator, MemoryPipeline
from app.services.tool_actions import ToolActionService
from app.services.voice import VoiceService
from app.tools.executor import ToolActivity
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
PCM_SAMPLE_RATE = 24000  # OpenAI TTS raw PCM output


# --- dependencies -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnServices:
    session: AsyncSession
    user: User
    chat: ChatService
    voice: VoiceService
    actions: ToolActionService


@dataclass(slots=True)
class SocketAuth:
    """The access token a WebSocket session runs under.

    Checked once at ``session.start`` and then kept honest for the session's lifetime:
    the session closes when the token expires or is revoked (logout), unless the
    client hands over a fresh one with ``auth.refresh``.
    """

    user_id: uuid.UUID
    jti: str
    expires_at: datetime
    checked_at: float = field(default_factory=time.monotonic)

    def seconds_left(self) -> float:
        return (self.expires_at - datetime.now(UTC)).total_seconds()


# How often an open session re-checks the access-token deny-list (catches logout).
REVOCATION_CHECK_SECONDS = 15.0


@dataclass(frozen=True)
class RealtimeDeps:
    """App-level collaborators for WebSocket sessions (sessions are made per turn)."""

    settings: Settings
    llm: LLMProvider
    stt: SpeechToTextProvider
    tts: TextToSpeechProvider
    embeddings: EmbeddingProvider
    runner: TaskRunner
    registry: ToolRegistry
    audio_store: AudioStore
    session_factory: SessionFactory
    redis: RedisClient
    tokens: TokenService
    passwords: PasswordHasher

    async def authenticate(self, token: str) -> SocketAuth:
        async with self.session_factory() as session:
            auth = AuthService(
                session,
                self.settings,
                self.tokens,
                self.passwords,
                RevokedAccessTokenStore(self.redis.client),
            )
            user, claims = await auth.authenticate(token)
            return SocketAuth(user.id, claims.jti, claims.expires_at)

    async def token_revoked(self, auth: SocketAuth) -> bool:
        """Re-check the deny-list for an open session. Fails open on Redis errors:
        the token was verified at connect time and the session ends at its expiry."""
        auth.checked_at = time.monotonic()
        try:
            return await RevokedAccessTokenStore(self.redis.client).is_revoked(auth.jti)
        except RedisError:
            logger.warning("token revocation check unavailable (Redis error)")
            return False

    @asynccontextmanager
    async def services(self, user_id: uuid.UUID) -> AsyncIterator[TurnServices]:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if user is None or not user.is_active:
                raise NotFoundError("User not found.")
            memory = MemoryCoordinator(
                session,
                self.embeddings,
                MemoryPipeline(self.session_factory, self.llm, self.embeddings, self.settings),
                self.runner,
                self.settings,
            )
            chat = ChatService(
                session,
                CompanionAgent(self.llm, self.settings),
                self.settings,
                memory,
                self.registry,
            )
            voice = VoiceService(session, chat, self.stt, self.tts, self.audio_store, self.settings)
            yield TurnServices(
                session, user, chat, voice, ToolActionService(session, self.registry, self.settings)
            )


# --- session ------------------------------------------------------------------------


class Sender:
    """Serialises sends from the receive loop and the turn task onto one socket."""

    def __init__(
        self,
        send_json: Callable[[dict], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._send_json, self._send_bytes = send_json, send_bytes
        self._lock = asyncio.Lock()
        self.closed = False

    async def json(self, payload: dict[str, Any]) -> None:
        await self._send(self._send_json, payload)

    async def bytes(self, data: bytes) -> None:
        await self._send(self._send_bytes, data)

    async def _send(self, fn, arg) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                await fn(arg)
            except Exception:  # client went away mid-send
                self.closed = True


@dataclass(slots=True)
class _TurnState:
    response_id: str
    started: float
    user_text: str | None = None
    partial: list[str] = field(default_factory=list)
    actions: list[ToolActivity] = field(default_factory=list)
    handle: Any = None
    message_type: MessageType = MessageType.VOICE
    stt_ms: float | None = None
    first_text_ms: float | None = None
    first_audio_ms: float | None = None


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


class VoiceSession:
    def __init__(
        self,
        sender: Sender,
        deps: RealtimeDeps,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        config: SessionStart,
    ) -> None:
        self.id = uuid.uuid4().hex
        self._send = sender
        self._deps = deps
        self._user_id = user_id
        self.conversation_id = conversation_id
        self._config = config
        self._output = OutputAudioFormat(config.output_format)
        self._utterance: UtteranceBuffer | None = None
        self._turn: asyncio.Task[None] | None = None
        self._closing = False
        self.turns = 0
        self.interruptions = 0

    # --- lifecycle --------------------------------------------------------------

    async def start(self) -> bool:
        if self.conversation_id is not None:
            async with self._deps.session_factory() as session:
                found = await ConversationRepository(session).get_for_user(
                    self.conversation_id, self._user_id
                )
            if found is None:
                await self._error(NotFoundError("Conversation not found."), fatal=True)
                return False
        await self._send.json(
            {
                "type": "session.ready",
                "session_id": self.id,
                "conversation_id": str(self.conversation_id) if self.conversation_id else None,
                "input_format": self._config.input_format.value,
                "output_format": None if self._config.text_only else self._output.value,
                "output_mime_type": None if self._config.text_only else OUTPUT_MIME[self._output],
                "output_sample_rate": PCM_SAMPLE_RATE
                if self._output is OutputAudioFormat.PCM
                else None,
                "limits": {
                    "max_utterance_bytes": self._deps.settings.max_audio_size_bytes,
                    "max_frame_bytes": self._deps.settings.ws_max_frame_bytes,
                    "idle_timeout_seconds": self._deps.settings.ws_idle_timeout_seconds,
                },
            }
        )
        return True

    async def shutdown(self) -> None:
        self._closing = True
        await self._cancel_turn()

    # --- incoming ---------------------------------------------------------------

    async def on_message(self, message: Any) -> bool:
        """Handle one validated client message. Returns False to end the session."""
        match message:
            case SpeechStart():
                await self._begin_utterance()
            case SpeechEnd():
                await self._end_utterance()
            case TextMessage(text=text):
                await self._cancel_turn()
                self._start_turn(text=text)
            case ResponseCancel():
                await self._cancel_turn()
            case ConfirmationRespond():
                await self._confirm(message)
            case SessionEnd():
                return False
            case SessionStart():
                await self._error(AppError("Session already started."), code="protocol_error")
            case _:  # Ping
                await self._send.json({"type": "pong"})
        return True

    async def on_audio(self, chunk: bytes) -> None:
        if len(chunk) > self._deps.settings.ws_max_frame_bytes:
            self._utterance = None
            await self._error(AppError("Audio frame too large."), code="frame_too_large")
            return
        if self._utterance is None:
            await self._begin_utterance()  # implicit speech.start
        try:
            self._utterance.append(chunk)  # type: ignore[union-attr]
        except AppError as exc:
            self._utterance = None
            await self._error(exc)

    async def _begin_utterance(self) -> None:
        if await self._cancel_turn():
            self.interruptions += 1  # barge-in: the user started talking over the reply
        self._utterance = UtteranceBuffer(
            self._config.input_format,
            self._config.sample_rate,
            self._deps.settings.max_audio_size_bytes,
        )
        await self._send.json({"type": "speech.started"})

    async def _end_utterance(self) -> None:
        utterance, self._utterance = self._utterance, None
        if utterance is None or utterance.size == 0:
            await self._error(AppError("No audio received for this utterance."), code="no_audio")
            return
        try:
            audio = utterance.finish()
        except AppError as exc:
            await self._error(exc)
            return
        await self._send.json({"type": "speech.stopped", "bytes": utterance.size})
        await self._cancel_turn()
        self._start_turn(audio=audio)

    # --- turns ------------------------------------------------------------------

    def _start_turn(self, *, audio=None, text: str | None = None) -> None:
        self._turn = asyncio.create_task(self._run_turn(audio, text), name=f"voice-turn:{self.id}")

    async def _cancel_turn(self) -> bool:
        task, self._turn = self._turn, None
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task  # the turn saves its partial reply and reports response.cancelled
        return True

    async def _run_turn(self, audio, text: str | None) -> None:
        state = _TurnState(
            response_id=uuid.uuid4().hex,
            started=time.perf_counter(),
            message_type=MessageType.TEXT if text is not None else MessageType.VOICE,
        )
        speaker: asyncio.Task[None] | None = None
        try:
            async with self._deps.services(self._user_id) as svc:
                await self._rate_limit()
                state.user_text = text if text is not None else await self._transcribe(audio, state)
                state.handle = await svc.chat.begin_turn(
                    svc.user,
                    state.user_text,
                    self.conversation_id,
                    state.message_type,
                    spoken_reply=not self._config.text_only,
                )
                await self._send.json(
                    {"type": "response.started", "response_id": state.response_id}
                )

                queue: asyncio.Queue[str | None] = asyncio.Queue()
                if not self._config.text_only:
                    template = await svc.voice.speech_request(
                        svc.user,
                        "ok",
                        voice=self._config.voice,
                        speed=self._config.speed,
                        audio_format=self._output,
                    )
                    speaker = asyncio.create_task(self._speak(queue, template, state))
                chunker = SentenceChunker()
                result: ChatTurnResult | None = None
                async with aclosing(
                    svc.chat.stream_turn(
                        svc.user, state.user_text, state.handle, state.message_type
                    )
                ) as events:
                    async for event in events:
                        match event:
                            case StreamDelta(text=delta):
                                if state.first_text_ms is None:
                                    state.first_text_ms = _ms(state.started)
                                state.partial.append(delta)
                                await self._send.json(
                                    {
                                        "type": "response.text.delta",
                                        "response_id": state.response_id,
                                        "text": delta,
                                    }
                                )
                                for sentence in chunker.feed(delta):
                                    queue.put_nowait(sentence)
                            case StreamToolStarted(tool=tool):
                                await self._send.json({"type": "tool.started", "tool": tool})
                            case StreamToolFinished(activity=activity):
                                state.actions.append(activity)
                                await self._send.json(
                                    {"type": "tool.completed", "action": _action(activity)}
                                )
                            case StreamCompleted(result=done):
                                result = done
                            case StreamFailed(code=code, message=message):
                                await self._send.json(
                                    {
                                        "type": "error",
                                        "code": code,
                                        "message": message,
                                        "fatal": False,
                                        "response_id": state.response_id,
                                    }
                                )
                for sentence in chunker.flush():
                    queue.put_nowait(sentence)
                queue.put_nowait(None)
                if speaker is not None:
                    await speaker
                if result is not None:
                    await self._finish(svc, result, state, audio)
        except asyncio.CancelledError:
            if speaker is not None:
                speaker.cancel()
            await asyncio.shield(self._save_interrupted(state))
            if self._closing:
                raise
        except AppError as exc:
            if speaker is not None:
                speaker.cancel()
            await self._error(exc, response_id=state.response_id)
        except Exception:
            if speaker is not None:
                speaker.cancel()
            logger.exception("voice session turn failed")
            await self._error(AppError(), response_id=state.response_id)

    async def _transcribe(self, audio, state: _TurnState) -> str:
        transcript: Transcript | None = None
        async for item in self._deps.stt.transcribe_stream(audio):
            if isinstance(item, Transcript):
                transcript = item
            elif item:
                await self._send.json({"type": "transcript.delta", "text": item})
        text = (transcript.text if transcript else "").strip()
        if not text:
            raise NoSpeechError()
        state.stt_ms = transcript.latency_ms if transcript else None
        await self._send.json({"type": "transcript.done", "text": text})
        return text

    async def _speak(
        self, queue: asyncio.Queue, template: SpeechRequest, state: _TurnState
    ) -> None:
        started, failed = False, False
        while (sentence := await queue.get()) is not None:
            spoken = prepare_text_for_speech(sentence)
            if not spoken or failed:
                continue
            try:
                async for chunk in self._deps.tts.stream(replace(template, text=spoken)):
                    if not started:
                        started = True
                        state.first_audio_ms = _ms(state.started)
                        await self._send.json(
                            {
                                "type": "response.audio.started",
                                "response_id": state.response_id,
                                "format": self._output.value,
                                "mime_type": OUTPUT_MIME[self._output],
                                "sample_rate": PCM_SAMPLE_RATE
                                if self._output is OutputAudioFormat.PCM
                                else None,
                            }
                        )
                    await self._send.bytes(chunk)
            except AppError as exc:  # keep the text reply flowing even if speech fails
                failed = True
                await self._error(exc, response_id=state.response_id)
        if started:
            await self._send.json({"type": "response.audio.done", "response_id": state.response_id})

    async def _finish(
        self, svc: TurnServices, result: ChatTurnResult, state: _TurnState, audio
    ) -> None:
        self.conversation_id = result.conversation.id
        self.turns += 1
        if audio is not None and self._deps.audio_store.enabled:
            await self._deps.audio_store.save(
                svc.user.id, result.conversation.id, result.user_message.id, audio
            )
        timings = {
            "stt_ms": state.stt_ms,
            "first_text_ms": state.first_text_ms,
            "first_audio_ms": state.first_audio_ms,
            "total_ms": _ms(state.started),
        }
        logger.info(
            "voice session turn completed", extra={"fields": {"session_id": self.id, **timings}}
        )
        await self._send.json(
            {
                "type": "response.done",
                "response_id": state.response_id,
                "conversation": ConversationResponse.model_validate(result.conversation).model_dump(
                    mode="json"
                ),
                "user_message": MessageResponse.model_validate(result.user_message).model_dump(
                    mode="json"
                ),
                "assistant_message": MessageResponse.model_validate(
                    result.assistant_message
                ).model_dump(mode="json"),
                "actions": [_action(a) for a in result.actions],
                "timings": timings,
            }
        )

    async def _save_interrupted(self, state: _TurnState) -> None:
        saved_text = "".join(state.partial).strip()
        if state.handle is not None and state.user_text:
            try:
                async with self._deps.services(self._user_id) as svc:
                    result = await svc.chat.save_interrupted(
                        svc.user,
                        state.user_text,
                        state.handle,
                        saved_text,
                        state.actions,
                        state.message_type,
                    )
                self.conversation_id = result.conversation.id
            except Exception:
                logger.exception("failed to save interrupted turn")
        logger.info(
            "voice session turn interrupted",
            extra={"fields": {"session_id": self.id, "chars": len(saved_text)}},
        )
        await self._send.json(
            {
                "type": "response.cancelled",
                "response_id": state.response_id,
                "saved_text": saved_text,
            }
        )

    # --- other ------------------------------------------------------------------

    async def _confirm(self, message: ConfirmationRespond) -> None:
        try:
            async with self._deps.services(self._user_id) as svc:
                if message.decision == "approve":
                    execution = await svc.actions.approve(svc.user, message.confirmation_id)
                else:
                    execution = await svc.actions.reject(svc.user, message.confirmation_id)
                payload = ToolExecutionResponse.model_validate(execution).model_dump(mode="json")
        except AppError as exc:
            await self._error(exc)
            return
        await self._send.json({"type": "confirmation.result", "execution": payload})

    async def _rate_limit(self) -> None:
        settings = self._deps.settings
        if not settings.rate_limit_enabled:
            return
        result = await RateLimiter(self._deps.redis.client).hit(
            f"chat:user:{self._user_id}",
            limit=settings.chat_rate_limit_per_minute,
            window_seconds=60,
        )
        if not result.allowed:
            raise RateLimitedError()

    async def _error(
        self,
        exc: AppError,
        *,
        fatal: bool = False,
        code: str | None = None,
        response_id: str | None = None,
    ) -> None:
        await self._send.json(
            {
                "type": "error",
                "code": code or exc.code,
                "message": exc.message,
                "fatal": fatal,
                "response_id": response_id,
            }
        )


def _action(activity: ToolActivity) -> dict[str, Any]:
    return ToolActionResponse.from_activity(activity).model_dump(mode="json")


class SessionSlots:
    """Caps concurrent voice sessions per user.

    A Redis sorted set per user holds one member per open session, scored by a lease
    expiry. The socket loop renews its lease while the session is alive, so a session
    lost in a crash frees its slot within ``lease_seconds`` instead of blocking the
    user. Fails open if Redis is down (authentication already fails closed then).
    """

    # Drop expired leases, then add ours only if under the limit (atomically).
    _ACQUIRE = """
    redis.call('zremrangebyscore', KEYS[1], '-inf', ARGV[1])
    if redis.call('zcard', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
    redis.call('zadd', KEYS[1], ARGV[2], ARGV[4])
    redis.call('expire', KEYS[1], ARGV[5])
    return 1
    """

    def __init__(self, redis: RedisClient, limit: int, lease_seconds: int = 60) -> None:
        self._redis, self._limit, self._lease = redis, limit, lease_seconds

    def _key(self, user_id: uuid.UUID) -> str:
        return f"ws:voice-sessions:{user_id}"

    async def acquire(self, user_id: uuid.UUID, session_id: str) -> bool:
        now = time.time()
        try:
            return bool(
                await self._redis.client.eval(
                    self._ACQUIRE,
                    1,
                    self._key(user_id),
                    now,
                    now + self._lease,
                    self._limit,
                    session_id,
                    self._lease * 2,
                )
            )
        except RedisError:
            logger.warning("session slot check skipped: Redis unavailable")
            return True

    async def renew(self, user_id: uuid.UUID, session_id: str) -> None:
        with contextlib.suppress(RedisError):
            async with self._redis.client.pipeline(transaction=True) as pipe:
                pipe.zadd(self._key(user_id), {session_id: time.time() + self._lease}, xx=True)
                pipe.expire(self._key(user_id), self._lease * 2)
                await pipe.execute()

    async def release(self, user_id: uuid.UUID, session_id: str) -> None:
        with contextlib.suppress(RedisError):
            await self._redis.client.zrem(self._key(user_id), session_id)
