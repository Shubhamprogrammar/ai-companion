#!/bin/sh
# Container entrypoint: apply migrations, then start the API server.
#
# Environment (all optional):
#   RUN_MIGRATIONS=false        skip `alembic upgrade head` (e.g. when running several
#                               replicas: run `alembic upgrade head` once as a release step)
#   WEB_CONCURRENCY=N           uvicorn worker processes (default 1)
#   FORWARDED_ALLOW_IPS=...     proxies trusted for X-Forwarded-For/-Proto (default
#                               127.0.0.1). Set it to your reverse proxy's address, or
#                               client IPs (rate limits, audit logs) are the proxy's.
#   GRACEFUL_SHUTDOWN_SECONDS   time given to in-flight requests on SIGTERM (default 20)
#
# WebSocket messages are capped at WS_MAX_FRAME_BYTES (+ JSON overhead) by uvicorn itself,
# so oversized frames are refused before being buffered.
set -e
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    alembic upgrade head
fi
exec uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    --proxy-headers \
    --no-server-header \
    --ws-max-size "$(( ${WS_MAX_FRAME_BYTES:-524288} + 16384 ))" \
    --timeout-graceful-shutdown "${GRACEFUL_SHUTDOWN_SECONDS:-20}"
