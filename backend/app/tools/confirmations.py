"""Confirmation gate for consequential actions.

A tool that needs confirmation is *not* run when the model calls it. Instead a
ToolExecution in status AWAITING_CONFIRMATION records the validated arguments and a
human-readable summary. It is executed only when:

* the user approves it via the API (POST /tools/confirmations/{id}/approve), or
* the model calls ``confirm_pending_action`` **and** the user's own current message
  is a short, explicit affirmative ("yes", "haan kar do", "हाँ" ...) **and** the
  confirmation was requested in an *earlier* turn. The model cannot approve on its own:
  the checks run on what the user actually said, and on when the question was asked.
  (Otherwise a prompt-injected instruction, e.g. from a web result, could request a
  deletion and approve it in one turn whenever the user happened to say "ok, ...".)

Confirmations expire after CONFIRMATION_TTL_MINUTES.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_event
from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.models.enums import ToolExecutionStatus
from app.models.productivity import ToolExecution
from app.tools.base import Tool, ToolArgs, ToolContext, ToolError
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_AFFIRMATIVE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|confirm(ed)?|go ahead|do it|please do|haan+|haa+n?|"
    r"han|hanji|haanji|ji haan|theek hai|thik hai|thik h|kar do|kardo|karo|bilkul|zaroor)\b"
    r"|हाँ|हां|ठीक है|कर दो|बिल्कुल",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(no|nope|nah|not|don'?t|cancel|stop|wait|hold on|nahi+|nahin|mat|ruko|rehne do)\b"
    r"|नहीं|मत|रुको",
    re.IGNORECASE,
)


# A confirmation answer is short ("haan kar do yaar"). A longer message that merely
# contains "ok" or "karo" is a new request, not an answer.
_MAX_ANSWER_WORDS = 8


def is_affirmative(text: str) -> bool:
    if len(text.split()) > _MAX_ANSWER_WORDS:
        return False
    return bool(_AFFIRMATIVE.search(text)) and not _NEGATIVE.search(text)


class ConfirmationService:
    def __init__(self, session: AsyncSession, registry: ToolRegistry, settings: Settings) -> None:
        self._session = session
        self._registry = registry
        self._settings = settings

    async def create(
        self, ctx: ToolContext, tool: Tool, args: ToolArgs, summary: str
    ) -> ToolExecution:
        execution = ToolExecution(
            user_id=ctx.user.id,
            conversation_id=ctx.conversation_id,
            tool_name=tool.name,
            input=args.model_dump(mode="json"),
            status=ToolExecutionStatus.AWAITING_CONFIRMATION,
            confirmation_summary=summary,
            expires_at=ctx.now + timedelta(minutes=self._settings.confirmation_ttl_minutes),
        )
        self._session.add(execution)
        await self._session.commit()
        return execution

    async def pending(self, user_id: uuid.UUID) -> list[ToolExecution]:
        rows = await self._session.scalars(
            select(ToolExecution)
            .where(
                ToolExecution.user_id == user_id,
                ToolExecution.status == ToolExecutionStatus.AWAITING_CONFIRMATION,
                ToolExecution.expires_at > datetime.now(UTC),
            )
            .order_by(ToolExecution.created_at.desc())
        )
        return list(rows.all())

    async def _load_pending(self, user_id: uuid.UUID, execution_id: uuid.UUID) -> ToolExecution:
        execution = await self._session.scalar(
            select(ToolExecution)
            .where(ToolExecution.id == execution_id, ToolExecution.user_id == user_id)
            .with_for_update()
        )
        if execution is None:
            raise NotFoundError("Confirmation not found.")
        if execution.status is not ToolExecutionStatus.AWAITING_CONFIRMATION:
            raise ConflictError(f"This action is already {execution.status.value}.")
        if execution.expires_at and execution.expires_at < datetime.now(UTC):
            execution.status = ToolExecutionStatus.EXPIRED
            await self._session.commit()
            raise ConflictError("This confirmation has expired. Please ask again.")
        return execution

    async def approve(self, ctx: ToolContext, execution_id: uuid.UUID) -> ToolExecution:
        execution = await self._load_pending(ctx.user.id, execution_id)
        tool = self._registry.get(execution.tool_name)
        if tool is None:
            raise ConflictError("This action is no longer available.")
        try:
            args = tool.Args.model_validate(execution.input)
            output = await tool.run(ctx, args)
        except (ToolError, AppError, ValidationError) as exc:
            await ctx.rollback()
            execution = await self._session.get(ToolExecution, execution_id)
            execution.status = ToolExecutionStatus.FAILED
            execution.error_code = getattr(exc, "code", "invalid_arguments")
            execution.output = {"error": getattr(exc, "message", "Invalid arguments.")}
        else:
            execution.status = ToolExecutionStatus.SUCCEEDED
            execution.output = output
        await self._session.commit()
        await self._session.refresh(execution)  # load server-set updated_at
        audit_event(
            "tool.confirmation_approved",
            user_id=str(ctx.user.id),
            tool=execution.tool_name,
            execution_id=str(execution.id),
            outcome=execution.status.value,
        )
        return execution

    async def reject(self, user_id: uuid.UUID, execution_id: uuid.UUID) -> ToolExecution:
        execution = await self._load_pending(user_id, execution_id)
        execution.status = ToolExecutionStatus.CANCELLED
        await self._session.commit()
        await self._session.refresh(execution)
        audit_event(
            "tool.confirmation_rejected",
            user_id=str(user_id),
            tool=execution.tool_name,
            execution_id=str(execution.id),
        )
        return execution


class ConfirmPendingAction(Tool["ConfirmPendingAction.Args"]):
    name = "confirm_pending_action"
    description = (
        "Carry out (approve) or cancel (reject) an action that is waiting for the user's "
        "confirmation. Call it ONLY after the user has clearly answered your confirmation "
        "question in their latest message. Never approve on the user's behalf."
    )

    class Args(ToolArgs):
        confirmation_id: uuid.UUID
        decision: Literal["approve", "reject"]

    def __init__(self, service_factory) -> None:  # (session) -> ConfirmationService
        self._service_factory = service_factory

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        service: ConfirmationService = self._service_factory(ctx.session)
        pending = await service._load_pending(ctx.user.id, args.confirmation_id)  # noqa: SLF001
        if pending.conversation_id != ctx.conversation_id:
            raise ToolError(
                "wrong_conversation", "That confirmation belongs to another conversation."
            )
        if args.decision == "reject":
            await service.reject(ctx.user.id, args.confirmation_id)
            return {"cancelled": True}
        if args.confirmation_id in ctx.created_confirmations:
            raise ToolError(
                "confirmation_not_answered",
                "The user hasn't seen this question yet. Ask them and wait for their reply.",
            )
        if not is_affirmative(ctx.user_message):
            raise ToolError(
                "user_did_not_confirm",
                "The user's latest message isn't an explicit yes. Ask them to confirm clearly.",
            )
        execution = await service.approve(ctx, args.confirmation_id)
        if execution.status is not ToolExecutionStatus.SUCCEEDED:
            raise ToolError(
                execution.error_code or "failed",
                (execution.output or {}).get("error", "It failed."),
            )
        return {"done": True, "action": execution.confirmation_summary, "result": execution.output}
