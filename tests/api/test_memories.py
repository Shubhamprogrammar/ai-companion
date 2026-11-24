import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.redis import RedisClient
from tests.conftest import _client_for, build_app, register_and_login
from tests.fakes import FakeEmbeddings

API = "/api/v1"


async def _create(client: httpx.AsyncClient, headers: dict, content: str, **kw) -> dict:
    response = await client.post(
        f"{API}/memories", json={"content": content, **kw}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_requires_auth(db_client: httpx.AsyncClient) -> None:
    assert (await db_client.get(f"{API}/memories")).status_code == 401


async def test_crud(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    created = await _create(
        db_client, headers, "My favourite food is rajma chawal", category="preference"
    )
    assert created["source"] == "user" and created["importance"] == 4
    assert "embedding" not in created
    mid = created["id"]

    listed = (await db_client.get(f"{API}/memories", headers=headers)).json()
    assert [m["id"] for m in listed["items"]] == [mid]

    patched = await db_client.patch(
        f"{API}/memories/{mid}",
        json={"content": "My favourite food is chole bhature", "importance": 5},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["content"] == "My favourite food is chole bhature"

    assert (await db_client.delete(f"{API}/memories/{mid}", headers=headers)).status_code == 204
    assert (await db_client.get(f"{API}/memories/{mid}", headers=headers)).status_code == 404


async def test_category_filter_and_pagination(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    for i in range(3):
        await _create(db_client, headers, f"Goal number {i} for this year", category="goal")
    await _create(db_client, headers, "Prefers short answers", category="preference")
    goals = (await db_client.get(f"{API}/memories?category=goal&limit=2", headers=headers)).json()
    assert len(goals["items"]) == 2 and goals["has_more"] is True
    assert {m["category"] for m in goals["items"]} == {"goal"}


async def test_semantic_search(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    await _create(db_client, headers, "User is learning Python and FastAPI")
    await _create(db_client, headers, "User's mother lives in Jaipur")
    response = await db_client.post(
        f"{API}/memories/search",
        json={"query": "what is the user learning, python?"},
        headers=headers,
    )
    results = response.json()["results"]
    assert results[0]["memory"]["content"] == "User is learning Python and FastAPI"
    assert 0 < results[0]["similarity"] <= 1


async def test_users_cannot_see_or_touch_each_others_memories(db_client: httpx.AsyncClient) -> None:
    owner = await register_and_login(db_client, "owner@example.com")
    intruder = await register_and_login(db_client, "intruder@example.com")
    mid = (await _create(db_client, owner, "Owner's secret plan is to learn Rust"))["id"]

    for method in ("GET", "PATCH", "DELETE"):
        r = await db_client.request(
            method, f"{API}/memories/{mid}", json={"importance": 1}, headers=intruder
        )
        assert r.status_code == 404, method
    assert (await db_client.get(f"{API}/memories", headers=intruder)).json()["items"] == []
    search = await db_client.post(
        f"{API}/memories/search", json={"query": "Rust plan"}, headers=intruder
    )
    assert search.json()["results"] == []


async def test_delete_all_requires_confirmation(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    await _create(db_client, headers, "Something worth keeping")
    assert (await db_client.delete(f"{API}/memories", headers=headers)).status_code == 422
    response = await db_client.delete(f"{API}/memories?confirm=true", headers=headers)
    assert response.json() == {"deleted": 1}
    assert (await db_client.get(f"{API}/memories", headers=headers)).json()["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"content": "hi"},
        {"content": "x" * 501},
        {"content": "ok ok", "importance": 9},
        {"content": "valid content", "category": "secret"},
        {"content": "valid", "embedding": [1]},
    ],
)
async def test_validation(db_client: httpx.AsyncClient, body: dict) -> None:
    headers = await register_and_login(db_client)
    assert (await db_client.post(f"{API}/memories", json=body, headers=headers)).status_code == 422


async def test_patch_rejects_nulls(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    mid = (await _create(db_client, headers, "Something worth keeping"))["id"]
    r = await db_client.patch(f"{API}/memories/{mid}", json={"content": None}, headers=headers)
    assert r.status_code == 422


async def test_create_fails_cleanly_when_embeddings_down(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    app = build_app(infra_settings, db_session, redis_client, embeddings=FakeEmbeddings(fail=True))
    async for client in _client_for(app):
        headers = await register_and_login(client)
        r = await client.post(f"{API}/memories", json={"content": "Something"}, headers=headers)
    assert r.status_code == 503


async def test_unknown_memory_is_404(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    assert (
        await db_client.get(f"{API}/memories/{uuid.uuid4()}", headers=headers)
    ).status_code == 404
