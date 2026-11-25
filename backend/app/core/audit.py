"""Audit trail for security-relevant actions.

Emitted on the dedicated ``app.audit`` logger so it can be routed/retained
separately. Never pass passwords, tokens or message content here.
"""

import logging
from typing import Any

_audit_logger = logging.getLogger("app.audit")


def audit_event(event: str, **fields: Any) -> None:
    _audit_logger.info(event, extra={"fields": {"audit": True, "event": event, **fields}})
