"""The in-process rate limiter."""

import pytest

from app import middleware
from app.middleware import CONSOLE_BUCKET, INGEST_BUCKET, _check_rate_limit


@pytest.mark.asyncio
async def test_allows_exactly_the_budget():
    results = [await _check_rate_limit("10.0.0.1", 3, 60) for _ in range(4)]
    assert results == [True, True, True, False]


@pytest.mark.asyncio
async def test_buckets_and_clients_are_independent():
    assert await _check_rate_limit("10.0.0.1", 1, 60, INGEST_BUCKET)
    assert await _check_rate_limit("10.0.0.1", 1, 60, CONSOLE_BUCKET)
    assert await _check_rate_limit("10.0.0.2", 1, 60, INGEST_BUCKET)
    assert not await _check_rate_limit("10.0.0.1", 1, 60, INGEST_BUCKET)


@pytest.mark.asyncio
async def test_old_requests_leave_the_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(middleware.time, "time", lambda: now[0])
    assert await _check_rate_limit("10.0.0.1", 1, 60)
    assert not await _check_rate_limit("10.0.0.1", 1, 60)
    now[0] += 61
    assert await _check_rate_limit("10.0.0.1", 1, 60)


@pytest.mark.asyncio
async def test_idle_clients_are_pruned_when_the_table_is_full(monkeypatch):
    monkeypatch.setattr(middleware, "MAX_TRACKED_CLIENTS", 3)
    now = [1000.0]
    monkeypatch.setattr(middleware.time, "time", lambda: now[0])
    for i in range(3):
        await _check_rate_limit(f"10.0.0.{i}", 5, 60)
    now[0] += 61
    await _check_rate_limit("10.0.1.1", 5, 60)
    assert list(middleware._memory_windows) == [f"{INGEST_BUCKET}:10.0.1.1"]
