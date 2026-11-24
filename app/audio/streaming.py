"""Helpers for real-time voice: utterance buffering and sentence-level TTS chunking."""

import re
import struct
from dataclasses import dataclass, field
from enum import StrEnum

from app.audio.audio_utils import ValidatedAudio, validate_audio
from app.core.exceptions import AudioTooLargeError


class StreamInputFormat(StrEnum):
    """Audio formats a WebSocket client may stream.

    Container formats (webm/ogg/mp4/...) must be the chunks of ONE recording per
    utterance (e.g. MediaRecorder output between speech.start and speech.end).
    ``pcm16`` is raw 16-bit little-endian mono at ``sample_rate``.
    """

    WEBM = "webm"
    OGG = "ogg"
    MP4 = "mp4"
    WAV = "wav"
    MP3 = "mp3"
    FLAC = "flac"
    PCM16 = "pcm16"


def pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw PCM16 in a minimal WAV header so the STT API can identify it."""
    byte_rate = sample_rate * channels * 2
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, channels * 2, 16
    )
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


@dataclass(slots=True)
class UtteranceBuffer:
    """Collects one utterance's audio frames, enforcing the size limit as they arrive."""

    input_format: StreamInputFormat
    sample_rate: int
    max_bytes: int
    _chunks: list[bytes] = field(default_factory=list)
    size: int = 0

    def append(self, chunk: bytes) -> None:
        if self.size + len(chunk) > self.max_bytes:
            raise AudioTooLargeError("This utterance is too long.")
        self._chunks.append(chunk)
        self.size += len(chunk)

    def finish(self) -> ValidatedAudio:
        data = b"".join(self._chunks)
        if self.input_format is StreamInputFormat.PCM16:
            data = pcm16_to_wav(data, self.sample_rate)
        # Validation sniffs the real format from bytes (the declared one isn't trusted).
        return validate_audio(data, self.max_bytes + 64)


# Sentence boundary: terminal punctuation (incl. Hindi danda) followed by whitespace.
_BOUNDARY = re.compile(r"(?<=[.!?।…])\s+|\n+")
_SOFT_BREAK = re.compile(r"[,;:—–]\s+")


class SentenceChunker:
    """Turns a stream of text deltas into speakable segments as early as possible.

    To minimise time-to-first-audio, the *first* segment may also end at a soft break
    (comma, dash...) once it has ``first_min_chars``; later segments are whole
    sentences, which sound more natural. Very long runs without punctuation are split
    at a soft break or a space.
    """

    def __init__(self, max_chars: int = 220, first_min_chars: int = 24) -> None:
        self._buffer = ""
        self._max = max_chars
        self._first_min = first_min_chars
        self._emitted = False

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        segments: list[str] = []
        while True:
            match = _BOUNDARY.search(self._buffer)
            if not self._emitted:
                # First segment: an earlier soft break (past first_min chars) beats the
                # sentence end, so speech can start sooner.
                soft = next(
                    (m for m in _SOFT_BREAK.finditer(self._buffer) if m.start() >= self._first_min),
                    None,
                )
                if soft and (match is None or soft.start() < match.start()):
                    segments.append(self._buffer[: soft.end()].strip())
                    self._buffer = self._buffer[soft.end() :]
                    self._emitted = True
                    continue
            if match and match.start() > 0:
                segment, self._buffer = self._buffer[: match.start()], self._buffer[match.end() :]
                if segment.strip():
                    segments.append(segment.strip())
                    self._emitted = True
                continue
            if len(self._buffer) > self._max:
                cut = self._split_point(self._buffer)
                segments.append(self._buffer[:cut].strip())
                self._buffer = self._buffer[cut:]
                continue
            if segments:
                self._emitted = True
            return segments

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []

    def _split_point(self, text: str) -> int:
        window = text[: self._max]
        softs = list(_SOFT_BREAK.finditer(window))
        if softs:
            return softs[-1].end()
        space = window.rfind(" ")
        return space + 1 if space > 0 else self._max
