"""Async Redis client wrapper."""

from redis.asyncio import Redis

from app.core.config import Settings


class RedisClient:
    def __init__(self, settings: Settings) -> None:
        self.client: Redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )

    async def ping(self) -> None:
        if not await self.client.ping():
            raise RuntimeError("Redis did not respond to PING")

    async def close(self) -> None:
        await self.client.aclose()
