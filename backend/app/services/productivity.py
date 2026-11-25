"""Reminders and tasks. Shared by the REST API and the companion's tools."""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationAppError
from app.models.enums import ReminderStatus, TaskStatus
from app.models.productivity import Reminder, Task
from app.models.user import User
from app.repositories.productivity import ReminderRepository, TaskRepository, apply_changes

MAX_SCHEDULE_AHEAD = timedelta(days=5 * 365)
# Allow a small grace window so "in one minute" isn't rejected by clock skew.
_PAST_GRACE = timedelta(seconds=30)


def validate_schedule(scheduled_at: datetime, *, now: datetime | None = None) -> datetime:
    if scheduled_at.tzinfo is None:
        raise ValidationAppError("scheduled_at must include a timezone.")
    now = now or datetime.now(UTC)
    if scheduled_at < now - _PAST_GRACE:
        raise ValidationAppError("That time is in the past.")
    if scheduled_at > now + MAX_SCHEDULE_AHEAD:
        raise ValidationAppError("Reminders can be at most 5 years ahead.")
    return scheduled_at.astimezone(UTC)


class ReminderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ReminderRepository(session)

    async def create(
        self, user: User, *, title: str, description: str | None, scheduled_at: datetime
    ) -> Reminder:
        reminder = await self._repo.add(
            Reminder(
                user_id=user.id,
                title=title,
                description=description,
                scheduled_at=validate_schedule(scheduled_at),
            )
        )
        await self._session.commit()
        return reminder

    async def list_for_user(
        self,
        user: User,
        *,
        status: ReminderStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Reminder], bool]:
        rows = await self._repo.list_for_user(
            user.id, status=status, start=start, end=end, limit=limit + 1, offset=offset
        )
        return rows[:limit], len(rows) > limit

    async def get(self, user: User, reminder_id: uuid.UUID) -> Reminder:
        reminder = await self._repo.get_for_user(reminder_id, user.id)
        if reminder is None:
            raise NotFoundError("Reminder not found.")
        return reminder

    async def update(self, user: User, reminder_id: uuid.UUID, changes: dict[str, Any]) -> Reminder:
        reminder = await self.get(user, reminder_id)
        if changes.get("scheduled_at") is not None:
            changes["scheduled_at"] = validate_schedule(changes["scheduled_at"])
        apply_changes(reminder, changes)
        await self._session.commit()
        await self._session.refresh(reminder)
        return reminder

    async def delete(self, user: User, reminder_id: uuid.UUID) -> None:
        await self._repo.delete(await self.get(user, reminder_id))
        await self._session.commit()


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TaskRepository(session)

    async def create(
        self, user: User, *, title: str, description: str | None, due_date: date | None
    ) -> Task:
        task = await self._repo.add(
            Task(user_id=user.id, title=title, description=description, due_date=due_date)
        )
        await self._session.commit()
        return task

    async def list_for_user(
        self, user: User, *, status: TaskStatus | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[Task], bool]:
        rows = await self._repo.list_for_user(
            user.id, status=status, limit=limit + 1, offset=offset
        )
        return rows[:limit], len(rows) > limit

    async def get(self, user: User, task_id: uuid.UUID) -> Task:
        task = await self._repo.get_for_user(task_id, user.id)
        if task is None:
            raise NotFoundError("Task not found.")
        return task

    async def update(self, user: User, task_id: uuid.UUID, changes: dict[str, Any]) -> Task:
        task = await self.get(user, task_id)
        apply_changes(task, changes)
        await self._session.commit()
        await self._session.refresh(task)
        return task

    async def delete(self, user: User, task_id: uuid.UUID) -> None:
        await self._repo.delete(await self.get(user, task_id))
        await self._session.commit()
