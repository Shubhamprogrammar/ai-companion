from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.enums import ToolExecutionStatus
from app.models.productivity import ToolExecution
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserPreferences, UserProfile
from app.services.maintenance import MaintenanceService
from scripts import maintenance as maintenance_script
from tests.conftest import make_settings, session_factory_for

NOW = datetime.now(UTC)


async def _user(session: AsyncSession, email: str = "m@example.com") -> User:
    user = User(email=email, password_hash="x")
    user.profile = UserProfile()
    user.preferences = UserPreferences()
    session.add(user)
    await session.flush()
    return user


async def _execution(
    session: AsyncSession,
    user: User,
    *,
    status: ToolExecutionStatus,
    age_days: float = 0,
    expires_in_minutes: float | None = None,
) -> ToolExecution:
    row = ToolExecution(
        user_id=user.id,
        tool_name="create_reminder",
        input={"title": "x"},
        status=status,
        expires_at=None
        if expires_in_minutes is None
        else NOW + timedelta(minutes=expires_in_minutes),
    )
    session.add(row)
    await session.flush()
    await session.execute(
        update(ToolExecution)
        .where(ToolExecution.id == row.id)
        .values(created_at=NOW - timedelta(days=age_days))
    )
    return row


async def _ids(session: AsyncSession, model) -> set:
    return set(await session.scalars(select(model.id)))


async def test_prunes_only_expired_refresh_tokens(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    expired = RefreshToken(user_id=user.id, jti="expired", expires_at=NOW - timedelta(hours=1))
    # Revoked but not yet expired: kept so reuse of a rotated token is still detected.
    revoked = RefreshToken(
        user_id=user.id, jti="revoked", expires_at=NOW + timedelta(days=1), revoked_at=NOW
    )
    live = RefreshToken(user_id=user.id, jti="live", expires_at=NOW + timedelta(days=1))
    db_session.add_all([expired, revoked, live])
    await db_session.flush()

    pruned = await MaintenanceService(db_session, make_settings()).prune_refresh_tokens(NOW)

    assert pruned == 1
    assert await _ids(db_session, RefreshToken) == {revoked.id, live.id}


async def test_expires_stale_confirmations(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    stale = await _execution(
        db_session, user, status=ToolExecutionStatus.AWAITING_CONFIRMATION, expires_in_minutes=-1
    )
    pending = await _execution(
        db_session, user, status=ToolExecutionStatus.AWAITING_CONFIRMATION, expires_in_minutes=5
    )

    count = await MaintenanceService(db_session, make_settings()).expire_confirmations(NOW)

    assert count == 1
    await db_session.refresh(stale)
    await db_session.refresh(pending)
    assert stale.status is ToolExecutionStatus.EXPIRED
    assert pending.status is ToolExecutionStatus.AWAITING_CONFIRMATION


async def test_prunes_old_tool_executions(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    old = await _execution(db_session, user, status=ToolExecutionStatus.SUCCEEDED, age_days=100)
    recent = await _execution(db_session, user, status=ToolExecutionStatus.FAILED, age_days=10)
    settings = make_settings(tool_execution_retention_days=90)

    assert await MaintenanceService(db_session, settings).prune_tool_executions(NOW) == 1
    assert await _ids(db_session, ToolExecution) == {recent.id}
    assert old.id not in await _ids(db_session, ToolExecution)


async def test_tool_execution_retention_can_be_disabled(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _execution(db_session, user, status=ToolExecutionStatus.SUCCEEDED, age_days=1000)
    settings = make_settings(tool_execution_retention_days=None)

    assert await MaintenanceService(db_session, settings).prune_tool_executions(NOW) == 0


async def test_run_reports_every_step(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    db_session.add(RefreshToken(user_id=user.id, jti="old", expires_at=NOW - timedelta(days=1)))
    await _execution(db_session, user, status=ToolExecutionStatus.SUCCEEDED, age_days=365)
    await db_session.flush()

    report = await MaintenanceService(db_session, make_settings()).run()

    assert report.refresh_tokens_pruned == 1
    assert report.tool_executions_pruned == 1
    assert report.conversations_purged == 0


async def test_run_once_is_exclusive(
    db_session: AsyncSession, redis_client: RedisClient, infra_settings: Settings
) -> None:
    database = SimpleNamespace(session_factory=session_factory_for(db_session))

    await redis_client.client.set(maintenance_script.LOCK_KEY, "someone-else", ex=60)
    assert await maintenance_script.run_once(infra_settings, database, redis_client) is False

    await redis_client.client.delete(maintenance_script.LOCK_KEY)
    assert await maintenance_script.run_once(infra_settings, database, redis_client) is True
    assert await redis_client.client.get(maintenance_script.LOCK_KEY) is None  # released
