from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.user import User
from tests.conftest import build_app

API = "/api/v1"
EMAIL = "friend@example.com"
PASSWORD = "a-good-password"


async def register(client: httpx.AsyncClient, email: str = EMAIL, **extra) -> httpx.Response:
    return await client.post(
        f"{API}/auth/register", json={"email": email, "password": PASSWORD, **extra}
    )


async def login(client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD) -> dict:
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- registration -------------------------------------------------------------


async def test_register_returns_public_user(db_client: httpx.AsyncClient) -> None:
    response = await register(db_client, display_name="Asha", timezone="Asia/Kolkata")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == EMAIL
    assert body["is_active"] is True
    assert "password" not in response.text and "password_hash" not in body


async def test_duplicate_email_is_case_insensitive(db_client: httpx.AsyncClient) -> None:
    assert (await register(db_client)).status_code == 201
    response = await register(db_client, email=EMAIL.upper())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_weak_password_rejected_without_echoing_it(db_client: httpx.AsyncClient) -> None:
    response = await db_client.post(
        f"{API}/auth/register", json={"email": EMAIL, "password": "tiny1"}
    )
    assert response.status_code == 422
    assert "tiny1" not in response.text


# --- login --------------------------------------------------------------------


async def test_login_returns_token_pair(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    tokens = await login(db_client, email="Friend@Example.com")
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert 0 < tokens["expires_in"] <= 30 * 60


@pytest.mark.parametrize(
    ("email", "password"), [(EMAIL, "wrong-password"), ("nobody@example.com", PASSWORD)]
)
async def test_bad_credentials_give_identical_401(
    db_client: httpx.AsyncClient, email: str, password: str
) -> None:
    await register(db_client)
    response = await db_client.post(
        f"{API}/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Incorrect email or password."
    assert response.headers["www-authenticate"] == "Bearer"


async def test_inactive_user_cannot_login(
    db_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await register(db_client)
    await db_session.execute(update(User).where(User.email == EMAIL).values(is_active=False))
    response = await db_client.post(
        f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 401


# --- protected endpoints ------------------------------------------------------


async def test_me_requires_authentication(db_client: httpx.AsyncClient) -> None:
    assert (await db_client.get(f"{API}/auth/me")).status_code == 401
    bad = await db_client.get(f"{API}/auth/me", headers=bearer("garbage"))
    assert bad.status_code == 401


async def test_me_with_access_token(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    tokens = await login(db_client)
    response = await db_client.get(f"{API}/auth/me", headers=bearer(tokens["access_token"]))
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


async def test_refresh_token_rejected_as_access_token(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    tokens = await login(db_client)
    response = await db_client.get(f"{API}/auth/me", headers=bearer(tokens["refresh_token"]))
    assert response.status_code == 401


# --- refresh ------------------------------------------------------------------


async def test_refresh_rotates_tokens(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    first = await login(db_client)
    response = await db_client.post(
        f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = await db_client.get(f"{API}/auth/me", headers=bearer(second["access_token"]))
    assert me.status_code == 200


async def test_refresh_token_reuse_revokes_all_sessions(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    first = await login(db_client)
    rotated = (
        await db_client.post(f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()

    # Replaying the already-rotated token is treated as theft...
    replay = await db_client.post(
        f"{API}/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401
    # ...which also invalidates the legitimately rotated token.
    after = await db_client.post(
        f"{API}/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert after.status_code == 401


async def test_access_token_rejected_by_refresh(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    tokens = await login(db_client)
    response = await db_client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert response.status_code == 401


# --- logout -------------------------------------------------------------------


async def test_logout_revokes_access_and_refresh_tokens(db_client: httpx.AsyncClient) -> None:
    await register(db_client)
    tokens = await login(db_client)
    headers = bearer(tokens["access_token"])

    response = await db_client.post(
        f"{API}/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=headers
    )
    assert response.status_code == 204

    assert (await db_client.get(f"{API}/auth/me", headers=headers)).status_code == 401
    refresh = await db_client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_logout_requires_authentication(db_client: httpx.AsyncClient) -> None:
    assert (await db_client.post(f"{API}/auth/logout", json={})).status_code == 401


# --- rate limiting ------------------------------------------------------------


@pytest.fixture
async def limited_client(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> AsyncIterator[httpx.AsyncClient]:
    settings = infra_settings.model_copy(update={"auth_rate_limit_per_minute": 2})
    transport = httpx.ASGITransport(app=build_app(settings, db_session, redis_client))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_login_is_rate_limited(limited_client: httpx.AsyncClient) -> None:
    body = {"email": EMAIL, "password": "wrong-password"}
    statuses = [
        (await limited_client.post(f"{API}/auth/login", json=body)).status_code for _ in range(3)
    ]
    assert statuses == [401, 401, 429]
    blocked = await limited_client.post(f"{API}/auth/login", json=body)
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0
