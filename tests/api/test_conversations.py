import uuid

import httpx

from tests.conftest import register_and_login

API = "/api/v1"


async def test_requires_auth(db_client: httpx.AsyncClient) -> None:
    assert (await db_client.get(f"{API}/conversations")).status_code == 401
    assert (await db_client.post(f"{API}/conversations", json={})).status_code == 401


async def test_create_get_update_delete(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)

    created = await db_client.post(
        f"{API}/conversations", json={"title": "Career"}, headers=headers
    )
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["title"] == "Career"
    assert conversation["status"] == "active"
    cid = conversation["id"]

    fetched = await db_client.get(f"{API}/conversations/{cid}", headers=headers)
    assert fetched.json()["id"] == cid

    patched = await db_client.patch(
        f"{API}/conversations/{cid}", json={"status": "archived", "title": None}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "archived"
    assert patched.json()["title"] is None

    assert (
        await db_client.delete(f"{API}/conversations/{cid}", headers=headers)
    ).status_code == 204
    assert (await db_client.get(f"{API}/conversations/{cid}", headers=headers)).status_code == 404


async def test_status_cannot_be_null(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    cid = (await db_client.post(f"{API}/conversations", json={}, headers=headers)).json()["id"]
    response = await db_client.patch(
        f"{API}/conversations/{cid}", json={"status": None}, headers=headers
    )
    assert response.status_code == 422


async def test_list_pagination_and_status_filter(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    ids = [
        (
            await db_client.post(f"{API}/conversations", json={"title": f"c{i}"}, headers=headers)
        ).json()["id"]
        for i in range(3)
    ]
    await db_client.patch(
        f"{API}/conversations/{ids[0]}", json={"status": "archived"}, headers=headers
    )

    page = (await db_client.get(f"{API}/conversations?limit=2", headers=headers)).json()
    assert len(page["items"]) == 2 and page["has_more"] is True
    rest = (await db_client.get(f"{API}/conversations?limit=2&offset=2", headers=headers)).json()
    assert len(rest["items"]) == 1 and rest["has_more"] is False

    archived = (await db_client.get(f"{API}/conversations?status=archived", headers=headers)).json()
    assert [c["id"] for c in archived["items"]] == [ids[0]]

    assert (
        await db_client.get(f"{API}/conversations?limit=1000", headers=headers)
    ).status_code == 422


async def test_other_users_conversations_are_invisible(db_client: httpx.AsyncClient) -> None:
    owner = await register_and_login(db_client, "owner@example.com")
    intruder = await register_and_login(db_client, "intruder@example.com")
    cid = (
        await db_client.post(f"{API}/conversations", json={"title": "private"}, headers=owner)
    ).json()["id"]

    for method in ("GET", "PATCH", "DELETE"):
        response = await db_client.request(
            method, f"{API}/conversations/{cid}", json={"title": "x"}, headers=intruder
        )
        assert response.status_code == 404, method
    messages = await db_client.get(f"{API}/conversations/{cid}/messages", headers=intruder)
    assert messages.status_code == 404
    listing = (await db_client.get(f"{API}/conversations", headers=intruder)).json()
    assert listing["items"] == []


async def test_unknown_conversation_is_404(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.get(f"{API}/conversations/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
