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
