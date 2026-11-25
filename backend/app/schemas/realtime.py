"""WebSocket voice protocol: client → server messages (validated) and close codes.

The full protocol is documented in docs/websocket-protocol.md.
"""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.audio.audio_utils import OutputAudioFormat
from app.audio.streaming import StreamInputFormat
from app.schemas.chat import MAX_MESSAGE_CHARS
from app.schemas.users import VoiceName


class _ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionStart(_ClientMessage):
    type: Literal["session.start"]
    token: Annotated[str | None, Field(max_length=4096)] = None
    input_format: StreamInputFormat = StreamInputFormat.WEBM
    sample_rate: Annotated[int, Field(ge=8000, le=48000)] = 24000  # pcm16 only
    output_format: Literal["mp3", "opus", "aac", "pcm"] = "mp3"
    voice: VoiceName | None = None
    speed: Annotated[float, Field(ge=0.25, le=4.0)] | None = None
    text_only: bool = False  # skip speech synthesis; text events only


class SpeechStart(_ClientMessage):
    type: Literal["speech.start"]


class SpeechEnd(_ClientMessage):
    type: Literal["speech.end"]


class TextMessage(_ClientMessage):
    type: Literal["text.message"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_CHARS)]


class ResponseCancel(_ClientMessage):
    type: Literal["response.cancel"]


class ConfirmationRespond(_ClientMessage):
    type: Literal["confirmation.respond"]
    confirmation_id: uuid.UUID
    decision: Literal["approve", "reject"]


class AuthRefresh(_ClientMessage):
    """A fresh access token for a long session (sent before the current one expires)."""

    type: Literal["auth.refresh"]
    token: Annotated[str, Field(min_length=1, max_length=4096)]


class Ping(_ClientMessage):
    type: Literal["ping"]


class SessionEnd(_ClientMessage):
    type: Literal["session.end"]


ClientMessage = Annotated[
    SessionStart
    | SpeechStart
    | SpeechEnd
    | TextMessage
    | ResponseCancel
    | ConfirmationRespond
    | AuthRefresh
    | Ping
    | SessionEnd,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)

OUTPUT_FORMATS = {f.value: OutputAudioFormat(f.value) for f in OutputAudioFormat}


class CloseCode:
    NORMAL = 1000
    PROTOCOL_ERROR = 4400
    UNAUTHORIZED = 4401
    NOT_FOUND = 4404
    AUTH_TIMEOUT = 4408
    TOO_MANY_SESSIONS = 4429  # also: too many connection attempts from this IP
    INTERNAL_ERROR = 1011
