import pytest
from pydantic import ValidationError

from app.core.config import Environment
from tests.conftest import make_settings


def test_loads_from_environment() -> None:
    settings = make_settings()
    assert settings.app_env is Environment.TEST
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.audio_retention_enabled is False


def test_secret_key_is_not_exposed_in_repr() -> None:
    settings = make_settings()
    assert "test-secret-key" not in repr(settings)
    assert settings.secret_key.get_secret_value().startswith("test-secret-key")


def test_short_secret_key_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(secret_key="too-short")


def test_sync_database_driver_rejected() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make_settings(database_url="postgresql://u:p@localhost/db")


def test_log_level_normalised_and_validated() -> None:
    assert make_settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        make_settings(log_level="verbose")


def test_wildcard_cors_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="CORS"):
        make_settings(app_env="production", cors_origins=["*"])


def test_audio_size_bytes() -> None:
    assert make_settings(max_audio_size_mb=2).max_audio_size_bytes == 2 * 1024 * 1024


def test_openai_models_are_optional_and_not_hardcoded() -> None:
    settings = make_settings(openai_chat_model=None)
    assert settings.openai_chat_model is None


def test_default_tts_voice_must_be_allowed() -> None:
    assert make_settings(openai_tts_voice="coral").openai_tts_voice == "coral"
    with pytest.raises(ValidationError, match="OPENAI_TTS_VOICE"):
        make_settings(openai_tts_voice="robot")


def test_audio_size_capped_at_provider_limit() -> None:
    with pytest.raises(ValidationError):
        make_settings(max_audio_size_mb=50)
