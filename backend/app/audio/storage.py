"""Optional retention of raw user audio (off by default: AUDIO_RETENTION_ENABLED=false).

Files are stored at ``<base>/<user_id>/<conversation_id>/<message_id>.<ext>``. All
path components are UUIDs generated server-side, so user input never reaches the path.
"""

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from app.audio.audio_utils import ValidatedAudio


class AudioStore(Protocol):
    enabled: bool

    async def save(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        audio: ValidatedAudio,
    ) -> None: ...

    async def delete_conversation(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None: ...


class DisabledAudioStore:
    """Default: audio is processed in memory and never written anywhere."""

    enabled = False

    async def save(self, *_: object) -> None:
        return None

    async def delete_conversation(self, *_: object) -> None:
        return None


class LocalAudioStore:
    enabled = True

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path).resolve()

    def _conversation_dir(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Path:
        return self._base / str(uuid.UUID(str(user_id))) / str(uuid.UUID(str(conversation_id)))

    def path_for(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, ext: str
    ) -> Path:
        return (
            self._conversation_dir(user_id, conversation_id) / f"{uuid.UUID(str(message_id))}.{ext}"
        )

    async def save(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        audio: ValidatedAudio,
    ) -> None:
        path = self.path_for(user_id, conversation_id, message_id, audio.format.value)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(audio.data)
            path.chmod(0o600)

        await asyncio.to_thread(_write)

    async def delete_conversation(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        directory = self._conversation_dir(user_id, conversation_id)
        await asyncio.to_thread(shutil.rmtree, directory, True)


def build_audio_store(enabled: bool, base_path: str) -> AudioStore:
    return LocalAudioStore(base_path) if enabled else DisabledAudioStore()
