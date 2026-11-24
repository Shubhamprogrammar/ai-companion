"""OpenAI integration: the shared SDK client and the Responses-API LLM provider."""

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI
from openai.types.responses import Response

from app.ai.llm import (
    JsonSchema,
    LLMRequest,
    LLMResponse,
    LLMStreamEvent,
    LLMUsage,
    TextDelta,
    ToolCall,
)
from app.core.config import Settings
from app.core.exceptions import AIUnavailableError

logger = logging.getLogger(__name__)

# Errors worth surfacing as "try again later"; the SDK already retried these with backoff.
_TRANSIENT = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def log_openai_failure(operation: str, exc: openai.OpenAIError, start: float) -> bool:
    """Log a provider error without its message (which may echo input). Returns transient?"""
    transient = isinstance(exc, _TRANSIENT)
    logger.log(
        logging.WARNING if transient else logging.ERROR,
        f"{operation} request failed",
        extra={
            "fields": {
                "provider": "openai",
                "operation": operation,
                "error_type": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
                "transient": transient,
                "duration_ms": elapsed_ms(start),
            }
        },
    )
    return transient


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def build_openai_client(settings: Settings) -> AsyncOpenAI | None:
    """Single SDK client for the app (connection pooling). None when no API key is set.

    Retries: the SDK retries connection errors, 408/409/429 and 5xx with exponential
    backoff (``OPENAI_MAX_RETRIES``). Generation has no side effects, so this is safe.
    """
    if settings.openai_api_key is None:
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )


class OpenAIResponsesLLM:
    """``LLMProvider`` backed by the OpenAI Responses API."""

    def __init__(self, client: AsyncOpenAI | None, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _params(self, request: LLMRequest) -> dict[str, Any]:
        if self._client is None or not self._settings.openai_chat_model:
            logger.error("OpenAI chat is not configured (OPENAI_API_KEY / OPENAI_CHAT_MODEL)")
            raise AIUnavailableError()
        input_items: list[Any] = [{"role": m.role, "content": m.content} for m in request.messages]
        for round_ in request.tool_rounds:
            input_items.extend(round_.provider_items)
            input_items.extend(
                {"type": "function_call_output", "call_id": r.call_id, "output": r.output}
                for r in round_.results
            )
        params: dict[str, Any] = {
            "model": request.model or self._settings.openai_chat_model,
            "instructions": request.instructions,
            "input": input_items,
            "max_output_tokens": request.max_output_tokens
            or self._settings.openai_max_output_tokens,
            "store": self._settings.openai_store_responses,
        }
        if request.safety_identifier:
            params["safety_identifier"] = request.safety_identifier
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "strict": True,
                }
                for t in request.tools
            ]
            # With store=false, reasoning state must travel with the replayed items.
            params["include"] = ["reasoning.encrypted_content"]
            if request.tool_choice == "none":
                params["tool_choice"] = "none"
        if self._settings.openai_reasoning_effort:
            params["reasoning"] = {"effort": self._settings.openai_reasoning_effort}
        return params

    async def generate(self, request: LLMRequest) -> LLMResponse:
        params = self._params(request)
        start = time.perf_counter()
        try:
            response = await self._client.responses.create(**params)  # type: ignore[union-attr]
        except openai.OpenAIError as exc:
            raise self._translate(exc, start) from exc
        return self._finish(response, start)

    async def generate_json(self, request: LLMRequest, schema: JsonSchema) -> dict[str, Any]:
        params = self._params(request)
        params["text"] = {
            "format": {
                "type": "json_schema",
                "name": schema.name,
                "schema": schema.schema,
                "strict": True,
            }
        }
        start = time.perf_counter()
        try:
            response = await self._client.responses.create(**params)  # type: ignore[union-attr]
        except openai.OpenAIError as exc:
            raise self._translate(exc, start) from exc
        result = self._finish(response, start)
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            self._log_failure("invalid_json", start, schema.name)
            raise AIUnavailableError() from exc
        if not isinstance(data, dict):
            self._log_failure("json_not_object", start, schema.name)
            raise AIUnavailableError()
        return data

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamEvent]:
        params = self._params(request)
        start = time.perf_counter()
        final: Response | None = None
        try:
            stream = await self._client.responses.create(**params, stream=True)  # type: ignore[union-attr]
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield TextDelta(event.delta)
                elif event.type in ("response.completed", "response.incomplete"):
                    final = event.response
                elif event.type == "response.failed":
                    self._log_failure("response_failed", start, event.response.error)
                    raise AIUnavailableError()
                elif event.type == "error":
                    self._log_failure("stream_error", start, event.code)
                    raise AIUnavailableError()
        except openai.OpenAIError as exc:
            raise self._translate(exc, start) from exc
        if final is None:
            self._log_failure("stream_ended_without_response", start, None)
            raise AIUnavailableError()
        yield self._finish(final, start)

    # --- helpers ----------------------------------------------------------------

    def _finish(self, response: Response, start: float) -> LLMResponse:
        latency_ms = elapsed_ms(start)
        text = response.output_text.strip()
        truncated = response.status == "incomplete"
        usage = (
            LLMUsage(response.usage.input_tokens, response.usage.output_tokens)
            if response.usage
            else None
        )
        logger.info(
            "llm request completed",
            extra={
                "fields": {
                    "provider": "openai",
                    "model": response.model,
                    "status": response.status,
                    "duration_ms": latency_ms,
                    "input_tokens": usage.input_tokens if usage else None,
                    "output_tokens": usage.output_tokens if usage else None,
                }
            },
        )
        tool_calls = tuple(
            ToolCall(call_id=item.call_id, name=item.name, arguments=item.arguments)
            for item in response.output
            if item.type == "function_call"
        )
        if not text and not tool_calls:
            # e.g. the whole token budget went to reasoning; never return an empty reply.
            self._log_failure("empty_output", start, response.status)
            raise AIUnavailableError()
        return LLMResponse(
            text=text,
            model=response.model,
            usage=usage,
            truncated=truncated,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
            provider_items=tuple(item.model_dump(exclude_none=True) for item in response.output)
            if tool_calls
            else (),
        )

    def _translate(self, exc: openai.OpenAIError, start: float) -> AIUnavailableError:
        log_openai_failure("llm", exc, start)
        return AIUnavailableError()

    def _log_failure(self, reason: str, start: float, detail: object) -> None:
        logger.error(
            "llm request failed",
            extra={
                "fields": {
                    "provider": "openai",
                    "reason": reason,
                    "detail": str(detail)[:200] if detail is not None else None,
                    "duration_ms": elapsed_ms(start),
                }
            },
        )
