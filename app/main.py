"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.client import OpenAIResponsesLLM, build_openai_client
from app.api.router import api_router
from app.audio.storage import build_audio_store
from app.audio.stt import OpenAISpeechToText
from app.audio.tts import OpenAITextToSpeech
from app.core.background import BackgroundTaskRunner
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.redis import RedisClient
from app.core.security import PasswordHasher, TokenService
from app.memory.embeddings import OpenAIEmbeddings
from app.tools.defaults import build_registry
from app.tools.web_search import build_web_search

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.database = Database(settings)
        app.state.redis = RedisClient(settings)
        logger.info("application started", extra={"fields": {"env": settings.app_env.value}})
        app.state.task_runner = BackgroundTaskRunner()
        try:
            yield
        finally:
            await app.state.task_runner.drain()  # let in-flight memory jobs finish
            if openai_client is not None:
                await openai_client.close()
            await app.state.redis.close()
            await app.state.database.dispose()
            logger.info("application stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        # Interactive docs are disabled in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
    app.state.settings = settings
    app.state.password_hasher = PasswordHasher()
    app.state.token_service = TokenService(settings)
    openai_client = build_openai_client(settings)
    app.state.llm = OpenAIResponsesLLM(openai_client, settings)
    app.state.stt = OpenAISpeechToText(openai_client, settings)
    app.state.tts = OpenAITextToSpeech(openai_client, settings)
    app.state.embeddings = OpenAIEmbeddings(openai_client, settings)
    app.state.tool_registry = build_registry(
        settings, app.state.embeddings, build_web_search(settings, openai_client)
    )
    app.state.audio_store = build_audio_store(
        settings.audio_retention_enabled, settings.audio_storage_path
    )

    # Reject oversized bodies before they're read: audio uploads get the audio limit
    # (+ multipart overhead); everything else is JSON and stays small.
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=1024 * 1024,
        limits={f"{settings.api_v1_prefix}/voice": settings.max_audio_size_bytes + 64 * 1024},
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
