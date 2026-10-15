"""Alerter: store + log always, webhook gated by level/env, dedupe persisted, failures swallowed.
No network: a fake sender records calls."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from quantbot.alerts import FLAG_PREFIX, Alerter
from quantbot.config import AlertConfig
from quantbot.store import Store

ENV = "QB_TEST_ALERT_WEBHOOK"
URL = "https://hooks.example.invalid/secret-token-abcdef"


class FakeSender:
    def __init__(self, fail: Exception | None = None):
        self.calls = []
        self.fail = fail

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.fail:
            raise self.fail


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a.db")
    yield s
    s.close()


def _alerter(store, sender, min_level="warning", clock=None):
    return Alerter(AlertConfig(webhook_url_env=ENV, min_level=min_level), store, sender=sender,
                   clock=clock or Clock(datetime(2026, 10, 15, 15, 0, tzinfo=UTC)))


def test_records_event_and_skips_webhook_without_env(store, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    fake = FakeSender()
    assert _alerter(store, fake).send("critical", "breaker", "daily breaker tripped", symbol="NVDA") is False
    assert fake.calls == []
    ev = store.events()[0]
    assert (ev["level"], ev["kind"], ev["message"]) == ("critical", "breaker", "daily breaker tripped")
    assert ev["fields"] == {"symbol": "NVDA"}


def test_level_gate(store, monkeypatch):
    monkeypatch.setenv(ENV, URL)
    fake = FakeSender()
    a = _alerter(store, fake, min_level="warning")
    assert a.send("info", "cycle", "cycle done") is False
    assert a.send("warning", "data", "stale bars for AMD") is True
    assert a.send("critical", "kill", "kill switch engaged") is True
    assert len(fake.calls) == 2
    url, payload, timeout = fake.calls[0]
    assert url == URL and timeout == 5.0
    assert set(payload) == {"text"} and "stale bars for AMD" in payload["text"]
    assert "PAPER" in payload["text"]
    assert len(store.events()) == 3       # every alert is stored, webhook or not


def test_dedupe_within_30_minutes_and_persisted_across_restart(store, monkeypatch):
    monkeypatch.setenv(ENV, URL)
    clock = Clock(datetime(2026, 10, 15, 15, 0, tzinfo=UTC))
    fake = FakeSender()
    a = _alerter(store, fake, clock=clock)
    assert a.send("critical", "breaker", "same message") is True
    clock.t += timedelta(minutes=10)
    assert a.send("critical", "breaker", "same message") is False
    assert a.send("critical", "breaker", "different message") is True     # other message: not deduped
    assert a.send("critical", "other_kind", "same message") is True       # other kind: not deduped

    # "restart": a fresh Alerter on the same store still remembers
    fake2 = FakeSender()
    b = _alerter(store, fake2, clock=clock)
    clock.t += timedelta(minutes=15)                                       # 25 min after first send
    assert b.send("critical", "breaker", "same message") is False
    clock.t += timedelta(minutes=6)                                        # 31 min
    assert b.send("critical", "breaker", "same message") is True
    assert len(fake.calls) == 3 and len(fake2.calls) == 1
    assert store.get_flag(FLAG_PREFIX + "breaker") is not None
    assert len(store.events()) == 6                                         # deduped ones still stored


def test_webhook_failure_is_swallowed_logged_and_not_marked_sent(store, monkeypatch, caplog):
    monkeypatch.setenv(ENV, URL)
    fake = FakeSender(fail=OSError(f"connection refused to {URL}"))
    a = _alerter(store, fake)
    with caplog.at_level(logging.ERROR, logger="quantbot.alerts"):
        assert a.send("critical", "kill", "boom") is False
    assert any(r.getMessage() == "alert_webhook_failed" for r in caplog.records)
    for r in caplog.records:
        assert "secret-token" not in json.dumps(getattr(r, "fields", {}), default=str)
    # not marked as sent, so the next try goes out again
    fake.fail = None
    assert a.send("critical", "kill", "boom") is True
    assert len(fake.calls) == 2


def test_store_failure_does_not_crash(store, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)

    def broken(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(store, "record_event", broken)
    assert _alerter(store, FakeSender()).send("warning", "x", "y") is False


def test_secrets_redacted_from_payload_and_event(store, monkeypatch):
    monkeypatch.setenv(ENV, URL)
    monkeypatch.setenv("ALPACA_SECRET_KEY", "supersecretvalue123")
    fake = FakeSender()
    _alerter(store, fake).send("critical", "auth", "login failed with supersecretvalue123 and PKABCDEFGHIJKLMNOPQR",
                               detail="supersecretvalue123")
    text = fake.calls[0][1]["text"]
    assert "supersecretvalue123" not in text and "PKABCDEFGHIJKLMNOPQR" not in text
    assert URL not in text
    ev = store.events()[0]
    assert "supersecretvalue123" not in ev["message"]
    assert ev["fields"]["detail"] == "***"


def test_unknown_level_treated_as_warning(store, monkeypatch):
    monkeypatch.setenv(ENV, URL)
    fake = FakeSender()
    assert _alerter(store, fake).send("loud", "k", "m") is True
    assert store.events()[0]["level"] == "warning"


def test_field_names_that_shadow_log_params_are_fine(store, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _alerter(store, FakeSender()).send("info", "k", "m", msg="x", logger="y", fields="z")
    assert store.events()[0]["fields"] == {"msg": "x", "logger": "y", "fields": "z"}
