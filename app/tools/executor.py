"""Runs the model's tool calls: validate → (confirm?) → execute → audit.

Every call, including unknown tools, malformed JSON and rejected arguments, gets a
ToolExecution row, and the model always receives a structured result it can
explain to the user. Nothing the model sends is executed without passing the
tool's Pydantic validation, and tools only see the authenticated user's data.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import elapsed_ms
from app.ai.llm import ToolCall, ToolResultItem
from app.core.audit import audit_event
from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.enums import ToolExecutionStatus
from app.models.productivity import ToolExecution
from app.tools.base import Tool, ToolContext, ToolError
from app.tools.confirmations import ConfirmationService
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_MAX_RESULT_CHARS = 8000


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    confirmation_id: uuid.UUID
    summary: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ToolActivity:
    """What a client may show: which action ran and how it went (no raw arguments)."""

    execution_id: uuid.UUID
    tool: str
    status: ToolExecutionStatus
    error_code: str | None = None
    confirmation: PendingConfirmation | None = None


@dataclass(frozen=True, slots=True)
class ToolRunResult:
    result: ToolResultItem
    activity: ToolActivity


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        ctx: ToolContext,
        *,
        ensure_conversation: Callable[[], Awaitable[None]],
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._ctx = ctx
        self._ensure_conversation = ensure_conversation
        self._settings = settings
        self._confirmations = ConfirmationService(ctx.session, registry, settings)
        self._calls = 0

    @property
    def specs(self):
        return self._registry.specs()

    async def run(self, call: ToolCall) -> ToolRunResult:
        self._calls += 1
        start = time.perf_counter()
        await self._ensure_conversation()  # executions reference the conversation

        if self._calls > self._settings.tool_max_calls_per_turn:
            return await self._finish(
                call,
                start,
                ToolExecutionStatus.INVALID,
                {},
                None,
                "too_many_tool_calls",
                "Too many actions in one turn.",
            )
        tool = self._registry.get(call.name)
        if tool is None:
            return await self._finish(
                call,
                start,
                ToolExecutionStatus.INVALID,
                {},
                None,
                "unknown_tool",
                f"There is no tool called {call.name!r}.",
            )
        try:
            raw = json.loads(call.arguments or "{}")
            if not isinstance(raw, dict):
                raise ValueError("arguments must be an object")
        except ValueError:
            return await self._finish(
                call,
                start,
                ToolExecutionStatus.INVALID,
                {},
                None,
                "invalid_json",
                "Arguments were not valid JSON.",
            )
        try:
            args = tool.Args.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
            )
            return await self._finish(
                call,
                start,
                ToolExecutionStatus.INVALID,
                raw,
                None,
                "invalid_arguments",
                f"Invalid arguments: {problems}",
            )
        validated = args.model_dump(mode="json")

        if tool.needs_confirmation(args):
            return await self._request_confirmation(call, start, tool, args, validated)

        try:
            output = await tool.run(self._ctx, args)
        except (ToolError, AppError) as exc:
            await self._ctx.rollback()
            return await self._finish(
                call, start, ToolExecutionStatus.FAILED, validated, None, exc.code, exc.message
            )
        except Exception:
            await self._ctx.rollback()
            logger.exception("tool crashed", extra={"fields": {"tool": call.name}})
            return await self._finish(
                call,
                start,
                ToolExecutionStatus.FAILED,
                validated,
                None,
                "internal_error",
                "Something went wrong doing that.",
            )
        return await self._finish(call, start, ToolExecutionStatus.SUCCEEDED, validated, output)

    async def _request_confirmation(
        self, call: ToolCall, start: float, tool: Tool, args: Any, validated: dict[str, Any]
    ) -> ToolRunResult:
        try:
            summary = await tool.confirmation_summary(self._ctx, args)
        except (ToolError, AppError) as exc:  # e.g. the item to delete doesn't exist
            return await self._finish(
                call, start, ToolExecutionStatus.FAILED, validated, None, exc.code, exc.message
            )
        execution = await self._confirmations.create(self._ctx, tool, args, summary)
        pending = PendingConfirmation(execution.id, summary, execution.expires_at)
        payload = {
            "status": "confirmation_required",
            "confirmation_id": str(execution.id),
            "action": summary,
            "expires_in_minutes": self._settings.confirmation_ttl_minutes,
            "next_step": (
                "Nothing has been done yet. Ask the user, in plain words, to confirm this "
                "action. Call confirm_pending_action only after they clearly say yes."
            ),
        }
        self._log(call.name, ToolExecutionStatus.AWAITING_CONFIRMATION, start)
        audit_event(
            "tool.confirmation_requested",
            user_id=str(self._ctx.user.id),
            tool=call.name,
            execution_id=str(execution.id),
        )
        return ToolRunResult(
            ToolResultItem(call.call_id, _dump(payload)),
            ToolActivity(
                execution.id,
                call.name,
                ToolExecutionStatus.AWAITING_CONFIRMATION,
                confirmation=pending,
            ),
        )

    async def _finish(
        self,
        call: ToolCall,
        start: float,
        status: ToolExecutionStatus,
        tool_input: dict[str, Any],
        output: dict[str, Any] | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ToolRunResult:
        execution = ToolExecution(
            user_id=self._ctx.user.id,
            conversation_id=self._ctx.conversation_id,
            tool_name=call.name[:64],
            input=tool_input,
            output=output if error_code is None else {"error": error_message},
            status=status,
            error_code=error_code,
        )
        self._ctx.session.add(execution)
        await self._ctx.session.commit()
        self._log(call.name, status, start, error_code)

        payload = (
            {"status": "succeeded", "result": output}
            if error_code is None
            else {"status": "error", "error_code": error_code, "message": error_message}
        )
        return ToolRunResult(
            ToolResultItem(call.call_id, _dump(payload)),
            ToolActivity(execution.id, call.name, status, error_code),
        )

    def _log(
        self, tool: str, status: ToolExecutionStatus, start: float, code: str | None = None
    ) -> None:
        logger.info(
            "tool execution completed",
            extra={
                "fields": {
                    "tool": tool,
                    "status": status.value,
                    "error_code": code,
                    "duration_ms": elapsed_ms(start),
                }
            },
        )


def _dump(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, default=str, ensure_ascii=False)
    return text if len(text) <= _MAX_RESULT_CHARS else text[:_MAX_RESULT_CHARS] + "…(truncated)"


def build_tool_context(
    *,
    session: AsyncSession,
    settings: Settings,
    user,
    profile,
    conversation_id: uuid.UUID | None,
    user_message: str,
    now: datetime,
) -> ToolContext:
    return ToolContext(
        user=user,
        profile=profile,
        session=session,
        settings=settings,
        conversation_id=conversation_id,
        user_message=user_message,
        now=now,
    )
