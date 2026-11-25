from datetime import UTC, datetime

from redis.asyncio import Redis


class RevokedAccessTokenStore:
    """Redis deny-list for access tokens revoked before expiry (e.g. on logout).

    Entries expire together with the token, so the set never grows unbounded.
    """

    _PREFIX = "auth:revoked-access:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        ttl = int((expires_at - datetime.now(UTC)).total_seconds())
        if ttl > 0:
            await self._redis.set(self._PREFIX + jti, "1", ex=ttl)

    async def is_revoked(self, jti: str) -> bool:
        return bool(await self._redis.exists(self._PREFIX + jti))
