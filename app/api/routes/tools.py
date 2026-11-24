import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUserDep, ToolActionServiceDep, ToolRegistryDep
from app.models.enums import ToolExecutionStatus
from app.schemas.tools import ToolExecutionPage, ToolExecutionResponse, ToolInfo

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[ToolInfo])
async def list_tools(_: CurrentUserDep, registry: ToolRegistryDep) -> list[ToolInfo]:
    """Actions the companion can take, and whether each needs your confirmation."""
    return [
        ToolInfo(name=t.name, description=t.description, confirmation=t.confirmation)
        for t in registry.tools()
    ]


@router.get("/executions", response_model=ToolExecutionPage)
async def list_executions(
    user: CurrentUserDep,
    service: ToolActionServiceDep,
    status: ToolExecutionStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ToolExecutionPage:
    """Audit log of every action the companion took or attempted for you, newest first."""
    items, has_more = await service.executions(user, status=status, limit=limit, offset=offset)
    return ToolExecutionPage(
        items=[ToolExecutionResponse.model_validate(e) for e in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get("/confirmations", response_model=list[ToolExecutionResponse])
async def pending_confirmations(
    user: CurrentUserDep, service: ToolActionServiceDep
) -> list[ToolExecutionResponse]:
    """Actions waiting for your approval (not yet expired)."""
    return [ToolExecutionResponse.model_validate(e) for e in await service.pending(user)]


@router.post("/confirmations/{confirmation_id}/approve", response_model=ToolExecutionResponse)
async def approve(
    confirmation_id: uuid.UUID, user: CurrentUserDep, service: ToolActionServiceDep
) -> ToolExecutionResponse:
    """Carry out a pending action. Returns the execution with its final status/output."""
    return ToolExecutionResponse.model_validate(await service.approve(user, confirmation_id))


@router.post("/confirmations/{confirmation_id}/reject", response_model=ToolExecutionResponse)
async def reject(
    confirmation_id: uuid.UUID, user: CurrentUserDep, service: ToolActionServiceDep
) -> ToolExecutionResponse:
    return ToolExecutionResponse.model_validate(await service.reject(user, confirmation_id))
