"""Shared test configuration.

Environment defaults are set *before* any ``app`` import so settings load
without a real .env. No test in this suite calls a paid external API.

Infrastructure-backed fixtures (``infra_settings`` and everything built on it)
use a dedicated test database and Redis DB, skip when the services aren't
running, and auto-mark the test as ``integration``.
"""

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/companion_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/15")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("OPENAI_TTS_VOICE", "coral")

import asyncio  # noqa: E402
import sys  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from redis.exceptions import RedisError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine  # noqa: E402

from app.api.dependencies import (  # noqa: E402
    get_audio_store,
    get_db_session,
    get_embeddings,
    get_llm,
    get_redis,
    get_session_factory,
    get_stt,
    get_task_runner,
    get_tool_registry,
    get_tts,
)
from app.audio.storage import AudioStore, DisabledAudioStore  # noqa: E402
from app.core.background import InlineTaskRunner  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.redis import RedisClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app.tools.defaults import build_registry  # noqa: E402
from tests.fakes import FakeEmbeddings, FakeLLM, FakeSTT, FakeTTS  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/companion_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")

_INFRA_FIXTURES = {"infra_settings"}


def make_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _INFRA_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.integration)


# --- infrastructure-free fixtures ---------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client without lifespan: infrastructure deps must be overridden by the test."""
    async for ac in _client_for(app):
        yield ac


# --- infrastructure fixtures (PostgreSQL + Redis) -----------------------------


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""), timeout=2)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


async def _redis_reachable() -> bool:
    redis = Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=2)
    try:
        return bool(await redis.ping())
    except (OSError, RedisError):
        return False
    finally:
        await redis.aclose()


@pytest.fixture(scope="session")
async def infra_settings() -> Settings:
    if not (await _postgres_reachable() and await _redis_reachable()):
        pytest.skip("PostgreSQL/Redis not reachable; run `docker compose up -d db redis`")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-x",
        f"db_url={TEST_DATABASE_URL}",
        "upgrade",
        "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await proc.communicate()
    if proc.returncode != 0:
        pytest.fail(f"alembic upgrade failed:\n{output.decode()}")
    return make_settings(database_url=TEST_DATABASE_URL, redis_url=TEST_REDIS_URL)


@pytest.fixture(scope="session")
async def db_engine(infra_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(infra_settings.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction that is rolled back after the test.

    ``create_savepoint`` turns the code-under-test's ``commit()`` calls into
    savepoint releases, so services behave normally yet nothing persists.
    """
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def redis_client(infra_settings: Settings) -> AsyncIterator[RedisClient]:
    redis = RedisClient(infra_settings)
    await redis.client.flushdb()  # test Redis DB only (see TEST_REDIS_URL)
    yield redis
    await redis.client.flushdb()
    await redis.close()


def build_app(
    settings: Settings,
    session: AsyncSession,
    redis: RedisClient,
    llm: FakeLLM | None = None,
    *,
    stt: FakeSTT | None = None,
    tts: FakeTTS | None = None,
    audio_store: AudioStore | None = None,
    embeddings: FakeEmbeddings | None = None,
    runner: InlineTaskRunner | None = None,
    web_search=None,
) -> FastAPI:
    """App wired to the per-test DB session, Redis client and fake AI/voice providers.

    Background jobs go to an InlineTaskRunner; tests run them with ``run_pending()``.
    They reuse the test's session, so everything still rolls back afterwards.
    """
    application = create_app(settings)
    embeddings = embeddings or FakeEmbeddings()
    fakes = {
        get_db_session: session,
        get_redis: redis,
        get_llm: llm or FakeLLM(),
        get_stt: stt or FakeSTT(),
        get_tts: tts or FakeTTS(),
        get_audio_store: audio_store or DisabledAudioStore(),
        get_embeddings: embeddings,
        get_tool_registry: build_registry(settings, embeddings, web_search),
        get_task_runner: runner or InlineTaskRunner(),
        get_session_factory: session_factory_for(session),
    }
    for dependency, value in fakes.items():
        application.dependency_overrides[dependency] = _provide(value)
    return application


def session_factory_for(session: AsyncSession):
    """A session factory that hands out the test's own (savepoint-wrapped) session."""

    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _provide(value: object):
    # A zero-argument closure: FastAPI would treat a defaulted lambda arg as a query param.
    def dependency() -> object:
        return value

    return dependency


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_stt() -> FakeSTT:
    return FakeSTT()


@pytest.fixture
def fake_tts() -> FakeTTS:
    return FakeTTS()


@pytest.fixture
def fake_embeddings() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
def task_runner() -> InlineTaskRunner:
    return InlineTaskRunner()


async def register_and_login(
    client: httpx.AsyncClient, email: str = "friend@example.com", **profile: object
) -> dict[str, str]:
    """Create a user and return Authorization headers for them."""
    password = "a-good-password"
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, **profile}
    )
    assert response.status_code == 201, response.text
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
async def db_client(
    infra_settings: Settings,
    db_session: AsyncSession,
    redis_client: RedisClient,
    fake_llm: FakeLLM,
    fake_stt: FakeSTT,
    fake_tts: FakeTTS,
    fake_embeddings: FakeEmbeddings,
    task_runner: InlineTaskRunner,
) -> AsyncIterator[httpx.AsyncClient]:
    application = build_app(
        infra_settings,
        db_session,
        redis_client,
        fake_llm,
        stt=fake_stt,
        tts=fake_tts,
        embeddings=fake_embeddings,
        runner=task_runner,
    )
    async for ac in _client_for(application):
        yield ac
