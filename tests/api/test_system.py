import httpx
from fastapi import FastAPI

from app.api.dependencies import get_health_service
from app.main import create_app
from app.services.health import HealthService
from tests.conftest import make_settings


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("down")


def _override_checks(app: FastAPI, **checks) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(checks)


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


async def test_ready_when_dependencies_up(app: FastAPI, client: httpx.AsyncClient) -> None:
    _override_checks(app, database=_ok, redis=_ok)
    response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_not_ready_returns_503(app: FastAPI, client: httpx.AsyncClient) -> None:
    _override_checks(app, database=_ok, redis=_fail)
    response = await client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "error"


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    generated = await client.get("/api/v1/health")
    assert len(generated.headers["x-request-id"]) == 32

    echoed = await client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"


async def test_malformed_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "bad id\x01" * 10})
    assert response.headers["x-request-id"] != "bad id\x01" * 10


async def test_unknown_route_uses_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "http_error"
    assert error["request_id"] == response.headers["x-request-id"]


async def test_docs_disabled_in_production() -> None:
    app = create_app(make_settings(app_env="production"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
