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


async def test_security_headers_on_every_response(client: httpx.AsyncClient) -> None:
    for response in (await client.get("/api/v1/health"), await client.get("/nope")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"


async def test_trusted_hosts_reject_unknown_host_header() -> None:
    app = create_app(make_settings(trusted_hosts=["api.example.com"]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.test") as client:
        assert (await client.get("/api/v1/health")).status_code == 400
    async with httpx.AsyncClient(transport=transport, base_url="http://api.example.com") as client:
        assert (await client.get("/api/v1/health")).status_code == 200
    # Loopback stays allowed for the container healthcheck.
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        assert (await client.get("/api/v1/health")).status_code == 200


async def test_chunked_body_without_content_length_is_cut_off(client: httpx.AsyncClient) -> None:
    """A streamed body can't bypass the size limit by omitting Content-Length."""

    async def oversized():
        yield b'{"email": "a@example.com", "password": "'
        for _ in range(3):
            yield b"x" * (512 * 1024)  # 1.5 MB total > the 1 MB JSON limit
        yield b'"}'

    response = await client.post("/api/v1/auth/login", content=oversized())
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


async def test_small_chunked_body_still_works(db_client: httpx.AsyncClient) -> None:
    async def small():
        yield b"{}"

    response = await db_client.post("/api/v1/auth/login", content=small())
    assert response.status_code == 422  # reached validation: the body was read normally


async def test_cors_headers_present_on_413() -> None:
    """Browsers can only read an error response if it carries CORS headers."""
    app = create_app(make_settings(cors_origins=["https://app.example.com"]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            content=b"x" * (2 * 1024 * 1024),
            headers={"Origin": "https://app.example.com", "content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


async def test_voice_test_page_served_in_development(client: httpx.AsyncClient) -> None:
    response = await client.get("/voice-test")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/ws/voice/" in response.text


async def test_voice_test_page_absent_in_production_or_when_disabled() -> None:
    for settings in (make_settings(app_env="production"), make_settings(test_ui_enabled=False)):
        transport = httpx.ASGITransport(app=create_app(settings))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/voice-test")).status_code == 404
