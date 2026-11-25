import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import DateTime, delete, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.models.enums import ConversationStatus
from app.models.user import UserPreferences


class ConversationRepository:
    """Every read is scoped by ``user_id``: there is no unscoped lookup by id."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, conversation: Conversation) -> Conversation:
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get_for_user(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Conversation | None:
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if for_update:
            # populate_existing: refresh an already-loaded instance with the locked row's values.
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self._session.scalar(query)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        status: ConversationStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[Conversation]:
        query = select(Conversation).where(Conversation.user_id == user_id)
        if status is not None:
            query = query.where(Conversation.status == status)
        query = query.order_by(Conversation.updated_at.desc(), Conversation.id).limit(limit)
        return (await self._session.scalars(query.offset(offset))).all()

    async def delete(self, conversation: Conversation) -> None:
        await self._session.delete(conversation)
        await self._session.flush()

    async def touch(self, conversation: Conversation) -> None:
        conversation.updated_at = func.clock_timestamp()
        await self._session.flush()
        await self._session.refresh(conversation)

    async def expired(self, now: datetime | None = None) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """``(conversation_id, user_id)`` of conversations idle past their owner's retention."""
        now = now or datetime.now(UTC)
        retention = (
            select(UserPreferences.user_id, UserPreferences.conversation_retention_days)
            .where(UserPreferences.conversation_retention_days.is_not(None))
            .subquery()
        )
        stmt = select(Conversation.id, Conversation.user_id).where(
            Conversation.user_id == retention.c.user_id,
            Conversation.updated_at
            < literal(now, DateTime(timezone=True))
            - func.make_interval(0, 0, 0, retention.c.conversation_retention_days),
        )
        return [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]

    async def delete_by_ids(self, conversation_ids: Sequence[uuid.UUID]) -> None:
        if conversation_ids:
            await self._session.execute(
                delete(Conversation).where(Conversation.id.in_(conversation_ids))
            )


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: Message) -> Message:
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def recent(self, conversation_id: uuid.UUID, limit: int) -> list[Message]:
        """The latest ``limit`` messages, returned oldest-first."""
        rows = await self._session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(rows.all()))

    async def count(self, conversation_id: uuid.UUID) -> int:
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )
            or 0
        )

    async def range(self, conversation_id: uuid.UUID, *, offset: int, limit: int) -> list[Message]:
        """Messages by position (oldest first): used to summarise older turns."""
        return await self.list_page(conversation_id, limit=limit, offset=offset)

    async def list_page(
        self, conversation_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[Message]:
        rows = await self._session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all())
