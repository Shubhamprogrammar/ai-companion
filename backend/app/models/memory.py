import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import MemoryCategory, MemorySource
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum

# Fixed by the migration; embeddings are requested at this size (text-embedding-3-* support it).
EMBEDDING_DIMENSIONS = 1536


class Memory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A long-term fact about the user, with an embedding for semantic recall.

    Similarity search is exact (sequential within one user's rows, via the user_id
    index). That is accurate and fast at per-user scale; an HNSW index can be added
    later, but HNSW with a user filter is approximate and can under-return.
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 5", name="importance_range"),
        Index("ix_memories_user_id_updated_at", "user_id", "updated_at"),
        Index("ix_memories_source_conversation_id", "source_conversation_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[MemoryCategory] = mapped_column(str_enum(MemoryCategory), nullable=False)
    importance: Mapped[int] = mapped_column(SmallInteger, default=3, nullable=False)
    source: Mapped[MemorySource] = mapped_column(str_enum(MemorySource), nullable=False)
    # Kept when the conversation is deleted (facts outlive chats) except summaries,
    # which the service deletes together with their conversation.
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
