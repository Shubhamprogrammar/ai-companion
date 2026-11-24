import httpx
import pytest

API = "/api/v1"


@pytest.fixture
async def auth_headers(db_client: httpx.AsyncClient) -> dict[str, str]:
    await db_client.post(
        f"{API}/auth/register",
        json={"email": "me@example.com", "password": "a-good-password", "display_name": "Asha"},
    )
    response = await db_client.post(
        f"{API}/auth/login", json={"email": "me@example.com", "password": "a-good-password"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_endpoints_require_auth(db_client: httpx.AsyncClient) -> None:
    for method, path in [
        ("GET", "/users/me"),
        ("PATCH", "/users/me"),
        ("GET", "/users/me/preferences"),
        ("PATCH", "/users/me/preferences"),
    ]:
        response = await db_client.request(method, f"{API}{path}", json={})
        assert response.status_code == 401, path


async def test_get_me_includes_profile(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await db_client.get(f"{API}/users/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "me@example.com"
    assert body["profile"] == {
        "display_name": "Asha",
        "preferred_language": "auto",
        "timezone": "UTC",
        "updated_at": body["profile"]["updated_at"],
    }


async def test_patch_me_updates_only_sent_fields(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await db_client.patch(
        f"{API}/users/me",
        json={"preferred_language": "hinglish", "timezone": "Asia/Kolkata"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["preferred_language"] == "hinglish"
    assert profile["timezone"] == "Asia/Kolkata"
    assert profile["display_name"] == "Asha"  # untouched


async def test_patch_me_rejects_invalid_values(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    for body in ({"timezone": "Nowhere/City"}, {"preferred_language": "klingon"}, {"email": "x"}):
        response = await db_client.patch(f"{API}/users/me", json=body, headers=auth_headers)
        assert response.status_code == 422, body


async def test_default_preferences(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await db_client.get(f"{API}/users/me/preferences", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["personality"] == "warm_friend"
    assert body["voice"] is None
    assert body["speech_speed"] == 1.0
    assert body["memory_enabled"] is True
    assert body["conversation_retention_days"] is None


async def test_patch_preferences(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await db_client.patch(
        f"{API}/users/me/preferences",
        json={"memory_enabled": False, "speech_speed": 1.25, "conversation_retention_days": 30},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["memory_enabled"] is False
    assert body["speech_speed"] == 1.25
    assert body["conversation_retention_days"] == 30

    # Persisted
    again = await db_client.get(f"{API}/users/me/preferences", headers=auth_headers)
    assert again.json()["memory_enabled"] is False


async def test_users_only_see_their_own_data(db_client: httpx.AsyncClient) -> None:
    tokens = {}
    for email in ("one@example.com", "two@example.com"):
        await db_client.post(
            f"{API}/auth/register", json={"email": email, "password": "a-good-password"}
        )
        login = await db_client.post(
            f"{API}/auth/login", json={"email": email, "password": "a-good-password"}
        )
        tokens[email] = {"Authorization": f"Bearer {login.json()['access_token']}"}

    await db_client.patch(
        f"{API}/users/me", json={"display_name": "One"}, headers=tokens["one@example.com"]
    )
    two = await db_client.get(f"{API}/users/me", headers=tokens["two@example.com"])
    assert two.json()["email"] == "two@example.com"
    assert two.json()["profile"]["display_name"] is None
