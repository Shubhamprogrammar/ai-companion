"""Tool calling through /chat, /chat/stream, /voice/turn and the /tools API."""

import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.productivity import Reminder, ToolExecution
from tests.conftest import _client_for, build_app, register_and_login
from tests.fakes import FakeLLM, FakeWebSearch, fake_wav, tool_results

API = "/api/v1"


def _tomorrow_local() -> str:
    return (datetime.now(UTC) + timedelta(days=1)).date().isoformat()


def _create(title: str = "Study Python", at: str | None = None):
    return [
        (
            "create_reminder",
            {
                "title": title,
                "scheduled_at": at or f"{_tomorrow_local()}T09:00",
                "description": None,
            },
        )
    ]


async def _app(settings, session, redis, llm, **kw):
    return build_app(settings, session, redis, llm, **kw)


async def test_chat_creates_reminder_via_tool(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM([_create(), "Done yaar, kal subah 9 baje yaad dila dungi."])
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client, timezone="Asia/Kolkata")
        response = await client.post(
            f"{API}/chat",
            json={"message": "Remind me tomorrow at 9 AM to study Python"},
            headers=headers,
        )
        body = response.json()
        assert response.status_code == 200, body
        assert body["assistant_message"]["content"].startswith("Done yaar")
        [action] = body["actions"]
        assert action["tool"] == "create_reminder" and action["status"] == "succeeded"

        # The model saw a structured success result with the resolved local time.
        [result] = tool_results(llm.requests[1])
        assert result["status"] == "succeeded"
        assert result["result"]["scheduled_for"].endswith("09:00 (Asia/Kolkata)")

        reminders = (await client.get(f"{API}/reminders", headers=headers)).json()["items"]
        assert [r["title"] for r in reminders] == ["Study Python"]

        audit = (await client.get(f"{API}/tools/executions", headers=headers)).json()["items"]
        assert audit[0]["id"] == action["execution_id"]
        assert audit[0]["conversation_id"] == body["conversation"]["id"]

    # The tool prompt and tool specs were offered on the first round.
    assert "# Using your tools" in llm.requests[0].instructions
    assert {t.name for t in llm.requests[0].tools} >= {"create_reminder", "list_tasks"}


async def test_failed_tool_is_reported_not_faked(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM([_create(at="2020-01-01T09:00"), "Oops, that time has already passed. When?"])
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        body = (
            await client.post(f"{API}/chat", json={"message": "remind me at 9"}, headers=headers)
        ).json()
        reminders = (await client.get(f"{API}/reminders", headers=headers)).json()["items"]
    assert body["actions"][0]["status"] == "failed"
    assert tool_results(llm.requests[1])[0]["error_code"] == "validation_error"
    assert reminders == []


async def _delete_flow(client: httpx.AsyncClient, llm_steps_after: list, reply: str, headers):
    """Create a reminder via REST, then ask the companion to delete it."""
    at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    rid = (
        await client.post(
            f"{API}/reminders", json={"title": "Gym", "scheduled_at": at}, headers=headers
        )
    ).json()["id"]
    return rid


async def test_conversational_confirmation_flow(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM()
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        rid = await _delete_flow(client, [], "", headers)

        llm._replies += [
            [("delete_reminder", {"reminder_id": rid})],  # noqa: SLF001
            "Pakka? Gym wala reminder delete kar doon?",
        ]
        first = (
            await client.post(
                f"{API}/chat", json={"message": "gym wala reminder hata do"}, headers=headers
            )
        ).json()
        [action] = first["actions"]
        assert action["status"] == "awaiting_confirmation"
        confirmation_id = action["confirmation"]["confirmation_id"]
        assert 'Delete reminder "Gym"' in action["confirmation"]["summary"]
        assert (await client.get(f"{API}/reminders/{rid}", headers=headers)).status_code == 200

        # A non-affirmative reply: the model tries to approve anyway → refused.
        cid = first["conversation"]["id"]
        llm._replies += [
            [
                (
                    "confirm_pending_action",
                    {
                        "confirmation_id": confirmation_id,  # noqa: SLF001
                        "decision": "approve",
                    },
                )
            ],
            "Okay, rehne dete hain.",
        ]
        await client.post(
            f"{API}/chat",
            json={"conversation_id": cid, "message": "hmm wait, nahi"},
            headers=headers,
        )
        assert tool_results(llm.requests[-1])[0]["error_code"] == "user_did_not_confirm"
        assert (
            f"confirmation_id={confirmation_id}" in llm.requests[-2].instructions
        )  # pending in prompt
        assert (await client.get(f"{API}/reminders/{rid}", headers=headers)).status_code == 200

        # An explicit yes from the user: now it runs.
        llm._replies += [
            [
                (
                    "confirm_pending_action",
                    {
                        "confirmation_id": confirmation_id,  # noqa: SLF001
                        "decision": "approve",
                    },
                )
            ],
            "Ho gaya, delete kar diya.",
        ]
        third = (
            await client.post(
                f"{API}/chat",
                json={"conversation_id": cid, "message": "haan kar do"},
                headers=headers,
            )
        ).json()
        assert tool_results(llm.requests[-1])[0]["status"] == "succeeded"
        assert third["actions"][0]["status"] == "succeeded"
        assert (await client.get(f"{API}/reminders/{rid}", headers=headers)).status_code == 404
        pending = (await client.get(f"{API}/tools/confirmations", headers=headers)).json()
        assert pending == []


async def test_api_approve_and_reject(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM()
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        other = await register_and_login(client, "other@example.com")
        ids = []
        for _ in range(2):
            rid = await _delete_flow(client, [], "", headers)
            llm._replies += [[("delete_reminder", {"reminder_id": rid})], "Confirm?"]  # noqa: SLF001
            body = (
                await client.post(f"{API}/chat", json={"message": "delete it"}, headers=headers)
            ).json()
            ids.append((rid, body["actions"][0]["confirmation"]["confirmation_id"]))

        (rid1, c1), (rid2, c2) = ids
        assert (
            await client.post(f"{API}/tools/confirmations/{c1}/approve", headers=other)
        ).status_code == 404
        approved = await client.post(f"{API}/tools/confirmations/{c1}/approve", headers=headers)
        assert approved.json()["status"] == "succeeded"
        assert (await client.get(f"{API}/reminders/{rid1}", headers=headers)).status_code == 404
        again = await client.post(f"{API}/tools/confirmations/{c1}/approve", headers=headers)
        assert again.status_code == 409

        rejected = await client.post(f"{API}/tools/confirmations/{c2}/reject", headers=headers)
        assert rejected.json()["status"] == "cancelled"
        assert (await client.get(f"{API}/reminders/{rid2}", headers=headers)).status_code == 200


async def test_expired_confirmation_cannot_be_approved(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM()
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        rid = await _delete_flow(client, [], "", headers)
        llm._replies += [[("delete_reminder", {"reminder_id": rid})], "Confirm?"]  # noqa: SLF001
        body = (
            await client.post(f"{API}/chat", json={"message": "delete"}, headers=headers)
        ).json()
        cid = body["actions"][0]["confirmation"]["confirmation_id"]
        await db_session.execute(
            update(ToolExecution).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await db_session.commit()
        response = await client.post(f"{API}/tools/confirmations/{cid}/approve", headers=headers)
        assert response.status_code == 409
        assert "expired" in response.json()["error"]["message"]
    assert (await db_session.scalars(select(Reminder))).all() != []


async def test_stream_emits_tool_events(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM([_create(), "Ho gaya!"])
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        raw = (
            await client.post(
                f"{API}/chat/stream", json={"message": "remind me tomorrow 9"}, headers=headers
            )
        ).text
    events = []
    for block in raw.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    names = [n for n, _ in events]
    assert names[:3] == ["start", "tool_started", "tool"]
    assert events[2][1]["status"] == "succeeded"
    assert names[-1] == "done"
    assert events[-1][1]["actions"][0]["tool"] == "create_reminder"


async def test_voice_turn_can_use_tools(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM([_create(), "Done!"])
    async for client in _client_for(build_app(infra_settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        body = (
            await client.post(
                f"{API}/voice/turn",
                files={"file": ("a.wav", fake_wav(), "audio/wav")},
                headers=headers,
            )
        ).json()
    assert body["actions"][0]["tool"] == "create_reminder"


async def test_web_search_only_offered_when_configured(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    search = FakeWebSearch()
    llm = FakeLLM([[("search_web", {"query": "Pune weather today"})], "Pune mein aaj dhoop hai."])
    app = build_app(infra_settings, db_session, redis_client, llm, web_search=search)
    async for client in _client_for(app):
        headers = await register_and_login(client)
        tools = {t["name"] for t in (await client.get(f"{API}/tools", headers=headers)).json()}
        await client.post(f"{API}/chat", json={"message": "aaj Pune ka mausam?"}, headers=headers)
    assert "search_web" in tools
    assert search.queries == ["Pune weather today"]
    result = tool_results(llm.requests[1])[0]["result"]
    assert result["sources"] == [{"title": "Example", "url": "https://example.com/a"}]


async def test_tools_list_without_search(db_client: httpx.AsyncClient) -> None:
    headers = await register_and_login(db_client)
    tools = {
        t["name"]: t["confirmation"]
        for t in (await db_client.get(f"{API}/tools", headers=headers)).json()
    }
    assert "search_web" not in tools
    assert tools["delete_reminder"] == "always"
    assert tools["update_preferences"] == "conditional"
    assert tools["create_reminder"] == "never"
