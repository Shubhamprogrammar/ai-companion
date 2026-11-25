"""Memory workflow around a conversational turn.

Before the reply (request path, latency-sensitive):
    recall: embed the user's message → similar + core memories → prompt
After the reply (background, never delays the user):
    extract: propose add/update operations → validate → de-duplicate → embed → save
    summarise: once enough turns scroll out of the context window, update the rolling
               conversation summary (and a searchable summary memory, if memory is on)
"""

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import LLMProvider
from app.core.background import TaskRunner
from app.core.config import Settings
from app.core.exceptions import AppError
from app.memory.embeddings import EmbeddingProvider
from app.memory.extraction import MAX_MEMORY_CHARS, MemoryExtractor, worth_extracting
from app.memory.retrieval import MemoryRetriever, RecalledMemory
from app.memory.summarizer import ConversationSummarizer
from app.models.conversation import Conversation
from app.models.enums import MemoryCategory, MemorySource
from app.models.memory import Memory
from app.models.user import UserPreferences
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.memories import MemoryRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
_UPDATE_CANDIDATES = 8
_UPDATE_CANDIDATE_DISTANCE = 0.8


def memory_enabled_for(settings: Settings, preferences: UserPreferences) -> bool:
    return settings.memory_enabled and preferences.memory_enabled


class MemoryPipeline:
    """Background half. Owns its DB session (the request's session is gone by then)."""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm: LLMProvider,
        embeddings: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._embeddings = embeddings
        self._settings = settings
        model = settings.openai_memory_model
        self._extractor = MemoryExtractor(llm, model)
        self._summarizer = ConversationSummarizer(llm, model)

    async def after_turn(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_text: str,
        assistant_text: str,
        memory_enabled: bool,
    ) -> None:
        async with self._session_factory() as session:
            if memory_enabled and worth_extracting(user_text):
                try:
                    await self.extract(session, user_id, conversation_id, user_text, assistant_text)
                except AppError as exc:
                    await session.rollback()
                    logger.warning("memory extraction failed", extra={"fields": {"code": exc.code}})
            try:
                await self.summarize(session, user_id, conversation_id, memory_enabled)
            except AppError as exc:
                await session.rollback()
                logger.warning("conversation summary failed", extra={"fields": {"code": exc.code}})

    # --- extraction ---------------------------------------------------------------

    async def extract(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_text: str,
        assistant_text: str,
    ) -> None:
        repo = MemoryRepository(session)
        [query_vector] = await self._embeddings.embed([user_text])
        candidates = await repo.search(
            user_id,
            query_vector,
            limit=_UPDATE_CANDIDATES,
            max_distance=_UPDATE_CANDIDATE_DISTANCE,
            exclude_categories=[MemoryCategory.CONVERSATION_SUMMARY],
        )
        operations = await self._extractor.extract(
            user_text, assistant_text, [m for m, _ in candidates]
        )
        if not operations:
            return

        vectors = await self._embeddings.embed([op.content for op in operations])
        room = self._settings.memory_max_per_user - await repo.count_for_user(user_id)
        added = updated = 0
        for op, vector in zip(operations, vectors, strict=True):
            target: Memory | None = None
            if op.action == "update" and op.memory_id is not None:
                target = await repo.get_for_user(op.memory_id, user_id)  # ownership re-check
            if target is None:
                # Near-identical to something already stored? Refresh it instead of duplicating.
                nearest = await repo.search(
                    user_id,
                    vector,
                    limit=1,
                    max_distance=self._settings.memory_dedup_distance,
                    exclude_categories=[MemoryCategory.CONVERSATION_SUMMARY],
                )
                target = nearest[0][0] if nearest else None

            if target is not None:
                target.content = op.content
                target.category = op.category
                target.importance = max(target.importance, op.importance)
                target.embedding = vector
                updated += 1
            elif room > 0:
                await repo.add(
                    Memory(
                        user_id=user_id,
                        content=op.content,
                        category=op.category,
                        importance=op.importance,
                        source=MemorySource.CONVERSATION,
                        source_conversation_id=conversation_id,
                        embedding=vector,
                    )
                )
                room -= 1
                added += 1
            else:
                logger.warning("memory limit reached; new memory not saved")
        await session.commit()
        logger.info(
            "memory extraction completed",
            extra={"fields": {"proposed": len(operations), "added": added, "updated": updated}},
        )

    # --- summarisation ------------------------------------------------------------

    async def summarize(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        memory_enabled: bool,
    ) -> bool:
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        total = await messages.count(conversation_id)
        outside_window = total - self._settings.chat_context_max_messages

        # Cheap unlocked check first: most turns need no summary.
        conversation = await conversations.get_for_user(conversation_id, user_id)
        if conversation is None or not self._needs_summary(conversation, outside_window):
            return False
        # Lock the row and re-check, so concurrent jobs can't summarise the same span twice.
        conversation = await conversations.get_for_user(conversation_id, user_id, for_update=True)
        if conversation is None or not self._needs_summary(conversation, outside_window):
            await session.commit()  # release the lock
            return False
        pending = outside_window - conversation.summarized_message_count

        batch = await messages.range(
            conversation_id, offset=conversation.summarized_message_count, limit=pending
        )
        summary = await self._summarizer.summarize(conversation.summary, batch)
        if not summary:
            await session.commit()  # release the lock
            return False
        conversation.summary = summary
        conversation.summarized_message_count = outside_window

        if memory_enabled:
            await self._upsert_summary_memory(
                session, user_id, conversation.id, conversation.title, summary
            )
        await session.commit()
        logger.info("conversation summary updated", extra={"fields": {"messages": pending}})
        return True

    def _needs_summary(self, conversation: Conversation, outside_window: int) -> bool:
        pending = outside_window - conversation.summarized_message_count
        return pending >= self._settings.summary_trigger_messages

    async def _upsert_summary_memory(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        title: str | None,
        summary: str,
    ) -> None:
        repo = MemoryRepository(session)
        label = f'Conversation "{title}"' if title else "A conversation"
        content = f"{label} ({datetime.now(UTC):%d %b %Y}): {summary}"[:MAX_MEMORY_CHARS]
        [vector] = await self._embeddings.embed([content])
        existing = await repo.summary_for_conversation(user_id, conversation_id)
        if existing:
            existing.content, existing.embedding = content, vector
            return
        await repo.add(
            Memory(
                user_id=user_id,
                content=content,
                category=MemoryCategory.CONVERSATION_SUMMARY,
                importance=2,
                source=MemorySource.SUMMARY,
                source_conversation_id=conversation_id,
                embedding=vector,
            )
        )


class MemoryCoordinator:
    """Request-scoped entry point used by ChatService."""

    def __init__(
        self,
        session: AsyncSession,
        embeddings: EmbeddingProvider,
        pipeline: MemoryPipeline,
        runner: TaskRunner,
        settings: Settings,
    ) -> None:
        self._retriever = MemoryRetriever(
            MemoryRepository(session),
            embeddings,
            recall_limit=settings.memory_recall_limit,
            core_limit=settings.memory_core_limit,
            max_distance=settings.memory_max_distance,
        )
        self._pipeline = pipeline
        self._runner = runner
        self._settings = settings

    def enabled_for(self, preferences: UserPreferences) -> bool:
        return memory_enabled_for(self._settings, preferences)

    async def recall(self, user_id: uuid.UUID, text: str) -> list[RecalledMemory]:
        return await self._retriever.recall(user_id, text)

    def schedule_after_turn(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        user_text: str,
        assistant_text: str,
        memory_enabled: bool,
    ) -> None:
        async def job() -> None:
            await self._pipeline.after_turn(
                user_id, conversation_id, user_text, assistant_text, memory_enabled
            )

        self._runner.submit(f"memory:{conversation_id}", job)
