"""Resolving the model's date/time arguments in the user's timezone."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from app.tools.base import ToolError


def parse_local_datetime(value: str, timezone: str) -> datetime:
    """'2026-11-25T09:00' (user-local) or with an explicit offset → aware datetime."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError("invalid_datetime", f"Couldn't read the time '{value}'.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError("invalid_date", f"Couldn't read the date '{value}'.") from exc


def local_day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day, time.max, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def format_local(moment: datetime, timezone: str) -> str:
    """e.g. 'Wed 25 Nov 2026, 09:00 (Asia/Kolkata)'. Unambiguous for the model to relay."""
    return f"{moment.astimezone(ZoneInfo(timezone)):%a %d %b %Y, %H:%M} ({timezone})"
