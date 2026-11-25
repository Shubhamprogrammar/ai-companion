import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ReminderStatus, TaskStatus
from app.models.productivity import Reminder, Task


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, reminder: Reminder) -> Reminder:
        self._session.add(reminder)
        await self._session.flush()
        await self._session.refresh(reminder)
        return reminder

    async def get_for_user(self, reminder_id: uuid.UUID, user_id: uuid.UUID) -> Reminder | None:
        return await self._session.scalar(
            select(Reminder).where(Reminder.id == reminder_id, Reminder.user_id == user_id)
        )

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        status: ReminderStatus | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> Sequence[Reminder]:
        query = select(Reminder).where(Reminder.user_id == user_id)
        if status is not None:
            query = query.where(Reminder.status == status)
        if start is not None:
            query = query.where(Reminder.scheduled_at >= start)
        if end is not None:
            query = query.where(Reminder.scheduled_at < end)
        query = query.order_by(Reminder.scheduled_at, Reminder.id).limit(limit).offset(offset)
        return (await self._session.scalars(query)).all()

    async def delete(self, reminder: Reminder) -> None:
        await self._session.delete(reminder)
        await self._session.flush()


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: Task) -> Task:
        self._session.add(task)
        await self._session.flush()
        await self._session.refresh(task)
        return task

    async def get_for_user(self, task_id: uuid.UUID, user_id: uuid.UUID) -> Task | None:
        return await self._session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )

    async def list_for_user(
        self, user_id: uuid.UUID, *, status: TaskStatus | None, limit: int, offset: int
    ) -> Sequence[Task]:
        query = select(Task).where(Task.user_id == user_id)
        if status is not None:
            query = query.where(Task.status == status)
        query = (
            query.order_by(Task.due_date.asc().nulls_last(), Task.created_at, Task.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(query)).all()

    async def delete(self, task: Task) -> None:
        await self._session.delete(task)
        await self._session.flush()


def apply_changes(entity: Any, changes: dict[str, Any]) -> None:
    for field, value in changes.items():
        setattr(entity, field, value)
