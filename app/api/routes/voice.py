import base64
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.api.dependencies import CurrentUserDep, SettingsDep, VoiceServiceDep, user_rate_limit
from app.audio.audio_utils import OUTPUT_MIME, OutputAudioFormat
from app.core.exceptions import AudioTooLargeError
from app.schemas.conversations import ConversationResponse, MessageResponse
from app.schemas.tools import ToolActionResponse
from app.schemas.voice import (
    SynthesizeRequest,
    TranscriptionResponse,
    VoiceError,
    VoiceTimings,
    VoiceTurnAudio,
    VoiceTurnResponse,
)

router = APIRouter(prefix="/voice", tags=["voice"])

AudioFile = Annotated[UploadFile, File(description="flac, mp3, mp4/m4a, ogg, wav or webm")]
_ERRORS = {
    413: {"description": "Audio too large"},
    415: {"description": "Unsupported audio format"},
    422: {"description": "Invalid audio or no speech detected"},
    503: {"description": "Speech/AI provider unavailable"},
}


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    data = await file.read(max_bytes + 1)  # never read more than the limit
    if len(data) > max_bytes:
        raise AudioTooLargeError(
            f"The audio file is too large (max {max_bytes // (1024 * 1024)} MB)."
        )
    return data


@router.post(
    "/transcribe",
    response_model=TranscriptionResponse,
    dependencies=[Depends(user_rate_limit("voice"))],
    responses=_ERRORS,
)
async def transcribe(
    file: AudioFile, _: CurrentUserDep, voice: VoiceServiceDep, settings: SettingsDep
) -> TranscriptionResponse:
    """Speech-to-text only. Nothing is stored."""
    transcript = await voice.transcribe(await _read_upload(file, settings.max_audio_size_bytes))
    return TranscriptionResponse(text=transcript.text, duration_seconds=transcript.duration_seconds)


@router.post(
    "/synthesize",
    response_class=StreamingResponse,
    dependencies=[Depends(user_rate_limit("voice"))],
    responses={200: {"content": {m: {} for m in set(OUTPUT_MIME.values())}}, **_ERRORS},
)
async def synthesize(
    body: SynthesizeRequest, user: CurrentUserDep, voice: VoiceServiceDep
) -> StreamingResponse:
    """Text-to-speech, streamed as it is generated. Voice/speed default to your preferences."""
    request = await voice.speech_request(
        user, body.text, voice=body.voice, speed=body.speed, audio_format=body.format
    )
    chunks = await voice.synthesize_stream(request)
    # Pull the first chunk before responding so provider errors become proper HTTP errors.
    first = await anext(chunks, b"")

    async def body_iter() -> AsyncIterator[bytes]:
        if first:
            yield first
        async for chunk in chunks:
            yield chunk

    return StreamingResponse(
        body_iter(),
        media_type=OUTPUT_MIME[body.format],
        headers={"Content-Disposition": f'inline; filename="speech.{body.format.value}"'},
    )


@router.post(
    "/turn",
    response_model=VoiceTurnResponse,
    dependencies=[Depends(user_rate_limit("chat"))],
    responses=_ERRORS,
)
async def voice_turn(
    file: AudioFile,
    user: CurrentUserDep,
    voice: VoiceServiceDep,
    settings: SettingsDep,
    conversation_id: Annotated[
        uuid.UUID | None, Form(description="Omit to start a new conversation")
    ] = None,
    response_format: Annotated[OutputAudioFormat, Form()] = OutputAudioFormat.MP3,
) -> VoiceTurnResponse:
    """Complete voice turn: your audio → transcript → companion reply → spoken reply.

    The reply audio is returned base64-encoded. If synthesis fails, the turn is still
    saved and `audio` is null with `audio_error` explaining why.
    """
    data = await _read_upload(file, settings.max_audio_size_bytes)
    result = await voice.turn(user, data, conversation_id, response_format)
    speech, error = result.speech, result.speech_error
    return VoiceTurnResponse(
        conversation=ConversationResponse.model_validate(result.chat.conversation),
        user_message=MessageResponse.model_validate(result.chat.user_message),
        assistant_message=MessageResponse.model_validate(result.chat.assistant_message),
        audio=VoiceTurnAudio(
            format=speech.format,
            mime_type=speech.mime_type,
            data_base64=base64.b64encode(speech.audio).decode(),
        )
        if speech
        else None,
        audio_error=VoiceError(code=error.code, message=error.message) if error else None,
        actions=[ToolActionResponse.from_activity(a) for a in result.chat.actions],
        timings=VoiceTimings(**asdict(result.timings)),
    )
