import uuid
from datetime import UTC, datetime

from app.ai.agent import CompanionAgent, CompanionTurn
from app.models.conversation import Message
from app.models.enums import Language, MessageRole, PersonalityStyle
from app.models.user import UserPreferences, UserProfile
from tests.conftest import make_settings
from tests.fakes import FakeLLM


def _turn(
    user_id: uuid.UUID, text: str = "Yaar mujhe samajh nahi aa raha kya karu"
) -> CompanionTurn:
    return CompanionTurn(
        user_id=user_id,
        profile=UserProfile(
            display_name="Asha", preferred_language=Language.AUTO, timezone="Asia/Kolkata"
        ),
        preferences=UserPreferences(personality=PersonalityStyle.WARM_FRIEND),
        history=[
            Message(role=MessageRole.USER, content="Hi Mira"),
            Message(role=MessageRole.ASSISTANT, content="Hey! How's your day going?"),
        ],
        user_text=text,
    )


def test_prepare_builds_request_with_history_and_hints() -> None:
    agent = CompanionAgent(FakeLLM(), make_settings(companion_name="Mira"))
    prepared = agent.prepare(_turn(uuid.uuid4()), now=datetime(2026, 11, 24, 16, tzinfo=UTC))

    assert prepared.detected_language is Language.HINGLISH
    assert [m.content for m in prepared.request.messages] == [
        "Hi Mira",
        "Hey! How's your day going?",
        "Yaar mujhe samajh nahi aa raha kya karu",
    ]
    assert "You are Mira" in prepared.request.instructions
    assert "21:30" in prepared.request.instructions  # 16:00 UTC in Asia/Kolkata
    assert "remember things between separate conversations" in prepared.request.instructions


def test_safety_identifier_is_stable_and_not_the_raw_id() -> None:
    agent = CompanionAgent(FakeLLM(), make_settings())
    user_id = uuid.uuid4()
    first = agent.safety_identifier(user_id)
    assert first == agent.safety_identifier(user_id)
    assert first != agent.safety_identifier(uuid.uuid4())
    assert str(user_id) not in first and user_id.hex not in first


async def test_reply_delegates_to_llm() -> None:
    llm = FakeLLM(["Arre, chill. Let's sort it out."])
    agent = CompanionAgent(llm, make_settings())
    reply = await agent.reply(agent.prepare(_turn(uuid.uuid4())))
    assert reply.text == "Arre, chill. Let's sort it out."
    assert len(llm.requests) == 1


def test_capabilities_follow_memory_setting() -> None:
    agent = CompanionAgent(FakeLLM(), make_settings())
    on = agent.prepare(_with(_turn(uuid.uuid4()), True))
    assert "You can: remember important things the user shares" in on.request.instructions
    assert "User's name is Asha" in on.request.instructions

    off = agent.prepare(_with(_turn(uuid.uuid4()), False))
    assert (
        "You cannot (yet): remember things between separate conversations"
        in off.request.instructions
    )


def _with(turn: CompanionTurn, memory_enabled: bool) -> CompanionTurn:
    from dataclasses import replace

    return replace(
        turn,
        memory_enabled=memory_enabled,
        memories=["User's name is Asha"] if memory_enabled else [],
    )
