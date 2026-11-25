"""Reusable field validators for request schemas."""

from functools import lru_cache
from zoneinfo import available_timezones


@lru_cache(maxsize=1)
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(value: str) -> str:
    if value not in _timezones():
        raise ValueError("must be a valid IANA timezone, e.g. 'Asia/Kolkata'")
    return value


def validate_password(value: str) -> str:
    if not value.strip():
        raise ValueError("password must not be blank")
    return value
