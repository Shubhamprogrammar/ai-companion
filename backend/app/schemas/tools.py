import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.models.enums import ToolExecutionStatus
from app.schemas.common import ORMResponse
from app.tools.executor import ToolActivity


class PendingConfirmationResponse(BaseModel):
    confirmation_id: uuid.UUID
    summary: str
    expires_at: datetime


class ToolActionResponse(BaseModel):
    """An action the companion took (or proposed) during a turn."""

    execution_id: uuid.UUID
    tool: str
    status: ToolExecutionStatus
    error_code: str | None = None
    confirmation: PendingConfirmationResponse | None = None

    @classmethod
    def from_activity(cls, activity: ToolActivity) -> "ToolActionResponse":
        c = activity.confirmation
        return cls(
            execution_id=activity.execution_id,
            tool=activity.tool,
            status=activity.status,
            error_code=activity.error_code,
            confirmation=PendingConfirmationResponse(
                confirmation_id=c.confirmation_id, summary=c.summary, expires_at=c.expires_at
            )
            if c
            else None,
        )


class ToolInfo(BaseModel):
    name: str
    description: str
    confirmation: Literal["never", "always", "conditional"]


class ToolExecutionResponse(ORMResponse):
    id: uuid.UUID
    tool_name: str
    status: ToolExecutionStatus
    input: dict[str, Any]
    output: dict[str, Any] | None
    error_code: str | None
    confirmation_summary: str | None
    expires_at: datetime | None
    conversation_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ToolExecutionPage(BaseModel):
    items: list[ToolExecutionResponse]
    limit: int
    offset: int
    has_more: bool
