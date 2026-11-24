"""Redis fixed-window rate limiter."""

import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Count one request against ``key``.

        Fails open (allows the request) if Redis is unavailable: rate limiting is a
        protective layer, and an outage should not take authentication down with it.
        """
        redis_key = f"ratelimit:{key}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, window_seconds, nx=True)
                pipe.ttl(redis_key)
                count, _, ttl = await pipe.execute()
        except RedisError:
            logger.warning("rate limiter unavailable; allowing request", exc_info=False)
            return RateLimitResult(allowed=True, remaining=limit, retry_after_seconds=0)

        count = int(count)
        return RateLimitResult(
            allowed=count <= limit,
            remaining=max(limit - count, 0),
            retry_after_seconds=max(int(ttl), 1),
        )
