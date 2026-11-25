"""Housekeeping: retention purge, refresh-token and tool-audit pruning, stale confirmations.

    python -m scripts.maintenance            # run once (cron / systemd timer)
    python -m scripts.maintenance --loop     # run every MAINTENANCE_INTERVAL_MINUTES

The compose ``maintenance`` service runs ``--loop``. A Redis lock ensures only one
run at a time even if several schedulers are configured. Exit code 0 means the run
succeeded or was skipped because another run held the lock.
"""

import argparse
import asyncio
import contextlib
import logging
import signal
import uuid

from redis.exceptions import RedisError

from app.audio.storage import build_audio_store
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.logging import configure_logging
from app.core.redis import RedisClient
from app.services.maintenance import MaintenanceReport, MaintenanceService

logger = logging.getLogger("scripts.maintenance")

LOCK_KEY = "maintenance:lock"
LOCK_TTL_SECONDS = 15 * 60  # a run takes seconds; the TTL only matters if one crashes

# Release only our own lock (another run may hold it after ours expired).
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


async def run_once(settings: Settings, database: Database, redis: RedisClient) -> bool:
    """One locked maintenance pass. Returns False if skipped because another run holds the lock."""
    token = uuid.uuid4().hex
    try:
        acquired = await redis.client.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_SECONDS)
    except RedisError:
        # Every step is idempotent, so running unlocked is safe; it just isn't exclusive.
        logger.warning("maintenance lock unavailable (Redis down); running without it")
        acquired, token = True, None
    if not acquired:
        logger.info("maintenance skipped: another run holds the lock")
        return False
    try:
        audio_store = build_audio_store(
            settings.audio_retention_enabled, settings.audio_storage_path
        )
        async with database.session_factory() as session:
            report: MaintenanceReport = await MaintenanceService(
                session, settings, audio_store
            ).run()
        print(report.as_dict(), flush=True)
        return True
    finally:
        if token is not None:
            with contextlib.suppress(RedisError):  # the lock expires on its own
                await redis.client.eval(_RELEASE, 1, LOCK_KEY, token)


async def main(loop: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    database, redis = Database(settings), RedisClient(settings)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    failed = False
    try:
        while True:
            try:
                await run_once(settings, database, redis)
                failed = False
            except Exception:
                failed = True
                logger.exception("maintenance run failed")
                if not loop:
                    break
            if not loop:
                break
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.maintenance_interval_minutes * 60
                )
                break  # asked to stop
            except TimeoutError:
                continue
    finally:
        await redis.close()
        await database.dispose()
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--loop", action="store_true", help="run every MAINTENANCE_INTERVAL_MINUTES"
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().loop)))
