"""Semantic recall: which long-term memories to put in front of the model this turn."""

import logging
import uuid
from dataclasses import dataclass

from app.core.exceptions import AIUnavailableError
from app.memory.embeddings import EmbeddingProvider
from app.models.enums import MemoryCategory
from app.models.memory import Memory
from app.repositories.memories import MemoryRepository

logger = logging.getLogger(__name__)

CORE_MIN_IMPORTANCE = 4


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    id: uuid.UUID
    content: str
    category: MemoryCategory
    distance: float | None  # None for core memories included regardless of similarity


class MemoryRetriever:
    def __init__(
        self,
        repository: MemoryRepository,
        embeddings: EmbeddingProvider,
        *,
        recall_limit: int,
        core_limit: int,
        max_distance: float,
    ) -> None:
        self._repo = repository
        self._embeddings = embeddings
        self._recall_limit = recall_limit
        self._core_limit = core_limit
        self._max_distance = max_distance

    async def recall(self, user_id: uuid.UUID, query: str) -> list[RecalledMemory]:
        """Similar memories plus the user's core facts, de-duplicated, closest first.

        Never raises for provider problems: the companion still replies, just
        without long-term memory, and the failure is logged.
        """
        similar: list[tuple[Memory, float]] = []
        if self._recall_limit:
            try:
                [vector] = await self._embeddings.embed([query])
                similar = await self._repo.search(
                    user_id, vector, limit=self._recall_limit, max_distance=self._max_distance
                )
            except AIUnavailableError:
                logger.warning("memory recall skipped: embeddings unavailable")
        core = (
            await self._repo.core(
                user_id, limit=self._core_limit, min_importance=CORE_MIN_IMPORTANCE
            )
            if self._core_limit
            else []
        )

        recalled: dict[uuid.UUID, RecalledMemory] = {}
        for memory, distance in similar:
            recalled[memory.id] = RecalledMemory(
                memory.id, memory.content, memory.category, distance
            )
        for memory in core:
            recalled.setdefault(
                memory.id, RecalledMemory(memory.id, memory.content, memory.category, None)
            )
        result = list(recalled.values())
        await self._repo.mark_used([m.id for m in result])
        return result
