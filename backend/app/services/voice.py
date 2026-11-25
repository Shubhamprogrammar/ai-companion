"""Voice engine: transcription, synthesis and the complete audio turn.

Complete turn (Stage 1, request/response):
    audio ─► validate ─► STT ─► ChatService (voice mode) ─► TTS ─► audio

The chat turn is persisted as soon as the companion replies. If speech synthesis
then fails, the turn still succeeds with ``speech_error`` set (and no audio):
retrying would otherwise duplicate the conversation turn.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import elapsed_ms
from app.ai.personality import get_personality
from app.audio.audio_utils import OutputAudioFormat, prepare_text_for_speech, validate_audio
from app.audio.storage import AudioStore
from app.audio.stt import SpeechToTextProvider, Transcript
from app.audio.tts import SpeechRequest, SynthesizedSpeech, TextToSpeechProvider
from app.core.config import Settings
from app.core.exceptions import (
    AppError,
    NoSpeechError,
    NotFoundError,
    SpeechUnavailableError,
    ValidationAppError,
)
from app.models.enums import MessageType
from app.models.user import User, UserPreferences
from app.repositories.users import UserRepository
from app.schemas.chat import MAX_MESSAGE_CHARS
from app.services.chat import ChatService, ChatTurnResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VoiceTimings:
    stt_ms: float | None
    llm_ms: float | None
    tts_ms: float | None
    total_ms: float


@dataclass(frozen=True, slots=True)
class VoiceTurnResult:
    chat: ChatTurnResult
    transcript: Transcript
    speech: SynthesizedSpeech | None
    speech_error: AppError | None
    timings: VoiceTimings


class VoiceService:
    def __init__(
        self,
        session: AsyncSession,
        chat: ChatService,
        stt: SpeechToTextProvider,
        tts: TextToSpeechProvider,
        audio_store: AudioStore,
        settings: Settings,
    ) -> None:
        self._users = UserRepository(session)
        self._chat = chat
        self._stt = stt
        self._tts = tts
        self._audio_store = audio_store
        self._settings = settings

    # --- building blocks ----------------------------------------------------------

    async def transcribe(self, data: bytes) -> Transcript:
        audio = validate_audio(data, self._settings.max_audio_size_bytes)
        return self._require_speech(await self._stt.transcribe(audio))

    async def speech_request(
        self,
        user: User,
        text: str,
        *,
        voice: str | None = None,
        speed: float | None = None,
        audio_format: OutputAudioFormat = OutputAudioFormat.MP3,
    ) -> SpeechRequest:
        """Resolve voice settings: explicit override > user preference > server default."""
        preferences = await self._preferences(user)
        resolved_voice = voice or preferences.voice or self._settings.openai_tts_voice
        if not resolved_voice:
            logger.error("no TTS voice configured (set OPENAI_TTS_VOICE)")
            raise SpeechUnavailableError()
        if resolved_voice not in self._settings.openai_tts_voices:
            raise ValidationAppError(f"Unknown voice '{resolved_voice}'.")
        spoken = prepare_text_for_speech(text)
        if not spoken:
            raise ValidationAppError("There is no speakable text.")
        return SpeechRequest(
            text=spoken,
            voice=resolved_voice,
            speed=speed if speed is not None else preferences.speech_speed,
            format=audio_format,
            instructions=get_personality(preferences.personality).voice_instructions or None,
        )

    async def synthesize_stream(self, request: SpeechRequest) -> AsyncIterator[bytes]:
        return self._tts.stream(request)

    # --- complete turn ------------------------------------------------------------

    async def turn(
        self,
        user: User,
        data: bytes,
        conversation_id: uuid.UUID | None = None,
        audio_format: OutputAudioFormat = OutputAudioFormat.MP3,
    ) -> VoiceTurnResult:
        start = time.perf_counter()
        audio = validate_audio(data, self._settings.max_audio_size_bytes)

        transcript = self._require_speech(await self._stt.transcribe(audio))
        if len(transcript.text) > MAX_MESSAGE_CHARS:
            raise ValidationAppError("The recording is too long for a single turn.")

        chat = await self._chat.send(
            user, transcript.text, conversation_id, message_type=MessageType.VOICE
        )

        if self._audio_store.enabled:
            await self._audio_store.save(user.id, chat.conversation.id, chat.user_message.id, audio)

        speech: SynthesizedSpeech | None = None
        speech_error: AppError | None = None
        try:
            request = await self.speech_request(
                user, chat.assistant_message.content, audio_format=audio_format
            )
            speech = await self._tts.synthesize(request)
        except AppError as exc:
            logger.warning(
                "voice turn: speech synthesis failed", extra={"fields": {"code": exc.code}}
            )
            speech_error = exc

        timings = VoiceTimings(
            stt_ms=transcript.latency_ms,
            llm_ms=chat.assistant_message.message_metadata.get("latency_ms"),
            tts_ms=speech.latency_ms if speech else None,
            total_ms=elapsed_ms(start),
        )
        logger.info(
            "voice turn completed",
            extra={
                "fields": {
                    "stt_ms": timings.stt_ms,
                    "llm_ms": timings.llm_ms,
                    "tts_ms": timings.tts_ms,
                    "total_ms": timings.total_ms,
                    "speech_ok": speech is not None,
                }
            },
        )
        return VoiceTurnResult(chat, transcript, speech, speech_error, timings)

    @staticmethod
    def _require_speech(transcript: Transcript) -> Transcript:
        text = transcript.text.strip()
        if not text:
            raise NoSpeechError()
        return replace(transcript, text=text)

    async def _preferences(self, user: User) -> UserPreferences:
        preferences = await self._users.get_preferences(user.id)
        if preferences is None:
            raise NotFoundError("Preferences not found.")
        return preferences
