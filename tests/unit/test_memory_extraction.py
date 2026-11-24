import uuid

import pytest

from app.memory.extraction import (
    MAX_OPERATIONS_PER_TURN,
    MemoryExtractor,
    validate_operations,
    worth_extracting,
)
from app.models.enums import MemoryCategory
from app.models.memory import Memory
from tests.fakes import FakeLLM


def _op(**overrides) -> dict:
    return {
        "action": "add",
        "memory_ref": None,
        "content": "User is learning FastAPI",
        "category": "learning",
        "importance": 3,
    } | overrides


def _existing() -> dict[str, Memory]:
    return {
        "m1": Memory(
            id=uuid.uuid4(), content="User lives in Pune", category=MemoryCategory.PERSONAL
        )
    }


def test_valid_add_and_update() -> None:
    aliases = _existing()
    ops = validate_operations(
        {
            "operations": [
                _op(),
                _op(
                    action="update",
                    memory_ref="m1",
                    content="User lives in Bengaluru",
                    category="personal",
                    importance=4,
                ),
            ]
        },
        aliases,
    )
    assert [(o.action, o.memory_id) for o in ops] == [("add", None), ("update", aliases["m1"].id)]
    assert ops[0].category is MemoryCategory.LEARNING


@pytest.mark.parametrize(
    "bad",
    [
        _op(category="password"),
        _op(category="conversation_summary"),
        _op(content="hi"),
        _op(content="x" * 501),
        _op(importance=0),
        _op(importance=6),
        _op(importance=True),
        _op(importance="5"),
        _op(action="delete"),
        _op(action="update", memory_ref="m99"),  # unknown alias
        _op(action="update", memory_ref=str(uuid.uuid4())),  # raw ids are never accepted
        "not a dict",
    ],
)
def test_invalid_operations_are_dropped(bad) -> None:
    assert validate_operations({"operations": [bad]}, _existing()) == []


@pytest.mark.parametrize("data", [None, [], {"operations": "nope"}, {"ops": []}])
def test_malformed_payloads_yield_nothing(data) -> None:
    assert validate_operations(data, {}) == []


def test_duplicates_and_whitespace_collapsed_and_capped() -> None:
    raw = [_op(content=f"User likes topic number {i}") for i in range(10)]
    raw.insert(0, _op(content="  User   likes  chai  "))
    raw.insert(1, _op(content="user likes chai"))
    ops = validate_operations({"operations": raw}, {})
    assert ops[0].content == "User likes chai"
    assert len(ops) == MAX_OPERATIONS_PER_TURN
    assert sum(o.content.casefold() == "user likes chai" for o in ops) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ok", False),
        ("haha sahi hai", False),
        ("I'm preparing for the GATE exam next year", True),
        ("yaad rakhna", True),
        ("Remember: chai > coffee", True),
        ("याद रखना", True),
    ],
)
def test_worth_extracting(text: str, expected: bool) -> None:
    assert worth_extracting(text) is expected


async def test_extractor_shows_aliases_not_database_ids() -> None:
    llm = FakeLLM(
        json_replies=[
            {
                "operations": [
                    _op(
                        action="update",
                        memory_ref="m1",
                        content="User lives in Bengaluru now",
                        category="personal",
                    )
                ]
            }
        ]
    )
    existing = list(_existing().values())
    ops = await MemoryExtractor(llm, model="cheap-model").extract(
        "Main ab Bengaluru shift ho gaya", "Arre wah!", existing
    )
    prompt = llm.json_requests[0].messages[0].content
    assert "m1: [personal] User lives in Pune" in prompt
    assert str(existing[0].id) not in prompt
    assert llm.json_requests[0].model == "cheap-model"
    assert ops[0].memory_id == existing[0].id
