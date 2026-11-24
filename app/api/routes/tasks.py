import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencies import CurrentUserDep, TaskServiceDep
from app.models.enums import TaskStatus
from app.schemas.productivity import TaskCreateRequest, TaskPage, TaskResponse, TaskUpdateRequest

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreateRequest, user: CurrentUserDep, service: TaskServiceDep
) -> TaskResponse:
    task = await service.create(
        user, title=body.title, description=body.description, due_date=body.due_date
    )
    return TaskResponse.model_validate(task)


@router.get("", response_model=TaskPage)
async def list_tasks(
    user: CurrentUserDep,
    service: TaskServiceDep,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> TaskPage:
    """Earliest due date first; tasks without a due date last."""
    items, has_more = await service.list_for_user(
        user, status=status_filter, limit=limit, offset=offset
    )
    return TaskPage(
        items=[TaskResponse.model_validate(t) for t in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID, user: CurrentUserDep, service: TaskServiceDep
) -> TaskResponse:
    return TaskResponse.model_validate(await service.get(user, task_id))


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: uuid.UUID, body: TaskUpdateRequest, user: CurrentUserDep, service: TaskServiceDep
) -> TaskResponse:
    return TaskResponse.model_validate(
        await service.update(user, task_id, body.model_dump(exclude_unset=True))
    )


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, user: CurrentUserDep, service: TaskServiceDep) -> None:
    await service.delete(user, task_id)
