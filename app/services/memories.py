"""User-facing memory management: view, add, edit, delete and search."""

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_event
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError
from app.memory.embeddings import EmbeddingProvider
from app.models.enums import MemoryCategory, MemorySource
from app.models.memory import Memory
from app.models.user import User
from app.repositories.memories import MemoryRepository
from app.schemas.memories import MemoryCreateRequest, MemoryUpdateRequest

_SEARCH_MAX_DISTANCE = 1.0  # explicit searches show weaker matches than automatic recall


class MemoryService:
    def __init__(
        self, session: AsyncSession, embeddings: EmbeddingProvider, settings: Settings
    ) -> None:
        self._session = session
        self._embeddings = embeddings
        self._settings = settings
        self._memories = MemoryRepository(session)

    async def list_for_user(
        self, user: User, *, category: MemoryCategory | None, limit: int, offset: int
    ) -> tuple[Sequence[Memory], bool]:
        rows = await self._memories.list_for_user(
            user.id, category=category, limit=limit + 1, offset=offset
        )
        return rows[:limit], len(rows) > limit

    async def get(self, user: User, memory_id: uuid.UUID) -> Memory:
        memory = await self._memories.get_for_user(memory_id, user.id)
        if memory is None:
            raise NotFoundError("Memory not found.")
        return memory

    async def create(self, user: User, data: MemoryCreateRequest) -> Memory:
        if await self._memories.count_for_user(user.id) >= self._settings.memory_max_per_user:
            raise ConflictError("Memory limit reached. Delete some memories first.")
        [vector] = await self._embeddings.embed([data.content])
        memory = await self._memories.add(
            Memory(
                user_id=user.id,
                content=data.content,
                category=data.category,
                importance=data.importance,
                source=MemorySource.USER,
                embedding=vector,
            )
        )
        await self._session.commit()
        return memory

    async def update(self, user: User, memory_id: uuid.UUID, data: MemoryUpdateRequest) -> Memory:
        memory = await self.get(user, memory_id)
        changes = data.model_dump(exclude_unset=True)
        if "content" in changes and changes["content"] != memory.content:
            [memory.embedding] = await self._embeddings.embed([changes["content"]])
        for field, value in changes.items():
            setattr(memory, field, value)
        memory.source = MemorySource.USER  # the user has now curated it
        await self._session.commit()
        await self._session.refresh(memory)
        return memory

    async def delete(self, user: User, memory_id: uuid.UUID) -> None:
        memory = await self.get(user, memory_id)
        await self._memories.delete(memory)
        await self._session.commit()
        audit_event("memory.deleted", user_id=str(user.id), memory_id=str(memory_id))

    async def delete_all(self, user: User) -> int:
        deleted = await self._memories.delete_all_for_user(user.id)
        await self._session.commit()
        audit_event("memory.deleted_all", user_id=str(user.id), count=deleted)
        return deleted

    async def search(self, user: User, query: str, limit: int) -> list[tuple[Memory, float]]:
        [vector] = await self._embeddings.embed([query])
        return await self._memories.search(
            user.id, vector, limit=limit, max_distance=_SEARCH_MAX_DISTANCE
        )
