from app.ai.romanize import has_devanagari, romanize_hinglish
from tests.fakes import FakeLLM

SPOKEN = "बहुत मज़ा आ रहा है तुमसे बात करके।"


async def test_latin_text_is_returned_without_a_model_call() -> None:
    llm = FakeLLM()
    assert await romanize_hinglish(llm, "Yaar kal meeting hai") == "Yaar kal meeting hai"
    assert llm.requests == []


async def test_devanagari_is_rewritten_in_roman_script() -> None:
    llm = FakeLLM(["Bahut maza aa raha hai tumse baat karke."])
    result = await romanize_hinglish(llm, SPOKEN, model="cheap-model")
    assert result == "Bahut maza aa raha hai tumse baat karke."
    request = llm.requests[0]
    assert request.model == "cheap-model"
    assert f"<transcript>{SPOKEN}</transcript>" in request.messages[0].content
    assert "Transliterate only" in request.instructions


async def test_falls_back_to_original_on_model_error() -> None:
    assert await romanize_hinglish(FakeLLM(fail=True), SPOKEN) == SPOKEN


async def test_rejects_output_that_is_still_devanagari_or_empty() -> None:
    assert await romanize_hinglish(FakeLLM([SPOKEN]), SPOKEN) == SPOKEN
    assert await romanize_hinglish(FakeLLM(["   "]), SPOKEN) == SPOKEN


async def test_rejects_output_that_is_far_longer_than_the_input() -> None:
    rambling = "Sure! Here is the transliteration you asked for. " * 10
    assert await romanize_hinglish(FakeLLM([rambling]), SPOKEN) == SPOKEN


async def test_strips_echoed_transcript_tags() -> None:
    llm = FakeLLM(["<transcript>Bahut maza aa raha hai</transcript>"])
    assert await romanize_hinglish(llm, "बहुत मज़ा आ रहा है") == "Bahut maza aa raha hai"


def test_has_devanagari() -> None:
    assert has_devanagari("कल meeting है")
    assert not has_devanagari("kal meeting hai")
