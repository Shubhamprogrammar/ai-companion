"""In-process background work (e.g. memory extraction after a reply has been sent).

Deliberately small: no broker, no persistence. Jobs are best-effort. If the process
stops, pending jobs are lost, which is acceptable for work that only enriches memory.
Shutdown waits briefly for in-flight jobs.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class TaskRunner(Protocol):
    def submit(self, name: str, job: Job) -> None: ...


class BackgroundTaskRunner:
    def __init__(self, max_concurrency: int = 4) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def submit(self, name: str, job: Job) -> None:
        task = asyncio.create_task(self._run(name, job), name=name)
        self._tasks.add(task)  # keep a reference so the task isn't garbage-collected
        task.add_done_callback(self._tasks.discard)

    async def _run(self, name: str, job: Job) -> None:
        async with self._semaphore:
            try:
                await job()
            except Exception:
                logger.exception("background job failed", extra={"fields": {"job": name}})

    async def drain(self, grace_seconds: float = 10.0) -> None:
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning(
                "cancelled background jobs at shutdown", extra={"fields": {"count": len(pending)}}
            )


class InlineTaskRunner:
    """Collects jobs and runs them when ``run_pending`` is awaited (tests, scripts)."""

    def __init__(self) -> None:
        self.pending: list[tuple[str, Job]] = []

    def submit(self, name: str, job: Job) -> None:
        self.pending.append((name, job))

    async def run_pending(self) -> None:
        while self.pending:
            name, job = self.pending.pop(0)
            await job()
