"""Operator alerts: every alert is stored as an event and logged; important ones also go to a webhook.

``Alerter(cfg, store).send(level, kind, message, **fields)``

  * Always: ``store.record_event(level, kind, message, **fields)`` and a structured log line.
  * Webhook: only if ``level >= cfg.min_level`` and the environment variable named by
    ``cfg.webhook_url_env`` holds a URL. The body is ``{"text": "..."}`` (Slack/Discord/most chat
    webhooks accept it), POSTed with urllib and a 5 s timeout.
  * Dedupe: the same ``kind`` + ``message`` is not re-sent to the webhook within 30 minutes. Last-sent
    times are persisted in store flags ``alert_last:<kind>`` so a restart does not re-spam.
  * Never raises: webhook and store failures are logged and swallowed so alerts cannot crash the bot.
  * The URL itself is never logged, and the outgoing text is passed through ``logging_setup.redact``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import AlertConfig
from .logging_setup import log_event, redact

if TYPE_CHECKING:  # pragma: no cover
    from .store import Store

log = logging.getLogger("quantbot.alerts")

LEVELS = {"info": 0, "warning": 1, "critical": 2}
_LOG_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.CRITICAL}
DEDUPE_WINDOW = timedelta(minutes=30)
FLAG_PREFIX = "alert_last:"
WEBHOOK_TIMEOUT_S = 5.0
_MAX_TEXT = 3000

Sender = Callable[[str, dict, float], None]


def urllib_sender(url: str, payload: dict, timeout: float) -> None:
    """POST ``payload`` as JSON. Raises on a non-2xx answer or a network error."""
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("webhook URL must be http(s)")
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "quantbot-alerts"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
        status = getattr(resp, "status", 200)
        if not 200 <= status < 300:
            raise RuntimeError(f"webhook answered HTTP {status}")


def _digest(kind: str, message: str) -> str:
    return hashlib.sha256(f"{kind}\x00{message}".encode()).hexdigest()[:16]


class Alerter:
    def __init__(self, cfg: AlertConfig, store: "Store", sender: Sender | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.sender: Sender = sender or urllib_sender
        self.clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ public
    def send(self, level: str, kind: str, message: str, **fields: Any) -> bool:
        """Record + log the alert; returns True only if it was delivered to the webhook."""
        lvl = str(level).lower()
        if lvl not in LEVELS:
            log_event(log, logging.WARNING, "alert_unknown_level", level=str(level), kind=kind)
            lvl = "warning"
        message = redact(str(message))

        try:
            self.store.record_event(lvl, kind, message, **fields)
        except Exception as e:  # noqa: BLE001 - alerts must never crash the bot
            log_event(log, logging.ERROR, "alert_store_failed", kind=kind, error=f"{type(e).__name__}: {e}")
        log_event(log, _LOG_LEVELS[lvl], message, alert_kind=kind, alert_level=lvl, **fields)

        if LEVELS[lvl] < LEVELS[self.cfg.min_level]:
            return False
        url = os.environ.get(self.cfg.webhook_url_env, "").strip()
        if not url:
            return False
        now = self.clock()
        if self._recently_sent(kind, message, now):
            log_event(log, logging.INFO, "alert_webhook_deduped", alert_kind=kind)
            return False
        payload = {"text": self._format(lvl, kind, message, fields)}
        try:
            self.sender(url, payload, WEBHOOK_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - never crash the bot over a webhook
            # str(e) may contain the URL (e.g. urllib errors): redact the URL explicitly too.
            err = redact(f"{type(e).__name__}: {e}".replace(url, "***"))
            log_event(log, logging.ERROR, "alert_webhook_failed", alert_kind=kind, error=err)
            return False
        self._mark_sent(kind, message, now)
        return True

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _format(level: str, kind: str, message: str, fields: dict) -> str:
        text = f"[{level.upper()}] quantbot (PAPER) {kind}: {message}"
        if fields:
            extras = ", ".join(f"{k}={v}" for k, v in fields.items())
            text += f" ({extras})"
        text = redact(text)
        return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 3] + "..."

    def _load(self, kind: str) -> dict[str, str]:
        try:
            raw = self.store.get_flag(FLAG_PREFIX + kind)
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - corrupt/missing dedupe state just means "not sent"
            return {}

    def _recently_sent(self, kind: str, message: str, now: datetime) -> bool:
        last = self._load(kind).get(_digest(kind, message))
        if not last:
            return False
        try:
            return now - datetime.fromisoformat(last) < DEDUPE_WINDOW
        except (TypeError, ValueError):
            return False

    def _mark_sent(self, kind: str, message: str, now: datetime) -> None:
        data = self._load(kind)
        # keep only entries still inside the window so the flag stays small
        fresh = {}
        for k, v in data.items():
            try:
                if now - datetime.fromisoformat(v) < DEDUPE_WINDOW:
                    fresh[k] = v
            except (TypeError, ValueError):
                continue
        fresh[_digest(kind, message)] = now.astimezone(UTC).isoformat()
        try:
            self.store.set_flag(FLAG_PREFIX + kind, json.dumps(fresh), "alert dedupe state")
        except Exception as e:  # noqa: BLE001
            log_event(log, logging.ERROR, "alert_dedupe_persist_failed", alert_kind=kind,
                      error=f"{type(e).__name__}: {e}")
