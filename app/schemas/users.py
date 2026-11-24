from datetime import datetime
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from app.models.enums import Language, PersonalityStyle
from app.schemas.auth import AuthUserResponse, DisplayName, Timezone
from app.schemas.common import ORMResponse, StrictRequest

VoiceName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]


def _reject_explicit_nulls(model: StrictRequest, fields: tuple[str, ...]) -> None:
    for name in fields:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} cannot be null")


class ProfileResponse(ORMResponse):
    display_name: str | None
    preferred_language: Language
    timezone: str
    updated_at: datetime


class UserMeResponse(AuthUserResponse):
    profile: ProfileResponse


class ProfileUpdateRequest(StrictRequest):
    display_name: DisplayName | None = None  # explicit null clears it
    preferred_language: Language | None = None
    timezone: Timezone | None = None

    @model_validator(mode="after")
    def _non_nullable(self) -> Self:
        _reject_explicit_nulls(self, ("preferred_language", "timezone"))
        return self


class PreferencesResponse(ORMResponse):
    personality: PersonalityStyle
    voice: str | None = Field(description="null means the server default voice")
    speech_speed: float
    memory_enabled: bool
    conversation_retention_days: int | None = Field(description="null means keep indefinitely")
    updated_at: datetime


class PreferencesUpdateRequest(StrictRequest):
    personality: PersonalityStyle | None = None
    voice: VoiceName | None = None  # explicit null resets to server default
    speech_speed: Annotated[float, Field(ge=0.25, le=4.0)] | None = None
    memory_enabled: bool | None = None
    conversation_retention_days: Annotated[int, Field(ge=1, le=3650)] | None = None

    @model_validator(mode="after")
    def _non_nullable(self) -> Self:
        _reject_explicit_nulls(self, ("personality", "speech_speed", "memory_enabled"))
        return self
