"""Speech-to-text: provider interface and OpenAI implementation."""

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from app.ai.client import elapsed_ms, log_openai_failure
from app.audio.audio_utils import ValidatedAudio
from app.core.config import Settings
from app.core.exceptions import InvalidAudioError, SpeechUnavailableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    duration_seconds: float | None = None
    latency_ms: float | None = None


class SpeechToTextProvider(Protocol):
    async def transcribe(
        self, audio: ValidatedAudio, *, prompt: str | None = None
    ) -> Transcript: ...

    def transcribe_stream(self, audio: ValidatedAudio) -> AsyncIterator[str | Transcript]:
        """Text deltas as they are recognised, then exactly one final ``Transcript``.

        This streams the *output* of transcribing a complete utterance. It does not
        transcribe while the user is still speaking.
        """
        ...


class OpenAISpeechToText:
    """OpenAI audio transcriptions. Idempotent, so the SDK's retries are safe here."""

    def __init__(self, client: AsyncOpenAI | None, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _params(self, audio: ValidatedAudio) -> dict[str, Any]:
        if self._client is None or not self._settings.openai_stt_model:
            logger.error("OpenAI STT is not configured (OPENAI_API_KEY / OPENAI_STT_MODEL)")
            raise SpeechUnavailableError()
        params: dict[str, Any] = {
            "model": self._settings.openai_stt_model,
            "file": (audio.filename, audio.data, audio.mime_type),
        }
        if self._settings.openai_stt_languages:
            params["languages"] = list(self._settings.openai_stt_languages)
        if self._settings.openai_stt_prompt:
            params["prompt"] = self._settings.openai_stt_prompt
        return params

    async def transcribe(self, audio: ValidatedAudio, *, prompt: str | None = None) -> Transcript:
        params = {**self._params(audio), "response_format": "json"}
        if prompt:
            params["prompt"] = prompt

        start = time.perf_counter()
        try:
            result = await self._client.audio.transcriptions.create(**params)  # type: ignore[union-attr]
        except openai.BadRequestError as exc:  # corrupt / undecodable audio
            log_openai_failure("stt", exc, start)
            raise InvalidAudioError() from exc
        except openai.OpenAIError as exc:
            log_openai_failure("stt", exc, start)
            raise SpeechUnavailableError() from exc

        latency = elapsed_ms(start)
        duration = getattr(getattr(result, "usage", None), "seconds", None)
        self._log(audio, latency, duration, streamed=False)
        return Transcript(text=result.text.strip(), duration_seconds=duration, latency_ms=latency)

    async def transcribe_stream(self, audio: ValidatedAudio) -> AsyncIterator[str | Transcript]:
        params = self._params(audio)
        start = time.perf_counter()
        final_text = ""
        try:
            stream = await self._client.audio.transcriptions.create(**params, stream=True)  # type: ignore[union-attr]
            async for event in stream:
                if event.type == "transcript.text.delta":
                    yield event.delta
                elif event.type == "transcript.text.done":
                    final_text = event.text
        except openai.BadRequestError as exc:
            log_openai_failure("stt", exc, start)
            raise InvalidAudioError() from exc
        except openai.OpenAIError as exc:
            log_openai_failure("stt", exc, start)
            raise SpeechUnavailableError() from exc
        latency = elapsed_ms(start)
        self._log(audio, latency, None, streamed=True)
        yield Transcript(text=final_text.strip(), latency_ms=latency)

    def _log(
        self, audio: ValidatedAudio, latency: float, seconds: float | None, *, streamed: bool
    ) -> None:
        logger.info(
            "stt request completed",
            extra={
                "fields": {
                    "provider": "openai",
                    "model": self._settings.openai_stt_model,
                    "duration_ms": latency,
                    "audio_bytes": len(audio.data),
                    "audio_format": audio.format.value,
                    "audio_seconds": seconds,
                    "streamed": streamed,
                }
            },
        )
