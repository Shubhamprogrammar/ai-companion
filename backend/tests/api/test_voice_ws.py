"""WebSocket voice sessions end to end (same event loop as the DB fixtures)."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wsproto.events import BytesMessage, TextMessage

from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.conversation import Message
from app.models.enums import MessageRole
from tests.conftest import build_app, register_and_login
from tests.fakes import FakeLLM, FakeSTT, FakeTTS, fake_wav

WS = "http://test/api/v1/ws/voice"
LONG = "Arre yaar sun. " + " ".join(f"Sentence number {i} goes here." for i in range(40))
TERMINAL = {"response.done", "response.cancelled", "error", "session.closed"}


@contextlib.asynccontextmanager
async def _clients(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app), base_url="http://test"
    ) as c:
        yield c


async def _token(client: httpx.AsyncClient, email: str = "voice@example.com") -> str:
    headers = await register_and_login(client, email, timezone="Asia/Kolkata")
    return headers["Authorization"].removeprefix("Bearer ")


async def _events(ws, until: set[str] = TERMINAL, limit: int = 500) -> list[tuple[str, object]]:
    """Collect (type, payload) until a terminal event; binary frames are ("audio", bytes)."""
    out: list[tuple[str, object]] = []
    for _ in range(limit):
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        if isinstance(message, BytesMessage):
            out.append(("audio", bytes(message.data)))
            continue
        assert isinstance(message, TextMessage)
        payload = json.loads(message.data)
        out.append((payload["type"], payload))
        if payload["type"] in until:
            return out
    raise AssertionError("no terminal event")


async def _expect_close(ws) -> int:
    """Read any remaining frames (e.g. session.closed) until the server closes the socket."""
    with pytest.raises(WebSocketDisconnect) as closed:
        for _ in range(10):
            await asyncio.wait_for(ws.receive(), timeout=5)
    return closed.value.code


def _types(events) -> list[str]:
    return [t for t, _ in events]


async def _start(ws, token: str, **config) -> dict:
    await ws.send_json({"type": "session.start", "token": token, "input_format": "wav", **config})
    ready = await ws.receive_json()
    assert ready["type"] == "session.ready", ready
    return ready


async def _speak(ws, audio: bytes = fake_wav(4096)) -> None:
    await ws.send_json({"type": "speech.start"})
    half = len(audio) // 2
    await ws.send_bytes(audio[:half])
    await ws.send_bytes(audio[half:])
    await ws.send_json({"type": "speech.end"})


@pytest.fixture
def ws_app(infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient):
    def factory(llm: FakeLLM | None = None, settings: Settings | None = None, **kw):
        return build_app(
            settings or infra_settings, db_session, redis_client, llm or FakeLLM(), **kw
        )

    return factory


# --- connection, auth, limits ------------------------------------------------------


async def test_full_voice_turn_streams_text_and_audio(ws_app, db_session: AsyncSession) -> None:
    llm, stt, tts = FakeLLM(["Arre yaar, chill kar. Kal baat karte hain!"]), FakeSTT(), FakeTTS()
    app = ws_app(llm, stt=stt, tts=tts)
    async with _clients(app) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            ready = await _start(ws, token)
            assert ready["conversation_id"] is None and ready["output_format"] == "mp3"
            await _speak(ws)
            events = await _events(ws)

    types = _types(events)
    assert types[:2] == ["speech.started", "speech.stopped"]
    assert "transcript.delta" in types
    assert types.index("transcript.done") < types.index("response.started")
    assert types.index("response.started") < types.index("response.text.delta")
    assert (
        types.index("response.audio.started")
        < types.index("audio")
        < types.index("response.audio.done")
    )
    assert types[-1] == "response.done"

    transcript = next(p for t, p in events if t == "transcript.done")
    assert transcript["text"] == stt.text
    text = "".join(p["text"] for t, p in events if t == "response.text.delta")
    assert text == "Arre yaar, chill kar. Kal baat karte hain!"
    # One TTS request per sentence, streamed as binary frames.
    assert [r.text for r in tts.requests] == ["Arre yaar, chill kar.", "Kal baat karte hain!"]
    audio = b"".join(p for t, p in events if t == "audio")
    assert audio == FakeTTS.AUDIO * 2

    done = events[-1][1]
    assert done["user_message"]["message_type"] == "voice"
    assert done["assistant_message"]["content"] == text
    assert set(done["timings"]) == {"stt_ms", "first_text_ms", "first_audio_ms", "total_ms"}
    assert "This is a voice conversation" in llm.requests[0].instructions
    roles = (await db_session.scalars(select(Message.role).order_by(Message.created_at))).all()
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT]


async def test_multiple_turns_share_the_conversation(ws_app) -> None:
    llm = FakeLLM(["First reply.", "Second reply."])
    async with _clients(ws_app(llm)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await _speak(ws)
            first = (await _events(ws))[-1][1]
            await _speak(ws)
            second = (await _events(ws))[-1][1]
    assert first["conversation"]["id"] == second["conversation"]["id"]
    assert [m.content for m in llm.requests[1].messages][:2] == [FakeSTT().text, "First reply."]


async def test_header_auth_and_existing_conversation(ws_app) -> None:
    async with _clients(ws_app()) as client:
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        cid = (await client.post("/api/v1/conversations", json={}, headers=headers)).json()["id"]
        async with aconnect_ws(f"{WS}/{cid}", client, headers=headers) as ws:
            await ws.send_json({"type": "session.start", "input_format": "wav"})
            ready = await ws.receive_json()
    assert ready["conversation_id"] == cid


@pytest.mark.parametrize(
    ("first", "code"),
    [
        ({"type": "session.start", "token": "garbage"}, 4401),
        ({"type": "session.start"}, 4401),
        ({"type": "speech.start"}, 4400),
        ({"type": "session.start", "token": "x", "input_format": "midi"}, 4400),
    ],
)
async def test_bad_handshake_is_rejected(ws_app, first: dict, code: int) -> None:
    async with _clients(ws_app()) as client, aconnect_ws(f"{WS}/new", client) as ws:
        await ws.send_json(first)
        error = await ws.receive_json()
        assert error["type"] == "error" and error["fatal"] is True
        with pytest.raises(WebSocketDisconnect) as closed:
            await ws.receive_text()
    assert closed.value.code == code


async def test_auth_timeout(ws_app, infra_settings: Settings) -> None:
    settings = infra_settings.model_copy(update={"ws_auth_timeout_seconds": 0.2})
    async with (
        _clients(ws_app(settings=settings)) as client,
        aconnect_ws(f"{WS}/new", client) as ws,
    ):
        assert (await ws.receive_json())["code"] == "auth_timeout"
        assert await _expect_close(ws) == 4408


async def test_someone_elses_conversation_is_not_found(ws_app) -> None:
    async with _clients(ws_app()) as client:
        owner = await register_and_login(client, "owner@example.com")
        cid = (await client.post("/api/v1/conversations", json={}, headers=owner)).json()["id"]
        intruder = await _token(client, "intruder@example.com")

        async with aconnect_ws(f"{WS}/{cid}", client) as ws:
            await ws.send_json({"type": "session.start", "token": intruder})
            assert (await ws.receive_json())["code"] == "not_found"
            assert await _expect_close(ws) == 4404

        async with aconnect_ws(f"{WS}/not-a-uuid", client) as ws:
            assert (await ws.receive_json())["code"] == "not_found"
            assert await _expect_close(ws) == 4404


async def test_concurrent_session_cap(ws_app, infra_settings: Settings) -> None:
    settings = infra_settings.model_copy(update={"ws_max_sessions_per_user": 1})
    async with _clients(ws_app(settings=settings)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as first:
            await _start(first, token)
            async with aconnect_ws(f"{WS}/new", client) as second:
                await second.send_json({"type": "session.start", "token": token})
                error = await second.receive_json()
                assert error["code"] == "too_many_sessions"
                assert await _expect_close(second) == 4429
        # Slot is released on close: a new session works again.
        async with aconnect_ws(f"{WS}/new", client) as third:
            await _start(third, token)


# --- protocol behaviour ------------------------------------------------------------


async def test_barge_in_cancels_and_saves_partial_reply(ws_app, db_session: AsyncSession) -> None:
    llm = FakeLLM([LONG, "Haan bolo, kya hua?"], delay=0.02)
    async with _clients(ws_app(llm)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await _speak(ws)
            events = await _events(ws, until={"response.text.delta"})
            await ws.send_json({"type": "speech.start"})  # user talks over the reply
            events += await _events(ws, until={"response.cancelled"})
            cancelled = events[-1][1]
            assert (await ws.receive_json())["type"] == "speech.started"
            # The next utterance is answered normally.
            half = fake_wav(4096)
            await ws.send_bytes(half)
            await ws.send_json({"type": "speech.end"})
            final = (await _events(ws))[-1]
    assert cancelled["saved_text"] and cancelled["saved_text"] != LONG
    assert LONG.startswith(cancelled["saved_text"])
    assert final[0] == "response.done"
    assert final[1]["assistant_message"]["content"] == "Haan bolo, kya hua?"
    interrupted = (
        await db_session.scalars(
            select(Message)
            .where(Message.role == MessageRole.ASSISTANT)
            .order_by(Message.created_at)
        )
    ).all()
    assert interrupted[0].message_metadata.get("interrupted") is True
    assert interrupted[0].content == cancelled["saved_text"]


async def test_response_cancel(ws_app) -> None:
    async with _clients(ws_app(FakeLLM([LONG], delay=0.02))) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token, text_only=True)
            await ws.send_json({"type": "text.message", "text": "tell me a long story"})
            await _events(ws, until={"response.text.delta"})
            await ws.send_json({"type": "response.cancel"})
            events = await _events(ws, until={"response.cancelled"})
    assert events[-1][0] == "response.cancelled"


async def test_text_message_and_text_only_mode(ws_app) -> None:
    llm, tts = FakeLLM(["Theek hai. Chalo!"]), FakeTTS()
    async with _clients(ws_app(llm, tts=tts)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            ready = await _start(ws, token, text_only=True)
            await ws.send_json({"type": "text.message", "text": "Kal milte hain?"})
            events = await _events(ws)
    assert ready["output_format"] is None
    assert "audio" not in _types(events) and tts.requests == []
    done = events[-1][1]
    assert done["user_message"]["message_type"] == "text"
    assert "This is a voice conversation" not in llm.requests[0].instructions  # not spoken


async def test_pcm16_input_is_wrapped_for_stt(ws_app) -> None:
    stt = FakeSTT()
    async with _clients(ws_app(stt=stt)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token, input_format="pcm16", sample_rate=16000)
            await _speak(ws, b"\x01\x00" * 4000)
            await _events(ws)
    assert stt.calls[0].filename == "audio.wav"


async def test_protocol_errors_are_non_fatal(ws_app, infra_settings: Settings) -> None:
    settings = infra_settings.model_copy(update={"max_audio_size_mb": 1})
    async with _clients(ws_app(settings=settings)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await ws.send_text("{not json")
            assert (await ws.receive_json())["code"] == "invalid_message"
            await ws.send_json({"type": "speech.end"})
            assert (await ws.receive_json())["code"] == "no_audio"
            await ws.send_json({"type": "speech.start"})
            await ws.receive_json()
            for _ in range(3):
                await ws.send_bytes(b"\x00" * 400_000)  # 1.2 MB > 1 MB utterance limit
            errors = await _events(ws, until={"error"})
            assert errors[-1][1]["code"] == "audio_too_large"
            await ws.send_json({"type": "ping"})
            assert (await ws.receive_json())["type"] == "pong"
            await ws.send_json({"type": "session.end"})
            assert (await ws.receive_json()) == {"type": "session.closed", "reason": "session_end"}


async def test_no_speech_is_reported(ws_app) -> None:
    async with _clients(ws_app(stt=FakeSTT(text="  "))) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await _speak(ws)
            events = await _events(ws)
    assert events[-1][1]["code"] == "no_speech_detected"


async def test_tools_and_confirmation_over_websocket(ws_app) -> None:
    llm = FakeLLM()
    async with _clients(ws_app(llm)) as client:
        token = await _token(client)
        headers = {"Authorization": f"Bearer {token}"}
        rid = (
            await client.post(
                "/api/v1/reminders",
                headers=headers,
                json={"title": "Gym", "scheduled_at": "2030-01-01T09:00:00+05:30"},
            )
        ).json()["id"]
        llm._replies += [[("delete_reminder", {"reminder_id": rid})], "Pakka delete karun?"]  # noqa: SLF001
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token, text_only=True)
            await ws.send_json({"type": "text.message", "text": "gym reminder hata do"})
            events = await _events(ws)
            completed = next(p for t, p in events if t == "tool.completed")
            confirmation_id = completed["action"]["confirmation"]["confirmation_id"]
            assert "tool.started" in _types(events)
            await ws.send_json(
                {
                    "type": "confirmation.respond",
                    "confirmation_id": confirmation_id,
                    "decision": "approve",
                }
            )
            result = await ws.receive_json()
        reminder = await client.get(f"/api/v1/reminders/{rid}", headers=headers)
    assert result["type"] == "confirmation.result"
    assert result["execution"]["status"] == "succeeded"
    assert reminder.status_code == 404  # actually deleted


async def test_tts_failure_keeps_text_flowing(ws_app) -> None:
    async with _clients(ws_app(FakeLLM(["One. Two."]), tts=FakeTTS(fail=True))) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await _speak(ws)
            events = await _events(ws, until={"response.done"})
    types = _types(events)
    assert "error" in types and "audio" not in types
    assert types[-1] == "response.done"


async def test_typed_text_in_spoken_session_gets_voice_style_reply(ws_app) -> None:
    llm, tts = FakeLLM(["Haan bilkul."]), FakeTTS()
    async with _clients(ws_app(llm, tts=tts)) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await ws.send_json({"type": "text.message", "text": "Kya haal hai?"})
            events = await _events(ws)
    assert "This is a voice conversation" in llm.requests[0].instructions
    assert "audio" in _types(events)
    assert events[-1][1]["user_message"]["message_type"] == "text"


# --- token lifetime, connection limits, frame sizes ----------------------------------


async def test_session_closes_when_token_is_revoked(ws_app, monkeypatch) -> None:
    """Logging out elsewhere ends an open voice session (no more paid calls on that token)."""
    monkeypatch.setattr("app.api.websocket.voice.REVOCATION_CHECK_SECONDS", 0.05)
    async with _clients(ws_app()) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            logout = await client.post(
                "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}, json={}
            )
            assert logout.status_code == 204
            closed = await ws.receive_json()
            assert closed == {"type": "session.closed", "reason": "token_revoked"}
            assert await _expect_close(ws) == 4401


async def test_session_closes_when_token_expires(ws_app, monkeypatch) -> None:
    from app.services.voice_session import SocketAuth

    async with _clients(ws_app()) as client:
        token = await _token(client)
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            monkeypatch.setattr(SocketAuth, "seconds_left", lambda self: -1.0)
            # The receive already in progress delivers this ping; the next pass closes.
            await ws.send_json({"type": "ping"})
            assert (await ws.receive_json())["type"] == "pong"
            closed = await ws.receive_json()
            assert closed == {"type": "session.closed", "reason": "token_expired"}
            assert await _expect_close(ws) == 4401


async def test_auth_refresh_accepts_only_the_same_users_token(ws_app) -> None:
    async with _clients(ws_app()) as client:
        token = await _token(client)
        other = await _token(client, "someone-else@example.com")
        login = await client.post(  # the same user signs in again: a second valid token
            "/api/v1/auth/login",
            json={"email": "voice@example.com", "password": "a-good-password"},
        )
        fresh = login.json()["access_token"]
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await ws.send_json({"type": "auth.refresh", "token": other})
            assert (await ws.receive_json())["code"] == "unauthenticated"
            await ws.send_json({"type": "auth.refresh", "token": "garbage"})
            assert (await ws.receive_json())["code"] == "unauthenticated"
            await ws.send_json({"type": "auth.refresh", "token": fresh})
            refreshed = await ws.receive_json()
            assert refreshed["type"] == "auth.refreshed" and refreshed["expires_at"]
            await ws.send_json({"type": "ping"})  # session still open
            assert (await ws.receive_json())["type"] == "pong"


async def test_connection_attempts_are_rate_limited_per_ip(ws_app, infra_settings) -> None:
    settings = infra_settings.model_copy(update={"ws_connect_rate_limit_per_minute": 2})
    async with _clients(ws_app(settings=settings)) as client:
        for _ in range(2):
            async with aconnect_ws(f"{WS}/new", client) as ws:
                await ws.send_json({"type": "session.start", "token": "garbage"})
                assert await _expect_close(ws) == 4401
        async with aconnect_ws(f"{WS}/new", client) as ws:
            error = await ws.receive_json()
            assert error["code"] == "rate_limited" and error["fatal"] is True
            assert await _expect_close(ws) == 4429


async def test_oversized_frames_are_rejected(ws_app, infra_settings) -> None:
    settings = infra_settings.model_copy(update={"ws_max_frame_bytes": 2048})
    async with _clients(ws_app(settings=settings)) as client:
        token = await _token(client)
        # Pre-auth: a huge first frame isn't parsed.
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await ws.send_text(json.dumps({"type": "session.start", "token": "x" * 20_000}))
            assert (await ws.receive_json())["code"] == "protocol_error"
            assert await _expect_close(ws) == 4400
        async with aconnect_ws(f"{WS}/new", client) as ws:
            await _start(ws, token)
            await ws.send_text(json.dumps({"type": "text.message", "text": "a" * 3000}))
            assert (await ws.receive_json())["code"] == "frame_too_large"
            await ws.send_bytes(b"\0" * 3000)
            assert (await ws.receive_json())["code"] == "frame_too_large"
            await ws.send_json({"type": "ping"})
            assert (await ws.receive_json())["type"] == "pong"
