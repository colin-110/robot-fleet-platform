# Performance

[← Back to README](../README.md)

All figures below were measured on a single developer machine running the entire stack (API, worker, PostgreSQL, Redis, and the simulator) simultaneously. They are a reproducible single-node lower bound, not an aspirational target.

Machine-readable results live in [`benchmarks.json`](./benchmarks.json); the full per-experiment breakdown, including per-run spread, is regenerated into [`benchmarks.md`](./benchmarks.md) by the harness itself.

---

## A/B optimization benchmarks

Every optimization sits behind an `OPT_*` flag whose "off" state restores the implementation it replaced. [`scripts/benchmark_matrix.py`](../scripts/benchmark_matrix.py) uses this to measure each optimization **in isolation**: it restarts the affected services with the flag off, runs a workload, restarts with the flag on, runs the identical workload, and reports the delta.

| Optimization | Baseline it replaces | Metric | Baseline | Optimized | Change |
| :--- | :--- | :--- | ---: | ---: | ---: |
| Redis read-through cache (fleet status) | Recompute window-function query per request | p99 | 11,172 ms | 265 ms | **-97.6%** |
| Redis cache (analytics aggregation) | Recompute all aggregations per request | p99 | 2,169 ms | 165 ms | **-92.4%** |
| Redis Stream ingest buffer | Synchronous `INSERT` on the request path | p99 | 1,271 ms | 158 ms | **-87.6%** |
| Pure ASGI middleware | Starlette `BaseHTTPMiddleware` | p99 | 176 ms | 89 ms | **-49.5%** |
| Worker bulk `INSERT` | One `INSERT` + `COMMIT` per row | throughput | 242 rows/s | 985 rows/s | **+308%** |
| Bounded-queue WebSocket fan-out | `create_task` per client per message | throughput | 5,977 msg/s | 14,352 msg/s | **+140%** |

Three interleaved rounds per experiment, zero failed requests.

```bash
python scripts/benchmark_matrix.py --repeats 3
```

### Methodology

Measuring "everything on" against "everything off" shows that a stack got faster but not which change did it, so the harness isolates one variable per experiment. Arms are interleaved (off, on, off, on) so background load on the host biases both equally. Each configuration is warmed up before measurement, and the reported figure is the median of N runs with the full per-run spread printed alongside.

Before each run the harness reads `/health` and asserts the flags actually came up as requested, because a restart that silently kept the previous configuration would produce a meaningless zero-percent delta that looks like a real result. Read-path experiments re-seed a fixed dataset first, so results do not depend on what a previous experiment left in the table. Any arm where more than five percent of requests failed is rejected rather than reported, because the p99 of zero successful requests is `0.0`, which otherwise reads as infinitely fast.

### On a retired optimization

The orjson response class was measured at 17.9% faster on p99 and has since been
removed. The number was real but narrow: FastAPI serializes through Pydantic
directly whenever a route declares a `response_model`, bypassing the custom
response class entirely, so the gain only ever applied to the handful of routes
without one. FastAPI has since deprecated `ORJSONResponse` for that same reason.

The measurement is kept because finding that constraint was the point — the
harness is what turned a plausible library-level claim into a scoped fact.

### On measurement error

Two of these experiments initially reported wrong numbers, which is worth recording because it is precisely the failure mode this harness exists to catch.

The bulk-`INSERT` experiment first reported +1.1 percent: it was timing the HTTP request, but with the Redis buffer enabled that request only performs an `XADD`, so both arms were running identical code. It now measures worker drain throughput instead.

The analytics-cache experiment first reported +0.0 percent because every request had failed and the p99 of an empty sample is zero. The harness now fails loudly on that second class of error rather than reporting it.

---

## Load and stress testing

A separate harness ([`scripts/stress_test.py`](../scripts/stress_test.py)) drives single and batch ingest, REST reads, and WebSocket fan-out across a concurrency sweep up to 2,000, plus a sustained mixed-load run.

```bash
export TELEMETRY_API_KEY=$(grep TELEMETRY_API_KEY backend/.env | cut -d= -f2)
python scripts/stress_test.py --base-url http://localhost:8000
```

### WebSocket fan-out

| Concurrent clients | Connection failures | Messages (10 s) | Throughput |
| :--- | :--- | :--- | :--- |
| 500 | 0 | 67,692 | ~6,800 msg/s |
| 1,500 | 0 | 171,331 | ~17,100 msg/s |
| **2,000** | **0–17** | **~152k–178k** | **~15,000–18,000 msg/s** |

Across repeated runs the fan-out tier sustained 2,000 concurrent clients at 15,000 to 18,000 msg/s with greater than 99 percent delivery, which validates the bounded-queue design.

### HTTP ingest latency (single node)

| Concurrency | Success | p50 | p99 |
| :--- | :--- | :--- | :--- |
| 1 | 100% | 8 ms | 16 ms |
| 10 | 100% | 62 ms | 63 ms |
| 50 | 100% | 219 ms | 375 ms |

A 30-second sustained mixed-load run (ingest plus status and analytics reads at concurrency 50) completed roughly 2,900 to 3,900 requests with zero failures, at p50 380 to 410 ms.

### On variance

Because a single machine hosts the whole stack, results move with host load: a quiet machine reproduces the higher figures and a busy one the lower. The WebSocket tier is consistently strong. HTTP throughput is the honest single-node ceiling and is exactly what horizontal scaling addresses.
