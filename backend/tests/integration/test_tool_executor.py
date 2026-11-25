"""ToolExecutor against real PostgreSQL: validation, ownership, confirmations, audit."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import ToolCall
from app.core.config import Settings
from app.models.enums import ToolExecutionStatus
from app.models.productivity import Reminder, ToolExecution
from app.models.user import User, UserPreferences, UserProfile
from app.tools.defaults import build_registry
from app.tools.executor import ToolExecutor, build_tool_context
from tests.fakes import FakeEmbeddings


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    user.profile = UserProfile(timezone="Asia/Kolkata")
    user.preferences = UserPreferences()
    session.add(user)
    await session.commit()
    return user


def _executor(
    session: AsyncSession, settings: Settings, user: User, text: str = "do it"
) -> ToolExecutor:
    async def ensure() -> None:
        return None

    ctx = build_tool_context(
        session=session,
        settings=settings,
        user=user,
        profile=user.profile,
        conversation_id=None,
        user_message=text,
        now=datetime.now(UTC),
    )
    return ToolExecutor(
        build_registry(settings, FakeEmbeddings(), None),
        ctx,
        ensure_conversation=ensure,
        settings=settings,
    )


def _call(name: str, args) -> ToolCall:
    return ToolCall(
        call_id="c1", name=name, arguments=args if isinstance(args, str) else json.dumps(args)
    )


async def _executions(session: AsyncSession, user: User) -> list[ToolExecution]:
    return list(
        (await session.scalars(select(ToolExecution).where(ToolExecution.user_id == user.id))).all()
    )


async def test_create_reminder_resolves_time_in_user_timezone(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    user = await _user(db_session, "me@example.com")
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).astimezone().date()
    outcome = await _executor(db_session, infra_settings, user).run(
        _call(
            "create_reminder",
            {"title": "Study Python", "scheduled_at": f"{tomorrow}T09:00", "description": None},
        )
    )
    payload = json.loads(outcome.result.output)
    assert payload["status"] == "succeeded"
    assert payload["result"]["scheduled_for"].endswith("09:00 (Asia/Kolkata)")

    [reminder] = (await db_session.scalars(select(Reminder))).all()
    assert reminder.scheduled_at.astimezone(UTC).strftime("%H:%M") == "03:30"
    [execution] = await _executions(db_session, user)
    assert execution.status is ToolExecutionStatus.SUCCEEDED
    assert execution.tool_name == "create_reminder"
    assert outcome.activity.execution_id == execution.id


async def test_rejected_calls_are_audited_and_explained(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    user = await _user(db_session, "me@example.com")
    settings = infra_settings.model_copy(update={"tool_max_calls_per_turn": 10})
    executor = _executor(db_session, settings, user)
    cases = [
        (_call("rm_rf", {}), "unknown_tool"),
        (_call("create_reminder", "{not json"), "invalid_json"),
        (_call("create_reminder", {"title": "x"}), "invalid_arguments"),  # missing fields
        (
            _call(
                "create_reminder",
                {
                    "title": "x",
                    "scheduled_at": "2026-01-01T09:00",
                    "description": None,
                    "user_id": "someone",
                },
            ),
            "invalid_arguments",
        ),
        (
            _call(
                "update_reminder",
                {
                    "reminder_id": "not-a-uuid",
                    "title": None,
                    "scheduled_at": None,
                    "description": None,
                    "status": None,
                },
            ),
            "invalid_arguments",
        ),
        (
            _call(
                "create_reminder",
                {"title": "x", "scheduled_at": "2020-01-01T09:00", "description": None},
            ),
            "validation_error",
        ),  # in the past
        (
            _call(
                "create_reminder",
                {"title": "x", "scheduled_at": "tomorrow 9am", "description": None},
            ),
            "invalid_datetime",
        ),
    ]
    for call, code in cases:
        payload = json.loads((await executor.run(call)).result.output)
        assert payload["status"] == "error" and payload["error_code"] == code, (call.name, payload)
    executions = await _executions(db_session, user)
    assert len(executions) == len(cases)
    assert (await db_session.scalars(select(Reminder))).all() == []


async def test_cannot_touch_another_users_items(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    owner = await _user(db_session, "owner@example.com")
    intruder = await _user(db_session, "intruder@example.com")
    reminder = Reminder(
        user_id=owner.id, title="Owner's", scheduled_at=datetime.now(UTC) + timedelta(days=1)
    )
    db_session.add(reminder)
    await db_session.commit()

    reminder_id = str(reminder.id)  # ORM objects expire when a failed tool rolls back
    executor = _executor(db_session, infra_settings, intruder)
    update = await executor.run(
        _call(
            "update_reminder",
            {
                "reminder_id": reminder_id,
                "title": "pwned",
                "scheduled_at": None,
                "description": None,
                "status": None,
            },
        )
    )
    delete = await executor.run(_call("delete_reminder", {"reminder_id": reminder_id}))
    assert json.loads(update.result.output)["error_code"] == "not_found"
    assert json.loads(delete.result.output)["error_code"] == "not_found"  # no confirmation offered
    await db_session.refresh(reminder)
    assert reminder.title == "Owner's"


async def test_delete_requires_confirmation_and_does_nothing_yet(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    user = await _user(db_session, "me@example.com")
    reminder = Reminder(
        user_id=user.id, title="Gym", scheduled_at=datetime.now(UTC) + timedelta(days=1)
    )
    db_session.add(reminder)
    await db_session.commit()

    outcome = await _executor(db_session, infra_settings, user).run(
        _call("delete_reminder", {"reminder_id": str(reminder.id)})
    )
    payload = json.loads(outcome.result.output)
    assert payload["status"] == "confirmation_required"
    assert 'Delete reminder "Gym"' in payload["action"]
    assert outcome.activity.confirmation is not None
    assert await db_session.get(Reminder, reminder.id) is not None  # still there


async def test_per_turn_call_cap(db_session: AsyncSession, infra_settings: Settings) -> None:
    user = await _user(db_session, "me@example.com")
    settings = infra_settings.model_copy(update={"tool_max_calls_per_turn": 2})
    executor = _executor(db_session, settings, user)
    codes = []
    for _ in range(3):
        out = json.loads((await executor.run(_call("list_tasks", {"status": "all"}))).result.output)
        codes.append(out.get("error_code"))
    assert codes == [None, None, "too_many_tool_calls"]


async def test_memory_off_via_tool_needs_confirmation_but_other_prefs_dont(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    user = await _user(db_session, "me@example.com")
    executor = _executor(db_session, infra_settings, user)
    speed = await executor.run(
        _call(
            "update_preferences",
            {
                "voice": "marin",
                "speech_speed": 1.2,
                "preferred_language": "hinglish",
                "memory_enabled": None,
            },
        )
    )
    assert json.loads(speed.result.output)["status"] == "succeeded"
    off = await executor.run(
        _call(
            "update_preferences",
            {
                "voice": None,
                "speech_speed": None,
                "preferred_language": None,
                "memory_enabled": False,
            },
        )
    )
    assert json.loads(off.result.output)["status"] == "confirmation_required"
    await db_session.refresh(user.preferences)
    assert user.preferences.memory_enabled is True
    assert user.preferences.voice == "marin"

    bad_voice = await executor.run(
        _call(
            "update_preferences",
            {
                "voice": "robot",
                "speech_speed": None,
                "preferred_language": None,
                "memory_enabled": None,
            },
        )
    )
    assert json.loads(bad_voice.result.output)["error_code"] == "validation_error"


async def test_confirmation_cannot_be_approved_in_the_turn_that_created_it(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    """A prompt-injected "delete + approve" in one turn must not run, even if the user said yes."""
    user = await _user(db_session, "inject@example.com")
    reminder = Reminder(
        user_id=user.id, title="Gym", scheduled_at=datetime.now(UTC) + timedelta(days=1)
    )
    db_session.add(reminder)
    await db_session.commit()
    reminder_id = reminder.id  # a failed tool's rollback expires loaded objects
    executor = _executor(db_session, infra_settings, user, text="ok")

    requested = json.loads(
        (
            await executor.run(_call("delete_reminder", {"reminder_id": str(reminder_id)}))
        ).result.output
    )
    confirmation_id = requested["confirmation_id"]
    approve = {"confirmation_id": confirmation_id, "decision": "approve"}
    refused = json.loads(
        (await executor.run(_call("confirm_pending_action", approve))).result.output
    )

    assert refused["error_code"] == "confirmation_not_answered"
    assert await db_session.get(Reminder, reminder_id) is not None

    # Next turn, the user answers: now it may run. (A new turn reloads the user.)
    await db_session.refresh(user, ["profile"])
    later = _executor(db_session, infra_settings, user, text="haan kar do")
    done = json.loads((await later.run(_call("confirm_pending_action", approve))).result.output)
    assert done["status"] == "succeeded"
    db_session.expunge_all()
    assert await db_session.get(Reminder, reminder_id) is None
