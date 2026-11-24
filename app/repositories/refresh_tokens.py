import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user_id: uuid.UUID, jti: str, expires_at: datetime) -> RefreshToken:
        record = RefreshToken(user_id=user_id, jti=jti, expires_at=expires_at)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_jti_for_update(self, jti: str) -> RefreshToken | None:
        """Row-locked fetch so concurrent refreshes of the same token serialise."""
        return await self._session.scalar(
            select(RefreshToken).where(RefreshToken.jti == jti).with_for_update()
        )

    async def revoke(self, record: RefreshToken) -> None:
        record.revoked_at = datetime.now(UTC)
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
