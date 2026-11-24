import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import CurrentUserDep, MemoryServiceDep, user_rate_limit
from app.core.exceptions import ValidationAppError
from app.models.enums import MemoryCategory
from app.schemas.memories import (
    MemoryCreateRequest,
    MemoryDeleteAllResponse,
    MemoryPage,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    MemoryUpdateRequest,
)

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=MemoryPage)
async def list_memories(
    user: CurrentUserDep,
    service: MemoryServiceDep,
    category: MemoryCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> MemoryPage:
    """Everything the companion remembers about you, most recently updated first."""
    items, has_more = await service.list_for_user(
        user, category=category, limit=limit, offset=offset
    )
    return MemoryPage(
        items=[MemoryResponse.model_validate(m) for m in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.post(
    "",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(user_rate_limit("memory"))],
)
async def create_memory(
    body: MemoryCreateRequest, user: CurrentUserDep, service: MemoryServiceDep
) -> MemoryResponse:
    return MemoryResponse.model_validate(await service.create(user, body))


@router.post(
    "/search",
    response_model=MemorySearchResponse,
    dependencies=[Depends(user_rate_limit("memory"))],
)
async def search_memories(
    body: MemorySearchRequest, user: CurrentUserDep, service: MemoryServiceDep
) -> MemorySearchResponse:
    """Semantic search: finds memories by meaning, not exact words."""
    results = await service.search(user, body.query, body.limit)
    return MemorySearchResponse(
        results=[
            MemorySearchResult(
                memory=MemoryResponse.model_validate(m), similarity=round(1 - distance, 4)
            )
            for m, distance in results
        ]
    )


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: uuid.UUID, user: CurrentUserDep, service: MemoryServiceDep
) -> MemoryResponse:
    return MemoryResponse.model_validate(await service.get(user, memory_id))


@router.patch(
    "/{memory_id}",
    response_model=MemoryResponse,
    dependencies=[Depends(user_rate_limit("memory"))],
)
async def update_memory(
    memory_id: uuid.UUID, body: MemoryUpdateRequest, user: CurrentUserDep, service: MemoryServiceDep
) -> MemoryResponse:
    return MemoryResponse.model_validate(await service.update(user, memory_id, body))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID, user: CurrentUserDep, service: MemoryServiceDep
) -> None:
    await service.delete(user, memory_id)


@router.delete("", response_model=MemoryDeleteAllResponse)
async def delete_all_memories(
    user: CurrentUserDep,
    service: MemoryServiceDep,
    confirm: Annotated[bool, Query(description="Must be true: this cannot be undone")] = False,
) -> MemoryDeleteAllResponse:
    """Permanently delete every memory. Requires `?confirm=true`."""
    if not confirm:
        raise ValidationAppError("Pass confirm=true to delete all memories.")
    return MemoryDeleteAllResponse(deleted=await service.delete_all(user))
