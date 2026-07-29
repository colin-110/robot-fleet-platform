"""Logging configuration and request correlation.

The property that matters: an ``X-Request-ID`` handed back to a caller must be
findable in the logs. It was previously returned as a header and never written
anywhere, so a user reporting "request abc123 failed" gave you an identifier
that appeared in no log line.
"""

import asyncio
import json
import logging

import pytest

from app.config import get_settings
from app.logging_config import (
    JsonFormatter,
    RequestIdFilter,
    configure_logging,
    request_id_var,
    reset_request_id,
    set_request_id,
)

settings = get_settings()


def _record(msg: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ── The filter ──────────────────────────────────────────────────────


def test_filter_stamps_the_current_request_id():
    token = set_request_id("req-abc123")
    try:
        record = _record()
        RequestIdFilter("api").filter(record)
        assert record.request_id == "req-abc123"
        assert record.service == "api"
    finally:
        reset_request_id(token)


def test_records_outside_a_request_get_a_placeholder():
    """Startup and worker lines have no request; they must still format."""
    record = _record()
    RequestIdFilter("worker").filter(record)

    assert record.request_id == "-"
    assert record.service == "worker"


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_share_a_request_id():
    """A ContextVar is per-task; a module global would leak across requests
    and attribute one caller's log lines to another."""
    seen = {}

    async def handle(name: str, delay: float):
        token = set_request_id(name)
        try:
            await asyncio.sleep(delay)
            record = _record()
            RequestIdFilter("api").filter(record)
            seen[name] = record.request_id
        finally:
            reset_request_id(token)

    # Interleaved on purpose: the later-started task finishes first.
    await asyncio.gather(handle("req-slow", 0.03), handle("req-fast", 0.01))

    assert seen == {"req-slow": "req-slow", "req-fast": "req-fast"}


def test_reset_restores_the_previous_value():
    token = set_request_id("outer")
    inner_token = set_request_id("inner")
    reset_request_id(inner_token)

    assert request_id_var.get() == "outer"
    reset_request_id(token)


# ── The JSON formatter ──────────────────────────────────────────────


def test_json_formatter_emits_one_object_per_line():
    record = _record("fleet status computed")
    RequestIdFilter("api").filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "fleet status computed"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "-"
    assert payload["service"] == "api"
    assert "timestamp" in payload


def test_extra_fields_become_queryable_keys():
    """The point of structured logging: a dashboard filters on http_status
    rather than regexing it back out of a sentence."""
    record = _record("GET /x 200", http_status=200, duration_ms=12.5, http_path="/x")
    RequestIdFilter("api").filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["http_status"] == 200
    assert payload["duration_ms"] == 12.5
    assert payload["http_path"] == "/x"


def test_exceptions_are_serialized_into_the_object():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    RequestIdFilter("api").filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]


def test_unserialisable_values_do_not_lose_the_record():
    """A stray object must degrade to a string rather than raise inside the
    logging call and drop the line entirely."""

    class Opaque:
        def __repr__(self):
            return "<opaque>"

    record = _record("odd", thing=Opaque())
    RequestIdFilter("api").filter(record)

    payload = json.loads(JsonFormatter().format(record))
    assert payload["thing"] == "<opaque>"


# ── configure_logging ───────────────────────────────────────────────


def test_configure_logging_is_idempotent():
    """Called by both the app and the worker, and again on reload. Appending
    handlers each time would emit every line once per call."""
    configure_logging(service="api")
    first = len(logging.getLogger().handlers)
    configure_logging(service="api")

    assert len(logging.getLogger().handlers) == first == 1


def test_format_follows_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "log_format_setting", "json")
    configure_logging(service="api")
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)

    monkeypatch.setattr(settings, "log_format_setting", "text")
    configure_logging(service="api")
    assert not isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def test_production_defaults_to_json(monkeypatch):
    """Plain text is right at a terminal and wrong in an aggregator."""
    monkeypatch.setattr(settings, "log_format_setting", None)
    monkeypatch.setattr(settings, "app_env", "production")
    assert settings.log_format == "json"

    monkeypatch.setattr(settings, "app_env", "development")
    assert settings.log_format == "text"


# ── Access logging through the real middleware ──────────────────────


@pytest.mark.asyncio
async def test_request_id_is_returned_and_logged(client, caplog):
    """The whole point: the id the caller is given is the id in the logs."""
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        response = await client.get("/api/v1/robots/status")

    request_id = response.headers["x-request-id"]
    assert request_id

    access_lines = [r for r in caplog.records if getattr(r, "http_path", None)]
    assert access_lines, "no access log line was emitted"
    assert any(r.request_id == request_id for r in access_lines)


@pytest.mark.asyncio
async def test_access_line_carries_structured_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        await client.get("/api/v1/robots/status")

    line = next(r for r in caplog.records if getattr(r, "http_path", None))
    assert line.http_method == "GET"
    assert line.http_path == "/api/v1/robots/status"
    assert line.http_status == 200
    assert line.duration_ms >= 0


@pytest.mark.asyncio
async def test_an_inbound_request_id_is_honoured(client, caplog):
    """A trace started upstream must survive into this service rather than
    being renamed at the door."""
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        response = await client.get(
            "/api/v1/robots/status", headers={"X-Request-ID": "from-upstream"}
        )

    assert response.headers["x-request-id"] == "from-upstream"
    line = next(r for r in caplog.records if getattr(r, "http_path", None))
    assert line.request_id == "from-upstream"


@pytest.mark.asyncio
async def test_health_and_metrics_are_not_access_logged(client, caplog):
    """Scraped every few seconds; logging them buries real traffic."""
    with caplog.at_level(logging.INFO, logger="app.middleware"):
        await client.get("/health")

    assert not [r for r in caplog.records if getattr(r, "http_path", None) == "/health"]


@pytest.mark.asyncio
async def test_access_logging_can_be_turned_off(client, caplog, monkeypatch):
    monkeypatch.setattr(settings, "access_log", False)

    with caplog.at_level(logging.INFO, logger="app.middleware"):
        await client.get("/api/v1/robots/status")

    assert not [r for r in caplog.records if getattr(r, "http_path", None)]


@pytest.mark.asyncio
async def test_the_request_id_does_not_leak_between_requests(client):
    """Uvicorn reuses tasks; a stale id is worse than none."""
    first = await client.get("/api/v1/robots/status")
    second = await client.get("/api/v1/robots/status")

    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert request_id_var.get() == "-"
