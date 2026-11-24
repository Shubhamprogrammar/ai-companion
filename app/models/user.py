import uuid

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import Language, PersonalityStyle
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    # Always stored lower-cased (normalised in the schema layer); unique index below.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # lazy="raise": async code must load relations explicitly (no hidden IO).
    profile: Mapped["UserProfile"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="raise"
    )
    preferences: Mapped["UserPreferences"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="raise"
    )


class UserProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(100))
    preferred_language: Mapped[Language] = mapped_column(
        str_enum(Language), default=Language.AUTO, nullable=False
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    user: Mapped[User] = relationship(back_populates="profile", lazy="raise")


class UserPreferences(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    personality: Mapped[PersonalityStyle] = mapped_column(
        str_enum(PersonalityStyle), default=PersonalityStyle.WARM_FRIEND, nullable=False
    )
    # None => use the server default voice (OPENAI_TTS_VOICE).
    voice: Mapped[str | None] = mapped_column(String(32))
    speech_speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # None => keep conversations until the user deletes them.
    conversation_retention_days: Mapped[int | None] = mapped_column(Integer)

    user: Mapped[User] = relationship(back_populates="preferences", lazy="raise")
