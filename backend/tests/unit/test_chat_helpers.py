from app.services.chat import make_title


def test_short_message_is_used_as_is() -> None:
    assert make_title("  Career   confusion ") == "Career confusion"


def test_long_message_is_cut_at_word_boundary() -> None:
    title = make_title(
        "Yaar, I'm really confused about my career and I don't know what to focus on at all"
    )
    assert title.endswith("…")
    assert len(title) <= 61
    assert not title[:-1].endswith(" ")
