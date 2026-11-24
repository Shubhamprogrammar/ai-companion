"""Audio validation and text preparation for speech.

Formats are identified from the file's magic bytes. The client-supplied
filename and content type are never trusted.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.core.exceptions import AudioTooLargeError, InvalidAudioError, UnsupportedAudioError

_MIN_AUDIO_BYTES = 256  # anything smaller can't hold meaningful speech


class InputAudioFormat(StrEnum):
    """Formats accepted for transcription (the set OpenAI's STT accepts)."""

    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"
    WEBM = "webm"
    MP4 = "mp4"  # includes .m4a


class OutputAudioFormat(StrEnum):
    """Formats the TTS provider can produce."""

    MP3 = "mp3"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    WAV = "wav"
    PCM = "pcm"  # raw 24 kHz 16-bit little-endian mono


INPUT_MIME = {
    InputAudioFormat.WAV: "audio/wav",
    InputAudioFormat.MP3: "audio/mpeg",
    InputAudioFormat.OGG: "audio/ogg",
    InputAudioFormat.FLAC: "audio/flac",
    InputAudioFormat.WEBM: "audio/webm",
    InputAudioFormat.MP4: "audio/mp4",
}

OUTPUT_MIME = {
    OutputAudioFormat.MP3: "audio/mpeg",
    OutputAudioFormat.OPUS: "audio/ogg",
    OutputAudioFormat.AAC: "audio/aac",
    OutputAudioFormat.FLAC: "audio/flac",
    OutputAudioFormat.WAV: "audio/wav",
    OutputAudioFormat.PCM: "audio/pcm",
}

# Formats whose byte streams can be concatenated into one playable file
# (needed when long replies are synthesised in several chunks).
CONCATENABLE_OUTPUT = frozenset(
    {OutputAudioFormat.MP3, OutputAudioFormat.OPUS, OutputAudioFormat.AAC, OutputAudioFormat.PCM}
)


@dataclass(frozen=True, slots=True)
class ValidatedAudio:
    data: bytes
    format: InputAudioFormat

    @property
    def filename(self) -> str:
        # The provider identifies the format from the extension, so it must match the bytes.
        return f"audio.{self.format.value}"

    @property
    def mime_type(self) -> str:
        return INPUT_MIME[self.format]


def detect_audio_format(data: bytes) -> InputAudioFormat | None:
    head = data[:16]
    if head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return InputAudioFormat.WAV
    if head[:4] == b"OggS":
        return InputAudioFormat.OGG
    if head[:4] == b"fLaC":
        return InputAudioFormat.FLAC
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return InputAudioFormat.WEBM
    if data[4:8] == b"ftyp":
        return InputAudioFormat.MP4
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return InputAudioFormat.MP3
    return None


def validate_audio(data: bytes, max_bytes: int) -> ValidatedAudio:
    if len(data) > max_bytes:
        raise AudioTooLargeError(
            f"The audio file is too large (max {max_bytes // (1024 * 1024)} MB)."
        )
    if len(data) < _MIN_AUDIO_BYTES:
        raise InvalidAudioError("The audio file is empty or too short.")
    audio_format = detect_audio_format(data)
    if audio_format is None:
        raise UnsupportedAudioError()
    return ValidatedAudio(data=data, format=audio_format)


# --- text → speech preparation ----------------------------------------------------

_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, symbols, flags
    "☀-➿"  # misc symbols & dingbats
    "️‍"  # variation selector, zero-width joiner
    "]+"
)
_MARKDOWN = re.compile(r"(\*\*|__|\*|`+|^#{1,6}\s*|^>\s*)", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_WHITESPACE = re.compile(r"[ \t]+")


def prepare_text_for_speech(text: str) -> str:
    """Strip markup and emoji that a TTS engine would read out or mangle."""
    text = _LINK.sub(r"\1", text)
    text = _MARKDOWN.sub("", text)
    text = _EMOJI.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


_SENTENCE_END = re.compile(r"(?<=[.!?।…])\s+")


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Split at sentence boundaries into chunks of at most ``max_chars``."""
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > max_chars:  # a single overlong sentence: hard-wrap on spaces
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c for c in chunks if c]
