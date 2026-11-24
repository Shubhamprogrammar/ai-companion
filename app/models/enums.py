"""Domain enums shared by ORM models and API schemas."""

from enum import StrEnum


class Language(StrEnum):
    AUTO = "auto"  # follow whatever language the user speaks
    ENGLISH = "en"
    HINDI = "hi"
    HINGLISH = "hinglish"


class PersonalityStyle(StrEnum):
    """Companion personality presets. The prompt for each is defined in Phase 3."""

    WARM_FRIEND = "warm_friend"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageType(StrEnum):
    TEXT = "text"
    VOICE = "voice"  # transcribed from / synthesised to audio (Phase 4)


class MemoryCategory(StrEnum):
    PREFERENCE = "preference"
    PERSONAL = "personal"  # facts the user shared about themselves / their life
    GOAL = "goal"
    LEARNING = "learning"
    PROJECT = "project"
    DECISION = "decision"
    ROUTINE = "routine"  # recurring habits or requirements
    CONVERSATION_SUMMARY = "conversation_summary"
    OTHER = "other"


class MemorySource(StrEnum):
    USER = "user"  # created/edited by the user via the API
    CONVERSATION = "conversation"  # extracted from a conversation
    SUMMARY = "summary"  # a conversation summary


class ReminderStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class ToolExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # the tool ran (or tried to) and reported an error
    INVALID = "invalid"  # rejected by backend validation before running
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCELLED = "cancelled"  # the user declined
    EXPIRED = "expired"  # confirmation not given in time
