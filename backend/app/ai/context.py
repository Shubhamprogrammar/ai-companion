"""Short-term conversational context: which recent turns are sent to the model."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.ai.llm import ChatMessage
from app.models.conversation import Message
from app.models.enums import MessageRole


@dataclass(frozen=True, slots=True)
class ContextWindow:
    messages: list[ChatMessage]
    dropped: int  # how many older history messages didn't fit


def build_context_window(
    history: Sequence[Message],
    new_user_text: str,
    *,
    max_messages: int,
    max_chars: int,
) -> ContextWindow:
    """Most recent history that fits the budget, oldest-first, ending with the new message.

    The new user message is always included. Older history is dropped first, so
    the model always sees the most recent turns intact.
    """
    budget = max_chars - len(new_user_text)
    kept: list[ChatMessage] = []
    for message in reversed(history[-(max_messages - 1) :] if max_messages > 1 else []):
        if not message.content.strip():  # e.g. a reply interrupted before any text
            continue
        if len(message.content) > budget:
            break
        budget -= len(message.content)
        role = "assistant" if message.role is MessageRole.ASSISTANT else "user"
        kept.append(ChatMessage(role=role, content=message.content))
    kept.reverse()
    kept.append(ChatMessage(role="user", content=new_user_text))
    return ContextWindow(messages=kept, dropped=len(history) - (len(kept) - 1))
