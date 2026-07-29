# Operations

[← Back to README](../README.md)

Configuration, deployment, security posture, and the scaling roadmap. For local setup see [Running Locally](../README.md#running-locally) in the README.

---

## Configuration

All settings are environment variables, loaded and validated by `pydantic-settings` at startup. See [`backend/.env.example`](../backend/.env.example) for the full list.

### Operational settings

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DATABASE_URL` | *required* | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `TELEMETRY_API_KEY` | *required* | Master key for the `X-API-Key` header (ingest, robot-facing command polling) and the signing key that console tickets are derived from |
| `TICKET_TTL_SECONDS` | `300` | Lifetime of a browser console ticket |
| `APP_ENV` | `development` | `production` additionally rejects wildcard CORS and API keys shorter than 16 characters |
| `CORS_ORIGINS` | localhost origins | Comma-separated allowed origins |
| `TRUSTED_PROXY_COUNT` | `0` | Number of reverse proxies in front of the app; controls how the rate limiter resolves the client IP |
| `RATE_LIMIT_PER_MINUTE` | `600` | Ingest requests permitted per client per minute |
| `CONSOLE_RATE_LIMIT_PER_MINUTE` | `60` | Separate, tighter budget for what a browser can reach without the master key: minting console tickets and dispatching commands |
| `REQUIRE_AUTH_FOR_READS` | `false` | Whether fleet status, analytics, and events require an API key |
| `OFFLINE_AFTER_SECONDS` | `60` | Seconds of silence before a robot is reported OFFLINE |
| `FLEET_WINDOW_MINUTES` | `15` | How far back fleet status scans for telemetry |
| `MAX_CLOCK_SKEW_SECONDS` | `300` | Future-dated device timestamps beyond this are clamped to server time |
| `TELEMETRY_STREAM_MAXLEN` | `100000` | Entries retained in the ingest stream; the worker's catch-up headroom |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Required when running more than one Uvicorn worker |

### Optimization flags

Each flag defaults to enabled. Setting one to `false` restores the naive implementation it replaced, which is how the [benchmark harness](./performance.md) measures its individual contribution.

| Flag | Enabled | Disabled |
| :--- | :--- | :--- |
| `OPT_REDIS_BUFFER` | `XADD` to a Redis Stream, worker persists | Synchronous `INSERT` on the request path |
| `OPT_READ_CACHE` | Redis read-through cache, 10 s TTL | Recompute the aggregation per request |
| `OPT_ASGI_MIDDLEWARE` | Pure ASGI middleware | Starlette `BaseHTTPMiddleware` |
| `OPT_BOUNDED_FANOUT` | Bounded per-client queue and sender task | `create_task` per client per message |
| `OPT_BATCH_INSERT` | One multi-row `INSERT` per batch | One `INSERT` and `COMMIT` per row |

---

## Deployment

A single `t3.micro` EC2 instance runs the whole stack via Docker Compose, backed by managed Amazon RDS (PostgreSQL) and ElastiCache (Redis), fronted by CloudFront for HTTPS. On boot the instance pulls the repository and self-deploys the backend, worker, nginx frontend, and simulator. Nginx serves the single-page application and proxies REST and WebSocket traffic to the backend. GitHub Actions publishes container images to GHCR on every push to `main`.

```
Viewer --HTTPS--> CloudFront --HTTP--> EC2 (nginx :80 -> FastAPI :8000)
                                          |--> Amazon RDS (PostgreSQL)
                                          '--> Amazon ElastiCache (Redis)
```

This is the cost-optimized single-node topology. The multi-node high-availability version (ALB, Auto Scaling Group, Multi-AZ RDS) is described under [Scaling roadmap](#scaling-roadmap); the application tier is already stateless and ready for it.

The production compose file refuses to start without an explicit `CORS_ORIGINS` value, and sets `TRUSTED_PROXY_COUNT=2` to account for nginx and CloudFront.

### CI/CD

On every push and pull request to `main`:

1. **`backend-test`** — `ruff check` across `backend/`, `scripts/`, and `simulator/`, plus `ruff format --check`. Both are blocking. Then pytest with coverage against real PostgreSQL and Redis service containers.
2. **`frontend-test`** — ESLint (zero errors, zero warnings), Vitest with coverage, and a production Vite build. All blocking.
3. **`publish-images`** — builds and pushes versioned `backend` and `frontend` images to GHCR on `main`.
4. **`deploy-aws`** — gated SSM-based rollout, opt-in via a repository variable.

---

## Security posture

| Control | Implementation |
| :--- | :--- |
| Browser credentials | The dashboard is never built with the master key. It requests a short-lived console ticket at runtime (`POST /api/v1/auth/ticket`) — HMAC-signed over its own scope and expiry, so a holder can neither widen its permissions nor extend its life. Ingest still requires the master key, which now stays server-side, so reading the bundle no longer yields a credential that can forge telemetry for the fleet. Rotating the API key revokes every outstanding ticket, since the signing key is derived from it. |
| API key comparison | `secrets.compare_digest`. A plain `!=` short-circuits at the first differing byte, leaking key prefixes through response timing. |
| Secret defaults | None. `TELEMETRY_API_KEY` has no fallback value; the application fails at startup if it is missing. |
| Production guards | `APP_ENV=production` rejects wildcard CORS origins and API keys shorter than 16 characters at configuration load. |
| CORS | Credentials are dropped automatically when the origin list is a wildcard, since the specification forbids that combination and browsers reject the response. |
| Client IP resolution | The rate limiter reads `X-Forwarded-For` using a configured trusted-proxy count, taking the Nth entry from the right. Trusting the leftmost entry would let a client spoof its own address; trusting the socket peer would collapse the entire fleet into one bucket behind a proxy. |
| Rate limiting | Redis sorted-set sliding window on ingest endpoints. Fails open, so a Redis outage degrades rate limiting rather than halting ingestion. |
| State transitions | Atomic compare-and-set `UPDATE` statements, so concurrent writers cannot both commit a transition. |
| Read endpoints | Fleet status, analytics, and events are public by default so the hosted demo works without shipping a key to every browser. This is a setting (`REQUIRE_AUTH_FOR_READS`), not a hardcoded exemption; it is the wrong default for real fleet positions. |
| Secret hygiene | `.env`, `*.pem`, `*.key`, and `*.tfvars` are gitignored; the load-test harness reads its key from the environment rather than a literal. |

---

## Scaling roadmap

In priority order, if this needed to handle a genuinely large fleet:

1. **Scale the API horizontally.** The tier is already stateless. Place it behind an ALB in an Auto Scaling Group; every instance drains the same shared Redis stream, so ingest and fan-out capacity grow close to linearly with node count. This is the single largest win and the code supports it today.
2. **Partition the telemetry table**, using native PostgreSQL partitioning or TimescaleDB. Retention becomes an instant `DROP PARTITION` rather than a batched `DELETE`, and time-range queries get substantially cheaper.
3. **Replace Redis Streams with Kafka** if throughput reaches millions of messages per second, or if multiple independent consumer groups and long replay windows become necessary.
4. **Real authentication and multi-tenancy.** JWT or OIDC, per-fleet isolation, and per-key rate limiting.
5. **Managed high-availability data tier.** Multi-AZ RDS with read replicas and PgBouncer for the read path, plus a clustered or replicated Redis.
6. **Dedicated realtime gateway.** Move WebSocket fan-out to its own horizontally scaled tier so it scales independently of the ingest API.
7. **Deeper observability.** Distributed tracing (OpenTelemetry) across the ingest, worker, and database path, plus alerting on the existing Prometheus metrics.
