import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ReminderStatus, TaskStatus, ToolExecutionStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class Reminder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_user_id_scheduled_at", "user_id", "scheduled_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ReminderStatus] = mapped_column(
        str_enum(ReminderStatus), default=ReminderStatus.PENDING, nullable=False
    )


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_id_status", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus), default=TaskStatus.TODO, nullable=False
    )
    due_date: Mapped[date | None] = mapped_column(Date)


class ToolExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit record of every tool call, including rejected ones and pending confirmations."""

    __tablename__ = "tool_executions"
    __table_args__ = (Index("ix_tool_executions_user_id_created_at", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Validated arguments (or, for INVALID calls, the raw arguments as received).
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        str_enum(ToolExecutionStatus), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    # For confirmations: what the user is being asked to approve, and until when.
    confirmation_summary: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
