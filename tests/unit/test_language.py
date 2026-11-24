import pytest

from app.ai.language import detect_language
from app.models.enums import Language


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yaar mujhe samajh nahi aa raha kya karu.", Language.HINGLISH),
        ("Yaar, I'm really confused about my career.", Language.HINGLISH),
        ("Aaj ka din bahut accha tha", Language.HINGLISH),
        ("मुझे समझ नहीं आ रहा क्या करूँ", Language.HINDI),
        ("मेरा interview कल है", Language.HINDI),
        (
            "I think I want to get into AI, but my backend skills aren't strong enough.",
            Language.ENGLISH,
        ),
        ("The main thing to do is plan the day.", Language.ENGLISH),
        ("Could you explain how transformers work?", Language.ENGLISH),
        ("👍", None),
        ("", None),
    ],
)
def test_detect_language(text: str, expected: Language | None) -> None:
    assert detect_language(text) is expected
