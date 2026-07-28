"""
Prometheus metrics.

Multi-process correctness
-------------------------
The API runs under ``uvicorn --workers N``, which forks N independent
processes. ``prometheus_client``'s default registry lives in process memory, so
a ``/metrics`` scrape is served by whichever worker the OS happens to route it
to — reporting roughly 1/N of reality and jumping between scrapes.

The fix is the library's multiprocess mode: each worker writes its samples to
shared mmap files in ``PROMETHEUS_MULTIPROC_DIR``, and the scrape endpoint
builds a ``CollectorRegistry`` with a ``MultiProcessCollector`` that aggregates
across every live worker. This module owns that setup so no other module has to
care.

Metrics live here rather than in ``main.py`` so services can import them
directly instead of doing lazy in-function imports to dodge a circular
dependency (``main`` → ``routes`` → ``services`` → ``main``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

logger = logging.getLogger(__name__)

MULTIPROC_ENV_VAR = "PROMETHEUS_MULTIPROC_DIR"


def _multiproc_dir() -> Path | None:
    """Return the configured multiprocess directory, if enabled."""
    raw = os.environ.get(MULTIPROC_ENV_VAR)
    return Path(raw) if raw else None


def _ensure_dir() -> Path | None:
    """Create the multiprocess directory if configured.

    Must run at import time, before the metric objects below are constructed:
    in multiprocess mode ``prometheus_client`` opens its mmap file the moment a
    metric is defined, and errors out if the directory does not already exist.
    """
    target = _multiproc_dir()
    if target is None:
        return None
    target.mkdir(parents=True, exist_ok=True)
    return target


def init_multiproc_dir() -> None:
    """Clear stale metric files left by a previous run.

    Called once from the parent process before workers fork. Files on disk
    belong to PIDs from the last run; without this they are summed into the
    current numbers forever.
    """
    target = _ensure_dir()
    if target is None:
        logger.info(
            "%s not set — metrics are per-process. Set it when running "
            "more than one uvicorn worker.",
            MULTIPROC_ENV_VAR,
        )
        return

    for stale in target.glob("*.db"):
        stale.unlink(missing_ok=True)
    logger.info("Prometheus multiprocess metrics enabled at %s", target)


# Runs on import so the directory is in place before the definitions below.
_ensure_dir()


def build_registry() -> CollectorRegistry:
    """Return the registry a ``/metrics`` scrape should render.

    In multiprocess mode this is a fresh registry populated by a
    ``MultiProcessCollector`` that reads every worker's mmap files. Otherwise
    it's the default in-process registry.
    """
    if _multiproc_dir() is None:
        from prometheus_client import REGISTRY

        return REGISTRY

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return registry


def render() -> bytes:
    """Render the current metrics in Prometheus text exposition format."""
    return generate_latest(build_registry())


def mark_worker_exit() -> None:
    """Drop a worker's samples when it shuts down cleanly.

    Without this, a restarted worker's Gauge samples linger in the mmap files
    and inflate ``websocket_connections_active`` with connections that died
    with the process.
    """
    if _multiproc_dir() is None:
        return
    try:
        multiprocess.mark_process_dead(os.getpid())
    except Exception:  # pragma: no cover - best-effort cleanup
        logger.warning("Failed to mark process dead in metrics registry", exc_info=True)


# ── Metric definitions ──────────────────────────────────────────────
#
# Gauges need an explicit multiprocess_mode. "livesum" sums the values of all
# *live* workers, which is what you want for a connection count: 4 workers
# holding 500 sockets each should read 2000, not 500.

telemetry_ingested_total = Counter(
    "telemetry_ingested_total",
    "Total number of telemetry readings accepted by the ingest endpoints",
    ["path"],
)

telemetry_ingest_seconds = Histogram(
    "telemetry_ingest_seconds",
    "Wall-clock time spent handling a telemetry ingest request",
    ["path", "buffered"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

websocket_connections_active = Gauge(
    "websocket_connections_active",
    "Number of active WebSocket connections",
    multiprocess_mode="livesum",
)

websocket_frames_dropped_total = Counter(
    "websocket_frames_dropped_total",
    "Frames dropped because a client's outbound queue was full",
)

db_write_latency_seconds = Histogram(
    "db_write_latency_seconds",
    "Latency of telemetry writes to PostgreSQL",
    ["mode"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

worker_batch_size = Histogram(
    "worker_batch_size",
    "Number of telemetry rows persisted per worker batch",
    buckets=(1, 5, 10, 25, 50, 100, 250, 500),
)
