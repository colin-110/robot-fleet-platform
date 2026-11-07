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
| `DEFERRED_FLUSH_SECONDS` | `600` | How often deferred writes are flushed. Deferred writes assume a single API process, since the buffer and acknowledgements are per process |
| `ALLOW_SHARED_INGEST_KEY` | `true` | Whether the master key still works on robot endpoints. Set `false` once every robot has a device key |
| `DEVICE_KEY_REFRESH_SECONDS` | `60` | How often each instance reloads device keys (multi-instance only), which bounds how long a revoked key keeps working on other instances |
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
| `ACCESS_LOG` | `true` | One structured line per HTTP request; `/health` and `/health/live` are excluded so health polls do not bury real traffic |
| `DEFER_WRITES_WITHOUT_VIEWERS` | `false` | Direct-write path only: while no dashboard is connected, buffer telemetry in memory and flush every 10 minutes so serverless Postgres (Neon) can suspend |
| `WEBSOCKET_BACKEND` | `redis` | `direct` fans out in-process - correct for exactly one API instance, and saves Redis commands |
| `RATE_LIMIT_BACKEND` | `redis` | `memory` keeps the sliding window in-process - same one-instance reasoning |

### Deployment-shape flags

These two pick between the full stack and the single-instance stack production runs.

| Flag | `true` (local default) | `false` (production on EC2) |
| :--- | :--- | :--- |
| `OPT_REDIS_BUFFER` | `XADD` to a Redis Stream; the worker persists in batches | `INSERT` on the request path; no worker needed |
| `OPT_READ_CACHE` | Redis read-through cache, 10 s TTL | Recompute the aggregation per request (fewer Upstash commands) |

---

## Deployment

Production is one free-tier EC2 instance running [`docker-compose.ec2.yml`](../docker-compose.ec2.yml): just the backend and the simulator. Everything else is a managed free tier - **Neon** (Postgres), **Upstash** (Redis), **Vercel** (the static frontend build). nginx and Let's Encrypt run directly on the host, not in Compose, and proxy HTTPS to the backend on `127.0.0.1:8000`.

```
Viewer --HTTPS--> Vercel (static dashboard)
   |
   '--HTTPS/WSS--> EC2 nginx --> FastAPI :8000 (+ simulator container)
                                   |--> Neon (PostgreSQL)
                                   '--> Upstash (Redis)
```

There is no worker on this box, so the host's `.env` runs `OPT_REDIS_BUFFER=false` and `OPT_READ_CACHE=false`, plus `DEFER_WRITES_WITHOUT_VIEWERS=true` so Neon gets idle windows long enough to suspend. The Docker healthcheck polls `/health/live`, which does not touch the database - `/health` runs `SELECT 1`, and polling that every 15 seconds would keep Neon awake on its own.

The host **pulls pre-built images from GHCR rather than building**. Building on a t3.micro (1 GB RAM, 7.6 GB disk) filled the disk mid-`npm ci` and left the stack half-upgraded. CI only publishes images after the tests pass. `IMAGE_TAG` defaults to `latest` and can be pinned to a commit SHA for a deploy you can name and roll back to:

```bash
cd /opt/fleetops
IMAGE_TAG=<sha> docker compose -f docker-compose.ec2.yml -p fleetops pull
IMAGE_TAG=<sha> docker compose -f docker-compose.ec2.yml -p fleetops up -d
```

Every container caps its logs at 30 MB (`10m` × 3 files); the json-file driver is unbounded by default, which on a 7.6 GB disk is a disk-full outage waiting to happen.

### Provisioning the host

[`scripts/deploy_aws.ps1`](../scripts/deploy_aws.ps1) creates (or adopts) the instance, and [`scripts/ec2_user_data.sh`](../scripts/ec2_user_data.sh) installs Docker, adds 1 GB of swap, and clones the repo to `/opt/fleetops`. Two details matter:

- **Tag `role=fleet-app`.** It is the SSM target the `deploy-aws` job selects on. `send-command` treats a target that matches nothing as success, so an untagged host is a silent non-deploy.
- **Instance profile `fleet-ssm-profile`** (`AmazonSSMManagedInstanceCore`). Without it the SSM agent never registers and the host is invisible to the pipeline.

Two steps stay manual because they involve secrets: writing `/opt/fleetops/.env` (see [`backend/.env.example`](../backend/.env.example)), and `docker login ghcr.io` if the GHCR packages are private.

### Gotchas hit on this stack

- **`DATABASE_URL` needs the `sslmode`/`channel_binding` query params stripped.** Neon hands you a `libpq`-shaped string; asyncpg rejects `sslmode` with a `TypeError` before a single migration runs. Drop both; asyncpg negotiates TLS with Neon automatically.
- **Health routes must answer `HEAD`.** Uptime monitors and load balancers default to `HEAD`; `@app.get` only registers `GET`, so they read a healthy service as down. Health/root routes use `@app.api_route(path, methods=["GET", "HEAD"])`.
- **`CORS_ORIGINS` must match the browser's `Origin` exactly - no trailing slash.** `https://your-app.vercel.app/` never matches.
- **The direct ingest path registers robots itself.** Fleet status is the roster `LEFT JOIN`ed onto telemetry; with no worker, `TelemetryService` must call `RobotRepository.mark_seen()` or robots never appear.
- **Direct batch ingest commits once per batch.** Per-row commits to a remote Postgres were slow enough to exceed the simulator's POST timeout and silently drop whole batches.
- **Upstash URLs use `rediss://`.** `redis-py` picks TLS from the URL scheme alone.
- **CartoDB's free basemap CDN was discontinued.** The map uses OpenStreetMap tiles, with the dark theme approximated by a CSS filter.

### CI/CD

On every push and pull request to `main`:

1. **`backend-test`** - `ruff check` across `backend/` and `simulator/`, plus `ruff format --check`. Both are blocking. Then pytest with coverage against real PostgreSQL and Redis service containers.
2. **`frontend-test`** - ESLint (zero errors, zero warnings), Vitest with coverage, and a production Vite build. All blocking.
3. **`publish-images`** - builds and pushes the `backend` and `simulator` images to GHCR on `main`, tagged both `latest` and the commit SHA. These are the images the host pulls; nothing is built on the instance. The frontend is deployed by Vercel, not from an image.
4. **`deploy-aws`** - gated SSM-based rollout, opt-in via a repository variable. The AWS identity it assumes is created by [`scripts/setup_oidc_role.ps1`](../scripts/setup_oidc_role.ps1): a GitHub OIDC provider and a role trusted only by this repository on `main`, so the pipeline holds no long-lived key. Its one permission is `ssm:SendCommand`, conditioned on `ssm:resourceTag/role = fleet-app` - without that condition the role could run shell on every instance in the account. It resolves the tagged, online instances before sending anything and fails when there are none, then polls each invocation to completion and reports the host's stdout into the job log. `send-command` on its own is fire-and-forget, so without this a deploy that ran nowhere would still report success. The host is reset to the deployed SHA rather than to `origin/main`, then runs `docker-compose.ec2.yml`, so the compose file on the box comes from the same commit as the images `IMAGE_TAG` pins.

---

## Logging

One configuration for the API and the worker (`app/logging_config.py`), replacing two `basicConfig` calls that used different formats despite shipping in the same image.

Every line carries the `request_id` that `RequestIDMiddleware` returns as `X-Request-ID`, propagated through a `ContextVar` rather than threaded through call signatures - under asyncio that is per-task, so concurrent requests cannot see each other's value and a service function five frames deep gets correlation for free. An inbound `X-Request-ID` is honoured rather than replaced, so a trace started by nginx survives into the service.

`LOG_FORMAT=json` emits one JSON object per line; fields passed via `extra=` become queryable keys instead of text a dashboard has to regex. Production defaults to it.

Each request also produces a structured access line (`http_method`, `http_path`, `http_status`, `duration_ms`). Uvicorn runs with `--no-access-log` in production, so without this a deployed request left no trace at all.

---

## Monitoring

There is no metrics stack. The signals that exist are:

- `GET /health/live` - process liveness, no database round trip. This is what Docker polls.
- `GET /health` - checks the database and reports the active `opt_*` flags. Use it by hand; don't poll it against Neon.
- `GET /api/v1/viewers` (API key) - connected WebSocket clients.
- Structured logs (above). The worker, when it runs, logs a warning when the telemetry stream nears `TELEMETRY_STREAM_MAXLEN`, past which Redis trims unacknowledged entries.

Alerting is absent - see [Scaling roadmap](#scaling-roadmap).

---

## Security posture

| Control | Implementation |
| :--- | :--- |
| Operator identity | `AUTH_MODE=required` puts every console action behind a bcrypt-verified sign-in and an HS256 JWT carrying a signed role. Three roles, ranked: `viewer` reads, `operator` dispatches commands, `admin` covers both. Production defaults to required; the public demo sets `open` explicitly, which resolves every caller to an anonymous operator through the *same* dependency rather than skipping it - so the authenticated path is one code path, not a branch nobody runs. Login returns one message and one timing profile for both "no such user" and "wrong password", so it cannot enumerate accounts. |
| Browser credentials | The dashboard is never built with the master key. It requests a short-lived console ticket at runtime (`POST /api/v1/auth/ticket`) - HMAC-signed over its own scope and expiry, so a holder can neither widen its permissions nor extend its life. Ingest still requires the master key, which now stays server-side, so reading the bundle no longer yields a credential that can forge telemetry for the fleet. Rotating the API key revokes every outstanding ticket, since the signing key is derived from it. |
| Robot credentials | Each robot can have its own device key, `rk_<robot>_<random>`, issued with `POST /api/v1/robots/{id}/key` (master key or an admin account) and revoked with `DELETE` on the same path. A device key can only send telemetry and events for its own robot and only claim or update that robot's commands; anything else is a 403. Keys are random and only their SHA-256 hash is stored, so a revoked key can never become valid again, a database leak exposes no usable keys, and rotating the master key leaves device keys alone. Hashes are cached in memory, so checking a key does no database work. |
| API key comparison | `secrets.compare_digest`. A plain `!=` short-circuits at the first differing byte, leaking key prefixes through response timing. |
| Secret defaults | None. `TELEMETRY_API_KEY` has no fallback value; the application fails at startup if it is missing. |
| Production guards | `APP_ENV=production` rejects wildcard CORS origins and API keys shorter than 16 characters at configuration load. |
| CORS | Credentials are dropped automatically when the origin list is a wildcard, since the specification forbids that combination and browsers reject the response. |
| Client IP resolution | The rate limiter reads `X-Forwarded-For` using a configured trusted-proxy count, taking the Nth entry from the right. Trusting the leftmost entry would let a client spoof its own address; trusting the socket peer would collapse the entire fleet into one bucket behind a proxy. |
| Rate limiting | Redis sorted-set sliding window on ingest endpoints. Fails open, so a Redis outage degrades rate limiting rather than halting ingestion. |
| State transitions | Atomic compare-and-set `UPDATE` statements, so concurrent writers cannot both commit a transition. |
| Read endpoints | Fleet status, analytics, and events are public by default so the hosted demo works without shipping a key to every browser. This is a setting (`REQUIRE_AUTH_FOR_READS`), not a hardcoded exemption; it is the wrong default for real fleet positions. |
| Secret hygiene | `.env`, `*.pem`, `*.key`, and `*.tfvars` are gitignored |

---

## Scaling roadmap

In priority order, if this needed to handle a genuinely large fleet:

1. **Scale the API horizontally.** The tier is already stateless. Place it behind an ALB in an Auto Scaling Group; every instance drains the same shared Redis stream, so ingest and fan-out capacity grow close to linearly with node count. This is the single largest win and the code supports it today.
2. **Partition the telemetry table**, using native PostgreSQL partitioning or TimescaleDB. Retention becomes an instant `DROP PARTITION` rather than a batched `DELETE`, and time-range queries get substantially cheaper.
3. **Replace Redis Streams with Kafka** if throughput reaches millions of messages per second, or if multiple independent consumer groups and long replay windows become necessary.
4. **Multi-tenancy.** Per-fleet isolation, OIDC for operators, and per-key rate limiting.
5. **Managed high-availability data tier.** Multi-AZ RDS with read replicas and PgBouncer for the read path, plus a clustered or replicated Redis.
6. **Dedicated realtime gateway.** Move WebSocket fan-out to its own horizontally scaled tier so it scales independently of the ingest API.
7. **Observability.** Metrics (Prometheus or a hosted equivalent), alerting, and distributed tracing (OpenTelemetry) across the ingest, worker, and database path.
