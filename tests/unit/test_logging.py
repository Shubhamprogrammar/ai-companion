import json
import logging

from app.core.logging import JsonFormatter, RequestIdFilter, request_id_ctx


def _format(record_fields: dict) -> dict:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    record.fields = record_fields
    RequestIdFilter().filter(record)
    return json.loads(JsonFormatter().format(record))


def test_json_log_includes_request_id() -> None:
    token = request_id_ctx.set("req-123")
    try:
        payload = _format({"path": "/x"})
    finally:
        request_id_ctx.reset(token)
    assert payload["request_id"] == "req-123"
    assert payload["message"] == "hello"
    assert payload["path"] == "/x"


def test_sensitive_fields_are_redacted() -> None:
    payload = _format({"password": "hunter2", "access_token": "abc", "user": "u1"})
    assert payload["password"] == "[REDACTED]"
    assert payload["access_token"] == "[REDACTED]"
    assert payload["user"] == "u1"
