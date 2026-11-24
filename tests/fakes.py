"""Test doubles. No test ever reaches a real AI provider."""

import asyncio
import json as _json
from collections.abc import AsyncIterator
from typing import Any

from app.ai.llm import (
    JsonSchema,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    LLMUsage,
    TextDelta,
    ToolCall,
)
from app.core.exceptions import AIUnavailableError

DEFAULT_REPLY = "Arre, that sounds like a lot. Want to talk it through?"


ToolStep = list[tuple[str, dict[str, Any]]]


class FakeLLM:
    """Records every request; replies from a queue (or a default).

    A queued item may be a string (a text reply) or a list of ``(tool_name, arguments)``
    pairs, meaning "call these tools" for that model round.
    """

    def __init__(
        self,
        replies: list[str | ToolStep] | None = None,
        *,
        fail: bool = False,
        fail_after_deltas: int | None = None,
        json_replies: list[dict[str, Any]] | None = None,
        summary: str = "User talked about their career plans.",
        delay: float = 0.0,  # seconds between streamed deltas (to test interruptions)
    ) -> None:
        self.delay = delay
        self.requests: list[LLMRequest] = []
        self.json_requests: list[LLMRequest] = []
        self.summary_requests: list[LLMRequest] = []
        self._replies = list(replies or [])
        self._fail = fail
        self._fail_after_deltas = fail_after_deltas
        self._json_replies = list(json_replies or [])
        self.summary = summary

    def _next_reply(self) -> str | ToolStep:
        return self._replies.pop(0) if self._replies else DEFAULT_REPLY

    def _response(self, step: str | ToolStep) -> LLMResponse:
        if isinstance(step, list):
            calls = tuple(
                ToolCall(
                    call_id=f"call_{len(self.requests)}_{i}",
                    name=name,
                    arguments=args if isinstance(args, str) else _json.dumps(args),
                )
                for i, (name, args) in enumerate(step)
            )
            return LLMResponse(
                text="",
                model="fake-model",
                tool_calls=calls,
                provider_items=tuple({"call": c.call_id} for c in calls),
            )
        return LLMResponse(text=step, model="fake-model", usage=LLMUsage(42, 7), latency_ms=1.0)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if "running summary" in request.instructions:  # ConversationSummarizer
            self.summary_requests.append(request)
            return self._response(self.summary)
        self.requests.append(request)
        if self._fail:
            raise AIUnavailableError()
        return self._response(self._next_reply())

    async def generate_json(self, request: LLMRequest, schema: JsonSchema) -> dict[str, Any]:
        self.json_requests.append(request)
        return self._json_replies.pop(0) if self._json_replies else {"operations": []}

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]:
        self.requests.append(request)
        if self._fail:
            raise AIUnavailableError()
        step = self._next_reply()
        if isinstance(step, list):
            yield self._response(step)
            return
        text = step
        for i, word in enumerate(text.split(" ")):
            if self._fail_after_deltas is not None and i >= self._fail_after_deltas:
                raise AIUnavailableError()
            if self.delay:
                await asyncio.sleep(self.delay)
            yield TextDelta(word if i == 0 else " " + word)
        yield self._response(text)


# --- voice --------------------------------------------------------------------

from app.audio.audio_utils import ValidatedAudio  # noqa: E402
from app.audio.stt import Transcript  # noqa: E402
from app.audio.tts import SpeechRequest, SynthesizedSpeech  # noqa: E402
from app.core.exceptions import SpeechUnavailableError  # noqa: E402


def fake_wav(size: int = 2048) -> bytes:
    """Bytes with a valid WAV signature (content isn't decoded by fakes)."""
    return b"RIFF" + (size - 8).to_bytes(4, "little") + b"WAVE" + b"\x00" * (size - 12)


def fake_mp3(size: int = 2048) -> bytes:
    return b"ID3\x04\x00\x00" + b"\x00" * (size - 6)


class FakeSTT:
    def __init__(self, text: str = "Yaar aaj ka din bahut lamba tha", *, fail: bool = False):
        self.text = text
        self.fail = fail
        self.calls: list[ValidatedAudio] = []

    async def transcribe(self, audio: ValidatedAudio, *, prompt: str | None = None) -> Transcript:
        self.calls.append(audio)
        if self.fail:
            raise SpeechUnavailableError()
        return Transcript(text=self.text, duration_seconds=1.5, latency_ms=12.0)

    async def transcribe_stream(self, audio: ValidatedAudio):
        self.calls.append(audio)
        if self.fail:
            raise SpeechUnavailableError()
        for word in self.text.split(" "):
            yield word + " "
        yield Transcript(text=self.text, latency_ms=12.0)


class FakeTTS:
    max_input_chars = 4096
    AUDIO = b"FAKE-AUDIO-BYTES"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[SpeechRequest] = []

    async def synthesize(self, request: SpeechRequest) -> SynthesizedSpeech:
        self.requests.append(request)
        if self.fail:
            raise SpeechUnavailableError()
        return SynthesizedSpeech(audio=self.AUDIO, format=request.format, latency_ms=8.0)

    async def stream(self, request: SpeechRequest) -> AsyncIterator[bytes]:
        self.requests.append(request)
        if self.fail:
            raise SpeechUnavailableError()
        for i in range(0, len(self.AUDIO), 4):
            yield self.AUDIO[i : i + 4]


# --- memory -------------------------------------------------------------------

import hashlib  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402

from app.models.memory import EMBEDDING_DIMENSIONS  # noqa: E402

_WORD = re.compile(r"\w+", re.UNICODE)


class FakeEmbeddings:
    """Deterministic bag-of-words embeddings: texts sharing words are close in cosine space."""

    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    def vector(self, text: str) -> list[float]:
        v = [0.0] * self.dimensions
        v[0] = 0.05  # tiny shared component so no vector is all-zero
        for word in _WORD.findall(text.lower()):
            bucket = int(hashlib.sha256(word.encode()).hexdigest(), 16) % (self.dimensions - 1)
            v[bucket + 1] += 1.0
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise AIUnavailableError()
        return [self.vector(t) for t in texts]


# --- tools --------------------------------------------------------------------

from app.tools.web_search import WebSearchResult, WebSource  # noqa: E402


class FakeWebSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, *, timezone: str | None = None) -> WebSearchResult:
        self.queries.append(query)
        return WebSearchResult(
            answer=f"Fake answer for {query}",
            sources=[WebSource("Example", "https://example.com/a")],
        )


def tool_results(request) -> list[dict]:
    """Decoded tool outputs the model was given in this request (all rounds)."""
    return [_json.loads(r.output) for rnd in request.tool_rounds for r in rnd.results]
