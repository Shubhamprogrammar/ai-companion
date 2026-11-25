"""Registration, login, token refresh (with rotation + reuse detection) and logout."""

from dataclasses import dataclass
from datetime import UTC, datetime

from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_event
from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    RateLimitedError,
    ServiceUnavailableError,
)
from app.core.security import PasswordHasher, TokenClaims, TokenService, TokenType
from app.models.user import User, UserPreferences, UserProfile
from app.repositories.login_attempts import LoginAttemptStore
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.revoked_tokens import RevokedAccessTokenStore
from app.repositories.users import UserRepository
from app.schemas.auth import RegisterRequest

_INVALID_CREDENTIALS = "Incorrect email or password."


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        tokens: TokenService,
        passwords: PasswordHasher,
        revoked_access: RevokedAccessTokenStore,
        login_attempts: LoginAttemptStore | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._tokens = tokens
        self._passwords = passwords
        self._revoked_access = revoked_access
        self._login_attempts = login_attempts
        self._users = UserRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    # --- registration / login -------------------------------------------------

    async def register(self, data: RegisterRequest) -> User:
        if await self._users.get_by_email(data.email):
            raise ConflictError("An account with this email already exists.")

        user = User(email=data.email, password_hash=await self._passwords.hash(data.password))
        profile = UserProfile(
            display_name=data.display_name,
            preferred_language=data.preferred_language,
            timezone=data.timezone,
        )
        preferences = UserPreferences(memory_enabled=self._settings.memory_enabled)
        try:
            await self._users.add(user, profile, preferences)
            await self._session.commit()
        except IntegrityError as exc:  # lost a race with a concurrent registration
            await self._session.rollback()
            raise ConflictError("An account with this email already exists.") from exc

        audit_event("user.registered", user_id=str(user.id))
        return user

    async def login(self, email: str, password: str, client_ip: str | None) -> TokenPair:
        if self._login_attempts and (retry := await self._login_attempts.locked_for(email)):
            audit_event("auth.login_locked", client_ip=client_ip)
            raise RateLimitedError(
                "Too many failed sign-in attempts. Please try again later.",
                headers={"Retry-After": str(retry)},
            )
        user = await self._users.get_by_email(email)
        # Always run a hash verification so response time doesn't reveal whether the email exists.
        valid = await self._passwords.verify(user.password_hash if user else None, password)
        if not user or not valid or not user.is_active:
            audit_event(
                "auth.login_failed",
                user_id=str(user.id) if user else None,
                reason="inactive" if user and valid else "invalid_credentials",
                client_ip=client_ip,
            )
            if self._login_attempts:
                await self._login_attempts.record_failure(email)
            raise AuthenticationError(_INVALID_CREDENTIALS)

        if self._passwords.needs_rehash(user.password_hash):
            user.password_hash = await self._passwords.hash(password)

        pair = await self._issue_pair(user)
        await self._session.commit()
        if self._login_attempts:
            await self._login_attempts.clear(email)
        audit_event("auth.login_succeeded", user_id=str(user.id), client_ip=client_ip)
        return pair

    # --- tokens ---------------------------------------------------------------

    async def refresh(self, refresh_token: str) -> TokenPair:
        claims = self._tokens.decode(refresh_token, TokenType.REFRESH)
        record = await self._refresh_tokens.get_by_jti_for_update(claims.jti)
        if record is None or record.user_id != claims.user_id:
            raise AuthenticationError("Invalid token.")

        if record.revoked_at is not None:
            # A rotated-out token was presented again: treat as theft and kill every session.
            await self._refresh_tokens.revoke_all_for_user(record.user_id)
            await self._session.commit()
            audit_event("auth.refresh_token_reuse_detected", user_id=str(record.user_id))
            raise AuthenticationError("Invalid token.")

        user = await self._users.get_by_id(claims.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid token.")

        await self._refresh_tokens.revoke(record)
        pair = await self._issue_pair(user)
        await self._session.commit()
        return pair

    async def authenticate(self, access_token: str) -> tuple[User, TokenClaims]:
        claims = self._tokens.decode(access_token, TokenType.ACCESS)
        try:
            revoked = await self._revoked_access.is_revoked(claims.jti)
        except RedisError as exc:
            # Fail closed: if revocation can't be checked, a logged-out token must not work.
            raise ServiceUnavailableError() from exc
        if revoked:
            raise AuthenticationError("Token has been revoked.")
        user = await self._users.get_by_id(claims.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid token.")
        return user, claims

    async def logout(self, access_claims: TokenClaims, refresh_token: str | None) -> None:
        try:
            await self._revoked_access.revoke(access_claims.jti, access_claims.expires_at)
        except RedisError as exc:
            raise ServiceUnavailableError() from exc
        if refresh_token:
            try:
                claims = self._tokens.decode(refresh_token, TokenType.REFRESH)
            except AuthenticationError:
                claims = None  # already expired/invalid: nothing to revoke
            if claims and claims.user_id == access_claims.user_id:
                record = await self._refresh_tokens.get_by_jti_for_update(claims.jti)
                if record and record.revoked_at is None:
                    await self._refresh_tokens.revoke(record)
                    await self._session.commit()
        audit_event("auth.logout", user_id=str(access_claims.user_id))

    async def _issue_pair(self, user: User) -> TokenPair:
        access = self._tokens.issue(user.id, TokenType.ACCESS)
        refresh = self._tokens.issue(user.id, TokenType.REFRESH)
        await self._refresh_tokens.add(user.id, refresh.claims.jti, refresh.claims.expires_at)
        expires_in = int((access.claims.expires_at - datetime.now(UTC)).total_seconds())
        return TokenPair(access.token, refresh.token, expires_in)
