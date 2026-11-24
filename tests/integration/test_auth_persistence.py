"""Repository / service behaviour against real PostgreSQL and Redis."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.core.redis import RedisClient
from app.core.security import PasswordHasher, TokenService
from app.models.enums import Language, PersonalityStyle
from app.models.user import User
from app.repositories.revoked_tokens import RevokedAccessTokenStore
from app.repositories.users import UserRepository
from app.schemas.auth import RegisterRequest
from app.services.auth import AuthService


def _auth_service(session: AsyncSession, settings: Settings, redis: RedisClient) -> AuthService:
    return AuthService(
        session,
        settings,
        TokenService(settings),
        PasswordHasher(),
        RevokedAccessTokenStore(redis.client),
    )


async def test_register_creates_user_profile_and_preferences(
    db_session: AsyncSession, infra_settings: Settings, redis_client: RedisClient
) -> None:
    service = _auth_service(db_session, infra_settings, redis_client)
    user = await service.register(
        RegisterRequest(email="Asha@Example.com", password="correct horse", timezone="Asia/Kolkata")
    )

    repo = UserRepository(db_session)
    assert (await repo.get_by_email("asha@example.com")).id == user.id
    profile = await repo.get_profile(user.id)
    preferences = await repo.get_preferences(user.id)
    assert profile.timezone == "Asia/Kolkata"
    assert profile.preferred_language is Language.AUTO
    assert preferences.personality is PersonalityStyle.WARM_FRIEND
    assert preferences.memory_enabled is infra_settings.memory_enabled
    assert user.password_hash.startswith("$argon2id$")


async def test_database_rejects_non_lowercase_email(db_session: AsyncSession) -> None:
    db_session.add(User(email="Mixed@Example.com", password_hash="x"))
    with pytest.raises(IntegrityError, match="email_lowercase"):
        await db_session.flush()


async def test_deleting_user_cascades(
    db_session: AsyncSession, infra_settings: Settings, redis_client: RedisClient
) -> None:
    service = _auth_service(db_session, infra_settings, redis_client)
    user = await service.register(RegisterRequest(email="del@example.com", password="password123"))
    await service.login("del@example.com", "password123", None)

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    for table in ("user_profiles", "user_preferences", "refresh_tokens"):
        count = await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE user_id = :id"),  # noqa: S608 - fixed names
            {"id": user.id},
        )
        assert count == 0, table


async def test_revoked_access_token_store_expires_entries(redis_client: RedisClient) -> None:
    store = RevokedAccessTokenStore(redis_client.client)
    await store.revoke("jti-1", datetime.now(UTC) + timedelta(seconds=30))
    await store.revoke("jti-expired", datetime.now(UTC) - timedelta(seconds=1))

    assert await store.is_revoked("jti-1")
    assert not await store.is_revoked("jti-expired")
    ttl = await redis_client.client.ttl("auth:revoked-access:jti-1")
    assert 0 < ttl <= 30


async def test_rate_limiter_blocks_after_limit(redis_client: RedisClient) -> None:
    limiter = RateLimiter(redis_client.client)
    results = [await limiter.hit("test:ip", limit=3, window_seconds=60) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].retry_after_seconds > 0
