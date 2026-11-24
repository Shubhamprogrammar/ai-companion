import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencies import ConversationServiceDep, CurrentUserDep
from app.models.enums import ConversationStatus
from app.schemas.conversations import (
    ConversationCreateRequest,
    ConversationPage,
    ConversationResponse,
    ConversationUpdateRequest,
    MessagePage,
    MessageResponse,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreateRequest, user: CurrentUserDep, service: ConversationServiceDep
) -> ConversationResponse:
    return ConversationResponse.model_validate(await service.create(user, body))


@router.get("", response_model=ConversationPage)
async def list_conversations(
    user: CurrentUserDep,
    service: ConversationServiceDep,
    status_filter: Annotated[ConversationStatus | None, Query(alias="status")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> ConversationPage:
    """Most recently active first."""
    items, has_more = await service.list_for_user(
        user, status=status_filter, limit=limit, offset=offset
    )
    return ConversationPage(
        items=[ConversationResponse.model_validate(c) for c in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUserDep, service: ConversationServiceDep
) -> ConversationResponse:
    return ConversationResponse.model_validate(await service.get(user, conversation_id))


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdateRequest,
    user: CurrentUserDep,
    service: ConversationServiceDep,
) -> ConversationResponse:
    return ConversationResponse.model_validate(await service.update(user, conversation_id, body))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUserDep, service: ConversationServiceDep
) -> None:
    """Permanently deletes the conversation and all its messages."""
    await service.delete(user, conversation_id)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
async def list_messages(
    conversation_id: uuid.UUID,
    user: CurrentUserDep,
    service: ConversationServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> MessagePage:
    """Oldest first."""
    items, has_more = await service.list_messages(user, conversation_id, limit=limit, offset=offset)
    return MessagePage(
        items=[MessageResponse.model_validate(m) for m in items],
        limit=limit,
        offset=offset,
        has_more=has_more,
    )
