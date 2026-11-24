"""Password hashing (Argon2id) and JWT access/refresh tokens."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.core.exceptions import AuthenticationError


class PasswordHasher:
    """Argon2id hashing. CPU-heavy calls run in a worker thread to keep the event loop free."""

    def __init__(self) -> None:
        self._hasher = _Argon2Hasher()
        # Used to equalise timing when a login targets a non-existent account.
        self._dummy_hash = self._hasher.hash(uuid.uuid4().hex)

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        target = password_hash or self._dummy_hash
        try:
            ok = await asyncio.to_thread(self._hasher.verify, target, password)
        except (VerificationError, InvalidHashError):
            return False
        return ok and password_hash is not None

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    jti: str
    token_type: TokenType
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    claims: TokenClaims


class TokenService:
    _REQUIRED_CLAIMS = ("sub", "jti", "type", "iat", "exp")

    def __init__(self, settings: Settings) -> None:
        self._key = settings.secret_key.get_secret_value()
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.app_name
        self._lifetimes = {
            TokenType.ACCESS: timedelta(minutes=settings.access_token_expire_minutes),
            TokenType.REFRESH: timedelta(days=settings.refresh_token_expire_days),
        }

    def lifetime(self, token_type: TokenType) -> timedelta:
        return self._lifetimes[token_type]

    def issue(self, user_id: uuid.UUID, token_type: TokenType) -> IssuedToken:
        now = datetime.now(UTC)
        expires_at = now + self._lifetimes[token_type]
        jti = uuid.uuid4().hex
        payload = {
            "sub": str(user_id),
            "jti": jti,
            "type": token_type.value,
            "iss": self._issuer,
            "iat": now,
            "exp": expires_at,
        }
        token = jwt.encode(payload, self._key, algorithm=self._algorithm)
        return IssuedToken(token, TokenClaims(user_id, jti, token_type, expires_at))

    def decode(self, token: str, expected_type: TokenType) -> TokenClaims:
        """Validate signature, expiry, issuer and type. Raises AuthenticationError."""
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": [*self._REQUIRED_CLAIMS, "iss"]},
            )
            if payload["type"] != expected_type.value:
                raise AuthenticationError("Invalid token type.")
            return TokenClaims(
                user_id=uuid.UUID(payload["sub"]),
                jti=str(payload["jti"]),
                token_type=expected_type,
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired.") from exc
        except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
            raise AuthenticationError("Invalid token.") from exc
