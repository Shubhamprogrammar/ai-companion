from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import (
    AuthContextDep,
    AuthServiceDep,
    CurrentUserDep,
    client_ip,
    rate_limit,
)
from app.schemas.auth import (
    AuthUserResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPairResponse,
)
from app.services.auth import TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(pair: TokenPair) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/register",
    response_model=AuthUserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth:register"))],
)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> AuthUserResponse:
    user = await auth.register(body)
    return AuthUserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPairResponse,
    dependencies=[Depends(rate_limit("auth:login"))],
)
async def login(body: LoginRequest, request: Request, auth: AuthServiceDep) -> TokenPairResponse:
    return _token_response(await auth.login(body.email, body.password, client_ip(request)))


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    dependencies=[Depends(rate_limit("auth:refresh"))],
)
async def refresh(body: RefreshRequest, auth: AuthServiceDep) -> TokenPairResponse:
    """Rotates the refresh token: the one sent is revoked and a new pair is returned."""
    return _token_response(await auth.refresh(body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, context: AuthContextDep, auth: AuthServiceDep) -> None:
    """Revokes the current access token and, if provided, the refresh token."""
    await auth.logout(context.claims, body.refresh_token)


@router.get("/me", response_model=AuthUserResponse)
async def me(user: CurrentUserDep) -> AuthUserResponse:
    return AuthUserResponse.model_validate(user)
