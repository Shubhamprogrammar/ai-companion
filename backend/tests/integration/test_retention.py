from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.user import User, UserPreferences, UserProfile
from app.services.conversations import ConversationService


async def _user(session: AsyncSession, email: str, retention_days: int | None) -> User:
    user = User(email=email, password_hash="x")
    user.profile = UserProfile()
    user.preferences = UserPreferences(conversation_retention_days=retention_days)
    session.add(user)
    await session.flush()
    return user


async def _conversation(session: AsyncSession, user: User, age_days: int) -> Conversation:
    conversation = Conversation(user_id=user.id, title=f"{age_days}d")
    session.add(conversation)
    await session.flush()
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(updated_at=datetime.now(UTC) - timedelta(days=age_days))
    )
    return conversation


async def test_purge_respects_each_users_retention(db_session: AsyncSession) -> None:
    short = await _user(db_session, "short@example.com", retention_days=7)
    forever = await _user(db_session, "forever@example.com", retention_days=None)
    old_short = await _conversation(db_session, short, age_days=10)
    fresh_short = await _conversation(db_session, short, age_days=1)
    old_forever = await _conversation(db_session, forever, age_days=400)

    deleted = await ConversationService(db_session).purge_expired()

    remaining = set(await db_session.scalars(select(Conversation.id)))
    assert deleted == 1
    assert old_short.id not in remaining
    assert {fresh_short.id, old_forever.id} <= remaining
