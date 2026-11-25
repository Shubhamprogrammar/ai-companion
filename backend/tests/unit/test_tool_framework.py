from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from app.tools.base import ToolError
from app.tools.confirmations import is_affirmative
from app.tools.defaults import build_registry
from app.tools.schema import strict_json_schema
from app.tools.timeparse import format_local, local_day_bounds, parse_date, parse_local_datetime
from tests.conftest import make_settings
from tests.fakes import FakeEmbeddings, FakeWebSearch


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_every_registered_tool_has_a_strict_compatible_schema() -> None:
    registry = build_registry(make_settings(), FakeEmbeddings(), FakeWebSearch())
    assert len(registry) == 14
    for spec in registry.specs():
        for node in _walk(spec.parameters):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False, spec.name
                assert node["required"] == list(node.get("properties", {})), spec.name
            for banned in ("format", "minLength", "maxLength", "minimum", "maximum", "default"):
                assert banned not in node, (spec.name, banned)


def test_property_named_title_survives_schema_cleaning() -> None:
    """Regression: 'title' is a JSON-Schema keyword *and* a common field name."""
    registry = build_registry(make_settings(), FakeEmbeddings(), None)
    create = next(s for s in registry.specs() if s.name == "create_reminder")
    assert "title" in create.parameters["properties"]
    assert "title" in create.parameters["required"]


def test_nested_models_are_rejected() -> None:
    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Inner

    with pytest.raises(ValueError, match="nested"):
        strict_json_schema(Outer)


def test_registry_honours_configuration() -> None:
    names = build_registry(make_settings(), FakeEmbeddings(), None).names()
    assert "search_web" not in names  # no provider → not offered
    assert "confirm_pending_action" in names
    disabled = build_registry(
        make_settings(tools_disabled=["delete_task", "create_task"]), FakeEmbeddings(), None
    ).names()
    assert "delete_task" not in disabled and "create_task" not in disabled
    assert len(build_registry(make_settings(tools_enabled=False), FakeEmbeddings(), None)) == 0


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "Yes please do it",
        "haan kar do",
        "Haanji",
        "theek hai",
        "ok go ahead",
        "हाँ कर दो",
        "bilkul",
        "sure",
    ],
)
def test_affirmative(text: str) -> None:
    assert is_affirmative(text)


@pytest.mark.parametrize(
    "text",
    [
        "no",
        "nahi",
        "mat karo",
        "wait",
        "don't delete it",
        "yes... actually no",
        "what reminder?",
        "hmm",
        "नहीं",
        "ok but not now",
        # Long messages that merely contain a yes-word are new requests, not answers.
        "ok so search the web for the best biryani places near me tonight",
        "sure, and also tell me what the weather is like in Delhi",
    ],
)
def test_not_affirmative(text: str) -> None:
    assert not is_affirmative(text)


def test_parse_local_datetime_uses_user_timezone() -> None:
    parsed = parse_local_datetime("2026-11-25T09:00", "Asia/Kolkata")
    assert parsed.astimezone(UTC) == datetime(2026, 11, 25, 3, 30, tzinfo=UTC)
    explicit = parse_local_datetime("2026-11-25T09:00+00:00", "Asia/Kolkata")
    assert explicit.astimezone(UTC).hour == 9
    with pytest.raises(ToolError):
        parse_local_datetime("tomorrow morning", "Asia/Kolkata")


def test_dates_and_formatting() -> None:
    assert parse_date("2026-12-01").day == 1
    with pytest.raises(ToolError):
        parse_date("1st Dec")
    start, end = local_day_bounds(parse_date("2026-11-25"), "Asia/Kolkata")
    assert start == datetime(2026, 11, 24, 18, 30, tzinfo=UTC)
    assert end > start
    assert format_local(datetime(2026, 11, 25, 3, 30, tzinfo=UTC), "Asia/Kolkata") == (
        "Wed 25 Nov 2026, 09:00 (Asia/Kolkata)"
    )
