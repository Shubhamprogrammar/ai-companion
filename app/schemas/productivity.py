import uuid
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, StringConstraints, model_validator

from app.models.enums import ReminderStatus, TaskStatus
from app.schemas.common import ORMResponse, StrictRequest

Title = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=2000)]


def _reject_nulls(model: StrictRequest, fields: tuple[str, ...]) -> None:
    for name in fields:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} cannot be null")


class ReminderCreateRequest(StrictRequest):
    title: Title
    description: Description | None = None
    scheduled_at: AwareDatetime  # ISO 8601 with offset, e.g. 2026-11-25T09:00:00+05:30


class ReminderUpdateRequest(StrictRequest):
    title: Title | None = None
    description: Description | None = None
    scheduled_at: AwareDatetime | None = None
    status: ReminderStatus | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        _reject_nulls(self, ("title", "scheduled_at", "status"))
        return self


class ReminderResponse(ORMResponse):
    id: uuid.UUID
    title: str
    description: str | None
    scheduled_at: datetime
    status: ReminderStatus
    created_at: datetime
    updated_at: datetime


class ReminderPage(BaseModel):
    items: list[ReminderResponse]
    limit: int
    offset: int
    has_more: bool


class TaskCreateRequest(StrictRequest):
    title: Title
    description: Description | None = None
    due_date: date | None = None


class TaskUpdateRequest(StrictRequest):
    title: Title | None = None
    description: Description | None = None
    status: TaskStatus | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        _reject_nulls(self, ("title", "status"))
        return self


class TaskResponse(ORMResponse):
    id: uuid.UUID
    title: str
    description: str | None
    status: TaskStatus
    due_date: date | None
    created_at: datetime
    updated_at: datetime


class TaskPage(BaseModel):
    items: list[TaskResponse]
    limit: int
    offset: int
    has_more: bool
