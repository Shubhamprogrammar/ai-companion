import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.common import StrictRequest
from app.schemas.conversations import ConversationResponse, MessageResponse
from app.schemas.tools import ToolActionResponse

MAX_MESSAGE_CHARS = 8000


class ChatRequest(StrictRequest):
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Omit to start a new conversation"
    )
    message: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_CHARS)]


class ChatResponse(BaseModel):
    conversation: ConversationResponse
    user_message: MessageResponse
    assistant_message: MessageResponse
    actions: list[ToolActionResponse] = Field(
        default_factory=list,
        description="Actions taken or proposed this turn; check `confirmation` for ones "
        "awaiting your approval",
    )


class ChatStreamStart(BaseModel):
    conversation_id: uuid.UUID


class ChatStreamDelta(BaseModel):
    text: str


class ChatStreamToolStarted(BaseModel):
    tool: str


class ChatStreamError(BaseModel):
    code: str
    message: str
