"""
A/B optimization benchmark.

Each optimization in this system is gated by an ``OPT_*`` environment flag. This
harness measures one flag at a time: it brings the stack up with the flag OFF
(the naive implementation it replaced), runs a workload, brings the stack up
with the flag ON, runs the identical workload, and reports the delta.

Why one flag at a time
----------------------
Measuring "everything on" against "everything off" tells you the stack got
faster but not *which* change did it. Isolating one variable per experiment is
what makes a claim like "the Redis buffer cut p99 ingest latency 87%"
defensible when someone asks you to justify it.

Methodology
-----------
- **Warmup.** Every experiment discards a warmup pass so JIT-less Python import
  costs, connection-pool fill, and Postgres page-cache misses don't land in the
  measured window.
- **Repeats.** Each configuration runs ``--repeats`` times (default 3) and the
  reported figure is the *median* run, with the full spread printed. Latency on
  a developer machine is noisy; a single run is not a measurement.
- **Interleaving.** Baseline and optimized runs alternate rather than running
  all-baseline-then-all-optimized, so slow background drift on the host hits
  both arms equally instead of biasing one.
- **Verification.** Before each run the harness reads ``/health`` and asserts
  the flags actually came up as intended. A benchmark that silently measured
  the same configuration twice is worse than no benchmark.

Usage
-----
    docker compose up -d --build
    python scripts/benchmark_matrix.py                  # all experiments
    python scripts/benchmark_matrix.py --only redis_buffer read_cache
    python scripts/benchmark_matrix.py --repeats 5 --output docs/benchmarks.md

Requires: docker compose, aiohttp, websockets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "http://localhost:8000"

API_KEY = os.environ.get("TELEMETRY_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


# ── Measurement primitives ──────────────────────────────────────────


@dataclass
class Sample:
    """Latency/throughput figures from one workload run."""

    successful: int = 0
    failed: int = 0
    duration_s: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)
    # Why requests failed, deduplicated. An arm where everything failed produces
    # p99 = 0.0, which looks like "infinitely fast" unless the reason is kept.
    errors: dict[str, int] = field(default_factory=dict)

    def record_error(self, reason: str) -> None:
        self.failed += 1
        self.errors[reason] = self.errors.get(reason, 0) + 1

    @property
    def rps(self) -> float:
        return self.successful / self.duration_s if self.duration_s > 0 else 0.0

    def pct(self, q: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        idx = min(int(len(ordered) * q), len(ordered) - 1)
        return ordered[idx]

    @property
    def p50(self) -> float:
        return self.pct(0.50)

    @property
    def p95(self) -> float:
        return self.pct(0.95)

    @property
    def p99(self) -> float:
        return self.pct(0.99)

    @property
    def mean(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0


def median_sample(samples: list[Sample]) -> Sample:
    """Pick the run whose p99 is the median — the representative run."""
    return sorted(samples, key=lambda s: s.p99)[len(samples) // 2]


def pct_change(baseline: float, optimized: float) -> float:
    """Percent reduction from baseline to optimized. Positive = improvement."""
    if baseline == 0:
        return 0.0
    return (baseline - optimized) / baseline * 100.0


def pct_increase(baseline: float, optimized: float) -> float:
    """Percent increase from baseline to optimized. Positive = improvement."""
    if baseline == 0:
        return 0.0
    return (optimized - baseline) / baseline * 100.0


# ── Workloads ───────────────────────────────────────────────────────


def make_payload(robot_id: int | None = None) -> dict:
    rid = robot_id or random.randint(1, 200)
    return {
        "robot_id": rid,
        "battery": round(random.uniform(20, 100), 2),
        "temperature": round(random.uniform(25, 70), 2),
        "speed": round(random.uniform(0, 2.5), 2),
        "status": random.choice(["ACTIVE", "CHARGING", "LOW POWER", "OVERHEATING"]),
        "mission_id": f"M-{random.randint(1000, 9999)}",
        "mission_type": random.choice(["PATROL", "DELIVERY", "INSPECTION"]),
        "mission_progress": round(random.uniform(0, 100), 1),
        "battery_health": round(random.uniform(70, 100), 1),
        "motor_health": round(random.uniform(70, 100), 1),
        "sensor_health": round(random.uniform(70, 100), 1),
        "network_health": round(random.uniform(70, 100), 1),
        "x": round(random.uniform(-50, 50), 2),
        "y": round(random.uniform(-50, 50), 2),
    }


async def _drive(
    session: aiohttp.ClientSession,
    request_factory,
    total: int,
    concurrency: int,
) -> Sample:
    """Run *total* requests at a fixed concurrency, timing each one."""
    sample = Sample()
    sem = asyncio.Semaphore(concurrency)

    async def one() -> None:
        async with sem:
            start = time.perf_counter()
            try:
                async with request_factory() as resp:
                    body = await resp.read()
                    elapsed = (time.perf_counter() - start) * 1000
                    if resp.status == 200:
                        sample.successful += 1
                        sample.latencies_ms.append(elapsed)
                    else:
                        sample.record_error(f"HTTP {resp.status}: {body[:120]!r}")
            except Exception as exc:
                sample.record_error(f"{type(exc).__name__}: {str(exc)[:120]}")

    t0 = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(total)))
    sample.duration_s = time.perf_counter() - t0
    return sample


async def workload_ingest(base_url: str, total: int, concurrency: int) -> Sample:
    """POST single telemetry readings — exercises the write path."""
    url = f"{base_url}/api/v1/telemetry"
    async with aiohttp.ClientSession() as session:
        return await _drive(
            session,
            lambda: session.post(url, json=make_payload(), headers=HEADERS),
            total,
            concurrency,
        )


async def workload_ingest_batch(base_url: str, total: int, concurrency: int) -> Sample:
    """POST batches of 50 readings — exercises the worker's insert path."""
    url = f"{base_url}/api/v1/telemetry/batch"
    async with aiohttp.ClientSession() as session:
        return await _drive(
            session,
            lambda: session.post(
                url, json=[make_payload() for _ in range(50)], headers=HEADERS
            ),
            total,
            concurrency,
        )


async def workload_read_status(base_url: str, total: int, concurrency: int) -> Sample:
    """GET fleet status — the expensive per-robot window-function query."""
    url = f"{base_url}/api/v1/robots/status"
    async with aiohttp.ClientSession() as session:
        return await _drive(session, lambda: session.get(url), total, concurrency)


async def workload_read_analytics(base_url: str, total: int, concurrency: int) -> Sample:
    """GET fleet analytics — multiple aggregations plus Python post-processing."""
    url = f"{base_url}/api/v1/analytics/fleet"
    async with aiohttp.ClientSession() as session:
        return await _drive(session, lambda: session.get(url), total, concurrency)


async def workload_health(base_url: str, total: int, concurrency: int) -> Sample:
    """GET a trivial endpoint — isolates per-request framework overhead.

    Deliberately the cheapest possible handler: with almost no application work
    in the way, the measured time is dominated by the middleware and
    serialization layers, which is exactly what we want when benchmarking them.
    """
    url = f"{base_url}/"
    async with aiohttp.ClientSession() as session:
        return await _drive(session, lambda: session.get(url), total, concurrency)


async def workload_worker_drain(base_url: str, rows: int, batch_size: int) -> Sample:
    """Measure how fast the *worker* persists a burst of telemetry.

    The worker's INSERT strategy cannot affect HTTP latency — with the Redis
    buffer on, the request path only does an ``XADD`` and returns. Timing the
    HTTP call would compare two identical code paths and report ~0%, which is
    a measurement artifact, not a finding.

    What the strategy actually changes is drain throughput, so that's what we
    measure: empty the table, push a known number of rows through the stream,
    then poll the row count until the worker has caught up.
    """
    truncate_telemetry()

    sample = Sample()
    url = f"{base_url}/api/v1/telemetry/batch"
    batches = max(rows // batch_size, 1)

    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        for _ in range(batches):
            payload = [make_payload() for _ in range(batch_size)]
            try:
                async with session.post(url, json=payload, headers=HEADERS) as resp:
                    await resp.read()
                    if resp.status != 200:
                        sample.record_error(f"HTTP {resp.status}")
            except Exception as exc:
                sample.record_error(type(exc).__name__)

    expected = batches * batch_size
    deadline = time.perf_counter() + 180
    persisted = 0
    while time.perf_counter() < deadline:
        persisted = telemetry_row_count()
        if persisted >= expected:
            break
        await asyncio.sleep(0.25)

    sample.duration_s = time.perf_counter() - t0
    sample.successful = persisted
    if persisted < expected:
        sample.record_error(f"worker drained only {persisted}/{expected} rows in 180s")
    # Latency here is "time to durably persist the whole burst".
    sample.latencies_ms.append(sample.duration_s * 1000)
    return sample


async def workload_ws_fanout(base_url: str, clients: int, seconds: int) -> Sample:
    """Hold N WebSocket clients open and count frames received.

    Reported as throughput (frames/sec) rather than latency — this measures the
    fan-out tier's ability to keep many slow-ish consumers fed.
    """
    import websockets

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/ws?api_key={API_KEY}"

    sample = Sample()
    received = 0
    connected = 0
    stop_at = time.perf_counter() + seconds

    async def one_client() -> None:
        nonlocal received, connected
        try:
            async with websockets.connect(ws_url, open_timeout=15) as ws:
                connected += 1
                while time.perf_counter() < stop_at:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=2)
                        received += 1
                    except asyncio.TimeoutError:
                        continue
        except Exception:
            sample.failed += 1

    # Background traffic so there is something to fan out.
    async def producer() -> None:
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}/api/v1/telemetry/batch"
            while time.perf_counter() < stop_at:
                try:
                    async with session.post(
                        url,
                        json=[make_payload() for _ in range(25)],
                        headers=HEADERS,
                    ) as resp:
                        await resp.read()
                except Exception:  # noqa: S110 - producer is background traffic
                    # A dropped producer request just means slightly less to fan
                    # out; the measurement is frames received by the clients.
                    pass
                await asyncio.sleep(0.05)

    t0 = time.perf_counter()
    await asyncio.gather(
        *(one_client() for _ in range(clients)), producer(), return_exceptions=True
    )
    sample.duration_s = time.perf_counter() - t0
    sample.successful = received
    # Encode frames/sec through the rps property.
    sample.successful = received
    return sample


# ── Experiment definitions ──────────────────────────────────────────


@dataclass
class Experiment:
    key: str
    flag: str
    title: str
    claim: str
    workload: str
    baseline_label: str
    optimized_label: str
    metric: str = "latency"  # "latency" | "throughput"
    total: int = 600
    concurrency: int = 50
    # Restarting only these services is much faster than the whole stack.
    services: tuple[str, ...] = ("backend",)
    # Rows of telemetry to seed before each arm. Read-path experiments set this
    # so their measurement doesn't depend on what a previous experiment left in
    # the table. 0 = don't touch the data.
    seed_rows: int = 0
    # How to phrase this experiment's throughput result.
    throughput_unit: str = "msg/s"
    scale_label: str = "concurrency"


EXPERIMENTS: list[Experiment] = [
    Experiment(
        key="redis_buffer",
        flag="OPT_REDIS_BUFFER",
        title="Redis Stream ingest buffer",
        claim="Decoupling the write path from PostgreSQL with a Redis Stream",
        workload="ingest",
        baseline_label="Synchronous INSERT on request path",
        optimized_label="XADD to Redis Stream, worker persists",
        total=800,
        concurrency=50,
        services=("backend", "worker"),
    ),
    Experiment(
        key="read_cache",
        flag="OPT_READ_CACHE",
        title="Redis read-through cache",
        claim="Caching computed fleet status behind a 10s TTL",
        workload="read_status",
        baseline_label="Recompute window-function query per request",
        optimized_label="Redis cache hit",
        total=600,
        concurrency=50,
        seed_rows=20000,
    ),
    Experiment(
        key="analytics_cache",
        flag="OPT_READ_CACHE",
        title="Redis cache on analytics aggregation",
        claim="Caching multi-aggregate analytics behind a 10s TTL",
        workload="read_analytics",
        baseline_label="Recompute all aggregations per request",
        optimized_label="Redis cache hit",
        total=400,
        concurrency=50,
        seed_rows=20000,
    ),
    Experiment(
        key="asgi_middleware",
        flag="OPT_ASGI_MIDDLEWARE",
        title="Pure ASGI middleware",
        claim="Replacing two BaseHTTPMiddleware layers with pure ASGI middleware",
        workload="health",
        baseline_label="Starlette BaseHTTPMiddleware (task + anyio streams)",
        optimized_label="Pure ASGI send-wrapper",
        total=2000,
        concurrency=50,
    ),
    # Expect this one to come back near zero, and that is the useful result:
    # current FastAPI serializes via Pydantic whenever a route declares a
    # `response_model`, bypassing default_response_class. Measuring it is how
    # you find that out instead of repeating a claim that no longer applies.
    Experiment(
        key="orjson",
        flag="OPT_ORJSON",
        title="orjson serialization",
        claim="Serializing float-heavy telemetry responses with orjson",
        workload="read_status",
        baseline_label="stdlib json",
        optimized_label="orjson",
        total=600,
        concurrency=25,
    ),
    Experiment(
        key="batch_insert",
        flag="OPT_BATCH_INSERT",
        title="Worker bulk INSERT",
        claim="Persisting worker batches as one multi-row INSERT",
        workload="worker_drain",
        baseline_label="One INSERT + COMMIT per row",
        optimized_label="One multi-row INSERT per batch",
        metric="throughput",
        total=10000,  # rows to push through the stream
        concurrency=50,  # batch size
        services=("backend", "worker"),
        throughput_unit="rows/s persisted",
        scale_label="a 10,000-row burst",
    ),
    Experiment(
        key="bounded_fanout",
        flag="OPT_BOUNDED_FANOUT",
        title="Bounded-queue WebSocket fan-out",
        claim="Bounded per-client queues with drop-oldest backpressure",
        workload="ws_fanout",
        baseline_label="asyncio.create_task per client per message",
        optimized_label="Bounded queue + dedicated sender task",
        metric="throughput",
        total=300,  # concurrent websocket clients
        concurrency=15,  # seconds to hold them open
        throughput_unit="msg/s",
        scale_label="300 concurrent WebSocket clients",
    ),
]

WORKLOADS = {
    "ingest": workload_ingest,
    "ingest_batch": workload_ingest_batch,
    "read_status": workload_read_status,
    "read_analytics": workload_read_analytics,
    "health": workload_health,
    "ws_fanout": workload_ws_fanout,
    "worker_drain": workload_worker_drain,
}


# ── Stack control ───────────────────────────────────────────────────


def compose(*args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a docker compose command in the repo root."""
    cmd = ["docker", "compose", *args]
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd, cwd=REPO_ROOT, env=merged, check=check, capture_output=True, text=True
    )


def _psql(sql: str) -> str:
    """Run a statement against the compose Postgres and return stdout."""
    result = compose(
        "exec", "-T", "db", "psql", "-U", "postgres", "-d", "robotfleet", "-tAc", sql,
        check=False,
    )
    return result.stdout.strip()


def telemetry_row_count() -> int:
    out = _psql("SELECT count(*) FROM telemetry")
    try:
        return int(out.splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def truncate_telemetry() -> None:
    """Empty the telemetry table so an experiment starts from a known state."""
    _psql("TRUNCATE telemetry")


async def seed_telemetry(base_url: str, rows: int, batch_size: int = 50) -> None:
    """Reset the telemetry table to a fixed size before read-path experiments.

    Read latency depends heavily on how much data the aggregation has to scan,
    so an experiment that runs after a heavy ingest test would measure a much
    larger table than one that runs first. Re-seeding a fixed dataset makes
    each experiment independent of the order they run in — without it, the
    analytics arm can be scanning millions of rows left over from a previous
    experiment and simply time out.
    """
    truncate_telemetry()

    url = f"{base_url}/api/v1/telemetry/batch"
    async with aiohttp.ClientSession() as session:
        for _ in range(max(rows // batch_size, 1)):
            payload = [make_payload() for _ in range(batch_size)]
            try:
                async with session.post(url, json=payload, headers=HEADERS) as resp:
                    await resp.read()
            except Exception as exc:
                # Seeding is best-effort; the row-count check below is what
                # actually decides whether we have enough data to measure on.
                print(f"    (seed batch failed: {type(exc).__name__})", flush=True)

    # Wait for the worker to persist the seed; reads query PostgreSQL, not Redis.
    deadline = time.perf_counter() + 120
    while time.perf_counter() < deadline:
        if telemetry_row_count() >= rows * 0.9:
            return
        await asyncio.sleep(0.5)


async def wait_for_health(
    base_url: str, expected_flags: dict[str, bool], timeout: float = 120.0
) -> None:
    """Block until the API is healthy AND reports the flags we asked for.

    The flag assertion is the important half: a restart that silently kept the
    old configuration would produce two identical arms and a meaningless 0%
    delta that looks like a real result.
    """
    deadline = time.perf_counter() + timeout
    last_error = "no response"

    async with aiohttp.ClientSession() as session:
        while time.perf_counter() < deadline:
            try:
                async with session.get(f"{base_url}/health", timeout=5) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        if body.get("database") != "healthy":
                            last_error = f"database {body.get('database')}"
                        else:
                            actual = body.get("optimizations", {})
                            mismatched = {
                                k: (actual.get(k), v)
                                for k, v in expected_flags.items()
                                if actual.get(k) != v
                            }
                            if mismatched:
                                last_error = f"flag mismatch {mismatched}"
                            else:
                                return
            except Exception as exc:
                last_error = type(exc).__name__
            await asyncio.sleep(1.5)

    raise RuntimeError(f"stack did not become ready ({last_error})")


async def restart_with(
    experiment: Experiment, base_url: str, flag_value: bool
) -> None:
    """Recreate the affected services with one flag flipped."""
    env = {experiment.flag: "true" if flag_value else "false"}
    compose("up", "-d", "--force-recreate", "--no-deps", *experiment.services, env=env)

    expected = {experiment.flag.lower(): flag_value}
    await wait_for_health(base_url, expected)
    # Let connection pools fill and the worker attach to its consumer group.
    await asyncio.sleep(3)


# ── Experiment runner ───────────────────────────────────────────────


async def run_arm(
    experiment: Experiment, base_url: str, repeats: int
) -> list[Sample]:
    """Warm up, then take *repeats* measurements of one configuration."""
    workload = WORKLOADS[experiment.workload]

    if experiment.seed_rows:
        await seed_telemetry(base_url, experiment.seed_rows)

    # Warmup — discarded. Fills pools, warms Postgres page cache, primes caches.
    await workload(base_url, max(experiment.total // 4, 20), experiment.concurrency)
    await asyncio.sleep(1)

    samples = []
    for _ in range(repeats):
        sample = await workload(base_url, experiment.total, experiment.concurrency)

        # An arm where most requests failed still produces numbers — p99 of an
        # empty latency list is 0.0, which reads as "infinitely fast" and would
        # silently poison the comparison. Refuse to report it.
        attempted = sample.successful + sample.failed
        if attempted and sample.successful / attempted < 0.95:
            raise RuntimeError(
                f"{sample.failed}/{attempted} requests failed — refusing to "
                f"report this arm. Causes: {sample.errors}"
            )

        samples.append(sample)
        await asyncio.sleep(1.5)
    return samples


async def run_experiment(
    experiment: Experiment, base_url: str, repeats: int
) -> dict:
    """Run baseline and optimized arms interleaved, return a result record."""
    print(f"\n{'=' * 72}")
    print(f"  {experiment.title}   [{experiment.flag}]")
    print(f"{'=' * 72}")

    baseline_samples: list[Sample] = []
    optimized_samples: list[Sample] = []

    # Interleave: OFF, ON, OFF, ON ... so host drift hits both arms equally.
    for round_index in range(repeats):
        print(f"  round {round_index + 1}/{repeats}  baseline (flag off) ...", flush=True)
        await restart_with(experiment, base_url, False)
        baseline_samples.extend(await run_arm(experiment, base_url, 1))

        print(f"  round {round_index + 1}/{repeats}  optimized (flag on) ...", flush=True)
        await restart_with(experiment, base_url, True)
        optimized_samples.extend(await run_arm(experiment, base_url, 1))

    base = median_sample(baseline_samples)
    opt = median_sample(optimized_samples)

    if experiment.metric == "throughput":
        headline_metric = "throughput"
        headline_delta = pct_increase(base.rps, opt.rps)
    else:
        headline_metric = "p99 latency"
        headline_delta = pct_change(base.p99, opt.p99)

    record = {
        "key": experiment.key,
        "flag": experiment.flag,
        "title": experiment.title,
        "claim": experiment.claim,
        "workload": experiment.workload,
        "concurrency": experiment.concurrency,
        "requests": experiment.total,
        "repeats": repeats,
        "baseline_label": experiment.baseline_label,
        "optimized_label": experiment.optimized_label,
        "headline_metric": headline_metric,
        "headline_delta_pct": round(headline_delta, 1),
        "throughput_unit": experiment.throughput_unit,
        "scale_label": experiment.scale_label,
        "baseline": _summarize(base, baseline_samples),
        "optimized": _summarize(opt, optimized_samples),
        "deltas": {
            "p50_pct": round(pct_change(base.p50, opt.p50), 1),
            "p95_pct": round(pct_change(base.p95, opt.p95), 1),
            "p99_pct": round(pct_change(base.p99, opt.p99), 1),
            "mean_pct": round(pct_change(base.mean, opt.mean), 1),
            "rps_pct": round(pct_increase(base.rps, opt.rps), 1),
        },
    }

    print(
        f"  -> {headline_metric}: "
        f"{_fmt(base, experiment.metric)} -> {_fmt(opt, experiment.metric)}  "
        f"({headline_delta:+.1f}%)"
    )
    return record


def _fmt(sample: Sample, metric: str) -> str:
    if metric == "throughput":
        return f"{sample.rps:.0f}/s"
    return f"{sample.p99:.1f}ms"


def _summarize(chosen: Sample, all_samples: list[Sample]) -> dict:
    return {
        "p50_ms": round(chosen.p50, 2),
        "p95_ms": round(chosen.p95, 2),
        "p99_ms": round(chosen.p99, 2),
        "mean_ms": round(chosen.mean, 2),
        "rps": round(chosen.rps, 1),
        "successful": chosen.successful,
        "failed": chosen.failed,
        # The spread across runs. Report it — a delta smaller than this is noise.
        "p99_runs_ms": [round(s.p99, 2) for s in all_samples],
        "rps_runs": [round(s.rps, 1) for s in all_samples],
    }


# ── Reporting ───────────────────────────────────────────────────────


def render_markdown(records: list[dict], meta: dict) -> str:
    lines: list[str] = []
    lines.append("# Optimization Benchmarks\n")
    lines.append(
        "Every optimization in this system sits behind an `OPT_*` flag. Each row "
        "below was produced by running the same workload twice — once with the "
        "flag off (the naive implementation) and once with it on — restarting "
        "the affected services in between.\n"
    )
    rounds = meta["repeats"]
    lines.append(
        f"- **Host:** {meta['host']}\n"
        f"- **Repeats:** {rounds} interleaved round{'s' if rounds != 1 else ''} per "
        "experiment; the reported figure is the median run\n"
        f"- **Generated:** {meta['generated']}\n"
    )
    lines.append(
        "> Runs are interleaved (off, on, off, on…) so background load on the "
        "host biases both arms equally. The per-run spread is listed under each "
        "experiment; treat any delta smaller than that spread as noise.\n"
    )

    lines.append("## Summary\n")
    lines.append("| Optimization | Workload | Metric | Baseline | Optimized | Change |")
    lines.append("| :--- | :--- | :--- | ---: | ---: | ---: |")
    for r in records:
        if r["headline_metric"] == "throughput":
            unit = r.get("throughput_unit", "req/s")
            base_v = f"{r['baseline']['rps']:.0f} {unit}"
            opt_v = f"{r['optimized']['rps']:.0f} {unit}"
        else:
            base_v = f"{r['baseline']['p99_ms']:.1f} ms"
            opt_v = f"{r['optimized']['p99_ms']:.1f} ms"
        delta = r["headline_delta_pct"]
        arrow = "faster" if delta > 0 else "slower"
        lines.append(
            f"| {r['title']} | `{r['workload']}` | {r['headline_metric']} | "
            f"{base_v} | {opt_v} | **{abs(delta):.1f}% {arrow}** |"
        )

    lines.append("\n## Detail\n")
    for r in records:
        is_throughput = r["headline_metric"] == "throughput"
        unit = r.get("throughput_unit", "req/s")

        lines.append(f"### {r['title']}  (`{r['flag']}`)\n")
        lines.append(f"{r['claim']}.\n")

        if is_throughput:
            lines.append(f"Workload `{r['workload']}` — {r.get('scale_label', '')}.\n")
        else:
            lines.append(
                f"Workload `{r['workload']}` — {r['requests']} requests at "
                f"concurrency {r['concurrency']}.\n"
            )

        lines.append("| | Baseline | Optimized | Change |")
        lines.append("| :--- | ---: | ---: | ---: |")
        lines.append(f"| _implementation_ | {r['baseline_label']} | {r['optimized_label']} | |")

        # Percentiles only mean something when there is a distribution to
        # summarize. The throughput workloads produce a single timing per run,
        # so p50/p95/p99 would just be the same number repeated four times.
        if not is_throughput:
            for label, key, dkey in (
                ("p50", "p50_ms", "p50_pct"),
                ("p95", "p95_ms", "p95_pct"),
                ("p99", "p99_ms", "p99_pct"),
                ("mean", "mean_ms", "mean_pct"),
            ):
                lines.append(
                    f"| {label} latency | {r['baseline'][key]:.1f} ms | "
                    f"{r['optimized'][key]:.1f} ms | {r['deltas'][dkey]:+.1f}% |"
                )

        lines.append(
            f"| throughput | {r['baseline']['rps']:.0f} {unit} | "
            f"{r['optimized']['rps']:.0f} {unit} | {r['deltas']['rps_pct']:+.1f}% |"
        )
        lines.append(
            f"| failed | {r['baseline']['failed']} | {r['optimized']['failed']} | |"
        )

        if is_throughput:
            lines.append(
                f"\nPer-run spread ({unit}) — baseline `{r['baseline']['rps_runs']}`, "
                f"optimized `{r['optimized']['rps_runs']}`.\n"
            )
        else:
            lines.append(
                f"\nPer-run p99 spread (ms) — baseline `{r['baseline']['p99_runs_ms']}`, "
                f"optimized `{r['optimized']['p99_runs_ms']}`.\n"
            )

    return "\n".join(lines)


def render_resume_lines(records: list[dict]) -> str:
    """Print copy-pasteable, defensible one-liners."""
    out = ["", "=" * 72, "  RESUME-READY CLAIMS", "=" * 72, ""]
    for r in records:
        delta = r["headline_delta_pct"]
        if abs(delta) < 5:
            out.append(
                f"  [skip] {r['title']}: {delta:+.1f}% — within noise, "
                "do not claim this one."
            )
            continue
        if r["headline_metric"] == "throughput":
            out.append(
                f"  * {r['claim']} raised throughput {abs(delta):.0f}% "
                f"({r['baseline']['rps']:.0f} -> {r['optimized']['rps']:.0f} "
                f"{r.get('throughput_unit', 'req/s')}) on "
                f"{r.get('scale_label', 'the measured workload')}."
            )
        else:
            out.append(
                f"  * {r['claim']} cut p99 latency {abs(delta):.0f}% "
                f"({r['baseline']['p99_ms']:.0f}ms -> {r['optimized']['p99_ms']:.0f}ms) "
                f"at concurrency {r['concurrency']}."
            )
        out.append("")
    return "\n".join(out)


# ── Entry point ─────────────────────────────────────────────────────


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--only",
        nargs="*",
        choices=[e.key for e in EXPERIMENTS],
        help="Run only these experiments.",
    )
    parser.add_argument("--output", default="docs/benchmarks.md")
    parser.add_argument("--json-output", default="docs/benchmarks.json")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Re-render the markdown report from an existing benchmarks.json "
        "without re-running any experiments.",
    )
    args = parser.parse_args()

    if args.render_only:
        data = json.loads((REPO_ROOT / args.json_output).read_text(encoding="utf-8"))
        md_path = REPO_ROOT / args.output
        md_path.write_text(
            render_markdown(data["experiments"], data["meta"]), encoding="utf-8"
        )
        print(f"Re-rendered {md_path.relative_to(REPO_ROOT)} from existing results.")
        print(render_resume_lines(data["experiments"]))
        return 0

    if not API_KEY:
        print(
            "TELEMETRY_API_KEY is not set. Export the same key the stack is "
            "running with, e.g.\n"
            "    export TELEMETRY_API_KEY=$(grep TELEMETRY_API_KEY backend/.env | cut -d= -f2)",
            file=sys.stderr,
        )
        return 2

    selected = [e for e in EXPERIMENTS if not args.only or e.key in args.only]

    print(f"Running {len(selected)} experiment(s), {args.repeats} rounds each.")
    print("Each round restarts services — expect this to take a while.\n")

    records = []
    for experiment in selected:
        try:
            records.append(await run_experiment(experiment, args.base_url, args.repeats))
        except Exception as exc:
            print(f"  !! {experiment.key} failed: {exc}", file=sys.stderr)

    if not records:
        print("No experiments completed.", file=sys.stderr)
        return 1

    # Leave the stack in the fully-optimized state.
    print("\nRestoring all optimizations...")
    compose("up", "-d", "--force-recreate", "backend", "worker", check=False)

    meta = {
        "host": f"{os.cpu_count()} CPU, {sys.platform}",
        "repeats": args.repeats,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    md_path = REPO_ROOT / args.output
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(records, meta), encoding="utf-8")

    json_path = REPO_ROOT / args.json_output
    json_path.write_text(
        json.dumps({"meta": meta, "experiments": records}, indent=2), encoding="utf-8"
    )

    print(f"\nWrote {md_path.relative_to(REPO_ROOT)} and {json_path.relative_to(REPO_ROOT)}")
    print(render_resume_lines(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
