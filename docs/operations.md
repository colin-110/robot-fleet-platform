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
| `AUTH_MODE` | `open` (`required` in production) | `open` = public console, no accounts; `required` = every console action needs a JWT |
| `JWT_SECRET` | derived from `TELEMETRY_API_KEY` | Access-token signing key. 32+ chars required in production |
| `JWT_TTL_SECONDS` | `3600` | Access-token lifetime |
| `BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | unset | Creates the first admin at startup, only while the users table is empty |
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
| `LOG_FORMAT` | `text` (`json` in production) | `json` emits one object per line with the request id and structured fields, for a log aggregator |
| `ACCESS_LOG` | `true` | One structured line per HTTP request; `/health` and `/metrics` are excluded so scrapes do not bury real traffic |
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

The live demo runs on free tiers:

```
Browser --HTTPS--> Vercel (static dashboard build)
   |
   '--HTTPS/WSS--> EC2 host: nginx + Let's Encrypt --> backend container :8000
                                                     simulator container --'
                   backend --> Neon (PostgreSQL)
                           '-> Upstash (Redis)
```

- **Frontend:** Vercel builds `frontend/robot-fleet-dashboard` from source. `VITE_API_BASE_URL` points it at the EC2 host.
- **Backend and simulator:** one EC2 instance runs [`docker-compose.ec2.yml`](../docker-compose.ec2.yml), with no worker, Prometheus, or frontend container. nginx and the Let's Encrypt certificate run directly on the host, outside Compose, and proxy to the backend, which is bound to `127.0.0.1:8000`.
- **Data tier:** Neon (Postgres) and Upstash (Redis). Their URLs live in the `.env` next to the compose file on the host, not in this repository.
- **Ingest mode:** `OPT_REDIS_BUFFER=false`, so telemetry is written directly with no worker. While nobody has the dashboard open, writes are buffered in memory and flushed every 10 minutes (`app/telemetry_buffer.py`), which lets Neon's compute suspend between flushes.

The host pulls pre-built images from GHCR rather than building them. CI publishes the `backend` and `simulator` images on every push to `main` once the tests pass, tagged `latest` and with the commit SHA. To roll out, from the directory on the host that holds `docker-compose.ec2.yml` and `.env`:

```bash
IMAGE_TAG=<sha> docker compose -f docker-compose.ec2.yml -p fleetops pull
IMAGE_TAG=<sha> docker compose -f docker-compose.ec2.yml -p fleetops up -d
```

Pinning `IMAGE_TAG` gives you a deploy you can name and roll back to. Rollout is manual; nothing in CI deploys to the host.

Every container caps its logs at 30 MB (`10m` × 3 files). The json-file driver is unbounded by default, and with a simulator posting around the clock that is eventually a disk-full outage.

The backend healthcheck calls `/health/live`, which does not touch the database. `/health` runs `SELECT 1`, and polling that every 15 seconds would keep Neon's compute awake permanently.

### Lessons from the free-tier setup

Each of these caused a real outage:

- **Strip `sslmode`/`channel_binding` from Neon's `DATABASE_URL`.** Neon's connection string is shaped for `libpq`. SQLAlchemy's asyncpg dialect passes URL query params straight to `asyncpg.connect()`, which rejects `sslmode` with a `TypeError` before any migration runs. asyncpg negotiates TLS with Neon on its own.
- **`/health` must answer `HEAD`.** Uptime monitors and load-balancer probes send `HEAD`. A FastAPI `@app.get` route returns `405` for it, which monitors report as down. Health and root routes use `@app.api_route(path, methods=["GET", "HEAD"])`.
- **`CORS_ORIGINS` must match the `Origin` header exactly, with no trailing slash.** `https://your-app.vercel.app/` never matches, and the only symptom is a failed preflight.
- **The direct ingest path has to register robots itself.** Fleet status is the roster `LEFT JOIN`ed onto telemetry. With no worker, `TelemetryService` calls `RobotRepository.mark_seen()` on its own, otherwise robots never appear in `/robots/status`.
- **Direct batch ingest commits once per batch.** Committing per row meant up to 50 round trips to a remote Postgres per POST, slow enough to exceed the simulator's timeout.
- **Redis is required even with both `OPT_*` flags off.** Rate limiting, the read cache, and the `redis` WebSocket backend all use it. Upstash needs the `rediss://` scheme, because `redis-py` selects TLS from the scheme alone.
- **CartoDB's anonymous basemap CDN was discontinued.** The map uses OpenStreetMap tiles with a CSS filter for the dark theme.

### CI

On every push and pull request to `main`:

1. **`backend-test`:** `ruff check` across `backend/`, `scripts/`, and `simulator/`, plus `ruff format --check`, then pytest with coverage against real PostgreSQL and Redis service containers. All blocking.
2. **`frontend-test`:** ESLint, Vitest with coverage, and a production Vite build. All blocking.
3. **`publish-images`** (pushes to `main` only): builds and pushes the `backend` and `simulator` images to GHCR.

---

## Logging

One configuration for the API and the worker (`app/logging_config.py`), replacing two `basicConfig` calls that used different formats despite shipping in the same image.

Every line carries the `request_id` that `RequestIDMiddleware` returns as `X-Request-ID`, propagated through a `ContextVar` rather than threaded through call signatures — under asyncio that is per-task, so concurrent requests cannot see each other's value and a service function five frames deep gets correlation for free. An inbound `X-Request-ID` is honoured rather than replaced, so a trace started by nginx survives into the service.

`LOG_FORMAT=json` emits one JSON object per line; fields passed via `extra=` become queryable keys instead of text a dashboard has to regex. Production defaults to it.

Each request also produces a structured access line (`http_method`, `http_path`, `http_status`, `duration_ms`). Uvicorn runs with `--no-access-log` in production, so without this a deployed request left no trace at all.

---

## Monitoring

The backend exports Prometheus metrics at `/metrics`. Prometheus and Grafana run only in the local stack, as the opt-in `observability` profile:

```bash
docker compose --profile observability up -d   # Prometheus :9090, Grafana :3000
```

The production host runs neither, because they don't fit alongside the app on a free-tier instance. `/metrics` is currently reachable through the host's nginx without authentication. Metrics expose internal request volumes, so deny that path in the host nginx config, or restrict it to a known scraper. Alerting is not set up. See [Scaling roadmap](#scaling-roadmap).

---

## Security posture

| Control | Implementation |
| :--- | :--- |
| Operator identity | `AUTH_MODE=required` puts every console action behind a bcrypt-verified sign-in and an HS256 JWT carrying a signed role. Three roles, ranked: `viewer` reads, `operator` dispatches commands, `admin` covers both. Production defaults to required; the public demo sets `open` explicitly, which resolves every caller to an anonymous operator through the *same* dependency rather than skipping it — so the authenticated path is one code path, not a branch nobody runs. Login returns one message and one timing profile for both "no such user" and "wrong password", so it cannot enumerate accounts. |
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
