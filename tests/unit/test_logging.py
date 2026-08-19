"""Structured logging tests."""

from __future__ import annotations

import io
import json
import logging

from app.logging_config import JsonFormatter, configure_logging, get_logger


def _formatted(record_kwargs: dict) -> dict:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="event", args=(), exc_info=None,
    )
    for k, v in record_kwargs.items():
        setattr(record, k, v)
    return json.loads(formatter.format(record))


def test_json_output_has_expected_top_level_fields() -> None:
    payload = _formatted({})
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["msg"] == "event"
    assert "ts" in payload


def test_extras_are_included_and_secrets_redacted() -> None:
    payload = _formatted({
        "email_id": "abc123",
        "api_key": "sk-live-supersecret",
        "authorization": "Bearer topsecret",
        "password": "hunter2",
    })
    extras = payload["extra"]
    assert extras["email_id"] == "abc123"
    assert extras["api_key"] == "***"
    assert extras["authorization"] == "***"
    assert extras["password"] == "***"


def test_email_body_is_truncated() -> None:
    long_body = "a" * 500
    payload = _formatted({"body": long_body})
    truncated = payload["extra"]["body"]
    assert truncated.endswith("…(truncated)")
    assert len(truncated) < len(long_body)


def test_configure_logging_installs_json_handler_on_root() -> None:
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_get_logger_emits_json(caplog) -> None:
    configure_logging("INFO")
    # Redirect the JSON handler to a buffer we can inspect.
    buffer = io.StringIO()
    logging.getLogger().handlers[0].stream = buffer

    log = get_logger("app.test")
    log.info("hello", extra={"user_id": 42})

    line = buffer.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["msg"] == "hello"
    assert payload["extra"]["user_id"] == 42
