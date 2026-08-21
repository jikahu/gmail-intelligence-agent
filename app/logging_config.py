"""Structured logging setup.

Emits JSON-per-line log records so they are grep-able locally and parse cleanly
in the GitHub Actions run log. A small redaction layer strips fields that would
otherwise leak secrets or full email bodies:

* Any field whose name looks like a secret (``token``, ``password``, ``api_key``,
  ``client_secret``, ``authorization``, ``cookie``) is replaced with ``"***"``.
* Any field named ``body`` or ``email_body`` is truncated to 80 chars — the
  Gmail rules in ``CLAUDE.md`` §16 forbid logging full bodies.

This module is intentionally dependency-free (stdlib only) so it can be
imported before any third-party package is available.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

_SECRET_KEY_HINTS: tuple[str, ...] = (
    "token",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
)
_BODY_KEYS: frozenset[str] = frozenset({"body", "email_body", "content"})
_BODY_TRUNCATE_CHARS: int = 80

# Stdlib LogRecord attributes we should never re-emit as "extras".
_STANDARD_LOGRECORD_ATTRS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)


def _looks_like_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _redact_value(key: str, value: Any) -> Any:
    if _looks_like_secret(key):
        return "***"
    if key.lower() in _BODY_KEYS and isinstance(value, str):
        if len(value) > _BODY_TRUNCATE_CHARS:
            return value[:_BODY_TRUNCATE_CHARS] + "…(truncated)"
    return value


def _extract_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_LOGRECORD_ATTRS:
            continue
        if key.startswith("_"):
            continue
        extras[key] = _redact_value(key, value)
    return extras


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extras = _extract_extras(record)
        if extras:
            payload["extra"] = extras
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install a stdout JSON handler on the root logger.

    Safe to call multiple times — existing handlers are replaced so tests can
    reconfigure without duplicate output.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quiet down noisy libraries at INFO level.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))


def get_logger(name: str) -> logging.Logger:
    """Return a named logger; call :func:`configure_logging` first at startup."""
    return logging.getLogger(name)


__all__: Iterable[str] = ("configure_logging", "get_logger", "JsonFormatter")
