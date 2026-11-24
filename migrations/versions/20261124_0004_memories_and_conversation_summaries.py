"""memories and conversation summaries

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "preference",
                "personal",
                "goal",
                "learning",
                "project",
                "decision",
                "routine",
                "conversation_summary",
                "other",
                name="memorycategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("importance", sa.SmallInteger(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "user", "conversation", "summary", name="memorysource", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("source_conversation_id", sa.UUID(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("importance BETWEEN 1 AND 5", name=op.f("ck_memories_importance_range")),
        sa.ForeignKeyConstraint(
            ["source_conversation_id"],
            ["conversations.id"],
            name=op.f("fk_memories_source_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memories_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        "ix_memories_source_conversation_id", "memories", ["source_conversation_id"], unique=False
    )
    op.create_index(
        "ix_memories_user_id_updated_at", "memories", ["user_id", "updated_at"], unique=False
    )
    op.add_column(
        "conversations",
        sa.Column("summarized_message_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("conversations", "summarized_message_count")
    op.drop_index("ix_memories_user_id_updated_at", table_name="memories")
    op.drop_index("ix_memories_source_conversation_id", table_name="memories")
    op.drop_table("memories")
