"""Readiness checks for infrastructure dependencies."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.schemas.health import CheckStatus, ReadinessResponse

logger = logging.getLogger(__name__)

Check = Callable[[], Awaitable[None]]


class HealthService:
    def __init__(self, checks: dict[str, Check], timeout_seconds: float = 3.0) -> None:
        self._checks = checks
        self._timeout = timeout_seconds

    async def _run(self, name: str, check: Check) -> CheckStatus:
        try:
            await asyncio.wait_for(check(), timeout=self._timeout)
            return "ok"
        except Exception as exc:  # any failure means "not ready"
            # Log the type only: connection errors may embed DSNs/credentials.
            logger.warning(
                "readiness check failed",
                extra={"fields": {"check": name, "error_type": type(exc).__name__}},
            )
            return "error"

    async def readiness(self) -> ReadinessResponse:
        names = list(self._checks)
        results = await asyncio.gather(*(self._run(n, self._checks[n]) for n in names))
        checks = dict(zip(names, results, strict=True))
        ready = all(result == "ok" for result in results)
        return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
