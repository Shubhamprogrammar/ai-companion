"""User-facing view of tool activity: audit log and confirmation decisions."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.models.enums import ToolExecutionStatus
from app.models.productivity import ToolExecution
from app.models.user import User
from app.repositories.users import UserRepository
from app.tools.confirmations import ConfirmationService
from app.tools.executor import build_tool_context
from app.tools.registry import ToolRegistry


class ToolActionService:
    def __init__(self, session: AsyncSession, registry: ToolRegistry, settings: Settings) -> None:
        self._session = session
        self._registry = registry
        self._settings = settings
        self._confirmations = ConfirmationService(session, registry, settings)

    async def executions(
        self, user: User, *, status: ToolExecutionStatus | None, limit: int, offset: int
    ) -> tuple[Sequence[ToolExecution], bool]:
        query = select(ToolExecution).where(ToolExecution.user_id == user.id)
        if status is not None:
            query = query.where(ToolExecution.status == status)
        rows = (
            await self._session.scalars(
                query.order_by(ToolExecution.created_at.desc(), ToolExecution.id)
                .limit(limit + 1)
                .offset(offset)
            )
        ).all()
        return rows[:limit], len(rows) > limit

    async def pending(self, user: User) -> list[ToolExecution]:
        return await self._confirmations.pending(user.id)

    async def approve(self, user: User, execution_id: uuid.UUID) -> ToolExecution:
        profile = await UserRepository(self._session).get_profile(user.id)
        if profile is None:
            raise NotFoundError("Profile not found.")
        execution = await self._session.scalar(
            select(ToolExecution).where(
                ToolExecution.id == execution_id, ToolExecution.user_id == user.id
            )
        )
        if execution is None:
            raise NotFoundError("Confirmation not found.")
        ctx = build_tool_context(
            session=self._session,
            settings=self._settings,
            user=user,
            profile=profile,
            conversation_id=execution.conversation_id,
            user_message="",
            now=datetime.now(UTC),
        )
        return await self._confirmations.approve(ctx, execution_id)

    async def reject(self, user: User, execution_id: uuid.UUID) -> ToolExecution:
        return await self._confirmations.reject(user.id, execution_id)
