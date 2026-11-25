# Personal AI Voice Companion — Backend

Backend for a friendly, multilingual (English / Hindi / Hinglish) AI voice companion that
remembers context and can do things for its user. It uses FastAPI, PostgreSQL + pgvector,
Redis and the OpenAI API. **This repository is the backend only; there is no frontend.**

> **Status: all 8 phases complete:** foundation, auth & users, AI conversation, voice, memory,
> agent tools, real-time WebSocket voice, production readiness. See [docs/status.md](docs/status.md)
> for what exists today and what is planned, and [docs/deployment.md](docs/deployment.md) to
> deploy it. Features not marked as implemented there do not exist yet.

## Requirements

- Python 3.12+ (developed on 3.14; the Docker image uses 3.13)
- Docker with Docker Compose v2 (Podman Compose also works)

## Quick start (local Python + dockerized infrastructure)

```bash
# 1. Virtual environment and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 2. Configuration
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into SECRET_KEY in .env
# Set OPENAI_API_KEY, then choose a chat model your account has:
python -m scripts.list_openai_models   # free call; set OPENAI_CHAT_MODEL, OPENAI_STT_MODEL,
                                       # OPENAI_TTS_MODEL, OPENAI_TTS_VOICE, OPENAI_EMBEDDING_MODEL

# 3. Start PostgreSQL (pgvector) and Redis
docker compose up -d db redis

# 4. Run migrations
alembic upgrade head

# 5. Start the API (auto-reload)
uvicorn app.main:app --reload --port 8000
```

Then open:

- Swagger UI: <http://localhost:8000/docs> (disabled when `APP_ENV=production`)
- Liveness: `curl localhost:8000/api/v1/health`
- Readiness: `curl localhost:8000/api/v1/ready` → `200 {"status":"ready",...}` or `503`

## Trying the API

### Swagger

1. Open <http://localhost:8000/docs>.
2. `POST /api/v1/auth/register` with `{"email": "...", "password": "at-least-8-chars"}`.
3. `POST /api/v1/auth/login` and copy `access_token`.
4. Click **Authorize**, paste the token (without the `Bearer ` prefix), and call `/users/me`.

### curl

```bash
B=localhost:8000/api/v1
curl -s -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-good-password","timezone":"Asia/Kolkata"}' $B/auth/register
TOKEN=$(curl -s -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-good-password"}' $B/auth/login | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" $B/users/me
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"preferred_language":"hinglish"}' $B/users/me
```

### Talking to the companion

```bash
# New conversation (omit conversation_id)
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"message":"Yaar, aaj ka din bahut lamba tha"}' $B/chat
# Continue it
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"conversation_id":"<id from above>","message":"Haan, office mein bahut kaam tha"}' $B/chat
# Streaming (Server-Sent Events)
curl -N -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"message":"Tell me a fun fact"}' $B/chat/stream
```

The SSE stream sends `start` `{conversation_id}`, then `delta` `{text}` events, then either
`done` (the same body as `POST /chat`) or `error` `{code, message}`. A turn is saved only when
it completes successfully, so retrying a failed turn never creates duplicates.

### Voice

```bash
# Speech-to-text only
curl -s -H "Authorization: Bearer $TOKEN" -F file=@hello.m4a $B/voice/transcribe
# Text-to-speech (streams audio; voice and speed default to your preferences)
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"text":"Arre yaar, chalo kal milte hain!"}' $B/voice/synthesize -o reply.mp3
# Complete voice turn: audio in, transcript + reply + spoken reply out
curl -s -H "Authorization: Bearer $TOKEN" -F file=@hello.m4a \
  -F conversation_id=<optional> -F response_format=mp3 $B/voice/turn > turn.json
python3 -c "import json,base64;d=json.load(open('turn.json'));open('reply.mp3','wb').write(base64.b64decode(d['audio']['data_base64']))"
```

Uploads can be flac, mp3, mp4/m4a, ogg, wav or webm, up to `MAX_AUDIO_SIZE_MB`. The format is
detected from the file's bytes; its name and claimed type are ignored. Raw audio is **not
stored** unless `AUDIO_RETENTION_ENABLED=true`. In that case it's written under
`AUDIO_STORAGE_PATH` and deleted along with its conversation.

### Memory

The companion remembers important things you share, such as your name, goals, preferences,
projects and anything you ask it to remember, and brings them up naturally in later
conversations. This happens in the background after each reply, so it never slows a reply down.

```bash
curl -s -H "Authorization: Bearer $TOKEN" $B/memories                       # what it remembers
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"content":"I prefer short answers","category":"preference"}' $B/memories
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"what do I like to drink"}' $B/memories/search              # by meaning
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" "$B/memories?confirm=true"   # forget everything
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"memory_enabled":false}' $B/users/me/preferences                    # turn memory off
```

With memory off, nothing is recalled or saved. Existing memories stay until you delete them.
Passwords, PINs, card and ID numbers are never saved.

### Actions (tools)

The companion can act on requests made in plain language, in English, Hindi or Hinglish:
- **Reminders:** create, list, change or delete them ("Kal shaam 6 baje gym yaad dila dena").
- **To-do list:** add, list, update or delete tasks.
- **Settings:** change voice, speech speed, language or memory.
- **Memory:** look up or forget specific things it remembers.
- **Web search:** only if `WEB_SEARCH_PROVIDER=openai` is set.

Times are resolved in your timezone. If something is unclear ("remind me later"), it asks
instead of guessing.

**Confirmations.** Destructive actions (deleting, or turning memory off) are not carried out
straight away. The chat response includes an `actions` entry with
`status: "awaiting_confirmation"` and a `confirmation` object. Approve it with
`POST /tools/confirmations/{id}/approve`, or just say "yes" / "haan, kar do" in the next
message. A conversational approval only works when your actual message is an explicit yes,
so the model can't approve on your behalf. Confirmations expire after
`CONFIRMATION_TTL_MINUTES`.

Every action, including rejected attempts, is logged and visible at `GET /tools/executions`.

**Reminders are stored, not delivered.** There's no push or notification channel yet, because
no client app exists. They're listed by `GET /reminders`, and the companion is told not to
promise alarms.

### Voice test page (browser)

In development, open **<http://localhost:8000/voice-test>** (Chrome or Firefox):

1. Register or log in.
2. Click **Connect**.
3. Hold the button (or hold Space) and speak in English, Hindi or Hinglish, then release.

Mira's reply streams in as text and plays as she speaks. Other ways to use it:
- Tick **Hands-free** to have speech detected automatically. Use headphones, so Mira doesn't
  hear herself.
- Talk over her to interrupt.
- Type messages in the box at the bottom.
- Approve or reject confirmation requests with buttons.
- Choose Mira's voice and speed: **Preview** plays a sample, and **Save as my voice** stores
  it in your preferences, so every reply uses it, voice turns included.

Each turn shows its latency.

This is a single static file ([app/static/voice-test.html](app/static/voice-test.html)) that uses
the public WebSocket API. It's a developer tool, not the product frontend. It is never served
when `APP_ENV=production`, and `TEST_UI_ENABLED=false` turns it off elsewhere too. The
microphone needs `localhost` or HTTPS.

### Real-time voice (WebSocket)

`ws://localhost:8000/api/v1/ws/voice/{conversation_id|new}` carries a continuous voice
conversation:
- **Streaming:** the user's audio streams in, the transcript and the companion's reply stream
  back as text, and its **speech streams sentence by sentence**.
- **Barge-in:** talking over the companion cancels its reply instantly.

The full event protocol, close codes and what is and isn't truly real-time are in
[docs/websocket-protocol.md](docs/websocket-protocol.md).

### Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/ready` | – | Postgres + pgvector + Redis; 503 if any is down |
| POST | `/auth/register` | – | 201 → public user. Rate-limited |
| POST | `/auth/login` | – | → `{access_token, refresh_token, token_type, expires_in}`. Rate-limited |
| POST | `/auth/refresh` | – | Body `{refresh_token}`. Rotates: the old refresh token stops working |
| POST | `/auth/logout` | Bearer | Body `{refresh_token?}`. Revokes the access token and the given refresh token. 204 |
| GET | `/auth/me` | Bearer | Identity |
| GET/PATCH | `/users/me` | Bearer | User + profile (`display_name`, `preferred_language`, `timezone`) |
| GET/PATCH | `/users/me/preferences` | Bearer | `personality`, `voice`, `speech_speed`, `memory_enabled`, `conversation_retention_days` |
| POST | `/chat` | Bearer | `{message, conversation_id?}` → `{conversation, user_message, assistant_message}`. Per-user rate limit |
| POST | `/chat/stream` | Bearer | Same body; SSE response |
| POST | `/conversations` | Bearer | `{title?}` → 201 |
| GET | `/conversations` | Bearer | `?status=active\|archived&limit&offset`; most recent first |
| GET/PATCH/DELETE | `/conversations/{id}` | Bearer | PATCH `{title?, status?}`. DELETE removes the conversation and all its messages |
| GET | `/conversations/{id}/messages` | Bearer | `?limit&offset`; oldest first |
| POST | `/voice/transcribe` | Bearer | multipart `file` → `{text, duration_seconds}`. Nothing stored |
| POST | `/voice/synthesize` | Bearer | `{text, voice?, speed?, format?}` → streamed audio |
| GET | `/memories` | Bearer | `?category&limit&offset`; most recently updated first |
| POST | `/memories` | Bearer | `{content, category?, importance?}` → 201 |
| GET/PATCH/DELETE | `/memories/{id}` | Bearer | PATCH `{content?, category?, importance?}` |
| DELETE | `/memories?confirm=true` | Bearer | Delete all memories → `{deleted}` |
| POST | `/memories/search` | Bearer | `{query, limit?}` → `{results: [{memory, similarity}]}` |
| POST | `/reminders` | Bearer | `{title, scheduled_at (ISO 8601 with offset), description?}` → 201 |
| GET | `/reminders` | Bearer | `?status&from&to&limit&offset`; soonest first |
| GET/PATCH/DELETE | `/reminders/{id}` | Bearer | PATCH `{title?, scheduled_at?, description?, status?}` |
| POST | `/tasks` | Bearer | `{title, description?, due_date?}` → 201 |
| GET | `/tasks` | Bearer | `?status&limit&offset`; earliest due first |
| GET/PATCH/DELETE | `/tasks/{id}` | Bearer | PATCH `{title?, description?, status?, due_date?}` |
| GET | `/tools` | Bearer | Available actions and whether each needs confirmation |
| GET | `/tools/executions` | Bearer | Audit log of actions, `?status&limit&offset` |
| GET | `/tools/confirmations` | Bearer | Actions awaiting your approval |
| POST | `/tools/confirmations/{id}/approve` · `/reject` | Bearer | Decide on a pending action |
| WS | `/ws/voice/{conversation_id\|new}` | `session.start` token or Bearer header | Real-time voice; see docs/websocket-protocol.md |
| POST | `/voice/turn` | Bearer | multipart `file`, `conversation_id?`, `response_format?` → `{conversation, user_message, assistant_message, audio{format,mime_type,data_base64}, audio_error, timings}` |

All paths are under `/api/v1`. Errors always have the shape `{"error": {"code", "message", "request_id"}}`.

## Run everything in Docker

```bash
cp .env.example .env    # set SECRET_KEY first
docker compose up -d --build
curl localhost:8000/api/v1/ready
docker compose logs -f api
```

The `api` container runs `alembic upgrade head` before starting Uvicorn. Inside the Compose
network it talks to the `db` and `redis` services by name, which overrides the host-oriented
`DATABASE_URL` and `REDIS_URL` in `.env`. The `maintenance` container runs the housekeeping
loop (see [Operations](#operations)).

For production, layer `docker-compose.prod.yml` on top of the base file. It sets
`APP_ENV=production`, stops publishing the database, and requires `POSTGRES_PASSWORD` and
`FORWARDED_ALLOW_IPS`. See [docs/deployment.md](docs/deployment.md):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Ports

| Service    | Host address        | Notes                                                      |
|------------|---------------------|------------------------------------------------------------|
| API        | `127.0.0.1:8000`    | Change with `API_PORT`                                     |
| PostgreSQL | `127.0.0.1:5432`    | Change with `POSTGRES_PORT`                                |
| Redis      | `127.0.0.1:6380`    | Not 6379, so it doesn't clash with a host Redis. Change with `REDIS_PORT` |

All ports bind to localhost only.

### Fedora / SELinux

The Postgres init-script mount uses the `:Z` option so SELinux allows the container to read it.
If you use Podman, `podman compose up -d` works with the same file.

## Tests

```bash
pytest                     # everything; integration tests skip if Postgres isn't running
pytest -m "not integration"   # unit + API tests only, no infrastructure needed
pytest -m integration         # real PostgreSQL + Redis (needs `docker compose up -d db redis`)
ruff check . && ruff format --check .
```

- Integration tests use a separate `companion_test` database (created automatically the first
  time the Postgres volume starts) and Redis DB 15. Override them with `TEST_DATABASE_URL` and
  `TEST_REDIS_URL`.
- Each infrastructure-backed test runs inside a transaction that is rolled back, so nothing
  persists between tests. Any test that uses these fixtures is automatically marked
  `integration`.
- No test ever calls OpenAI or any other paid API. The OpenAI provider is tested through the
  real SDK against a mocked HTTP transport.
- Check compatibility with the Docker image's Python version (3.13):
  ```bash
  docker run --rm --network host -v "$PWD":/src:ro,Z -w /src -e PYTHONPYCACHEPREFIX=/tmp/pyc \
    python:3.13-slim sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"
  ```

### Manual test with the real OpenAI API

This costs a few cents and needs `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL` set.

```bash
python -m scripts.live_chat_smoke            # 6-turn English/Hinglish/Hindi conversation
python -m scripts.live_chat_smoke --stream   # same, streamed
python -m scripts.live_voice_smoke           # TTS → STT → companion → TTS round trip; saves mp3s
python -m scripts.live_memory_smoke          # against a running server: remember, recall, correct, forget
python -m scripts.live_tools_smoke           # against a running server: reminders, tasks, confirmations
python -m scripts.live_voice_ws_smoke --audio-dir DIR   # real WebSocket: latency, barge-in, tools
```

## Operations

The `maintenance` Compose service runs housekeeping every `MAINTENANCE_INTERVAL_MINUTES`
(default 6 h):

- the conversation retention purge
- deleting expired refresh tokens
- expiring stale confirmations
- deleting `tool_executions` older than `TOOL_EXECUTION_RETENTION_DAYS`

To run it by hand:

```bash
python -m scripts.maintenance            # one pass (also fine from cron)
docker compose run --rm maintenance python -m scripts.maintenance
python -m scripts.purge_conversations    # retention purge only
python -m scripts.load_check --base-url http://127.0.0.1:8000   # non-AI load check (throwaway stack)
```

Backups, monitoring, upgrades and the security checklist are in
[docs/deployment.md](docs/deployment.md).

## Configuration

All settings come from environment variables or `.env`. See [.env.example](.env.example).
`SECRET_KEY` (≥ 32 chars) and `DATABASE_URL` (must use `postgresql+asyncpg://`) are required.
OpenAI model names are intentionally blank in `.env.example`. Set them to models your
account has (`python -m scripts.list_openai_models`). The companion's name is `COMPANION_NAME`
(default `Mira`).

## Migrations

```bash
alembic upgrade head                          # apply
alembic revision --autogenerate -m "message"  # new migration after model changes
alembic downgrade -1                          # roll back one
alembic -x db_url=postgresql+asyncpg://... upgrade head   # target another database
```

## Project layout

```
app/
  main.py            app factory, lifespan, middleware wiring
  core/              config, database, redis, logging, middleware, exceptions
  api/               dependencies, router, routes/ (thin HTTP handlers), websocket/
  ai/                companion engine: agent, personality, prompts, context, language, OpenAI client
  audio/             voice engine: validation, STT, TTS, streaming helpers, optional audio storage
  memory/            memory engine: embeddings, retrieval, extraction, summaries
  tools/             tool engine: registry, strict schemas, executor, confirmations, tools
  services/          business logic (auth, users, conversations, chat, voice, memories, reminders/tasks, tool actions)
  schemas/           Pydantic request/response models
  models/            SQLAlchemy ORM models (users, profiles, preferences, refresh tokens, conversations, messages)
  repositories/      data access (PostgreSQL, Redis)
migrations/          Alembic (async)
docker/postgres/     DB init scripts
scripts/             start.sh (container entrypoint), maintenance, purge, load check, model listing, live smoke tests
tests/{unit,api,integration}
docs/                architecture, status, deployment, WebSocket protocol
```

Further documentation:

- [docs/architecture.md](docs/architecture.md): design and conventions
- [docs/status.md](docs/status.md): implementation status per phase
- [docs/websocket-protocol.md](docs/websocket-protocol.md): real-time voice protocol
- [docs/deployment.md](docs/deployment.md): production deployment and operations
