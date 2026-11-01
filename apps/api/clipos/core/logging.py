from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="unbound"
)

_SECRET_KEY_PATTERN = re.compile(
    r"(secret|token|password|authorization|api[_-]?key|cookie|credential|code_verifier)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:code|state|access_token|refresh_token|client_secret|code_verifier)=)[^&#\s]+"
)
_EMBEDDED_SECRET_PATTERN = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|client_secret|code_verifier|authorization)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^,}\s]+)"
)


def _sanitize_string(value: str) -> str:
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    redacted = _QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _EMBEDDED_SECRET_PATTERN.sub(r"\1[REDACTED]", redacted)
    return redacted[:2000] + ("…" if len(redacted) > 2000 else "")


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Best-effort structured redaction for logs and audit metadata."""
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SECRET_KEY_PATTERN.search(str(key))
            else sanitize(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:2000]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize_string(record.getMessage()),
            "correlation_id": correlation_id_var.get(),
        }
        extras = getattr(record, "event_data", None)
        if extras:
            payload["data"] = sanitize(extras)
        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
                "message": "internal error; inspect secured server logs",
            }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
