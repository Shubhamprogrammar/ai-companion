import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import AwareDatetime

from app.api.dependencies import CurrentUserDep, ReminderServiceDep
from app.models.enums import ReminderStatus
from app.schemas.productivity import (
    ReminderCreateRequest,
    ReminderPage,
    ReminderResponse,
    ReminderUpdateRequest,
)

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.post("", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    body: ReminderCreateRequest, user: CurrentUserDep, service: ReminderServiceDep
) -> ReminderResponse:
    reminder = await service.create(
        user, title=body.title, description=body.description, scheduled_at=body.scheduled_at
    )
    return ReminderResponse.model_validate(reminder)


@router.get("", response_model=ReminderPage)
async def list_reminders(
    user: CurrentUserDep,
    service: ReminderServiceDep,
    status_filter: Annotated[ReminderStatus | None, Query(alias="status")] = None,
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ReminderPage:
    """Soonest first. `from`/`to` filter by scheduled time (ISO 8601 with offset)."""
    items, has_more = await service.list_for_user(
        user, status=status_filter, start=start, end=end, limit=limit, offset=offset
    )
    return ReminderPage(
        items=[ReminderResponse.model_validate(r) for r in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get("/{reminder_id}", response_model=ReminderResponse)
async def get_reminder(
    reminder_id: uuid.UUID, user: CurrentUserDep, service: ReminderServiceDep
) -> ReminderResponse:
    return ReminderResponse.model_validate(await service.get(user, reminder_id))


@router.patch("/{reminder_id}", response_model=ReminderResponse)
async def update_reminder(
    reminder_id: uuid.UUID,
    body: ReminderUpdateRequest,
    user: CurrentUserDep,
    service: ReminderServiceDep,
) -> ReminderResponse:
    reminder = await service.update(user, reminder_id, body.model_dump(exclude_unset=True))
    return ReminderResponse.model_validate(reminder)


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reminder(
    reminder_id: uuid.UUID, user: CurrentUserDep, service: ReminderServiceDep
) -> None:
    await service.delete(user, reminder_id)
