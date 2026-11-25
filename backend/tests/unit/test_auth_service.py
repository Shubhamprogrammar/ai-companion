"""AuthService behaviour that doesn't need a database."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.exceptions import ServiceUnavailableError
from app.core.security import PasswordHasher, TokenService, TokenType
from app.services.auth import AuthService
from tests.conftest import make_settings


async def test_authenticate_fails_closed_when_revocation_store_is_down() -> None:
    settings = make_settings()
    tokens = TokenService(settings)
    store = MagicMock()
    store.is_revoked = AsyncMock(side_effect=RedisConnectionError("down"))
    service = AuthService(MagicMock(), settings, tokens, PasswordHasher(), store)

    token = tokens.issue(uuid.uuid4(), TokenType.ACCESS).token
    with pytest.raises(ServiceUnavailableError):
        await service.authenticate(token)
