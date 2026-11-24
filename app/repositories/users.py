import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserPreferences, UserProfile


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        return await self._session.scalar(select(User).where(User.email == email))

    async def add(self, user: User, profile: UserProfile, preferences: UserPreferences) -> User:
        user.profile = profile
        user.preferences = preferences
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_profile(self, user_id: uuid.UUID) -> UserProfile | None:
        return await self._session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

    async def get_preferences(self, user_id: uuid.UUID) -> UserPreferences | None:
        return await self._session.scalar(
            select(UserPreferences).where(UserPreferences.user_id == user_id)
        )
