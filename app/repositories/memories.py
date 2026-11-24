import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MemoryCategory
from app.models.memory import Memory


class MemoryRepository:
    """All queries are scoped by ``user_id``; there is no unscoped access path."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, memory: Memory) -> Memory:
        self._session.add(memory)
        await self._session.flush()
        await self._session.refresh(memory)
        return memory

    async def get_for_user(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> Memory | None:
        return await self._session.scalar(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        category: MemoryCategory | None,
        limit: int,
        offset: int,
    ) -> Sequence[Memory]:
        query = select(Memory).where(Memory.user_id == user_id)
        if category is not None:
            query = query.where(Memory.category == category)
        query = query.order_by(Memory.updated_at.desc(), Memory.id).limit(limit).offset(offset)
        return (await self._session.scalars(query)).all()

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        return (
            await self._session.scalar(
                select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
            )
            or 0
        )

    async def search(
        self,
        user_id: uuid.UUID,
        embedding: Sequence[float],
        *,
        limit: int,
        max_distance: float,
        exclude_categories: Sequence[MemoryCategory] = (),
    ) -> list[tuple[Memory, float]]:
        """Nearest memories by cosine distance, closest first."""
        distance = Memory.embedding.cosine_distance(embedding).label("distance")
        query = (
            select(Memory, distance)
            .where(Memory.user_id == user_id, distance <= max_distance)
            .order_by(distance)
            .limit(limit)
        )
        if exclude_categories:
            query = query.where(Memory.category.not_in(exclude_categories))
        return [(row[0], float(row[1])) for row in (await self._session.execute(query)).all()]

    async def core(self, user_id: uuid.UUID, *, limit: int, min_importance: int) -> list[Memory]:
        """The user's most important facts (always worth having in context)."""
        rows = await self._session.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.importance >= min_importance,
                Memory.category != MemoryCategory.CONVERSATION_SUMMARY,
            )
            .order_by(Memory.importance.desc(), Memory.updated_at.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def mark_used(self, memory_ids: Sequence[uuid.UUID]) -> None:
        if memory_ids:
            await self._session.execute(
                update(Memory)
                .where(Memory.id.in_(memory_ids))
                .values(last_used_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )

    async def summary_for_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Memory | None:
        return await self._session.scalar(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.source_conversation_id == conversation_id,
                Memory.category == MemoryCategory.CONVERSATION_SUMMARY,
            )
        )

    async def delete(self, memory: Memory) -> None:
        await self._session.delete(memory)
        await self._session.flush()

    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            delete(Memory).where(Memory.user_id == user_id).returning(Memory.id)
        )
        return len(result.all())

    async def delete_summaries_for_conversations(
        self, conversation_ids: Sequence[uuid.UUID]
    ) -> None:
        if conversation_ids:
            await self._session.execute(
                delete(Memory).where(
                    Memory.source_conversation_id.in_(conversation_ids),
                    Memory.category == MemoryCategory.CONVERSATION_SUMMARY,
                )
            )
