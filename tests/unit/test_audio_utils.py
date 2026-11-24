import pytest

from app.audio.audio_utils import (
    InputAudioFormat,
    detect_audio_format,
    prepare_text_for_speech,
    split_for_tts,
    validate_audio,
)
from app.core.exceptions import AudioTooLargeError, InvalidAudioError, UnsupportedAudioError
from tests.fakes import fake_mp3, fake_wav

PAD = b"\x00" * 512


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (fake_wav(), InputAudioFormat.WAV),
        (fake_mp3(), InputAudioFormat.MP3),
        (b"\xff\xfb\x90\x00" + PAD, InputAudioFormat.MP3),  # MPEG frame sync, no ID3
        (b"OggS" + PAD, InputAudioFormat.OGG),
        (b"fLaC" + PAD, InputAudioFormat.FLAC),
        (b"\x1a\x45\xdf\xa3" + PAD, InputAudioFormat.WEBM),
        (b"\x00\x00\x00\x20ftypM4A " + PAD, InputAudioFormat.MP4),
        (b"%PDF-1.7" + PAD, None),
        (b"<html>" + PAD, None),
    ],
)
def test_detect_audio_format(data: bytes, expected: InputAudioFormat | None) -> None:
    assert detect_audio_format(data) is expected


def test_validate_accepts_real_signature() -> None:
    audio = validate_audio(fake_wav(), max_bytes=10_000)
    assert audio.format is InputAudioFormat.WAV
    assert audio.filename == "audio.wav"
    assert audio.mime_type == "audio/wav"


def test_validate_rejects_oversized() -> None:
    with pytest.raises(AudioTooLargeError):
        validate_audio(fake_wav(4096), max_bytes=1024)


def test_validate_rejects_empty_or_tiny() -> None:
    with pytest.raises(InvalidAudioError):
        validate_audio(b"", max_bytes=1024)
    with pytest.raises(InvalidAudioError):
        validate_audio(b"RIFF1234WAVE", max_bytes=1024)


def test_validate_rejects_non_audio_even_if_named_audio() -> None:
    with pytest.raises(UnsupportedAudioError):
        validate_audio(b"#!/bin/sh\nrm -rf /\n" + PAD, max_bytes=10_000)


def test_prepare_text_for_speech_strips_markup_and_emoji() -> None:
    text = "**Haan** yaar 💙 check [this](https://x.com)\n\n# Step 1\n`code` here 👍🏽"
    assert prepare_text_for_speech(text) == "Haan yaar check this\nStep 1\ncode here"


def test_prepare_text_keeps_devanagari_and_punctuation() -> None:
    assert prepare_text_for_speech("यार, चिंता मत करो। Sab theek hoga!") == (
        "यार, चिंता मत करो। Sab theek hoga!"
    )


def test_split_for_tts_short_text_is_single_chunk() -> None:
    assert split_for_tts("Hello there.", 100) == ["Hello there."]
    assert split_for_tts("", 100) == []


def test_split_for_tts_respects_limit_and_sentence_boundaries() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(50))
    chunks = split_for_tts(text, 120)
    assert all(len(c) <= 120 for c in chunks)
    assert all(c.endswith(".") for c in chunks)
    assert " ".join(chunks) == text


def test_split_for_tts_hard_wraps_overlong_sentence() -> None:
    chunks = split_for_tts("word " * 100, 50)
    assert all(len(c) <= 50 for c in chunks)
    assert " ".join(chunks).split() == ["word"] * 100


def test_split_for_tts_handles_hindi_danda() -> None:
    chunks = split_for_tts("पहला वाक्य। दूसरा वाक्य। तीसरा वाक्य।", 20)
    assert chunks == ["पहला वाक्य।", "दूसरा वाक्य।", "तीसरा वाक्य।"]
