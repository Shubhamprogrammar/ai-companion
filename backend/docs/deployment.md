# Deployment and operations

How to run the backend in production with Docker Compose on a single host, behind a
TLS-terminating reverse proxy. For local development, see the [README](../README.md).

```
client ──HTTPS/WSS──▶ reverse proxy (TLS) ──▶ 127.0.0.1:8000 api ──▶ db (Postgres + pgvector)
                                                               └──▶ redis
                                              maintenance (same image, --loop)
```

## 1. Prepare the host

- Docker Engine with Compose v2.24 or newer (the production file uses `!reset` and
  `!override`).
- The machine needs outbound HTTPS to `api.openai.com`.
- Sizing: an API container with 2 workers idles at about 200–250 MB (4 workers: about 410 MB). Postgres and Redis are small
  until you have many users. Two vCPUs and 2 GB of RAM are enough to start.

## 2. Configure

```bash
cp .env.example .env
chmod 600 .env
```

Set at least the following. Everything else has a safe default.

| Variable | Production value |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Changing it signs everyone out. |
| `POSTGRES_PASSWORD` | A long random value. Use URL-safe characters, since it is embedded in `DATABASE_URL`. |
| `OPENAI_API_KEY` | Your key. It is only ever read from the environment. |
| `OPENAI_CHAT_MODEL`, `OPENAI_STT_MODEL`, `OPENAI_TTS_MODEL`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_TTS_VOICE` | Models your account can use (`python -m scripts.list_openai_models`). |
| `CORS_ORIGINS` | Your frontend origins, e.g. `["https://app.example.com"]`. A wildcard is refused. |
| `TRUSTED_HOSTS` | `["api.example.com"]`. Requests with any other `Host` header get a 400. |
| `FORWARDED_ALLOW_IPS` | The proxy's address as the container sees it (see below). Required. |

You don't set `APP_ENV`. The production Compose file sets it to `production`, which:

- turns off `/docs`, `/redoc` and `/openapi.json`;
- **refuses to start** if a setting is unsafe: wildcard CORS, `DB_ECHO=true`,
  `LOG_LEVEL=DEBUG`, `RATE_LIMIT_ENABLED=false` or `OPENAI_STORE_RESPONSES=true`.

Missing OpenAI settings are reported at startup as `configuration warning` log lines. The
affected features return 503 until you fix them.

### Client IPs and `FORWARDED_ALLOW_IPS`

Rate limits, the WebSocket connection limit and audit logs all use the client's IP address.
Behind a proxy, Uvicorn takes the real IP from `X-Forwarded-For`, but only from addresses
listed in `FORWARDED_ALLOW_IPS`.

- **Proxy on the host, talking to `127.0.0.1:8000`:** requests reach the container from the
  Docker bridge gateway. Find its address with
  `docker network inspect personal-ai-companion_default -f '{{(index .IPAM.Config 0).Gateway}}'`,
  e.g. `172.18.0.1`, and set `FORWARDED_ALLOW_IPS` to that.
- **Proxy as another container on the same network:** use that container's IP or its subnet.
- **Never use `*`.** It lets any client fake its IP and get around the rate limits.

Verified during Phase 8:
- With the default trust setting, a spoofed `X-Forwarded-For` header was ignored and every
  client appeared as the gateway, so they all shared one rate-limit bucket.
- With the gateway trusted, the forwarded client IP came through.

## 3. Reverse proxy

The proxy terminates TLS and adds HSTS. It must also:

- pass WebSocket upgrades;
- not buffer SSE (`/chat/stream`);
- allow uploads of about 21 MB (`/voice`);
- keep idle connections open longer than `WS_IDLE_TIMEOUT_SECONDS`.

nginx example:

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;
    # ssl_certificate ... (e.g. certbot)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    client_max_body_size 21m;          # MAX_AUDIO_SIZE_MB + multipart overhead

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 180s;       # > WS_IDLE_TIMEOUT_SECONDS and slow AI replies
    }
    location /api/v1/chat/stream {     # Server-Sent Events: don't buffer
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_read_timeout 180s;
    }
    location /api/v1/ws/ {             # WebSocket voice
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 180s;
    }
}
```

Caddy does all of this by default: `api.example.com { reverse_proxy 127.0.0.1:8000 }`.

## 4. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -s https://api.example.com/api/v1/ready
```

What the production overlay changes:

| | Development (`docker-compose.yml`) | Production (`+ docker-compose.prod.yml`) |
|---|---|---|
| `APP_ENV` | from `.env` | `production` |
| Postgres, Redis | published on `127.0.0.1` | not published at all |
| `POSTGRES_PASSWORD` | defaults to `postgres` | required |
| Test database init script | mounted | not mounted |
| API and maintenance containers | normal | read-only root filesystem, all capabilities dropped, `no-new-privileges` |
| Workers | 1 | `WEB_CONCURRENCY`, default 2 |

On start, the API container runs `alembic upgrade head` (skip it with `RUN_MIGRATIONS=false`).
It then starts Uvicorn with these settings:
- `--proxy-headers`
- `--no-server-header`
- a WebSocket message limit of `WS_MAX_FRAME_BYTES` plus a small allowance
- a graceful-shutdown period (`GRACEFUL_SHUTDOWN_SECONDS`, default 20; Compose waits up to 30 s)

**Audio retention:** with a read-only filesystem, `AUDIO_RETENTION_ENABLED=true` needs a
writable volume mounted at `AUDIO_STORAGE_PATH`. Retention is off by default, and the
recommended setting is to keep it off.

## 5. Maintenance (runs automatically)

The `maintenance` service runs `python -m scripts.maintenance --loop` every
`MAINTENANCE_INTERVAL_MINUTES` (default 6 h). A Redis lock stops two runs from overlapping.
Each run:

| Step | Rule |
|---|---|
| Conversation retention | Deletes conversations idle longer than each user's `conversation_retention_days`, along with their summary memories and any retained audio. |
| Refresh tokens | Deletes rows past their expiry. Revoked tokens that haven't expired yet are kept, because reuse detection needs them. |
| Stale confirmations | Marks `awaiting_confirmation` actions past their TTL as `expired`. |
| Tool audit log | Deletes `tool_executions` older than `TOOL_EXECUTION_RETENTION_DAYS` (default 90; leave blank to keep forever). These rows can contain personal details. |

Each run logs one `maintenance completed` line with the counts. To run it by hand:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm maintenance python -m scripts.maintenance
```

## 6. Backups

The data that matters is in Postgres: users, conversations, memories with their embeddings,
reminders and tasks. Redis holds only short-lived state (rate limits, the access-token
deny-list, session slots) and can be lost safely.

```bash
# nightly (cron): compressed logical dump
docker compose exec -T db pg_dump -U postgres -Fc companion > backup-$(date +%F).dump
# restore into an empty database
docker compose exec -T db pg_restore -U postgres -d companion --clean --if-exists < backup.dump
```

Backups contain personal data. Encrypt them, keep them off the host, and test a restore.

## 7. Monitoring

- **Liveness:** `GET /api/v1/health` (the container healthcheck uses it).
- **Readiness:** `GET /api/v1/ready` checks Postgres with pgvector, and Redis. It returns 503
  if either fails.
- **Logs:** one JSON object per line on stdout, each with a `request_id`. Clients can send
  their own `X-Request-ID`.
  - Useful lines: `request completed` (status, `duration_ms`), `llm`/`stt`/`tts`/`embedding
    request completed` (model, latency), `voice session closed` (reason, turns, duration)
    and `maintenance completed`.
  - Logs never contain message text, transcripts, passwords or tokens. Database errors hide
    their bound parameters.
- **Audit events:** logger `app.audit`. Ship these to longer-term storage:
  - authentication: `auth.login_failed`, `auth.login_locked`,
    `auth.refresh_token_reuse_detected`
  - confirmations: `tool.confirmation_requested`, `tool.confirmation_approved`
  - housekeeping: `conversation.retention_purged`, `tool_executions.pruned`
- **Alerts worth setting up:**
  - `/ready` is not 200
  - a rising rate of 5xx responses
  - `auth.refresh_token_reuse_detected` (a possible stolen token)
  - repeated `auth.login_locked`
  - OpenAI errors: `llm`/`stt`/`tts`/`embedding request failed` lines with `"provider": "openai"`

## 8. Upgrades and rollback

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build   # migrates on start
```

To roll back, check out the previous version and run `alembic downgrade <revision>` inside the
container before restarting. Take a backup first. Downgrades that drop tables lose data.

**Running more than one API replica:** set `RUN_MIGRATIONS=false` on the replicas and run
`alembic upgrade head` once as a release step, so replicas don't migrate at the same time.

## 9. Scaling notes

- **Workers:** set `WEB_CONCURRENCY` to roughly the number of CPU cores. Each worker has its
  own database pool of `DB_POOL_SIZE + DB_MAX_OVERFLOW` connections (15 by default), so keep
  the total under Postgres's `max_connections`, which is 100.
- **Shared state:** everything shared between workers or replicas lives in Redis: rate limits,
  the token deny-list, WebSocket session slots and the maintenance lock.
- **Background memory work:** extraction and summaries run inside the process that handled
  the request, so shutting down a worker waits for its pending jobs to finish.
- **Measured capacity** (Phase 8, everything on one 4-core laptop, non-AI endpoints only):
  - one worker served about 150–160 requests/s, with a median latency of 63 ms at 10
    concurrent clients;
  - 4 workers sustained about 240 requests/s with 0 errors while three load generators ran on
    the same machine, which made the generators the bottleneck;
  - login (Argon2id) takes about 115–145 ms.

  Real capacity is set by the OpenAI calls: a chat turn takes seconds, while the server's own
  overhead is a few milliseconds. Repeat the measurement with
  `python -m scripts.load_check --base-url ... --concurrency 50`, against a throwaway stack,
  since it leaves a test user behind.

## 10. Security checklist

| | |
|---|---|
| ✅ | `.env` is not in git (`.gitignore`), and the image doesn't contain it (`.dockerignore`). |
| ✅ | The container runs as non-root uid 10001 with a read-only root filesystem and no capabilities. |
| ✅ | Postgres and Redis aren't reachable from outside the Compose network. |
| ✅ | Dependencies are pinned. `pip-audit` reported no known vulnerabilities (2026-11-24). |
| ✅ | Request bodies have a size limit even without `Content-Length` (chunked uploads are cut off with a 413). |
| ✅ | Failed logins are limited per IP **and** per account (`LOGIN_MAX_FAILURES` within `LOGIN_LOCKOUT_MINUTES`). |
| ✅ | WebSocket: per-IP connection limit, a cap on frame size, and sessions that close when their token expires or the user logs out. |
| ✅ | Consequential tool actions need an explicit answer from the user in a later turn (see [architecture](architecture.md#tool-engine-phase-6)). |
| ☐ | Your part: TLS with HSTS at the proxy, `TRUSTED_HOSTS`, the exact `FORWARDED_ALLOW_IPS`, encrypted off-host backups, and a firewall that exposes only 443. |
| ☐ | Rotate `OPENAI_API_KEY` and `SECRET_KEY` if you suspect either has leaked. Rotating `SECRET_KEY` signs everyone out. |
