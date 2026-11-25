"""Shared FastAPI dependencies.

Infrastructure objects (settings, DB, Redis, hashers) are created once in
``create_app`` / lifespan and stored on ``app.state``; tests override them via
``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.ai.agent import CompanionAgent
from app.ai.llm import LLMProvider
from app.audio.storage import AudioStore
from app.audio.stt import SpeechToTextProvider
from app.audio.tts import TextToSpeechProvider
from app.core.background import TaskRunner
from app.core.config import Settings
from app.core.database import Database
from app.core.exceptions import AuthenticationError, RateLimitedError
from app.core.rate_limit import RateLimiter
from app.core.redis import RedisClient
from app.core.security import PasswordHasher, TokenClaims, TokenService
from app.memory.embeddings import EmbeddingProvider
from app.models.user import User
from app.repositories.login_attempts import LoginAttemptStore
from app.repositories.revoked_tokens import RevokedAccessTokenStore
from app.services.auth import AuthService
from app.services.chat import ChatService
from app.services.conversations import ConversationService
from app.services.health import HealthService
from app.services.memories import MemoryService
from app.services.memory_pipeline import MemoryCoordinator, MemoryPipeline, SessionFactory
from app.services.productivity import ReminderService, TaskService
from app.services.tool_actions import ToolActionService
from app.services.users import UserService
from app.services.voice import VoiceService
from app.services.voice_session import RealtimeDeps
from app.tools.registry import ToolRegistry

# --- infrastructure -----------------------------------------------------------


def get_app_settings(conn: HTTPConnection) -> Settings:
    return conn.app.state.settings


def get_database(conn: HTTPConnection) -> Database:
    return conn.app.state.database


def get_redis(conn: HTTPConnection) -> RedisClient:
    return conn.app.state.redis


def get_password_hasher(conn: HTTPConnection) -> PasswordHasher:
    return conn.app.state.password_hasher


def get_token_service(conn: HTTPConnection) -> TokenService:
    return conn.app.state.token_service


def get_llm(conn: HTTPConnection) -> LLMProvider:
    return conn.app.state.llm


def get_stt(conn: HTTPConnection) -> SpeechToTextProvider:
    return conn.app.state.stt


def get_tts(conn: HTTPConnection) -> TextToSpeechProvider:
    return conn.app.state.tts


def get_audio_store(conn: HTTPConnection) -> AudioStore:
    return conn.app.state.audio_store


def get_embeddings(conn: HTTPConnection) -> EmbeddingProvider:
    return conn.app.state.embeddings


def get_task_runner(conn: HTTPConnection) -> TaskRunner:
    return conn.app.state.task_runner


def get_tool_registry(conn: HTTPConnection) -> ToolRegistry:
    return conn.app.state.tool_registry


ToolRegistryDep = Annotated[ToolRegistry, Depends(get_tool_registry)]


def get_session_factory(
    database: Annotated[Database, Depends(get_database)],
) -> SessionFactory:
    """Factory for sessions that outlive the request (background jobs)."""
    return database.session_factory


async def get_db_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async for session in database.session():
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[RedisClient, Depends(get_redis)]

# --- services -----------------------------------------------------------------


def get_health_service(
    database: Annotated[Database, Depends(get_database)], redis: RedisDep
) -> HealthService:
    return HealthService({"database": database.ping, "redis": redis.ping})


def get_auth_service(
    session: DbSessionDep,
    settings: SettingsDep,
    redis: RedisDep,
    tokens: Annotated[TokenService, Depends(get_token_service)],
    passwords: Annotated[PasswordHasher, Depends(get_password_hasher)],
) -> AuthService:
    login_attempts = LoginAttemptStore(
        redis.client, settings.login_max_failures, settings.login_lockout_minutes * 60
    )
    return AuthService(
        session,
        settings,
        tokens,
        passwords,
        RevokedAccessTokenStore(redis.client),
        login_attempts if settings.rate_limit_enabled else None,
    )


AudioStoreDep = Annotated[AudioStore, Depends(get_audio_store)]


def get_user_service(session: DbSessionDep, settings: SettingsDep) -> UserService:
    return UserService(session, settings)


def get_conversation_service(
    session: DbSessionDep, audio_store: AudioStoreDep
) -> ConversationService:
    return ConversationService(session, audio_store)


def get_companion_agent(
    llm: Annotated[LLMProvider, Depends(get_llm)], settings: SettingsDep
) -> CompanionAgent:
    return CompanionAgent(llm, settings)


EmbeddingsDep = Annotated[EmbeddingProvider, Depends(get_embeddings)]


def get_memory_coordinator(
    session: DbSessionDep,
    embeddings: EmbeddingsDep,
    llm: Annotated[LLMProvider, Depends(get_llm)],
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    settings: SettingsDep,
) -> MemoryCoordinator:
    pipeline = MemoryPipeline(session_factory, llm, embeddings, settings)
    return MemoryCoordinator(session, embeddings, pipeline, runner, settings)


def get_chat_service(
    session: DbSessionDep,
    agent: Annotated[CompanionAgent, Depends(get_companion_agent)],
    memory: Annotated[MemoryCoordinator, Depends(get_memory_coordinator)],
    tools: ToolRegistryDep,
    settings: SettingsDep,
) -> ChatService:
    return ChatService(session, agent, settings, memory, tools)


def get_memory_service(
    session: DbSessionDep, embeddings: EmbeddingsDep, settings: SettingsDep
) -> MemoryService:
    return MemoryService(session, embeddings, settings)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_voice_service(
    session: DbSessionDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    stt: Annotated[SpeechToTextProvider, Depends(get_stt)],
    tts: Annotated[TextToSpeechProvider, Depends(get_tts)],
    audio_store: AudioStoreDep,
    settings: SettingsDep,
) -> VoiceService:
    return VoiceService(session, chat, stt, tts, audio_store, settings)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
VoiceServiceDep = Annotated[VoiceService, Depends(get_voice_service)]
MemoryServiceDep = Annotated[MemoryService, Depends(get_memory_service)]


def get_reminder_service(session: DbSessionDep) -> ReminderService:
    return ReminderService(session)


def get_task_service(session: DbSessionDep) -> TaskService:
    return TaskService(session)


ReminderServiceDep = Annotated[ReminderService, Depends(get_reminder_service)]


def get_tool_action_service(
    session: DbSessionDep, registry: ToolRegistryDep, settings: SettingsDep
) -> ToolActionService:
    return ToolActionService(session, registry, settings)


ToolActionServiceDep = Annotated[ToolActionService, Depends(get_tool_action_service)]
TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]

# --- authentication -----------------------------------------------------------

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    claims: TokenClaims


async def get_auth_context(
    auth: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    user, claims = await auth.authenticate(credentials.credentials)
    return AuthContext(user, claims)


async def get_current_user(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> User:
    return context.user


AuthContextDep = Annotated[AuthContext, Depends(get_auth_context)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

# --- rate limiting ------------------------------------------------------------


def client_ip(conn: HTTPConnection) -> str:
    return conn.client.host if conn.client else "unknown"


async def _enforce(settings: Settings, redis: RedisClient, key: str, limit: int) -> None:
    if not settings.rate_limit_enabled:
        return
    result = await RateLimiter(redis.client).hit(key, limit=limit, window_seconds=60)
    if not result.allowed:
        raise RateLimitedError(headers={"Retry-After": str(result.retry_after_seconds)})


def rate_limit(scope: str) -> Callable[..., Awaitable[None]]:
    """Per-IP limit for unauthenticated endpoints (AUTH_RATE_LIMIT_PER_MINUTE)."""

    async def dependency(request: Request, settings: SettingsDep, redis: RedisDep) -> None:
        await _enforce(
            settings, redis, f"{scope}:{client_ip(request)}", settings.auth_rate_limit_per_minute
        )

    return dependency


def user_rate_limit(scope: str) -> Callable[..., Awaitable[None]]:
    """Per-user limit for AI endpoints (CHAT_RATE_LIMIT_PER_MINUTE): they cost money."""

    async def dependency(user: CurrentUserDep, settings: SettingsDep, redis: RedisDep) -> None:
        await _enforce(
            settings, redis, f"{scope}:user:{user.id}", settings.chat_rate_limit_per_minute
        )

    return dependency


# --- realtime (WebSocket) ---------------------------------------------------------


def get_realtime_deps(
    settings: SettingsDep,
    redis: RedisDep,
    llm: Annotated[LLMProvider, Depends(get_llm)],
    stt: Annotated[SpeechToTextProvider, Depends(get_stt)],
    tts: Annotated[TextToSpeechProvider, Depends(get_tts)],
    embeddings: EmbeddingsDep,
    runner: Annotated[TaskRunner, Depends(get_task_runner)],
    registry: ToolRegistryDep,
    audio_store: AudioStoreDep,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    passwords: Annotated[PasswordHasher, Depends(get_password_hasher)],
) -> RealtimeDeps:
    return RealtimeDeps(
        settings=settings,
        llm=llm,
        stt=stt,
        tts=tts,
        embeddings=embeddings,
        runner=runner,
        registry=registry,
        audio_store=audio_store,
        session_factory=session_factory,
        redis=redis,
        tokens=tokens,
        passwords=passwords,
    )


RealtimeDepsDep = Annotated[RealtimeDeps, Depends(get_realtime_deps)]
