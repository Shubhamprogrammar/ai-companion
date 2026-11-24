import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.core.exceptions import AIUnavailableError
from app.memory.embeddings import OpenAIEmbeddings
from tests.conftest import make_settings


def _provider(handler) -> OpenAIEmbeddings:
    client = AsyncOpenAI(
        api_key="sk-test",
        base_url="http://openai.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return OpenAIEmbeddings(client, make_settings(openai_embedding_model="test-embed"))


def _payload(vectors: list[list[float]], order: list[int] | None = None) -> dict:
    order = order or list(range(len(vectors)))
    return {
        "object": "list",
        "model": "test-embed",
        "data": [{"object": "embedding", "index": i, "embedding": vectors[i]} for i in order],
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }


async def test_embed_requests_fixed_dimensions_and_keeps_order() -> None:
    seen: dict = {}
    a, b = [0.1] * 1536, [0.2] * 1536

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_payload([a, b], order=[1, 0]))  # out of order

    vectors = await _provider(handler).embed(["first", "second"])
    assert seen["model"] == "test-embed"
    assert seen["dimensions"] == 1536
    assert seen["input"] == ["first", "second"]
    assert vectors == [a, b]


async def test_wrong_dimensions_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload([[0.1] * 10]))

    with pytest.raises(AIUnavailableError):
        await _provider(handler).embed(["x"])


async def test_provider_error_and_empty_input() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down", "type": "x"}})

    provider = _provider(handler)
    with pytest.raises(AIUnavailableError):
        await provider.embed(["x"])
    assert await provider.embed([]) == []


async def test_not_configured() -> None:
    with pytest.raises(AIUnavailableError):
        await OpenAIEmbeddings(None, make_settings()).embed(["x"])
