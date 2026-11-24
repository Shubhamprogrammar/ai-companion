from app.ai.context import build_context_window
from app.models.conversation import Message
from app.models.enums import MessageRole


def _history(n: int, size: int = 10) -> list[Message]:
    roles = (MessageRole.USER, MessageRole.ASSISTANT)
    return [Message(role=roles[i % 2], content=f"{i:0{size}d}") for i in range(n)]


def test_new_message_is_always_last() -> None:
    window = build_context_window(_history(4), "hello", max_messages=10, max_chars=10_000)
    assert [m.role for m in window.messages] == ["user", "assistant", "user", "assistant", "user"]
    assert window.messages[-1].content == "hello"
    assert window.dropped == 0


def test_message_count_limit_keeps_most_recent() -> None:
    window = build_context_window(_history(10), "new", max_messages=4, max_chars=10_000)
    assert [m.content for m in window.messages] == ["0000000007", "0000000008", "0000000009", "new"]
    assert window.dropped == 7


def test_char_budget_drops_oldest_first() -> None:
    # Each history message is 10 chars; budget leaves room for exactly two of them.
    window = build_context_window(_history(5), "x" * 5, max_messages=50, max_chars=25)
    assert [m.content for m in window.messages] == ["0000000003", "0000000004", "xxxxx"]


def test_empty_history() -> None:
    window = build_context_window([], "hi", max_messages=30, max_chars=1000)
    assert [m.content for m in window.messages] == ["hi"]
