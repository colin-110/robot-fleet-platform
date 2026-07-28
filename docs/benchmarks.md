# Optimization Benchmarks

Every optimization in this system sits behind an `OPT_*` flag. Each row below was produced by running the same workload twice — once with the flag off (the naive implementation) and once with it on — restarting the affected services in between.

- **Host:** 12 CPU, win32
- **Repeats:** 3 interleaved rounds per experiment; the reported figure is the median run
- **Generated:** 2026-07-28 14:40:52

> Runs are interleaved (off, on, off, on…) so background load on the host biases both arms equally. The per-run spread is listed under each experiment; treat any delta smaller than that spread as noise.

## Summary

| Optimization | Workload | Metric | Baseline | Optimized | Change |
| :--- | :--- | :--- | ---: | ---: | ---: |
| Redis Stream ingest buffer | `ingest` | p99 latency | 1270.6 ms | 157.8 ms | **87.6% faster** |
| Redis read-through cache | `read_status` | p99 latency | 11172.0 ms | 264.9 ms | **97.6% faster** |
| Redis cache on analytics aggregation | `read_analytics` | p99 latency | 2169.5 ms | 164.5 ms | **92.4% faster** |
| Pure ASGI middleware | `health` | p99 latency | 176.3 ms | 89.0 ms | **49.5% faster** |
| orjson serialization | `read_status` | p99 latency | 224.0 ms | 183.8 ms | **17.9% faster** |
| Worker bulk INSERT | `worker_drain` | throughput | 242 rows/s persisted | 985 rows/s persisted | **307.7% faster** |
| Bounded-queue WebSocket fan-out | `ws_fanout` | throughput | 5977 msg/s | 14352 msg/s | **140.1% faster** |

## Detail

### Redis Stream ingest buffer  (`OPT_REDIS_BUFFER`)

Decoupling the write path from PostgreSQL with a Redis Stream.

Workload `ingest` — 800 requests at concurrency 50.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | Synchronous INSERT on request path | XADD to Redis Stream, worker persists | |
| p50 latency | 318.8 ms | 103.0 ms | +67.7% |
| p95 latency | 1095.8 ms | 154.3 ms | +85.9% |
| p99 latency | 1270.6 ms | 157.8 ms | +87.6% |
| mean latency | 375.6 ms | 111.1 ms | +70.4% |
| throughput | 131 msg/s | 438 msg/s | +234.3% |
| failed | 0 | 0 | |

Per-run p99 spread (ms) — baseline `[1335.05, 1243.73, 1270.57]`, optimized `[152.94, 162.83, 157.82]`.

### Redis read-through cache  (`OPT_READ_CACHE`)

Caching computed fleet status behind a 10s TTL.

Workload `read_status` — 600 requests at concurrency 50.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | Recompute window-function query per request | Redis cache hit | |
| p50 latency | 7002.3 ms | 204.1 ms | +97.1% |
| p95 latency | 9232.6 ms | 253.8 ms | +97.3% |
| p99 latency | 11172.0 ms | 264.9 ms | +97.6% |
| mean latency | 6973.9 ms | 212.3 ms | +97.0% |
| throughput | 7 msg/s | 228 msg/s | +3207.5% |
| failed | 0 | 0 | |

Per-run p99 spread (ms) — baseline `[11172.03, 12907.99, 10471.57]`, optimized `[264.89, 253.58, 315.45]`.

### Redis cache on analytics aggregation  (`OPT_READ_CACHE`)

Caching multi-aggregate analytics behind a 10s TTL.

Workload `read_analytics` — 400 requests at concurrency 50.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | Recompute all aggregations per request | Redis cache hit | |
| p50 latency | 848.5 ms | 71.6 ms | +91.6% |
| p95 latency | 1861.6 ms | 157.9 ms | +91.5% |
| p99 latency | 2169.5 ms | 164.5 ms | +92.4% |
| mean latency | 962.5 ms | 81.0 ms | +91.6% |
| throughput | 50 msg/s | 587 msg/s | +1073.6% |
| failed | 0 | 0 | |

Per-run p99 spread (ms) — baseline `[2169.47, 2137.36, 2261.14]`, optimized `[161.41, 175.39, 164.53]`.

### Pure ASGI middleware  (`OPT_ASGI_MIDDLEWARE`)

Replacing two BaseHTTPMiddleware layers with pure ASGI middleware.

Workload `health` — 2000 requests at concurrency 50.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | Starlette BaseHTTPMiddleware (task + anyio streams) | Pure ASGI send-wrapper | |
| p50 latency | 83.8 ms | 43.5 ms | +48.1% |
| p95 latency | 146.7 ms | 85.1 ms | +42.0% |
| p99 latency | 176.3 ms | 89.0 ms | +49.5% |
| mean latency | 91.1 ms | 48.0 ms | +47.3% |
| throughput | 537 msg/s | 1009 msg/s | +88.1% |
| failed | 0 | 0 | |

Per-run p99 spread (ms) — baseline `[168.19, 176.35, 176.74]`, optimized `[84.52, 90.46, 89.0]`.

### orjson serialization  (`OPT_ORJSON`)

Serializing float-heavy telemetry responses with orjson.

Workload `read_status` — 600 requests at concurrency 25.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | stdlib json | orjson | |
| p50 latency | 122.3 ms | 93.9 ms | +23.2% |
| p95 latency | 168.8 ms | 148.7 ms | +11.9% |
| p99 latency | 224.0 ms | 183.8 ms | +17.9% |
| mean latency | 125.7 ms | 98.4 ms | +21.7% |
| throughput | 193 msg/s | 247 msg/s | +28.1% |
| failed | 0 | 0 | |

Per-run p99 spread (ms) — baseline `[223.97, 195.25, 225.83]`, optimized `[186.64, 155.54, 183.82]`.

### Worker bulk INSERT  (`OPT_BATCH_INSERT`)

Persisting worker batches as one multi-row INSERT.

Workload `worker_drain` — a 10,000-row burst.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | One INSERT + COMMIT per row | One multi-row INSERT per batch | |
| throughput | 242 rows/s persisted | 985 rows/s persisted | +307.7% |
| failed | 0 | 0 | |

Per-run spread (rows/s persisted) — baseline `[242.6, 241.6, 237.7]`, optimized `[981.1, 988.4, 985.1]`.

### Bounded-queue WebSocket fan-out  (`OPT_BOUNDED_FANOUT`)

Bounded per-client queues with drop-oldest backpressure.

Workload `ws_fanout` — 300 concurrent WebSocket clients.

| | Baseline | Optimized | Change |
| :--- | ---: | ---: | ---: |
| _implementation_ | asyncio.create_task per client per message | Bounded queue + dedicated sender task | |
| throughput | 5977 msg/s | 14352 msg/s | +140.1% |
| failed | 0 | 0 | |

Per-run spread (msg/s) — baseline `[6209.2, 5977.3, 6200.0]`, optimized `[14370.6, 14351.9, 14048.4]`.
