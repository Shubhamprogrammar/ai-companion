"""WebSocket endpoint for real-time voice: /api/v1/ws/voice/{conversation_id | "new"}.

Thin transport layer: accept → authenticate → per-user session cap → pump frames
into ``VoiceSession``. The protocol is documented in docs/websocket-protocol.md.
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

from app.api.dependencies import RealtimeDepsDep
from app.core.exceptions import AuthenticationError, ServiceUnavailableError
from app.schemas.realtime import CloseCode, SessionStart, client_message_adapter
from app.services.voice_session import Sender, SessionSlots, VoiceSession

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


@router.websocket("/ws/voice/{conversation_ref}")
async def voice_socket(websocket: WebSocket, conversation_ref: str, deps: RealtimeDepsDep) -> None:
    settings = deps.settings
    await websocket.accept()
    opened = time.perf_counter()

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
        config = SessionStart.model_validate_json(first.get("text") or "")
    except ValidationError:
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
        user_id = await deps.authenticate(token)
    except (AuthenticationError, ServiceUnavailableError):
        await _fail(websocket, CloseCode.UNAUTHORIZED, "unauthenticated", "Authentication failed.")
        return

    # 3. Per-user concurrent session cap.
    slots = SessionSlots(
        deps.redis, settings.ws_max_sessions_per_user, settings.ws_max_session_minutes * 60 + 60
    )
    if not await slots.acquire(user_id):
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
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                close_code, reason = CloseCode.NORMAL, "max_duration"
                break
            try:
                frame = await _receive(websocket, min(settings.ws_idle_timeout_seconds, remaining))
            except TimeoutError:
                close_code, reason = CloseCode.NORMAL, "idle_timeout"
                break
            if frame.get("type") == "websocket.disconnect":
                reason = "client_disconnected"
                break
            if frame.get("bytes") is not None:
                await session.on_audio(frame["bytes"])
                continue
            try:
                message = client_message_adapter.validate_python(
                    json.loads(frame.get("text") or "")
                )
            except (ValueError, ValidationError):
                await sender.json(
                    {
                        "type": "error",
                        "code": "invalid_message",
                        "message": "Unrecognised or invalid message.",
                        "fatal": False,
                    }
                )
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
        await slots.release(user_id)
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
