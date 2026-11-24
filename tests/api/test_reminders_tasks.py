import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.conftest import register_and_login

API = "/api/v1"


def _future(hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


async def test_reminder_crud(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    created = await db_client.post(
        f"{API}/reminders",
        json={"title": "Study Python", "scheduled_at": _future()},
        headers=headers,
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    assert created.json()["status"] == "pending"

    patched = await db_client.patch(
        f"{API}/reminders/{rid}", json={"status": "done"}, headers=headers
    )
    assert patched.json()["status"] == "done"
    listing = await db_client.get(f"{API}/reminders?status=done", headers=headers)
    assert [r["id"] for r in listing.json()["items"]] == [rid]

    assert (await db_client.delete(f"{API}/reminders/{rid}", headers=headers)).status_code == 204
    assert (await db_client.get(f"{API}/reminders/{rid}", headers=headers)).status_code == 404


@pytest.mark.parametrize(
    "scheduled_at",
    ["2020-01-01T09:00:00+00:00", "2026-11-25T09:00:00", "2099-01-01T00:00:00+00:00", "tomorrow"],
)
async def test_reminder_time_validation(db_client: httpx.AsyncClient, scheduled_at: str) -> None:
    headers = await register_and_login(db_client)
    response = await db_client.post(
        f"{API}/reminders", json={"title": "x", "scheduled_at": scheduled_at}, headers=headers
    )
    assert response.status_code == 422


async def test_reminders_sorted_and_filtered_by_time(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    for title, hours in (("later", 48), ("soon", 2), ("middle", 24)):
        await db_client.post(
            f"{API}/reminders",
            json={"title": title, "scheduled_at": _future(hours)},
            headers=headers,
        )
    items = (await db_client.get(f"{API}/reminders", headers=headers)).json()["items"]
    assert [r["title"] for r in items] == ["soon", "middle", "later"]
    window = await db_client.get(
        f"{API}/reminders", params={"from": _future(12), "to": _future(36)}, headers=headers
    )
    assert [r["title"] for r in window.json()["items"]] == ["middle"]


async def test_task_crud_and_ordering(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    for title, due in (("no due", None), ("later", "2027-01-10"), ("sooner", "2026-12-01")):
        r = await db_client.post(
            f"{API}/tasks", json={"title": title, "due_date": due}, headers=headers
        )
        assert r.status_code == 201
    items = (await db_client.get(f"{API}/tasks", headers=headers)).json()["items"]
    assert [t["title"] for t in items] == ["sooner", "later", "no due"]

    tid = items[0]["id"]
    done = await db_client.patch(f"{API}/tasks/{tid}", json={"status": "done"}, headers=headers)
    assert done.json()["status"] == "done"
    todo = (await db_client.get(f"{API}/tasks?status=todo", headers=headers)).json()["items"]
    assert tid not in [t["id"] for t in todo]
    assert (await db_client.delete(f"{API}/tasks/{tid}", headers=headers)).status_code == 204


async def test_ownership_is_enforced(db_client: httpx.AsyncClient) -> None:
    owner = await register_and_login(db_client, "owner@example.com")
    intruder = await register_and_login(db_client, "intruder@example.com")
    rid = (
        await db_client.post(
            f"{API}/reminders", json={"title": "x", "scheduled_at": _future()}, headers=owner
        )
    ).json()["id"]
    tid = (await db_client.post(f"{API}/tasks", json={"title": "x"}, headers=owner)).json()["id"]
    for path in (f"/reminders/{rid}", f"/tasks/{tid}"):
        for method in ("GET", "PATCH", "DELETE"):
            if method == "GET" and path.startswith("/tasks"):
                continue
            r = await db_client.request(
                method, f"{API}{path}", json={"title": "hijack"}, headers=intruder
            )
            assert r.status_code == 404, (method, path)
    assert (await db_client.get(f"{API}/reminders", headers=intruder)).json()["items"] == []


async def test_unknown_ids_and_nulls(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    assert (
        await db_client.get(f"{API}/reminders/{uuid.uuid4()}", headers=headers)
    ).status_code == 404
    tid = (await db_client.post(f"{API}/tasks", json={"title": "x"}, headers=headers)).json()["id"]
    r = await db_client.patch(f"{API}/tasks/{tid}", json={"title": None}, headers=headers)
    assert r.status_code == 422
