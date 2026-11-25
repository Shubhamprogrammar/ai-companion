"""OpenAIResponsesLLM exercised through the real SDK against a mocked HTTP transport."""

import json
from collections.abc import Callable

import httpx
import pytest
from openai import AsyncOpenAI

from app.ai.client import OpenAIResponsesLLM, build_openai_client
from app.ai.llm import ChatMessage, LLMRequest, LLMResponse, TextDelta
from app.core.exceptions import AIUnavailableError
from tests.conftest import make_settings

REQUEST = LLMRequest(
    instructions="You are Mira.",
    messages=[
        ChatMessage("user", "Hi"),
        ChatMessage("assistant", "Hey!"),
        ChatMessage("user", "Kya haal?"),
    ],
    safety_identifier="abc123",
)


def _response_json(text: str = "Sab badhiya, yaar!", status: str = "completed") -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_790_000_000,
        "status": status,
        "model": "test-chat-model",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 9,
            "total_tokens": 129,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], **settings_overrides
) -> OpenAIResponsesLLM:
    settings = make_settings(
        openai_api_key="sk-test",
        openai_chat_model="test-chat-model",
        openai_max_retries=0,
        **settings_overrides,
    )
    client = AsyncOpenAI(
        api_key="sk-test",
        base_url="http://openai.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return OpenAIResponsesLLM(client, settings)


async def test_generate_sends_expected_payload_and_parses_reply() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_response_json())

    result = await _provider(handler).generate(REQUEST)

    assert seen["path"] == "/v1/responses"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["model"] == "test-chat-model"
    assert body["instructions"] == "You are Mira."
    assert body["input"] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hey!"},
        {"role": "user", "content": "Kya haal?"},
    ]
    assert body["store"] is False
    assert body["safety_identifier"] == "abc123"
    assert "reasoning" not in body  # only sent when configured
    assert "temperature" not in body

    assert result.text == "Sab badhiya, yaar!"
    assert result.model == "test-chat-model"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 9)
    assert result.truncated is False


async def test_reasoning_effort_is_sent_when_configured() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_response_json())

    await _provider(handler, openai_reasoning_effort="low").generate(REQUEST)
    assert seen["reasoning"] == {"effort": "low"}


async def test_incomplete_response_is_marked_truncated() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_json(status="incomplete"))

    assert (await _provider(handler).generate(REQUEST)).truncated is True


async def test_empty_output_is_an_error_not_a_blank_reply() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_json(text="   "))

    with pytest.raises(AIUnavailableError):
        await _provider(handler).generate(REQUEST)


@pytest.mark.parametrize("status_code", [400, 401, 404, 429, 500, 503])
async def test_provider_errors_become_ai_unavailable(status_code: int, caplog) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "nope sk-test", "type": "x"}})

    with pytest.raises(AIUnavailableError):
        await _provider(handler).generate(REQUEST)
    assert "sk-test" not in caplog.text


async def test_timeout_becomes_ai_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AIUnavailableError):
        await _provider(handler).generate(REQUEST)


async def test_not_configured_fails_without_network() -> None:
    provider = OpenAIResponsesLLM(None, make_settings(openai_chat_model="m"))
    with pytest.raises(AIUnavailableError):
        await provider.generate(REQUEST)
    no_model = OpenAIResponsesLLM(
        build_openai_client(make_settings(openai_api_key="sk")), make_settings(openai_chat_model="")
    )
    with pytest.raises(AIUnavailableError):
        await no_model.generate(REQUEST)


def test_client_not_built_without_api_key() -> None:
    assert build_openai_client(make_settings(openai_api_key="")) is None


def _sse(events: list[dict]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def _delta(text: str, seq: int) -> dict:
    return {
        "type": "response.output_text.delta",
        "delta": text,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "sequence_number": seq,
        "logprobs": [],
    }


async def test_stream_yields_deltas_then_final_response() -> None:
    body = _sse(
        [
            _delta("Sab ", 1),
            _delta("badhiya, ", 2),
            _delta("yaar!", 3),
            {"type": "response.completed", "sequence_number": 4, "response": _response_json()},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = [e async for e in _provider(handler).stream(REQUEST)]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Sab ", "badhiya, ", "yaar!"]
    assert isinstance(events[-1], LLMResponse)
    assert events[-1].text == "Sab badhiya, yaar!"


async def test_stream_failure_event_raises() -> None:
    failed = _response_json(status="failed") | {"error": {"code": "server_error", "message": "x"}}
    body = _sse(
        [_delta("Hi", 1), {"type": "response.failed", "sequence_number": 2, "response": failed}]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    with pytest.raises(AIUnavailableError):
        [e async for e in _provider(handler).stream(REQUEST)]


async def test_stream_without_final_event_raises() -> None:
    body = _sse([_delta("Hi", 1)])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    with pytest.raises(AIUnavailableError):
        [e async for e in _provider(handler).stream(REQUEST)]


# --- structured output ----------------------------------------------------------

from app.ai.llm import JsonSchema  # noqa: E402

SCHEMA = JsonSchema("ops", {"type": "object", "properties": {}, "additionalProperties": False})


async def test_generate_json_sends_strict_schema_and_parses() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_response_json(text='{"operations": []}'))

    override = LLMRequest(
        instructions="x", messages=[ChatMessage("user", "y")], model="cheap", max_output_tokens=500
    )
    result = await _provider(handler).generate_json(override, SCHEMA)
    assert result == {"operations": []}
    assert seen["text"]["format"] == {
        "type": "json_schema",
        "name": "ops",
        "schema": SCHEMA.schema,
        "strict": True,
    }
    assert seen["model"] == "cheap"
    assert seen["max_output_tokens"] == 500


@pytest.mark.parametrize("text", ["not json", "[1, 2]"])
async def test_generate_json_rejects_non_object(text: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response_json(text=text))

    with pytest.raises(AIUnavailableError):
        await _provider(handler).generate_json(REQUEST, SCHEMA)


# --- tool calling ---------------------------------------------------------------

from app.ai.llm import ToolResultItem, ToolRound, ToolSpec  # noqa: E402

SPEC = ToolSpec(
    "create_reminder",
    "Save a reminder",
    {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)


def _call_response() -> dict:
    data = _response_json()
    data["output"] = [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "create_reminder",
            "arguments": '{"title":"Study"}',
            "status": "completed",
        }
    ]
    return data


async def test_tools_are_sent_and_calls_parsed() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_call_response())

    result = await _provider(handler).generate(
        LLMRequest(instructions="x", messages=[ChatMessage("user", "remind me")], tools=[SPEC])
    )
    assert seen["tools"] == [
        {
            "type": "function",
            "name": "create_reminder",
            "description": "Save a reminder",
            "parameters": SPEC.parameters,
            "strict": True,
        }
    ]
    assert seen["include"] == ["reasoning.encrypted_content"]
    assert "tool_choice" not in seen
    assert result.text == ""
    assert [(c.call_id, c.name, c.arguments) for c in result.tool_calls] == [
        ("call_1", "create_reminder", '{"title":"Study"}')
    ]
    assert result.provider_items[0]["type"] == "function_call"


async def test_tool_rounds_are_replayed_and_tool_choice_none() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_response_json())

    item = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "create_reminder",
        "arguments": "{}",
    }
    await _provider(handler).generate(
        LLMRequest(
            instructions="x",
            messages=[ChatMessage("user", "remind me")],
            tools=[SPEC],
            tool_rounds=[ToolRound((item,), (ToolResultItem("call_1", '{"ok":true}'),))],
            tool_choice="none",
        )
    )
    assert seen["input"][1:] == [
        item,
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
    ]
    assert seen["tool_choice"] == "none"
