import contextlib
import hashlib

from redis.asyncio import Redis
from redis.exceptions import RedisError


class LoginAttemptStore:
    """Counts failed logins per account (Redis), independent of the client's IP.

    Per-IP limits alone don't stop a password-guessing run spread over many IPs, and
    behind a misconfigured proxy every client shares one IP. Keys use a hash of the
    normalised email so addresses don't appear in Redis. Counting happens whether or
    not the account exists, so the lock doesn't reveal which emails are registered.
    Fails open if Redis is unavailable (the per-IP limit still applies).
    """

    _PREFIX = "auth:login-failures:"

    def __init__(self, redis: Redis, max_failures: int, window_seconds: int) -> None:
        self._redis = redis
        self._max = max_failures
        self._window = window_seconds

    def _key(self, email: str) -> str:
        return self._PREFIX + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]

    async def locked_for(self, email: str) -> int:
        """Seconds until login may be retried, or 0 if not locked."""
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.get(self._key(email))
                pipe.ttl(self._key(email))
                count, ttl = await pipe.execute()
        except RedisError:
            return 0
        return max(int(ttl), 1) if count and int(count) >= self._max else 0

    async def record_failure(self, email: str) -> None:
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(self._key(email))
                pipe.expire(self._key(email), self._window, nx=True)
                await pipe.execute()
        except RedisError:
            pass

    async def clear(self, email: str) -> None:
        with contextlib.suppress(RedisError):
            await self._redis.delete(self._key(email))
