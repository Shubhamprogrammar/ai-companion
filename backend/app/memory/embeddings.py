"""Text embeddings: provider interface and OpenAI implementation."""

import logging
import time
from collections.abc import Sequence
from typing import Protocol

import openai
from openai import AsyncOpenAI

from app.ai.client import elapsed_ms, log_openai_failure
from app.core.config import Settings
from app.core.exceptions import AIUnavailableError
from app.models.memory import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per input text, in order."""
        ...


class OpenAIEmbeddings:
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, client: AsyncOpenAI | None, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None or not self._settings.openai_embedding_model:
            logger.error("OpenAI embeddings not configured (OPENAI_EMBEDDING_MODEL)")
            raise AIUnavailableError()
        start = time.perf_counter()
        try:
            response = await self._client.embeddings.create(
                model=self._settings.openai_embedding_model,
                input=list(texts),
                dimensions=self.dimensions,
            )
        except openai.OpenAIError as exc:
            log_openai_failure("embedding", exc, start)
            raise AIUnavailableError() from exc
        vectors = [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            logger.error("embedding response has unexpected shape")
            raise AIUnavailableError()
        logger.info(
            "embedding request completed",
            extra={
                "fields": {
                    "provider": "openai",
                    "model": self._settings.openai_embedding_model,
                    "inputs": len(texts),
                    "duration_ms": elapsed_ms(start),
                }
            },
        )
        return vectors
