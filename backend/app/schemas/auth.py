import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field, StringConstraints

from app.models.enums import Language
from app.schemas.common import ORMResponse, StrictRequest
from app.schemas.validators import validate_password, validate_timezone

NormalisedEmail = Annotated[EmailStr, AfterValidator(str.lower)]
# Passwords are not whitespace-stripped: users may intentionally use spaces.
Password = Annotated[str, Field(min_length=8, max_length=128), AfterValidator(validate_password)]
Timezone = Annotated[str, StringConstraints(max_length=64), AfterValidator(validate_timezone)]
DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class RegisterRequest(StrictRequest):
    email: NormalisedEmail
    password: Password
    display_name: DisplayName | None = None
    preferred_language: Language = Language.AUTO
    timezone: Timezone = "UTC"


class LoginRequest(StrictRequest):
    email: NormalisedEmail
    password: Annotated[str, Field(min_length=1, max_length=128)]


class RefreshRequest(StrictRequest):
    refresh_token: Annotated[str, Field(min_length=1, max_length=4096)]


class LogoutRequest(StrictRequest):
    refresh_token: Annotated[str | None, Field(max_length=4096)] = None


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int = Field(description="Access-token lifetime in seconds")


class AuthUserResponse(ORMResponse):
    id: uuid.UUID
    email: str
    is_active: bool
    created_at: datetime
