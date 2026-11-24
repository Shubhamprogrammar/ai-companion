"""Provider-neutral language-model interface.

Business logic depends only on ``LLMProvider``; the OpenAI implementation lives
in ``app/ai/client.py``. Tests substitute a fake provider.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A function the model may call. ``parameters`` is a strict JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text from the model: untrusted until validated


@dataclass(frozen=True, slots=True)
class ToolResultItem:
    call_id: str
    output: str  # JSON text returned to the model


@dataclass(frozen=True, slots=True)
class ToolRound:
    """One completed round of tool use, replayed to the model on the next request.

    ``provider_items`` are the provider's own output items for that round (opaque to
    the rest of the app); ``results`` are our answers to its tool calls.
    """

    provider_items: tuple[Any, ...]
    results: tuple[ToolResultItem, ...]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    instructions: str
    messages: Sequence[ChatMessage]
    # Stable, non-reversible per-user id the provider can use for abuse monitoring.
    safety_identifier: str | None = None
    # Per-request overrides (e.g. a cheaper model for background memory work).
    model: str | None = None
    max_output_tokens: int | None = None
    tools: Sequence[ToolSpec] = ()
    tool_rounds: Sequence[ToolRound] = ()
    # "none" forces a text answer (used after the last allowed tool round).
    tool_choice: Literal["auto", "none"] = "auto"


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage | None = None
    # True when generation stopped early (e.g. output-token limit reached).
    truncated: bool = False
    latency_ms: float | None = field(default=None, compare=False)
    # When non-empty, the model wants these tools run before it can answer.
    tool_calls: tuple[ToolCall, ...] = ()
    provider_items: tuple[Any, ...] = field(default=(), compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


# A stream yields zero or more TextDelta events followed by exactly one LLMResponse.
LLMStreamEvent = TextDelta | LLMResponse


@dataclass(frozen=True, slots=True)
class JsonSchema:
    """A strict JSON Schema the model's output must conform to."""

    name: str
    schema: dict[str, Any]


class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]: ...

    async def generate_json(self, request: LLMRequest, schema: JsonSchema) -> dict[str, Any]:
        """Structured output. Callers must still validate the result."""
        ...
