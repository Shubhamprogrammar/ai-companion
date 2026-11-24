import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.conversation import Conversation, Message
from tests.conftest import _client_for, build_app, register_and_login
from tests.fakes import DEFAULT_REPLY, FakeLLM

API = "/api/v1"
HINGLISH = "Yaar, I'm really confused about my career. I don't know what to focus on."


async def _count(session: AsyncSession, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_requires_auth(db_client: httpx.AsyncClient) -> None:
    response = await db_client.post(f"{API}/chat", json={"message": "hi"})
    assert response.status_code == 401


async def test_first_message_creates_conversation_and_persists_turn(
    db_client: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    headers = await register_and_login(db_client, display_name="Asha")
    response = await db_client.post(f"{API}/chat", json={"message": HINGLISH}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_message"]["content"] == HINGLISH
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["content"] == DEFAULT_REPLY
    assert body["assistant_message"]["role"] == "assistant"
    assert body["conversation"]["title"].startswith("Yaar, I'm really confused")

    # The companion was given the personality prompt, user context and language hint.
    request = fake_llm.requests[0]
    assert "Name: Asha" in request.instructions
    assert "appears to be in Hinglish" in request.instructions
    assert [m.content for m in request.messages] == [HINGLISH]

    cid = body["conversation"]["id"]
    messages = (await db_client.get(f"{API}/conversations/{cid}/messages", headers=headers)).json()
    assert [m["role"] for m in messages["items"]] == ["user", "assistant"]


async def test_follow_up_includes_previous_turns(
    db_client: httpx.AsyncClient,
    infra_settings: Settings,
    db_session: AsyncSession,
    redis_client: RedisClient,
) -> None:
    llm = FakeLLM(["Arre, don't stress yaar. Paths, or progress?", "Backend helps AI. Let's plan."])
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        first = (
            await client.post(f"{API}/chat", json={"message": HINGLISH}, headers=headers)
        ).json()
        cid = first["conversation"]["id"]
        second = await client.post(
            f"{API}/chat",
            json={"conversation_id": cid, "message": "I want AI, but my backend is weak."},
            headers=headers,
        )
    assert second.status_code == 200
    assert second.json()["conversation"]["id"] == cid
    assert [(m.role, m.content) for m in llm.requests[1].messages] == [
        ("user", HINGLISH),
        ("assistant", "Arre, don't stress yaar. Paths, or progress?"),
        ("user", "I want AI, but my backend is weak."),
    ]


async def test_ai_failure_returns_503_and_saves_nothing(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM(fail=True)
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        response = await client.post(f"{API}/chat", json={"message": "hello"}, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"
    assert await _count(db_session, Conversation) == 0
    assert await _count(db_session, Message) == 0


async def test_cannot_post_into_someone_elses_conversation(
    db_client: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    owner = await register_and_login(db_client, "owner@example.com")
    intruder = await register_and_login(db_client, "intruder@example.com")
    cid = (await db_client.post(f"{API}/chat", json={"message": "secret"}, headers=owner)).json()[
        "conversation"
    ]["id"]
    response = await db_client.post(
        f"{API}/chat", json={"conversation_id": cid, "message": "hi"}, headers=intruder
    )
    assert response.status_code == 404
    assert len(fake_llm.requests) == 1  # the intruder's turn never reached the model


@pytest.mark.parametrize("body", [{"message": ""}, {"message": "x" * 8001}, {"text": "hi"}])
async def test_invalid_chat_requests(db_client: httpx.AsyncClient, body: dict) -> None:
    headers = await register_and_login(db_client)
    assert (await db_client.post(f"{API}/chat", json=body, headers=headers)).status_code == 422


async def test_unknown_conversation_id(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/chat", json={"conversation_id": str(uuid.uuid4()), "message": "hi"}, headers=headers
    )
    assert response.status_code == 404


async def test_chat_is_rate_limited_per_user(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    settings = infra_settings.model_copy(update={"chat_rate_limit_per_minute": 2})
    async for client in _client_for(build_app(settings, db_session, redis_client)):
        headers = await register_and_login(client)
        codes = [
            (await client.post(f"{API}/chat", json={"message": "hi"}, headers=headers)).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429]


# --- streaming ----------------------------------------------------------------


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":")
        )
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_stream_emits_start_deltas_done_and_persists(
    db_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(f"{API}/chat/stream", json={"message": "hi"}, headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "start" and names[-1] == "done"
    assert set(names[1:-1]) == {"delta"}
    assert "".join(data["text"] for name, data in events if name == "delta") == DEFAULT_REPLY

    done = events[-1][1]
    assert done["conversation"]["id"] == events[0][1]["conversation_id"]
    assert done["assistant_message"]["content"] == DEFAULT_REPLY
    assert await _count(db_session, Message) == 2


async def test_stream_failure_emits_error_and_saves_nothing(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM(fail_after_deltas=2)
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        response = await client.post(f"{API}/chat/stream", json={"message": "hi"}, headers=headers)
    events = _parse_sse(response.text)
    assert [n for n, _ in events] == ["start", "delta", "delta", "error"]
    assert events[-1][1]["code"] == "ai_unavailable"
    assert await _count(db_session, Message) == 0


async def test_stream_unknown_conversation_is_plain_404(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/chat/stream",
        json={"conversation_id": str(uuid.uuid4()), "message": "hi"},
        headers=headers,
    )
    assert response.status_code == 404
