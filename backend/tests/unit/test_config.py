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


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_plain_postgres_url_upgraded_to_asyncpg(scheme: str) -> None:
    # Managed hosts (Render, Neon, Heroku) hand out plain postgres:// URLs.
    settings = make_settings(database_url=f"{scheme}://u:p@localhost/db?ssl=require")
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db?ssl=require"


def test_other_database_driver_rejected() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        make_settings(database_url="postgresql+psycopg2://u:p@localhost/db")


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


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"db_echo": True}, "DB_ECHO"),
        ({"log_level": "DEBUG"}, "LOG_LEVEL"),
        ({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED"),
        ({"openai_store_responses": True}, "OPENAI_STORE_RESPONSES"),
    ],
)
def test_unsafe_production_settings_rejected(overrides: dict, message: str) -> None:
    make_settings(app_env="development", **overrides)  # fine outside production
    with pytest.raises(ValidationError, match=message):
        make_settings(app_env="production", **overrides)


def test_production_guard_reports_all_problems_at_once() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_settings(app_env="production", db_echo=True, cors_origins=["*"])
    assert "DB_ECHO" in str(excinfo.value)
    assert "CORS" in str(excinfo.value)


def test_configuration_warnings() -> None:
    assert any(
        "OPENAI_API_KEY" in w for w in make_settings(openai_api_key=None).configuration_warnings()
    )
    warnings = make_settings(
        openai_api_key="sk-test", openai_chat_model="m", openai_stt_model=None
    ).configuration_warnings()
    assert "OPENAI_STT_MODEL is not set" in warnings
    assert all("sk-test" not in w for w in warnings)
    assert any(
        "TRUSTED_HOSTS" in w for w in make_settings(app_env="production").configuration_warnings()
    )


def test_blank_tool_retention_means_keep_forever() -> None:
    assert make_settings(tool_execution_retention_days="").tool_execution_retention_days is None
    assert make_settings().tool_execution_retention_days == 90
