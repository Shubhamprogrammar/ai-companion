from app.ai.personality import PERSONALITIES, WARM_FRIEND, get_personality
from app.models.enums import PersonalityStyle


def test_every_personality_style_is_defined() -> None:
    assert set(PERSONALITIES) == set(PersonalityStyle)


def test_get_personality_returns_matching_style() -> None:
    assert get_personality(PersonalityStyle.WARM_FRIEND) is WARM_FRIEND


def test_warm_friend_encodes_core_conversation_rules() -> None:
    rules = " ".join(WARM_FRIEND.style_rules).lower()
    assert "how can i help you" in rules  # explicitly forbidden
    assert "customer-support" in rules
    assert "motivational" in rules
    assert "name" in rules  # don't over-use the user's name
    assert "{name}" in WARM_FRIEND.persona
    assert "female" in WARM_FRIEND.persona
