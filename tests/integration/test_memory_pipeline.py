"""Memory engine against real PostgreSQL + pgvector (fake embeddings/LLM)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.memory.retrieval import MemoryRetriever
from app.models.conversation import Conversation, Message
from app.models.enums import MemoryCategory, MemorySource, MessageRole
from app.models.memory import Memory
from app.models.user import User, UserPreferences, UserProfile
from app.repositories.memories import MemoryRepository
from app.services.memory_pipeline import MemoryPipeline
from tests.conftest import session_factory_for
from tests.fakes import FakeEmbeddings, FakeLLM

EMB = FakeEmbeddings()


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    user.profile, user.preferences = UserProfile(), UserPreferences()
    session.add(user)
    await session.commit()  # savepoint release: still rolled back after the test
    return user


async def _memory(session: AsyncSession, user: User, content: str, **kw) -> Memory:
    memory = await MemoryRepository(session).add(
        Memory(
            user_id=user.id,
            content=content,
            category=kw.get("category", MemoryCategory.PERSONAL),
            importance=kw.get("importance", 3),
            source=MemorySource.USER,
            embedding=EMB.vector(content),
        )
    )
    await session.commit()
    return memory


def _op(content: str, action: str = "add", ref: str | None = None, **kw) -> dict:
    return {
        "action": action,
        "memory_ref": ref,
        "content": content,
        "category": kw.get("category", "personal"),
        "importance": kw.get("importance", 3),
    }


async def test_search_is_ranked_and_scoped_to_user(db_session: AsyncSession) -> None:
    me = await _user(db_session, "me@example.com")
    other = await _user(db_session, "other@example.com")
    await _memory(db_session, me, "User loves masala chai in the morning")
    await _memory(db_session, me, "User is preparing for a Python interview")
    await _memory(db_session, other, "User loves masala chai and samosa")

    results = await MemoryRepository(db_session).search(
        me.id, EMB.vector("what chai does the user love"), limit=5, max_distance=2
    )
    assert results[0][0].content == "User loves masala chai in the morning"
    assert all(m.user_id == me.id for m, _ in results)
    assert [d for _, d in results] == sorted(d for _, d in results)


async def test_retriever_combines_similar_and_core_and_marks_used(db_session: AsyncSession) -> None:
    me = await _user(db_session, "me@example.com")
    name = await _memory(db_session, me, "User's name is Rohan", importance=5)
    chai = await _memory(db_session, me, "User loves masala chai")
    await _memory(db_session, me, "User once visited Goa", importance=1)

    retriever = MemoryRetriever(
        MemoryRepository(db_session), EMB, recall_limit=1, core_limit=3, max_distance=0.9
    )
    recalled = await retriever.recall(me.id, "masala chai recommendation")
    ids = [m.id for m in recalled]
    assert ids[0] == chai.id  # semantic match first
    assert name.id in ids  # core fact always included
    assert len(ids) == len(set(ids))
    await db_session.refresh(chai)
    assert chai.last_used_at is not None


async def test_retriever_survives_embedding_outage(db_session: AsyncSession) -> None:
    me = await _user(db_session, "me@example.com")
    await _memory(db_session, me, "User's name is Rohan", importance=5)
    retriever = MemoryRetriever(
        MemoryRepository(db_session),
        FakeEmbeddings(fail=True),
        recall_limit=5,
        core_limit=3,
        max_distance=0.9,
    )
    recalled = await retriever.recall(me.id, "hello")
    assert [m.content for m in recalled] == ["User's name is Rohan"]  # core still works


def _pipeline(session: AsyncSession, settings: Settings, llm: FakeLLM) -> MemoryPipeline:
    return MemoryPipeline(session_factory_for(session), llm, EMB, settings)


async def _conversation(session: AsyncSession, user: User, messages: int = 0) -> Conversation:
    conversation = Conversation(user_id=user.id, title="Career chat")
    session.add(conversation)
    await session.flush()
    for i in range(messages):
        role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
        session.add(Message(conversation_id=conversation.id, role=role, content=f"message {i}"))
    await session.commit()
    return conversation


async def test_extraction_adds_new_memories(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    me = await _user(db_session, "me@example.com")
    conv = await _conversation(db_session, me)
    llm = FakeLLM(
        json_replies=[
            {"operations": [_op("User's sister Priya's birthday is 5 June", importance=5)]}
        ]
    )
    await _pipeline(db_session, infra_settings, llm).after_turn(
        me.id, conv.id, "Yaad rakhna, meri sister Priya ka birthday 5 June ko hai", "Done!", True
    )
    [memory] = (await db_session.scalars(select(Memory).where(Memory.user_id == me.id))).all()
    assert memory.content == "User's sister Priya's birthday is 5 June"
    assert memory.source is MemorySource.CONVERSATION
    assert memory.source_conversation_id == conv.id
    assert memory.importance == 5


async def test_near_duplicate_updates_instead_of_adding(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    me = await _user(db_session, "me@example.com")
    conv = await _conversation(db_session, me)
    existing = await _memory(db_session, me, "User loves masala chai", importance=2)
    llm = FakeLLM(json_replies=[{"operations": [_op("User loves masala chai", importance=4)]}])
    await _pipeline(db_session, infra_settings, llm).after_turn(
        me.id, conv.id, "Seriously, I love masala chai so much", "Haha same vibe!", True
    )
    memories = (await db_session.scalars(select(Memory).where(Memory.user_id == me.id))).all()
    assert [m.id for m in memories] == [existing.id]
    assert memories[0].importance == 4  # raised, never lowered


async def test_update_by_alias_changes_the_right_memory(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    me = await _user(db_session, "me@example.com")
    conv = await _conversation(db_session, me)
    city = await _memory(db_session, me, "User lives in Pune city")
    llm = FakeLLM(
        json_replies=[{"operations": [_op("User lives in Bengaluru city", "update", "m1")]}]
    )
    await _pipeline(db_session, infra_settings, llm).after_turn(
        me.id, conv.id, "Btw I moved from Pune city to Bengaluru city last month", "Nice!", True
    )
    await db_session.refresh(city)
    assert city.content == "User lives in Bengaluru city"
    assert "m1: [personal] User lives in Pune city" in llm.json_requests[0].messages[0].content


async def test_extraction_skipped_when_memory_disabled_or_trivial(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    me = await _user(db_session, "me@example.com")
    conv = await _conversation(db_session, me)
    llm = FakeLLM(json_replies=[{"operations": [_op("User likes cricket a lot")]}] * 2)
    pipeline = _pipeline(db_session, infra_settings, llm)
    await pipeline.after_turn(me.id, conv.id, "I really like watching cricket matches", "!", False)
    await pipeline.after_turn(me.id, conv.id, "ok", "!", True)
    assert llm.json_requests == []
    assert (await MemoryRepository(db_session).count_for_user(me.id)) == 0


async def test_memory_limit_blocks_new_adds(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    me = await _user(db_session, "me@example.com")
    conv = await _conversation(db_session, me)
    await _memory(db_session, me, "User's name is Rohan")
    settings = infra_settings.model_copy(update={"memory_max_per_user": 1})
    llm = FakeLLM(json_replies=[{"operations": [_op("User plays the guitar every weekend")]}])
    await _pipeline(db_session, settings, llm).after_turn(
        me.id, conv.id, "I play guitar every weekend with friends", "Wow!", True
    )
    assert (await MemoryRepository(db_session).count_for_user(me.id)) == 1


async def test_summary_created_once_enough_messages_scroll_out(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    settings = infra_settings.model_copy(
        update={"chat_context_max_messages": 4, "summary_trigger_messages": 4}
    )
    me = await _user(db_session, "me@example.com")
    llm = FakeLLM(summary="User is switching from backend to AI engineering.")
    pipeline = _pipeline(db_session, settings, llm)

    short = await _conversation(db_session, me, messages=6)  # only 2 outside window
    assert not await pipeline.summarize(db_session, me.id, short.id, memory_enabled=True)

    long = await _conversation(db_session, me, messages=10)  # 6 outside window
    assert await pipeline.summarize(db_session, me.id, long.id, memory_enabled=True)
    await db_session.refresh(long)
    assert long.summary == "User is switching from backend to AI engineering."
    assert long.summarized_message_count == 6
    transcript = llm.summary_requests[0].messages[0].content
    assert "message 0" in transcript and "message 5" in transcript and "message 6" not in transcript

    summary_memory = await MemoryRepository(db_session).summary_for_conversation(me.id, long.id)
    assert summary_memory.category is MemoryCategory.CONVERSATION_SUMMARY
    assert "Career chat" in summary_memory.content

    # Nothing new outside the window → no second summary.
    assert not await pipeline.summarize(db_session, me.id, long.id, memory_enabled=True)


async def test_summary_without_memory_creates_no_summary_memory(
    db_session: AsyncSession, infra_settings: Settings
) -> None:
    settings = infra_settings.model_copy(
        update={"chat_context_max_messages": 2, "summary_trigger_messages": 2}
    )
    me = await _user(db_session, "me@example.com")
    conv = await _conversation(db_session, me, messages=6)
    assert await _pipeline(db_session, settings, FakeLLM()).summarize(
        db_session, me.id, conv.id, memory_enabled=False
    )
    assert await MemoryRepository(db_session).summary_for_conversation(me.id, conv.id) is None
