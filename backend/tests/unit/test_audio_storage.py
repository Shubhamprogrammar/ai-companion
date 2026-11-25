import uuid
from pathlib import Path

from app.audio.audio_utils import validate_audio
from app.audio.storage import DisabledAudioStore, LocalAudioStore, build_audio_store
from tests.fakes import fake_wav


async def test_local_store_saves_and_deletes_per_conversation(tmp_path: Path) -> None:
    store = LocalAudioStore(tmp_path)
    user, conv, msg = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    audio = validate_audio(fake_wav(), max_bytes=10_000)

    await store.save(user, conv, msg, audio)
    path = store.path_for(user, conv, msg, "wav")
    assert path.read_bytes() == audio.data
    assert path.is_relative_to(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    await store.delete_conversation(user, conv)
    assert not path.parent.exists()
    await store.delete_conversation(user, conv)  # idempotent


def test_paths_only_accept_uuids(tmp_path: Path) -> None:
    store = LocalAudioStore(tmp_path)
    try:
        store.path_for("../../etc", uuid.uuid4(), uuid.uuid4(), "wav")  # type: ignore[arg-type]
    except ValueError:
        return
    raise AssertionError("non-UUID path component accepted")


def test_factory_respects_retention_flag(tmp_path: Path) -> None:
    assert isinstance(build_audio_store(False, str(tmp_path)), DisabledAudioStore)
    assert isinstance(build_audio_store(True, str(tmp_path)), LocalAudioStore)
