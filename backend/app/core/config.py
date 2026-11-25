"""Application configuration loaded from environment variables / .env.

All tunables (including OpenAI model names) live here so nothing is hardcoded
elsewhere in the codebase. Access settings via ``get_settings()``.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_env: Environment = Environment.DEVELOPMENT
    app_name: str = "Personal AI Companion"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True
    secret_key: SecretStr = Field(..., min_length=32)
    cors_origins: list[str] = Field(default_factory=list)
    # Host header allow-list (e.g. ["api.example.com"]). Empty disables the check.
    trusted_hosts: list[str] = Field(default_factory=list)

    # --- Database ---
    database_url: str
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_echo: bool = False

    # --- Redis ---
    redis_url: str

    # --- OpenAI (consumed from Phase 3; optional until then) ---
    openai_api_key: SecretStr | None = None
    openai_chat_model: str | None = None
    openai_stt_model: str | None = None
    openai_tts_model: str | None = None
    openai_embedding_model: str | None = None
    openai_tts_voice: str | None = None
    openai_timeout_seconds: float = Field(default=30.0, gt=0)
    openai_max_retries: int = Field(default=2, ge=0, le=5)
    openai_max_output_tokens: int = Field(default=2048, ge=64)
    # Only sent when set; supported values depend on the model (e.g. "low", "medium").
    openai_reasoning_effort: str | None = None
    # false: OpenAI does not retain responses; conversation state lives in our DB only.
    openai_store_responses: bool = False

    # Optional cheaper model for background memory work (extraction, summaries);
    # falls back to OPENAI_CHAT_MODEL.
    openai_memory_model: str | None = None

    # --- Memory ---
    memory_recall_limit: int = Field(default=5, ge=0, le=20)  # semantic matches per turn
    memory_core_limit: int = Field(default=5, ge=0, le=10)  # always-included important facts
    # Cosine distance (0 = identical, 2 = opposite). Matches beyond this are ignored.
    # Calibrated on text-embedding-3-small: relevant pairs measured 0.56–0.74 (incl.
    # Hinglish queries vs English memories), unrelated pairs ≥ 0.80.
    memory_max_distance: float = Field(default=0.78, gt=0, le=2)
    # A new memory this close to an existing one updates it instead of duplicating it.
    memory_dedup_distance: float = Field(default=0.15, ge=0, le=1)
    memory_max_per_user: int = Field(default=500, ge=1)
    # Summarise once this many messages have scrolled out of the context window.
    summary_trigger_messages: int = Field(default=10, ge=2)

    # --- Tools ---
    tools_enabled: bool = True
    tools_disabled: list[str] = Field(default_factory=list)  # names of tools to switch off
    tool_max_rounds: int = Field(default=3, ge=1, le=5)  # model ↔ tool round trips per turn
    tool_max_calls_per_turn: int = Field(default=5, ge=1, le=10)
    confirmation_ttl_minutes: int = Field(default=10, ge=1, le=1440)
    # Web search is off unless a provider is configured. "openai" uses the Responses API's
    # built-in web_search tool (billed per search by OpenAI; no extra key needed).
    web_search_provider: Literal["none", "openai"] = "none"
    openai_web_search_model: str | None = None  # defaults to OPENAI_CHAT_MODEL

    # Tool audit rows hold validated inputs/outputs (may include personal details).
    # Deleted after this many days by the maintenance job; unset keeps them forever.
    tool_execution_retention_days: int | None = Field(default=90, ge=1)

    # Developer test page for voice at /voice-test. Never served in production.
    test_ui_enabled: bool = True

    # --- Maintenance (scripts/maintenance.py --loop) ---
    maintenance_interval_minutes: int = Field(default=360, ge=5)

    # --- WebSocket voice sessions ---
    ws_auth_timeout_seconds: float = Field(default=10, gt=0)
    ws_idle_timeout_seconds: float = Field(default=120, gt=0)
    ws_max_session_minutes: int = Field(default=30, ge=1)
    ws_max_sessions_per_user: int = Field(default=3, ge=1)
    ws_max_frame_bytes: int = Field(default=512 * 1024, ge=1024)
    ws_connect_rate_limit_per_minute: int = Field(default=20, ge=1)  # per client IP

    # --- Voice ---
    # Language hints for speech-to-text (ISO-639-1). `languages` is supported by gpt-transcribe;
    # set to [] for models that don't accept it.
    openai_stt_languages: list[str] = Field(default_factory=lambda: ["en", "hi"])
    # Built-in voices accepted by the TTS API (users may pick any of these).
    openai_tts_voices: list[str] = Field(
        default_factory=lambda: [
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "fable",
            "onyx",
            "nova",
            "sage",
            "shimmer",
            "verse",
            "marin",
            "cedar",
        ]
    )
    audio_storage_path: str = "audio_storage"

    # --- Companion ---
    companion_name: str = Field(default="Mira", min_length=1, max_length=40)
    chat_context_max_messages: int = Field(default=30, ge=2, le=200)
    chat_context_max_chars: int = Field(default=24_000, ge=1_000)
    chat_rate_limit_per_minute: int = Field(default=20, ge=1)

    # --- Auth ---
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1)
    refresh_token_expire_days: int = Field(default=7, ge=1)

    # --- Rate limiting (Redis fixed window, per client IP) ---
    rate_limit_enabled: bool = True
    auth_rate_limit_per_minute: int = Field(default=10, ge=1)
    # Per-account lock after repeated wrong passwords (any IP).
    login_max_failures: int = Field(default=10, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)

    # --- Memory / audio ---
    memory_enabled: bool = True
    audio_retention_enabled: bool = False
    # OpenAI's transcription upload limit is 25 MB.
    max_audio_size_mb: int = Field(default=20, ge=1, le=25)

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {value}")
        return value

    @field_validator(
        "openai_chat_model",
        "openai_stt_model",
        "openai_tts_model",
        "openai_embedding_model",
        "openai_tts_voice",
        "openai_reasoning_effort",
        "openai_memory_model",
        "openai_web_search_model",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _blank_key_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// driver")
        return value

    @model_validator(mode="after")
    def _default_voice_is_allowed(self) -> "Settings":
        if self.openai_tts_voice and self.openai_tts_voice not in self.openai_tts_voices:
            raise ValueError("OPENAI_TTS_VOICE must be one of OPENAI_TTS_VOICES")
        return self

    @field_validator("tool_execution_retention_days", mode="before")
    @classmethod
    def _blank_int_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        """Refuse to start in production with settings that are only safe in development."""
        if self.app_env is not Environment.PRODUCTION:
            return self
        problems = []
        if "*" in self.cors_origins:
            problems.append("wildcard CORS origins are not allowed")
        if self.db_echo:
            problems.append("DB_ECHO logs SQL parameters (user content)")
        if self.log_level == "DEBUG":
            problems.append("LOG_LEVEL=DEBUG can log request bodies from HTTP clients")
        if not self.rate_limit_enabled:
            problems.append("RATE_LIMIT_ENABLED must be true")
        if self.openai_store_responses:
            problems.append("OPENAI_STORE_RESPONSES must be false (conversations stay in our DB)")
        if problems:
            raise ValueError("unsafe production settings: " + "; ".join(problems))
        return self

    def configuration_warnings(self) -> list[str]:
        """Non-fatal gaps worth logging at startup (features degrade to clear 503s)."""
        warnings = []
        if self.openai_api_key is None:
            warnings.append("OPENAI_API_KEY is not set: chat, voice and memory are unavailable")
        else:
            for name in ("chat", "stt", "tts", "embedding"):
                if not getattr(self, f"openai_{name}_model"):
                    warnings.append(f"OPENAI_{name.upper()}_MODEL is not set")
        if self.is_production and not self.trusted_hosts:
            warnings.append("TRUSTED_HOSTS is empty: Host headers are not validated")
        return warnings

    @property
    def is_production(self) -> bool:
        return self.app_env is Environment.PRODUCTION

    @property
    def max_audio_size_bytes(self) -> int:
        return self.max_audio_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
