"""Text-to-speech: provider interface and OpenAI implementation."""

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from app.ai.client import elapsed_ms, log_openai_failure
from app.audio.audio_utils import (
    CONCATENABLE_OUTPUT,
    OUTPUT_MIME,
    OutputAudioFormat,
    split_for_tts,
)
from app.core.config import Settings
from app.core.exceptions import SpeechUnavailableError, ValidationAppError

logger = logging.getLogger(__name__)

OPENAI_TTS_MAX_INPUT_CHARS = 4096
_STREAM_CHUNK_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    text: str
    voice: str
    speed: float = 1.0
    format: OutputAudioFormat = OutputAudioFormat.MP3
    # Delivery guidance (tone, pacing, accent). Ignored by models that don't support it.
    instructions: str | None = None


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    audio: bytes
    format: OutputAudioFormat
    latency_ms: float | None = None

    @property
    def mime_type(self) -> str:
        return OUTPUT_MIME[self.format]


class TextToSpeechProvider(Protocol):
    max_input_chars: int

    async def synthesize(self, request: SpeechRequest) -> SynthesizedSpeech: ...

    def stream(self, request: SpeechRequest) -> AsyncIterator[bytes]: ...


class OpenAITextToSpeech:
    max_input_chars = OPENAI_TTS_MAX_INPUT_CHARS

    def __init__(self, client: AsyncOpenAI | None, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _params(self, request: SpeechRequest, text: str) -> dict[str, Any]:
        if self._client is None or not self._settings.openai_tts_model:
            logger.error("OpenAI TTS is not configured (OPENAI_API_KEY / OPENAI_TTS_MODEL)")
            raise SpeechUnavailableError()
        params: dict[str, Any] = {
            "model": self._settings.openai_tts_model,
            "voice": request.voice,
            "input": text,
            "response_format": request.format.value,
            "speed": request.speed,
        }
        # tts-1 / tts-1-hd reject `instructions`.
        if request.instructions and not self._settings.openai_tts_model.startswith("tts-1"):
            params["instructions"] = request.instructions
        return params

    def _chunks(self, request: SpeechRequest) -> list[str]:
        chunks = split_for_tts(request.text, self.max_input_chars)
        if not chunks:
            raise ValidationAppError("There is no text to speak.")
        if len(chunks) > 1 and request.format not in CONCATENABLE_OUTPUT:
            raise ValidationAppError(
                f"Text longer than {self.max_input_chars} characters needs a streamable "
                "format (mp3, opus, aac or pcm)."
            )
        return chunks

    async def synthesize(self, request: SpeechRequest) -> SynthesizedSpeech:
        """Whole-file synthesis; long text is spoken in sentence-aligned chunks."""
        chunks = self._chunks(request)
        params = [self._params(request, c) for c in chunks]  # validates config before any call
        start = time.perf_counter()
        parts: list[bytes] = []
        try:
            for p in params:
                response = await self._client.audio.speech.create(**p)  # type: ignore[union-attr]
                parts.append(response.content)
        except openai.OpenAIError as exc:
            log_openai_failure("tts", exc, start)
            raise SpeechUnavailableError() from exc
        audio = b"".join(parts)
        latency = elapsed_ms(start)
        self._log_success(request, latency, len(audio), len(chunks))
        return SynthesizedSpeech(audio=audio, format=request.format, latency_ms=latency)

    async def stream(self, request: SpeechRequest) -> AsyncIterator[bytes]:
        """Yield audio bytes as the provider produces them (lower time-to-first-audio)."""
        chunks = self._chunks(request)
        params = [self._params(request, c) for c in chunks]  # fail fast before streaming
        start = time.perf_counter()
        total = 0
        try:
            for p in params:
                async with self._client.audio.speech.with_streaming_response.create(**p) as resp:  # type: ignore[union-attr]
                    async for data in resp.iter_bytes(_STREAM_CHUNK_BYTES):
                        total += len(data)
                        yield data
        except openai.OpenAIError as exc:
            log_openai_failure("tts", exc, start)
            raise SpeechUnavailableError() from exc
        self._log_success(request, elapsed_ms(start), total, len(chunks))

    def _log_success(self, request: SpeechRequest, latency: float, size: int, chunks: int) -> None:
        logger.info(
            "tts request completed",
            extra={
                "fields": {
                    "provider": "openai",
                    "model": self._settings.openai_tts_model,
                    "duration_ms": latency,
                    "characters": len(request.text),
                    "chunks": chunks,
                    "audio_bytes": size,
                    "format": request.format.value,
                }
            },
        )
