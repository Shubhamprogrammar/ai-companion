import base64
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.storage import LocalAudioStore
from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.conversation import Message
from tests.conftest import _client_for, build_app, register_and_login
from tests.fakes import DEFAULT_REPLY, FakeLLM, FakeSTT, FakeTTS, fake_mp3, fake_wav

API = "/api/v1"


def _upload(data: bytes, name: str = "clip.wav", mime: str = "audio/wav") -> dict:
    return {"file": (name, data, mime)}


async def _messages(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Message))


# --- auth & validation --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/voice/transcribe", "/voice/turn"])
async def test_requires_auth(db_client: httpx.AsyncClient, path: str) -> None:
    response = await db_client.post(f"{API}{path}", files=_upload(fake_wav()))
    assert response.status_code == 401


async def test_unsupported_format_is_415_and_never_reaches_provider(
    db_client: httpx.AsyncClient, fake_stt: FakeSTT
) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/voice/transcribe",
        files=_upload(b"%PDF-1.7" + b"\x00" * 1024, "evil.wav", "audio/wav"),
        headers=headers,
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_audio_format"
    assert fake_stt.calls == []


async def test_too_large_upload_is_413(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    settings = infra_settings.model_copy(update={"max_audio_size_mb": 1})
    async for client in _client_for(build_app(settings, db_session, redis_client)):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/transcribe", files=_upload(fake_wav(2 * 1024 * 1024)), headers=headers
        )
    assert response.status_code == 413


async def test_oversized_body_rejected_before_parsing(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/voice/transcribe",
        content=b"x" * 10,
        headers={**headers, "content-length": str(500 * 1024 * 1024), "content-type": "audio/wav"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


async def test_empty_file_is_422(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(f"{API}/voice/transcribe", files=_upload(b""), headers=headers)
    assert response.status_code == 422


# --- transcribe -----------------------------------------------------------------


async def test_transcribe_returns_text_and_stores_nothing(
    db_client: httpx.AsyncClient, fake_stt: FakeSTT, db_session: AsyncSession
) -> None:
    headers = await register_and_login(db_client)
    # The format comes from the bytes, not the (wrong) filename or content type.
    response = await db_client.post(
        f"{API}/voice/transcribe",
        files=_upload(fake_mp3(), "recording.wav", "application/octet-stream"),
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"text": fake_stt.text, "duration_seconds": 1.5}
    assert fake_stt.calls[0].filename == "audio.mp3"
    assert await _messages(db_session) == 0


async def test_transcribe_no_speech_is_422(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    app = build_app(infra_settings, db_session, redis_client, stt=FakeSTT(text=""))
    async for client in _client_for(app):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/transcribe", files=_upload(fake_wav()), headers=headers
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_speech_detected"


# --- synthesize -----------------------------------------------------------------


async def test_synthesize_streams_audio_using_preferences(
    db_client: httpx.AsyncClient, fake_tts: FakeTTS, infra_settings: Settings
) -> None:
    headers = await register_and_login(db_client)
    await db_client.patch(
        f"{API}/users/me/preferences", json={"voice": "marin", "speech_speed": 1.2}, headers=headers
    )
    response = await db_client.post(
        f"{API}/voice/synthesize", json={"text": "**Arre** yaar 💙 chill!"}, headers=headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == FakeTTS.AUDIO
    request = fake_tts.requests[0]
    assert (request.voice, request.speed) == ("marin", 1.2)
    assert request.text == "Arre yaar chill!"  # markup/emoji removed before speaking
    assert request.instructions  # personality's delivery instructions are applied


async def test_synthesize_explicit_overrides_and_format(
    db_client: httpx.AsyncClient, fake_tts: FakeTTS
) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/voice/synthesize",
        json={"text": "Hello", "voice": "cedar", "speed": 0.9, "format": "wav"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert (fake_tts.requests[0].voice, fake_tts.requests[0].speed) == ("cedar", 0.9)


async def test_synthesize_unknown_voice_is_422(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/voice/synthesize", json={"text": "Hello", "voice": "robot"}, headers=headers
    )
    assert response.status_code == 422


async def test_synthesize_without_any_voice_configured_is_503(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    settings = infra_settings.model_copy(update={"openai_tts_voice": None})
    async for client in _client_for(build_app(settings, db_session, redis_client)):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/synthesize", json={"text": "Hello"}, headers=headers
        )
    assert response.status_code == 503


async def test_synthesize_provider_failure_is_503_not_broken_stream(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    app = build_app(infra_settings, db_session, redis_client, tts=FakeTTS(fail=True))
    async for client in _client_for(app):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/synthesize", json={"text": "Hello", "voice": "coral"}, headers=headers
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "speech_unavailable"


async def test_preferences_reject_unknown_voice(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.patch(
        f"{API}/users/me/preferences", json={"voice": "robot"}, headers=headers
    )
    assert response.status_code == 422
    assert "coral" in response.json()["error"]["details"]["allowed"]


# --- complete turn --------------------------------------------------------------


async def test_voice_turn_full_pipeline(
    db_client: httpx.AsyncClient,
    fake_stt: FakeSTT,
    fake_llm: FakeLLM,
    fake_tts: FakeTTS,
) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(f"{API}/voice/turn", files=_upload(fake_wav()), headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["user_message"]["content"] == fake_stt.text
    assert body["user_message"]["message_type"] == "voice"
    assert body["assistant_message"]["content"] == DEFAULT_REPLY
    assert body["assistant_message"]["message_type"] == "voice"
    assert base64.b64decode(body["audio"]["data_base64"]) == FakeTTS.AUDIO
    assert body["audio"]["mime_type"] == "audio/mpeg"
    assert body["audio_error"] is None
    timings = body["timings"]
    assert timings["stt_ms"] == 12.0 and timings["tts_ms"] == 8.0
    assert timings["total_ms"] >= 0

    # The companion was told it's a voice conversation; TTS spoke the reply.
    assert "This is a voice conversation" in fake_llm.requests[0].instructions
    assert fake_tts.requests[0].text == DEFAULT_REPLY

    # Continue the same conversation by voice.
    cid = body["conversation"]["id"]
    second = await db_client.post(
        f"{API}/voice/turn",
        files=_upload(fake_wav()),
        data={"conversation_id": cid, "response_format": "opus"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["conversation"]["id"] == cid
    assert second.json()["audio"]["format"] == "opus"
    assert len(fake_llm.requests[1].messages) == 3  # previous turn + new transcript


async def test_voice_turn_no_speech_saves_nothing(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM()
    app = build_app(infra_settings, db_session, redis_client, llm, stt=FakeSTT(text="  "))
    async for client in _client_for(app):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/turn", files=_upload(fake_wav()), headers=headers
        )
    assert response.status_code == 422
    assert llm.requests == []
    assert await _messages(db_session) == 0


async def test_voice_turn_stt_failure_is_503_and_saves_nothing(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    app = build_app(infra_settings, db_session, redis_client, stt=FakeSTT(fail=True))
    async for client in _client_for(app):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/turn", files=_upload(fake_wav()), headers=headers
        )
    assert response.status_code == 503
    assert await _messages(db_session) == 0


async def test_voice_turn_tts_failure_still_returns_saved_text(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    app = build_app(infra_settings, db_session, redis_client, tts=FakeTTS(fail=True))
    async for client in _client_for(app):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/voice/turn", files=_upload(fake_wav()), headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert body["audio"] is None
    assert body["audio_error"]["code"] == "speech_unavailable"
    assert body["assistant_message"]["content"] == DEFAULT_REPLY
    assert await _messages(db_session) == 2


async def test_voice_turn_unknown_conversation_is_404(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/voice/turn",
        files=_upload(fake_wav()),
        data={"conversation_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert response.status_code == 404


# --- audio retention ------------------------------------------------------------


async def test_audio_not_retained_by_default(db_client: httpx.AsyncClient, tmp_path: Path) -> None:
    headers = await register_and_login(db_client)
    await db_client.post(f"{API}/voice/turn", files=_upload(fake_wav()), headers=headers)
    assert list(tmp_path.iterdir()) == []
    assert not Path("audio_storage").exists()


async def test_audio_retained_when_enabled_and_deleted_with_conversation(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient, tmp_path: Path
) -> None:
    store = LocalAudioStore(tmp_path)
    app = build_app(infra_settings, db_session, redis_client, audio_store=store)
    async for client in _client_for(app):
        headers = await register_and_login(client)
        body = (
            await client.post(f"{API}/voice/turn", files=_upload(fake_wav()), headers=headers)
        ).json()
        user_id = (await client.get(f"{API}/auth/me", headers=headers)).json()["id"]
        cid, mid = body["conversation"]["id"], body["user_message"]["id"]
        path = store.path_for(uuid.UUID(user_id), uuid.UUID(cid), uuid.UUID(mid), "wav")
        assert path.read_bytes() == fake_wav()

        assert (
            await client.delete(f"{API}/conversations/{cid}", headers=headers)
        ).status_code == 204
    assert not path.exists()
