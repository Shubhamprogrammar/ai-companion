"""Tool contract.

A tool declares a Pydantic ``Args`` model. That one model produces the strict JSON
schema the LLM sees *and* validates every call on the backend. The LLM's arguments
are never trusted, and tools can only reach the authenticated user's own data
through ``ToolContext``.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.user import User, UserProfile


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True, slots=True)
class ToolContext:
    user: User
    profile: UserProfile
    session: AsyncSession
    settings: Settings
    conversation_id: uuid.UUID | None
    user_message: str  # the user's current message, as they wrote or said it
    now: datetime  # aware UTC

    @property
    def timezone(self) -> str:
        return self.profile.timezone

    async def rollback(self) -> None:
        """Discard a failed tool's partial work.

        A rollback expires every ORM object in the session, including the authenticated
        user and profile that the rest of the turn still needs, so reload them right away.
        (Without this, the next attribute access would attempt lazy IO and crash.)
        """
        await self.session.rollback()
        await self.session.refresh(self.user)
        await self.session.refresh(self.profile)


class ToolError(Exception):
    """A failure the model may relay to the user (message must be safe to show)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class Tool[ArgsT: ToolArgs]:
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[ToolArgs]]
    # "never" | "always" | "conditional" (see needs_confirmation). Advertised by GET /tools.
    confirmation: ClassVar[str] = "never"

    def needs_confirmation(self, args: ArgsT) -> bool:
        return self.confirmation == "always"

    async def confirmation_summary(self, ctx: ToolContext, args: ArgsT) -> str:
        """Human-readable description of what the user is asked to approve.

        May raise ToolError, e.g. when the target doesn't exist (so nothing is proposed).
        """
        return f"Run {self.name}"

    async def run(self, ctx: ToolContext, args: ArgsT) -> dict[str, Any]:
        raise NotImplementedError
