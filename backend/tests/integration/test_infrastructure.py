import uuid

import httpx
import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.core.database import Database
from app.core.redis import RedisClient
from app.main import create_app


async def test_postgres_connects_and_pgvector_works(infra_settings: Settings) -> None:
    db = Database(infra_settings)
    try:
        await db.ping()
        async with db.engine.connect() as conn:
            distance = await conn.scalar(text("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector"))
        assert distance == pytest.approx(1.0)
    finally:
        await db.dispose()


async def test_redis_roundtrip(redis_client: RedisClient) -> None:
    key = f"test:{uuid.uuid4().hex}"
    await redis_client.ping()
    await redis_client.client.set(key, "value", ex=10)
    assert await redis_client.client.get(key) == "value"


async def test_ready_endpoint_with_real_dependencies(infra_settings: Settings) -> None:
    app = create_app(infra_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_session_slots_cap_release_and_lease_expiry(redis_client) -> None:
    import uuid

    from app.services.voice_session import SessionSlots

    user = uuid.uuid4()
    slots = SessionSlots(redis_client, limit=2, lease_seconds=60)
    assert await slots.acquire(user, "a") and await slots.acquire(user, "b")
    assert not await slots.acquire(user, "c")  # at the cap
    await slots.release(user, "a")
    assert await slots.acquire(user, "c")

    # A session that died without releasing (crash) frees its slot once its lease lapses.
    crashed = SessionSlots(redis_client, limit=1, lease_seconds=1)
    other = uuid.uuid4()
    assert await crashed.acquire(other, "lost")
    assert not await crashed.acquire(other, "new")
    await redis_client.client.zadd(f"ws:voice-sessions:{other}", {"lost": 0})  # lease lapsed
    assert await crashed.acquire(other, "new")
