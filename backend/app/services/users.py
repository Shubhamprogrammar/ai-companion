"""Profile and preference management for the authenticated user."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_event
from app.core.config import Settings
from app.core.exceptions import NotFoundError, ValidationAppError
from app.models.user import User, UserPreferences, UserProfile
from app.repositories.users import UserRepository
from app.schemas.users import PreferencesUpdateRequest, ProfileUpdateRequest


class UserService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)

    async def get_profile(self, user: User) -> UserProfile:
        profile = await self._users.get_profile(user.id)
        if profile is None:
            raise NotFoundError("Profile not found.")
        return profile

    async def update_profile(self, user: User, data: ProfileUpdateRequest) -> UserProfile:
        profile = await self.get_profile(user)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)
        await self._session.commit()
        await self._session.refresh(profile)
        return profile

    async def get_preferences(self, user: User) -> UserPreferences:
        preferences = await self._users.get_preferences(user.id)
        if preferences is None:
            raise NotFoundError("Preferences not found.")
        return preferences

    async def update_preferences(
        self, user: User, data: PreferencesUpdateRequest
    ) -> UserPreferences:
        changes = data.model_dump(exclude_unset=True)
        voice = changes.get("voice")
        if voice is not None and voice not in self._settings.openai_tts_voices:
            raise ValidationAppError(
                f"Unknown voice '{voice}'.",
                details={"allowed": list(self._settings.openai_tts_voices)},
            )
        preferences = await self.get_preferences(user)
        for field, value in changes.items():
            setattr(preferences, field, value)
        await self._session.commit()
        await self._session.refresh(preferences)

        privacy_fields = {"memory_enabled", "conversation_retention_days"} & changes.keys()
        if privacy_fields:
            audit_event(
                "user.privacy_settings_changed",
                user_id=str(user.id),
                **{f: changes[f] for f in privacy_fields},
            )
        return preferences
