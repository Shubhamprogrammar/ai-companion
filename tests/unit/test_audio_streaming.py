import struct

import pytest

from app.audio.audio_utils import InputAudioFormat
from app.audio.streaming import SentenceChunker, StreamInputFormat, UtteranceBuffer, pcm16_to_wav
from app.core.exceptions import AudioTooLargeError, UnsupportedAudioError


def _feed(chunker: SentenceChunker, text: str, step: int = 3) -> list[str]:
    out: list[str] = []
    for i in range(0, len(text), step):
        out += chunker.feed(text[i : i + step])
    return out + chunker.flush()


def test_sentences_are_emitted_as_soon_as_they_complete() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("Arre yaar, chill.") == []  # no boundary until whitespace follows
    assert chunker.feed(" Let's") == ["Arre yaar, chill."]
    assert chunker.feed(" plan it!") == []
    assert chunker.flush() == ["Let's plan it!"]


def test_first_segment_may_end_at_a_comma_for_faster_audio() -> None:
    chunker = SentenceChunker(first_min_chars=10)
    first = chunker.feed("Arre yaar sun na, aaj ka din kaisa tha, batao. Phir, plan banate hain.")
    assert first[0] == "Arre yaar sun na,"  # soft break allowed for the first segment only
    assert first[1:] == ["aaj ka din kaisa tha, batao."]
    assert chunker.flush() == ["Phir, plan banate hain."]  # later commas don't split


def test_handles_danda_newlines_and_streaming_fragments() -> None:
    text = "यार, चिंता मत करो। सब ठीक होगा।\nKal baat karte hain? Haan."
    assert _feed(SentenceChunker(), text) == [
        "यार, चिंता मत करो।",
        "सब ठीक होगा।",
        "Kal baat karte hain?",
        "Haan.",
    ]


def test_long_runs_without_punctuation_are_split_softly() -> None:
    text = "so basically " * 40
    segments = _feed(SentenceChunker(max_chars=60), text)
    assert all(len(s) <= 60 for s in segments)
    assert " ".join(segments).split() == text.split()


def test_pcm16_is_wrapped_as_valid_wav() -> None:
    pcm = b"\x01\x00" * 24000
    wav = pcm16_to_wav(pcm, 24000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert struct.unpack("<I", wav[24:28])[0] == 24000  # sample rate
    assert struct.unpack("<I", wav[40:44])[0] == len(pcm)
    assert wav[44:] == pcm


def test_utterance_buffer_enforces_limit_and_validates_format() -> None:
    buffer = UtteranceBuffer(StreamInputFormat.PCM16, 16000, max_bytes=10_000)
    buffer.append(b"\x00\x00" * 1000)
    assert buffer.finish().format is InputAudioFormat.WAV
    with pytest.raises(AudioTooLargeError):
        buffer.append(b"\x00" * 9_000)

    junk = UtteranceBuffer(StreamInputFormat.WEBM, 16000, max_bytes=10_000)
    junk.append(b"definitely not audio" * 50)
    with pytest.raises(UnsupportedAudioError):
        junk.finish()
