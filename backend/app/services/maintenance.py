"""Periodic housekeeping: retention purges and pruning of rows that only grow.

Run by ``scripts/maintenance.py`` (one-shot or ``--loop``). Every step is idempotent and
safe to run concurrently with the API; the script additionally takes a Redis lock so
multiple schedulers don't overlap.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.storage import AudioStore
from app.core.audit import audit_event
from app.core.config import Settings
from app.models.enums import ToolExecutionStatus
from app.models.productivity import ToolExecution
from app.models.refresh_token import RefreshToken
from app.services.conversations import ConversationService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    conversations_purged: int
    refresh_tokens_pruned: int
    confirmations_expired: int
    tool_executions_pruned: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class MaintenanceService:
    def __init__(
        self, session: AsyncSession, settings: Settings, audio_store: AudioStore | None = None
    ) -> None:
        self._session = session
        self._settings = settings
        self._audio_store = audio_store

    async def run(self, now: datetime | None = None) -> MaintenanceReport:
        now = now or datetime.now(UTC)
        report = MaintenanceReport(
            conversations_purged=await ConversationService(
                self._session, self._audio_store
            ).purge_expired(),
            refresh_tokens_pruned=await self.prune_refresh_tokens(now),
            confirmations_expired=await self.expire_confirmations(now),
            tool_executions_pruned=await self.prune_tool_executions(now),
        )
        logger.info("maintenance completed", extra={"fields": report.as_dict()})
        return report

    async def prune_refresh_tokens(self, now: datetime) -> int:
        """Delete refresh-token records past expiry.

        Revoked-but-unexpired records are kept on purpose: reuse detection needs them
        to recognise a stolen, already-rotated token (and then revoke the whole family).
        Once expired, the JWT itself is rejected, so the record has no further use.
        """
        result = await self._session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < now)
        )
        await self._session.commit()
        return result.rowcount or 0

    async def expire_confirmations(self, now: datetime) -> int:
        """Mark confirmations nobody answered in time as expired (they can't be approved)."""
        result = await self._session.execute(
            update(ToolExecution)
            .where(
                ToolExecution.status == ToolExecutionStatus.AWAITING_CONFIRMATION,
                ToolExecution.expires_at < now,
            )
            .values(status=ToolExecutionStatus.EXPIRED, updated_at=now)
        )
        await self._session.commit()
        return result.rowcount or 0

    async def prune_tool_executions(self, now: datetime) -> int:
        """Delete tool audit rows older than TOOL_EXECUTION_RETENTION_DAYS.

        Their inputs/outputs can contain personal details (reminder text, search
        queries), so they shouldn't be kept forever. Pending confirmations are never
        deleted here; they're expired first, then age out like any other row.
        """
        days = self._settings.tool_execution_retention_days
        if days is None:
            return 0
        result = await self._session.execute(
            delete(ToolExecution).where(
                ToolExecution.created_at < now - timedelta(days=days),
                ToolExecution.status != ToolExecutionStatus.AWAITING_CONFIRMATION,
            )
        )
        await self._session.commit()
        if result.rowcount:
            audit_event("tool_executions.pruned", count=result.rowcount)
        return result.rowcount or 0
