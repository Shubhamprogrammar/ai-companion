from fastapi import APIRouter

from app.api.dependencies import CurrentUserDep, UserServiceDep
from app.models.user import User, UserProfile
from app.schemas.users import (
    PreferencesResponse,
    PreferencesUpdateRequest,
    ProfileResponse,
    ProfileUpdateRequest,
    UserMeResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


def _me_response(user: User, profile: UserProfile) -> UserMeResponse:
    return UserMeResponse(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        profile=ProfileResponse.model_validate(profile),
    )


@router.get("/me", response_model=UserMeResponse)
async def get_me(user: CurrentUserDep, users: UserServiceDep) -> UserMeResponse:
    return _me_response(user, await users.get_profile(user))


@router.patch("/me", response_model=UserMeResponse)
async def update_me(
    body: ProfileUpdateRequest, user: CurrentUserDep, users: UserServiceDep
) -> UserMeResponse:
    return _me_response(user, await users.update_profile(user, body))


@router.get("/me/preferences", response_model=PreferencesResponse)
async def get_preferences(user: CurrentUserDep, users: UserServiceDep) -> PreferencesResponse:
    return PreferencesResponse.model_validate(await users.get_preferences(user))


@router.patch("/me/preferences", response_model=PreferencesResponse)
async def update_preferences(
    body: PreferencesUpdateRequest, user: CurrentUserDep, users: UserServiceDep
) -> PreferencesResponse:
    return PreferencesResponse.model_validate(await users.update_preferences(user, body))
