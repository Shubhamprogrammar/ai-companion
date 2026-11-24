"""Memory woven into chat: recall before the reply, extraction/summaries after it."""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.background import InlineTaskRunner
from app.core.config import Settings
from app.core.redis import RedisClient
from app.models.enums import MemoryCategory
from app.models.memory import Memory
from tests.conftest import _client_for, build_app, register_and_login
from tests.fakes import FakeEmbeddings, FakeLLM

API = "/api/v1"


def _op(content: str, **kw) -> dict:
    return {
        "action": "add",
        "memory_ref": None,
        "content": content,
        "category": kw.get("category", "personal"),
        "importance": kw.get("importance", 4),
    }


async def test_remembered_fact_is_recalled_in_a_new_conversation(
    db_client: httpx.AsyncClient, fake_llm: FakeLLM, task_runner: InlineTaskRunner
) -> None:
    headers = await register_and_login(db_client)
    fake_llm._json_replies.append(  # noqa: SLF001 - queue the extraction result
        {"operations": [_op("User's sister Priya's birthday is 5 June", importance=5)]}
    )
    first = await db_client.post(
        f"{API}/chat",
        json={"message": "Yaad rakhna, meri sister Priya ka birthday 5 June ko hai"},
        headers=headers,
    )
    assert first.status_code == 200
    assert task_runner.pending  # extraction was scheduled, not run inline with the reply
    await task_runner.run_pending()

    memories = (await db_client.get(f"{API}/memories", headers=headers)).json()["items"]
    assert [m["content"] for m in memories] == ["User's sister Priya's birthday is 5 June"]
    assert memories[0]["source"] == "conversation"

    # A brand-new conversation: the memory is recalled into the companion's prompt.
    await db_client.post(
        f"{API}/chat", json={"message": "When is Priya's birthday again?"}, headers=headers
    )
    instructions = fake_llm.requests[-1].instructions
    assert "<user_memories>" in instructions
    assert "User's sister Priya's birthday is 5 June" in instructions


async def test_memory_disabled_means_no_recall_and_no_extraction(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm, embeddings, runner = FakeLLM(), FakeEmbeddings(), InlineTaskRunner()
    app = build_app(
        infra_settings, db_session, redis_client, llm, embeddings=embeddings, runner=runner
    )
    async for client in _client_for(app):
        headers = await register_and_login(client)
        await client.post(
            f"{API}/memories",
            json={"content": "User's name is Rohan", "importance": 5},
            headers=headers,
        )
        await client.patch(
            f"{API}/users/me/preferences", json={"memory_enabled": False}, headers=headers
        )
        embeddings.calls.clear()
        await client.post(
            f"{API}/chat", json={"message": "Remember that I love cricket a lot"}, headers=headers
        )
        await runner.run_pending()
    instructions = llm.requests[-1].instructions
    assert "turned long-term memory off" in instructions
    assert "Rohan" not in instructions
    assert embeddings.calls == []  # not even embedded
    assert llm.json_requests == []  # no extraction


async def test_global_kill_switch_disables_memory(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    llm = FakeLLM()
    settings = infra_settings.model_copy(update={"memory_enabled": False})
    async for client in _client_for(build_app(settings, db_session, redis_client, llm)):
        headers = await register_and_login(client)
        await client.post(f"{API}/chat", json={"message": "hello there friend"}, headers=headers)
    assert "turned long-term memory off" in llm.requests[-1].instructions


async def test_chat_still_works_when_embeddings_are_down(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    runner = InlineTaskRunner()
    app = build_app(
        infra_settings,
        db_session,
        redis_client,
        embeddings=FakeEmbeddings(fail=True),
        runner=runner,
    )
    async for client in _client_for(app):
        headers = await register_and_login(client)
        response = await client.post(
            f"{API}/chat", json={"message": "I'm preparing for my GATE exam"}, headers=headers
        )
        await runner.run_pending()  # extraction fails quietly in the background
    assert response.status_code == 200


async def test_long_conversation_gets_summarised_and_summary_is_used(
    infra_settings: Settings, db_session: AsyncSession, redis_client: RedisClient
) -> None:
    settings = infra_settings.model_copy(
        update={
            "chat_context_max_messages": 4,
            "summary_trigger_messages": 2,
            "chat_rate_limit_per_minute": 100,
        }
    )
    llm, runner = (
        FakeLLM(summary="User is planning a move into AI engineering."),
        InlineTaskRunner(),
    )
    async for client in _client_for(
        build_app(settings, db_session, redis_client, llm, runner=runner)
    ):
        headers = await register_and_login(client)
        cid = None
        for i in range(4):  # 8 messages; 4 fall outside the 4-message window
            body = {
                "message": f"Turn number {i} about my career",
                **({"conversation_id": cid} if cid else {}),
            }
            cid = (await client.post(f"{API}/chat", json=body, headers=headers)).json()[
                "conversation"
            ]["id"]
            await runner.run_pending()
        conversation = (await client.get(f"{API}/conversations/{cid}", headers=headers)).json()
        assert conversation["summary"] == "User is planning a move into AI engineering."

        await client.post(
            f"{API}/chat", json={"conversation_id": cid, "message": "And now?"}, headers=headers
        )
        assert "<conversation_summary>" in llm.requests[-1].instructions

        # Deleting the conversation removes its summary memory too.
        await client.delete(f"{API}/conversations/{cid}", headers=headers)
    remaining = (
        await db_session.scalars(
            select(Memory).where(Memory.category == MemoryCategory.CONVERSATION_SUMMARY)
        )
    ).all()
    assert remaining == []


async def test_extracted_facts_survive_conversation_deletion(
    db_client: httpx.AsyncClient, fake_llm: FakeLLM, task_runner: InlineTaskRunner
) -> None:
    headers = await register_and_login(db_client)
    fake_llm._json_replies.append({"operations": [_op("User works as a backend developer")]})  # noqa: SLF001
    cid = (
        await db_client.post(
            f"{API}/chat", json={"message": "I work as a backend developer"}, headers=headers
        )
    ).json()["conversation"]["id"]
    await task_runner.run_pending()
    await db_client.delete(f"{API}/conversations/{cid}", headers=headers)
    [memory] = (await db_client.get(f"{API}/memories", headers=headers)).json()["items"]
    assert memory["content"] == "User works as a backend developer"
    assert memory["source_conversation_id"] is None
