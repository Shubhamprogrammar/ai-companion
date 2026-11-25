"""OpenAI STT/TTS providers exercised through the real SDK against a mocked transport."""

import json
from collections.abc import Callable

import httpx
import pytest
from openai import AsyncOpenAI

from app.audio.audio_utils import OutputAudioFormat, validate_audio
from app.audio.stt import OpenAISpeechToText
from app.audio.tts import OpenAITextToSpeech, SpeechRequest
from app.core.exceptions import InvalidAudioError, SpeechUnavailableError, ValidationAppError
from tests.conftest import make_settings
from tests.fakes import fake_wav

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="sk-test",
        base_url="http://openai.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _settings(**overrides):
    defaults = {
        "openai_api_key": "sk-test",
        "openai_stt_model": "test-stt",
        "openai_tts_model": "test-tts",
        "openai_tts_voice": "coral",
    }
    return make_settings(**(defaults | overrides))


AUDIO = validate_audio(fake_wav(), max_bytes=10_000)

# --- STT ----------------------------------------------------------------------------


async def test_transcribe_sends_multipart_with_model_languages_and_file() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "  Yaar aaj ka din lamba tha  "})

    transcript = await OpenAISpeechToText(_client(handler), _settings()).transcribe(AUDIO)

    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["content_type"].startswith("multipart/form-data")
    body = seen["body"]
    assert b'name="model"\r\n\r\ntest-stt' in body
    assert b'name="languages[]"\r\n\r\nen' in body and b"\r\n\r\nhi" in body
    assert b'filename="audio.wav"' in body and b"Content-Type: audio/wav" in body
    assert transcript.text == "Yaar aaj ka din lamba tha"
    assert transcript.latency_ms is not None


async def test_transcribe_omits_languages_when_disabled() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "hi"})

    await OpenAISpeechToText(_client(handler), _settings(openai_stt_languages=[])).transcribe(AUDIO)
    assert b"languages" not in seen["body"]


async def test_transcribe_bad_audio_is_invalid_audio() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid file format.", "type": "x"}})

    with pytest.raises(InvalidAudioError):
        await OpenAISpeechToText(_client(handler), _settings()).transcribe(AUDIO)


@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_transcribe_provider_errors_are_speech_unavailable(status_code: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "x", "type": "x"}})

    with pytest.raises(SpeechUnavailableError):
        await OpenAISpeechToText(_client(handler), _settings()).transcribe(AUDIO)


async def test_transcribe_not_configured() -> None:
    with pytest.raises(SpeechUnavailableError):
        await OpenAISpeechToText(None, _settings()).transcribe(AUDIO)


# --- TTS ----------------------------------------------------------------------------


def _speech_handler(calls: list[dict]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=b"MP3DATA-" + str(len(calls)).encode(),
            headers={"content-type": "audio/mpeg"},
        )

    return handler


async def test_synthesize_sends_voice_speed_format_and_instructions() -> None:
    calls: list[dict] = []
    tts = OpenAITextToSpeech(_client(_speech_handler(calls)), _settings())
    result = await tts.synthesize(
        SpeechRequest(text="Arre yaar!", voice="coral", speed=1.25, instructions="warm")
    )
    assert calls == [
        {
            "model": "test-tts",
            "voice": "coral",
            "input": "Arre yaar!",
            "response_format": "mp3",
            "speed": 1.25,
            "instructions": "warm",
        }
    ]
    assert result.audio == b"MP3DATA-1"
    assert result.mime_type == "audio/mpeg"


async def test_instructions_omitted_for_tts_1_models() -> None:
    calls: list[dict] = []
    tts = OpenAITextToSpeech(_client(_speech_handler(calls)), _settings(openai_tts_model="tts-1"))
    await tts.synthesize(SpeechRequest(text="Hi", voice="coral", instructions="warm"))
    assert "instructions" not in calls[0]


async def test_long_text_is_chunked_and_concatenated() -> None:
    calls: list[dict] = []
    tts = OpenAITextToSpeech(_client(_speech_handler(calls)), _settings())
    text = " ".join(["This is a sentence that keeps going."] * 200)  # > 4096 chars
    result = await tts.synthesize(SpeechRequest(text=text, voice="coral"))
    assert len(calls) >= 2
    assert all(len(c["input"]) <= 4096 for c in calls)
    assert result.audio == b"".join(f"MP3DATA-{i}".encode() for i in range(1, len(calls) + 1))


async def test_long_text_rejected_for_non_concatenable_format() -> None:
    tts = OpenAITextToSpeech(_client(_speech_handler([])), _settings())
    with pytest.raises(ValidationAppError):
        await tts.synthesize(
            SpeechRequest(text="word. " * 1000, voice="coral", format=OutputAudioFormat.WAV)
        )


async def test_stream_yields_provider_bytes() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 50_000, headers={"content-type": "audio/mpeg"})

    tts = OpenAITextToSpeech(_client(handler), _settings())
    chunks = [c async for c in tts.stream(SpeechRequest(text="Hello", voice="coral"))]
    assert b"".join(chunks) == b"x" * 50_000
    assert len(chunks) > 1


async def test_tts_errors_are_speech_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "x", "type": "x"}})

    with pytest.raises(SpeechUnavailableError):
        await OpenAITextToSpeech(_client(handler), _settings()).synthesize(
            SpeechRequest(text="Hi", voice="coral")
        )


async def test_tts_not_configured() -> None:
    with pytest.raises(SpeechUnavailableError):
        await OpenAITextToSpeech(None, _settings()).synthesize(SpeechRequest(text="Hi", voice="c"))
