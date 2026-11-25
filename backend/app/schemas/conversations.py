import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, StringConstraints, model_validator

from app.models.enums import ConversationStatus, MessageRole, MessageType
from app.schemas.common import ORMResponse, StrictRequest

Title = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ConversationCreateRequest(StrictRequest):
    title: Title | None = None


class ConversationUpdateRequest(StrictRequest):
    title: Title | None = None  # explicit null clears the title
    status: ConversationStatus | None = None

    @model_validator(mode="after")
    def _status_not_null(self) -> Self:
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


class ConversationResponse(ORMResponse):
    id: uuid.UUID
    title: str | None
    summary: str | None
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime


class MessageResponse(ORMResponse):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    message_type: MessageType
    created_at: datetime


class ConversationPage(BaseModel):
    items: list[ConversationResponse]
    limit: int
    offset: int
    has_more: bool


class MessagePage(BaseModel):
    items: list[MessageResponse]
    limit: int
    offset: int
    has_more: bool
