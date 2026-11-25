"""HTTP middleware: request IDs and access logging with latency."""

import json
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import RequestTooLargeHTTPException
from app.core.logging import request_id_ctx

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Pure ASGI middleware (works for HTTP and WebSocket scopes).

    Accepts a well-formed incoming ``X-Request-ID`` or generates one, exposes it
    via a context variable for logging, echoes it in the response, and logs
    method/path/status/duration. Query strings and bodies are never logged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = (
            dict(scope.get("headers") or []).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        )
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["type"] == "http":
                logger.info(
                    "request completed",
                    extra={
                        "fields": {
                            "method": scope.get("method"),
                            "path": scope.get("path"),
                            "status": status_code,
                            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                        }
                    },
                )
            request_id_ctx.reset(token)


class BodySizeLimitMiddleware:
    """Bound request bodies before they're buffered in memory or spooled to disk.

    ``limits`` maps path prefixes to byte limits (longest prefix wins); others get
    ``default_limit``. A declared Content-Length over the limit is rejected before any
    of the body is read. Bodies without one (chunked transfer) are counted as they
    arrive and cut off with a 413 as soon as they pass the limit.
    """

    def __init__(self, app: ASGIApp, default_limit: int, limits: dict[str, int]) -> None:
        self.app = app
        self.default_limit = default_limit
        self.limits = sorted(limits.items(), key=lambda kv: len(kv[0]), reverse=True)

    def _limit_for(self, path: str) -> int:
        return next(
            (lim for prefix, lim in self.limits if path.startswith(prefix)), self.default_limit
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope.get("path", ""))
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            body = json.dumps(
                {
                    "error": {
                        "code": "request_too_large",
                        "message": f"Request body too large (max {limit // (1024 * 1024)} MB).",
                        "request_id": request_id_ctx.get(),
                    }
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPExceptions from body parsing; the app's
                    # handler turns it into the usual 413 error envelope.
                    raise RequestTooLargeHTTPException(limit)
            return message

        await self.app(scope, limited_receive, send)


class SecurityHeadersMiddleware:
    """Conservative response headers for a JSON API.

    ``Cache-Control: no-store`` keeps tokens and personal data out of shared caches;
    routes that set their own Cache-Control (e.g. SSE) keep it. HSTS belongs on the
    TLS-terminating proxy, not here.
    """

    _HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cross-origin-resource-policy", b"same-origin"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in self._HEADERS if h[0] not in present)
                if b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
