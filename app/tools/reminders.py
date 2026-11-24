import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from app.models.enums import ReminderStatus
from app.models.productivity import Reminder
from app.services.productivity import ReminderService
from app.tools.base import Tool, ToolArgs, ToolContext
from app.tools.timeparse import format_local, local_day_bounds, parse_date, parse_local_datetime

LOCAL_TIME = (
    "Local date-time in the user's timezone as 'YYYY-MM-DDTHH:MM' (no offset). Resolve "
    "relative phrases ('tomorrow 9 AM', 'in 2 hours') from the current local time."
)
Title = Annotated[str, Field(min_length=1, max_length=200, description="Short reminder title")]
Description = Annotated[str | None, Field(max_length=2000, description="Optional details")]


def reminder_view(reminder: Reminder, timezone: str) -> dict[str, Any]:
    return {
        "reminder_id": str(reminder.id),
        "title": reminder.title,
        "description": reminder.description,
        "scheduled_for": format_local(reminder.scheduled_at, timezone),
        "status": reminder.status.value,
    }


def _resolve(value: str, ctx: ToolContext) -> datetime:
    return parse_local_datetime(value, ctx.timezone)


class CreateReminder(Tool["CreateReminder.Args"]):
    name = "create_reminder"
    description = (
        "Save a reminder for the user at a specific time. Only call this once you know what "
        "to remind and when; if the time is missing or vague (e.g. 'later', 'sometime'), "
        "ask the user first instead of guessing."
    )

    class Args(ToolArgs):
        title: Title
        scheduled_at: str = Field(description=LOCAL_TIME)
        description: Description = None

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        reminder = await ReminderService(ctx.session).create(
            ctx.user,
            title=args.title,
            description=args.description,
            scheduled_at=_resolve(args.scheduled_at, ctx),
        )
        return reminder_view(reminder, ctx.timezone)


class ListReminders(Tool["ListReminders.Args"]):
    name = "list_reminders"
    description = "List the user's reminders, optionally filtered by status and local date range."

    class Args(ToolArgs):
        status: Literal["pending", "done", "cancelled", "all"] = Field(
            description="Use 'pending' for upcoming reminders"
        )
        from_date: str | None = Field(description="First local day to include, YYYY-MM-DD")
        to_date: str | None = Field(description="Last local day to include, YYYY-MM-DD")

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        start = (
            local_day_bounds(parse_date(args.from_date), ctx.timezone)[0]
            if args.from_date
            else None
        )
        end = local_day_bounds(parse_date(args.to_date), ctx.timezone)[1] if args.to_date else None
        status = None if args.status == "all" else ReminderStatus(args.status)
        items, more = await ReminderService(ctx.session).list_for_user(
            ctx.user, status=status, start=start, end=end, limit=20
        )
        return {"reminders": [reminder_view(r, ctx.timezone) for r in items], "more": more}


class UpdateReminder(Tool["UpdateReminder.Args"]):
    name = "update_reminder"
    description = (
        "Change a reminder (title, time, details) or mark it done/cancelled. Get the "
        "reminder_id from list_reminders or a previous result. Pass null for fields to keep."
    )

    class Args(ToolArgs):
        reminder_id: uuid.UUID
        title: Annotated[str | None, Field(max_length=200)]
        scheduled_at: str | None = Field(description=LOCAL_TIME)
        description: Description
        status: Literal["pending", "done", "cancelled"] | None

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if args.title:
            changes["title"] = args.title
        if args.description is not None:
            changes["description"] = args.description
        if args.scheduled_at:
            changes["scheduled_at"] = _resolve(args.scheduled_at, ctx)
        if args.status:
            changes["status"] = ReminderStatus(args.status)
        reminder = await ReminderService(ctx.session).update(ctx.user, args.reminder_id, changes)
        return reminder_view(reminder, ctx.timezone)


class DeleteReminder(Tool["DeleteReminder.Args"]):
    name = "delete_reminder"
    description = (
        "Permanently delete a reminder. This needs the user's confirmation: the result will "
        "ask you to get it. (To just stop a reminder, prefer update_reminder status=cancelled.)"
    )
    confirmation = "always"

    class Args(ToolArgs):
        reminder_id: uuid.UUID

    async def confirmation_summary(self, ctx: ToolContext, args: Args) -> str:
        reminder = await ReminderService(ctx.session).get(ctx.user, args.reminder_id)
        return (
            f'Delete reminder "{reminder.title}" '
            f"({format_local(reminder.scheduled_at, ctx.timezone)})"
        )

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        await ReminderService(ctx.session).delete(ctx.user, args.reminder_id)
        return {"deleted": True, "reminder_id": str(args.reminder_id)}
