"""Application error hierarchy and safe, consistent error responses.

Clients always receive ``{"error": {"code", "message", "request_id"}}``.
Internal details (stack traces, SQL, provider errors) are logged, never returned.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Resource conflict."


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class ValidationAppError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "The request is invalid."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Please slow down."


class AIUnavailableError(AppError):
    """The language model could not produce a reply (provider error, timeout, misconfiguration)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "ai_unavailable"
    message = "The companion can't reply right now. Please try again in a moment."


class SpeechUnavailableError(AppError):
    """Speech-to-text or text-to-speech provider failure."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "speech_unavailable"
    message = "Voice processing is unavailable right now. Please try again in a moment."


class AudioTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "audio_too_large"
    message = "The audio file is too large."


class UnsupportedAudioError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_audio_format"
    message = "Unsupported audio format. Use flac, mp3, mp4/m4a, ogg, wav or webm."


class InvalidAudioError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_audio"
    message = "The audio could not be processed."


class NoSpeechError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "no_speech_detected"
    message = "No speech was detected in the audio."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A required service is temporarily unavailable."


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_ctx.get()}
    if details is not None:
        body["details"] = details
    return {"error": body}


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.error("application error", extra={"fields": {"code": exc.code}}, exc_info=exc)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
        headers=exc.headers,
    )


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(getattr(exc, "code", "http_error"), str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Strip submitted input values so secrets (e.g. passwords) are never echoed back.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_error_body("validation_error", "The request is invalid.", details),
    )


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(AppError.code, AppError.message),
    )


class RequestTooLargeHTTPException(StarletteHTTPException):
    """Raised while a streamed body is being read, once it passes the size limit."""

    code = "request_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Request body too large (max {limit // (1024 * 1024)} MB).",
        )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
