import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import PasswordHasher, TokenService, TokenType
from tests.conftest import make_settings


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def tokens() -> TokenService:
    return TokenService(make_settings())


async def test_password_hash_roundtrip(hasher: PasswordHasher) -> None:
    hashed = await hasher.hash("s3cret pass")
    assert hashed.startswith("$argon2id$")
    assert "s3cret" not in hashed
    assert await hasher.verify(hashed, "s3cret pass")
    assert not await hasher.verify(hashed, "wrong pass")


async def test_verify_without_hash_is_false(hasher: PasswordHasher) -> None:
    assert not await hasher.verify(None, "anything")


async def test_verify_garbage_hash_is_false(hasher: PasswordHasher) -> None:
    assert not await hasher.verify("not-a-hash", "anything")


def test_token_roundtrip(tokens: TokenService) -> None:
    user_id = uuid.uuid4()
    issued = tokens.issue(user_id, TokenType.ACCESS)
    claims = tokens.decode(issued.token, TokenType.ACCESS)
    assert claims.user_id == user_id
    assert claims.jti == issued.claims.jti
    assert claims.expires_at > datetime.now(UTC)


def test_each_token_has_unique_jti(tokens: TokenService) -> None:
    user_id = uuid.uuid4()
    assert (
        tokens.issue(user_id, TokenType.ACCESS).claims.jti
        != tokens.issue(user_id, TokenType.ACCESS).claims.jti
    )


def test_refresh_token_cannot_be_used_as_access(tokens: TokenService) -> None:
    refresh = tokens.issue(uuid.uuid4(), TokenType.REFRESH)
    with pytest.raises(AuthenticationError, match="type"):
        tokens.decode(refresh.token, TokenType.ACCESS)


def test_token_signed_with_other_key_rejected(tokens: TokenService) -> None:
    other = TokenService(make_settings(secret_key="a-completely-different-secret-key-value-123"))
    token = other.issue(uuid.uuid4(), TokenType.ACCESS).token
    with pytest.raises(AuthenticationError):
        tokens.decode(token, TokenType.ACCESS)


def test_expired_token_rejected(tokens: TokenService) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "jti": "x",
        "type": "access",
        "iss": "Personal AI Companion",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    token = jwt.encode(payload, make_settings().secret_key.get_secret_value(), algorithm="HS256")
    with pytest.raises(AuthenticationError, match="expired"):
        tokens.decode(token, TokenType.ACCESS)


def test_alg_none_token_rejected(tokens: TokenService) -> None:
    payload = {
        "sub": str(uuid.uuid4()),
        "jti": "x",
        "type": "access",
        "iss": "Personal AI Companion",
        "iat": 0,
        "exp": 9999999999,
    }
    token = jwt.encode(payload, None, algorithm="none")
    with pytest.raises(AuthenticationError):
        tokens.decode(token, TokenType.ACCESS)


def test_malformed_token_rejected(tokens: TokenService) -> None:
    with pytest.raises(AuthenticationError):
        tokens.decode("not.a.jwt", TokenType.ACCESS)
