"""WebSocket endpoint for real-time voice: /api/v1/ws/voice/{conversation_id | "new"}.

Thin transport layer: accept → per-IP connect limit → authenticate → per-user session
cap → pump frames into ``VoiceSession``, closing when the access token expires or is
revoked. The protocol is documented in docs/websocket-protocol.md.

Frame sizes are bounded twice: uvicorn's ``--ws-max-size`` (scripts/start.sh) rejects
oversized frames before they're buffered, and the checks here cover other servers.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.dependencies import RealtimeDepsDep, client_ip
from app.core.exceptions import AuthenticationError, ServiceUnavailableError
from app.core.rate_limit import RateLimiter
from app.schemas.realtime import AuthRefresh, CloseCode, SessionStart, client_message_adapter
from app.services.voice_session import (
    REVOCATION_CHECK_SECONDS,
    RealtimeDeps,
    Sender,
    SessionSlots,
    SocketAuth,
    VoiceSession,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


async def _fail(websocket: WebSocket, code: int, error: str, message: str) -> None:
    if websocket.client_state is WebSocketState.CONNECTED:
        with contextlib.suppress(Exception):  # client already gone
            await websocket.send_json(
                {"type": "error", "code": error, "message": message, "fatal": True}
            )
            await websocket.close(code=code, reason=error)


def _bearer(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def _receive(websocket: WebSocket, wait_seconds: float) -> dict[str, Any]:
    return await asyncio.wait_for(websocket.receive(), timeout=wait_seconds)


async def _connect_allowed(websocket: WebSocket, deps: RealtimeDeps) -> bool:
    if not deps.settings.rate_limit_enabled:
        return True
    result = await RateLimiter(deps.redis.client).hit(
        f"ws:connect:{client_ip(websocket)}",
        limit=deps.settings.ws_connect_rate_limit_per_minute,
        window_seconds=60,
    )
    return result.allowed


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "fatal": False}


async def _refresh(auth: SocketAuth, message: AuthRefresh, deps: RealtimeDeps) -> SocketAuth | None:
    """A fresh token for the same user, or None if it's invalid or belongs to someone else."""
    try:
        fresh = await deps.authenticate(message.token)
    except (AuthenticationError, ServiceUnavailableError):
        return None
    return fresh if fresh.user_id == auth.user_id else None


@router.websocket("/ws/voice/{conversation_ref}")
async def voice_socket(websocket: WebSocket, conversation_ref: str, deps: RealtimeDepsDep) -> None:
    settings = deps.settings
    await websocket.accept()
    opened = time.perf_counter()
    if not await _connect_allowed(websocket, deps):
        await _fail(
            websocket, CloseCode.TOO_MANY_SESSIONS, "rate_limited", "Too many connection attempts."
        )
        return

    if conversation_ref == "new":
        conversation_id = None
    else:
        try:
            conversation_id = uuid.UUID(conversation_ref)
        except ValueError:
            await _fail(websocket, CloseCode.NOT_FOUND, "not_found", "Conversation not found.")
            return

    # 1. The first message must be session.start (it may carry the access token).
    try:
        first = await _receive(websocket, settings.ws_auth_timeout_seconds)
    except TimeoutError:
        await _fail(websocket, CloseCode.AUTH_TIMEOUT, "auth_timeout", "Send session.start first.")
        return
    if first.get("type") == "websocket.disconnect":
        return
    try:
        text = first.get("text") or ""
        if len(text) > 16 * 1024:  # session.start is tiny; don't parse a huge pre-auth frame
            raise ValueError("session.start too large")
        config = SessionStart.model_validate_json(text)
    except (ValidationError, ValueError):
        await _fail(
            websocket,
            CloseCode.PROTOCOL_ERROR,
            "protocol_error",
            "The first message must be a valid session.start.",
        )
        return

    # 2. Authenticate (Authorization header for native clients, else token in session.start).
    token = _bearer(websocket) or config.token
    try:
        if not token:
            raise AuthenticationError()
        auth = await deps.authenticate(token)
    except (AuthenticationError, ServiceUnavailableError):
        await _fail(websocket, CloseCode.UNAUTHORIZED, "unauthenticated", "Authentication failed.")
        return

    user_id = auth.user_id

    # 3. Per-user concurrent session cap.
    slots = SessionSlots(deps.redis, settings.ws_max_sessions_per_user)
    slot_id = uuid.uuid4().hex
    if not await slots.acquire(user_id, slot_id):
        await _fail(
            websocket,
            CloseCode.TOO_MANY_SESSIONS,
            "too_many_sessions",
            "Too many open voice sessions.",
        )
        return

    sender = Sender(websocket.send_json, websocket.send_bytes)
    session = VoiceSession(sender, deps, user_id, conversation_id, config)
    deadline = opened + settings.ws_max_session_minutes * 60
    close_code, reason = CloseCode.NORMAL, "session_end"
    logger.info("voice session opened", extra={"fields": {"session_id": session.id}})
    try:
        if not await session.start():
            close_code, reason = CloseCode.NOT_FOUND, "not_found"
            return
        last_frame = last_renewal = time.monotonic()
        while True:
            if time.monotonic() - last_renewal >= REVOCATION_CHECK_SECONDS:
                await slots.renew(user_id, slot_id)  # keep our lease on the session slot
                last_renewal = time.monotonic()
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                close_code, reason = CloseCode.NORMAL, "max_duration"
                break
            if auth.seconds_left() <= 0:
                close_code, reason = CloseCode.UNAUTHORIZED, "token_expired"
                break
            due = time.monotonic() - auth.checked_at >= REVOCATION_CHECK_SECONDS
            if due and await deps.token_revoked(auth):  # e.g. the user logged out elsewhere
                close_code, reason = CloseCode.UNAUTHORIZED, "token_revoked"
                break
            idle_left = settings.ws_idle_timeout_seconds - (time.monotonic() - last_frame)
            if idle_left <= 0:
                close_code, reason = CloseCode.NORMAL, "idle_timeout"
                break
            wait = min(idle_left, remaining, auth.seconds_left(), REVOCATION_CHECK_SECONDS)
            try:
                frame = await _receive(websocket, max(wait, 0.01))
            except TimeoutError:
                continue  # re-check idle time, the session deadline and the token above
            last_frame = time.monotonic()
            if frame.get("type") == "websocket.disconnect":
                reason = "client_disconnected"
                break
            if frame.get("bytes") is not None:
                await session.on_audio(frame["bytes"])
                continue
            text = frame.get("text") or ""
            if len(text) > settings.ws_max_frame_bytes:
                await sender.json(_error("frame_too_large", "Message too large."))
                continue
            try:
                message = client_message_adapter.validate_python(json.loads(text))
            except (ValueError, ValidationError):
                await sender.json(_error("invalid_message", "Unrecognised or invalid message."))
                continue
            if isinstance(message, AuthRefresh):
                if fresh := await _refresh(auth, message, deps):
                    auth = fresh
                    await sender.json(
                        {"type": "auth.refreshed", "expires_at": auth.expires_at.isoformat()}
                    )
                else:
                    await sender.json(_error("unauthenticated", "That token isn't valid."))
                continue
            if not await session.on_message(message):
                break
    except WebSocketDisconnect:
        reason = "client_disconnected"
    except Exception:
        logger.exception("voice session crashed")
        close_code, reason = CloseCode.INTERNAL_ERROR, "internal_error"
    finally:
        await session.shutdown()
        await slots.release(user_id, slot_id)
        logger.info(
            "voice session closed",
            extra={
                "fields": {
                    "session_id": session.id,
                    "reason": reason,
                    "turns": session.turns,
                    "interruptions": session.interruptions,
                    "duration_s": round(time.perf_counter() - opened, 1),
                }
            },
        )
        if websocket.client_state is WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):  # client already gone
                await websocket.send_json({"type": "session.closed", "reason": reason})
                await websocket.close(code=close_code, reason=reason)
