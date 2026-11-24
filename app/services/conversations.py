"""Conversation CRUD for the authenticated user. Every lookup is ownership-scoped."""

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.storage import AudioStore, DisabledAudioStore
from app.core.audit import audit_event
from app.core.exceptions import NotFoundError
from app.models.conversation import Conversation, Message
from app.models.enums import ConversationStatus
from app.models.user import User
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.memories import MemoryRepository
from app.schemas.conversations import ConversationCreateRequest, ConversationUpdateRequest


class ConversationService:
    def __init__(self, session: AsyncSession, audio_store: AudioStore | None = None) -> None:
        self._session = session
        self._audio_store = audio_store or DisabledAudioStore()
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)
        self._memories = MemoryRepository(session)

    async def create(self, user: User, data: ConversationCreateRequest) -> Conversation:
        conversation = await self._conversations.add(
            Conversation(user_id=user.id, title=data.title)
        )
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

    async def list_for_user(
        self, user: User, *, status: ConversationStatus | None, limit: int, offset: int
    ) -> tuple[Sequence[Conversation], bool]:
        rows = await self._conversations.list_for_user(
            user.id, status=status, limit=limit + 1, offset=offset
        )
        return rows[:limit], len(rows) > limit

    async def get(self, user: User, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self._conversations.get_for_user(conversation_id, user.id)
        if conversation is None:
            # Same response whether it doesn't exist or belongs to someone else.
            raise NotFoundError("Conversation not found.")
        return conversation

    async def update(
        self, user: User, conversation_id: uuid.UUID, data: ConversationUpdateRequest
    ) -> Conversation:
        conversation = await self.get(user, conversation_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(conversation, field, value)
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

    async def delete(self, user: User, conversation_id: uuid.UUID) -> None:
        conversation = await self.get(user, conversation_id)
        # Summary memories describe this conversation, so they go with it. (Facts extracted
        # from it are kept, since they're about the user. Delete them via /memories.)
        await self._memories.delete_summaries_for_conversations([conversation.id])
        await self._conversations.delete(conversation)
        await self._session.commit()
        await self._audio_store.delete_conversation(user.id, conversation_id)
        audit_event(
            "conversation.deleted", user_id=str(user.id), conversation_id=str(conversation_id)
        )

    async def list_messages(
        self, user: User, conversation_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Message], bool]:
        await self.get(user, conversation_id)
        rows = await self._messages.list_page(conversation_id, limit=limit + 1, offset=offset)
        return rows[:limit], len(rows) > limit

    async def purge_expired(self) -> int:
        """Enforce every user's ``conversation_retention_days``. Returns rows deleted."""
        deleted = await self._conversations.expired()
        ids = [conversation_id for conversation_id, _ in deleted]
        await self._memories.delete_summaries_for_conversations(ids)
        await self._conversations.delete_by_ids(ids)
        await self._session.commit()
        for conversation_id, user_id in deleted:
            await self._audio_store.delete_conversation(user_id, conversation_id)
        if deleted:
            audit_event("conversation.retention_purged", count=len(deleted))
        return len(deleted)
