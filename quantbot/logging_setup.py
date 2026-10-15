"""Structured JSON logging with secret redaction.

Any value of a sensitive environment variable, and anything that looks like an Alpaca key,
is replaced with ``***`` before a record is written."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import UTC, datetime

SENSITIVE_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "QUANTBOT_ALERT_WEBHOOK")
_KEY_PATTERN = re.compile(r"\b(?:PK|AK|CK)[A-Z0-9]{16,}\b")


def redact(text: str) -> str:
    for name in SENSITIVE_ENV:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return _KEY_PATTERN.sub("***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def setup_logging(level: str = "INFO", stream=None) -> None:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def log_event(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    """``log_event(log, logging.INFO, "order_submitted", symbol="NVDA", qty=1.2)``"""
    logger.log(level, msg, extra={"fields": fields})
