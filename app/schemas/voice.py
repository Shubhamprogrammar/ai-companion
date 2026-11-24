from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.audio.audio_utils import OutputAudioFormat
from app.schemas.chat import MAX_MESSAGE_CHARS
from app.schemas.common import StrictRequest
from app.schemas.conversations import ConversationResponse, MessageResponse
from app.schemas.tools import ToolActionResponse
from app.schemas.users import VoiceName


class TranscriptionResponse(BaseModel):
    text: str
    duration_seconds: float | None = None


class SynthesizeRequest(StrictRequest):
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_CHARS)]
    voice: VoiceName | None = Field(default=None, description="Defaults to your preference")
    speed: Annotated[float, Field(ge=0.25, le=4.0)] | None = None
    format: OutputAudioFormat = OutputAudioFormat.MP3


class VoiceTurnAudio(BaseModel):
    format: OutputAudioFormat
    mime_type: str
    data_base64: str


class VoiceError(BaseModel):
    code: str
    message: str


class VoiceTimings(BaseModel):
    stt_ms: float | None
    llm_ms: float | None
    tts_ms: float | None
    total_ms: float


class VoiceTurnResponse(BaseModel):
    conversation: ConversationResponse
    user_message: MessageResponse = Field(description="The transcribed user speech")
    assistant_message: MessageResponse
    audio: VoiceTurnAudio | None = Field(description="null if speech synthesis failed")
    audio_error: VoiceError | None = None
    actions: list[ToolActionResponse] = Field(default_factory=list)
    timings: VoiceTimings
