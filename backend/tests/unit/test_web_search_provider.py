import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.tools.base import ToolError
from app.tools.web_search import OpenAIWebSearch, build_web_search
from tests.conftest import make_settings


def _client(handler) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="sk-test",
        base_url="http://openai.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _response(annotations: list, sources: list) -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "m",
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "q",
                    "sources": [{"type": "url", "url": u} for u in sources],
                },
            },
            {
                "type": "message",
                "id": "m1",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Clear, 22°C.", "annotations": annotations}
                ],
            },
        ],
    }


async def test_prefers_cited_sources_and_sends_search_tool() -> None:
    seen: dict = {}
    cite = {
        "type": "url_citation",
        "url": "https://a.example/x",
        "title": "A",
        "start_index": 0,
        "end_index": 5,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_response([cite], ["https://b.example/y"]))

    result = await OpenAIWebSearch(_client(handler), make_settings(openai_chat_model="m")).search(
        "pune weather", timezone="Asia/Kolkata"
    )
    assert seen["tools"] == [
        {"type": "web_search", "user_location": {"type": "approximate", "timezone": "Asia/Kolkata"}}
    ]
    assert seen["store"] is False
    assert result.answer == "Clear, 22°C."
    assert [(s.title, s.url) for s in result.sources] == [("A", "https://a.example/x")]


async def test_falls_back_to_consulted_sources() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response([], ["https://news.example/story"]))

    result = await OpenAIWebSearch(_client(handler), make_settings(openai_chat_model="m")).search(
        "q"
    )
    assert [(s.title, s.url) for s in result.sources] == [
        ("news.example", "https://news.example/story")
    ]


async def test_provider_error_is_a_tool_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "x", "type": "x"}})

    with pytest.raises(ToolError, match="isn't working"):
        await OpenAIWebSearch(_client(handler), make_settings(openai_chat_model="m")).search("q")


def test_disabled_by_default() -> None:
    assert build_web_search(make_settings(), _client(lambda r: httpx.Response(200))) is None
    assert build_web_search(make_settings(web_search_provider="openai"), None) is None
