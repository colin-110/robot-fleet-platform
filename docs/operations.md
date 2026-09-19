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

A single `t3.micro` EC2 instance runs the whole stack via Docker Compose ([`docker-compose.aws.yml`](../docker-compose.aws.yml)), backed by managed Amazon RDS (PostgreSQL) and ElastiCache (Redis), fronted by CloudFront for HTTPS.

The host **pulls pre-built images from GHCR rather than building**. Building on the box was a recurring failure: 900 MB of RAM and a 7.6 GB disk, where a Vite build exhausted the disk (`ENOSPC` inside `npm ci`) and left the stack half-upgraded. CI builds the same images on every push to main and only publishes them once the tests pass, so pulling is both lighter and better-verified than building locally. `IMAGE_TAG` defaults to `latest` and can be pinned to a commit SHA for a deploy you can name and roll back to.

```bash
cd /opt/fleetops
IMAGE_TAG=<sha> docker compose -f docker-compose.aws.yml -p fleetops pull
IMAGE_TAG=<sha> docker compose -f docker-compose.aws.yml -p fleetops up -d
```

### Provisioning the host

[`scripts/deploy_aws.ps1`](../scripts/deploy_aws.ps1) creates the instance, and [`scripts/ec2_user_data.sh`](../scripts/ec2_user_data.sh) brings it to the state the rollout assumes. Four of those details are load-bearing rather than incidental:

- **Tag `role=fleet-app`.** It is the SSM target the `deploy-aws` job selects on. An untagged instance is not a slow deploy, it is a silent one — `send-command` treats a target that matches nothing as a success.
- **Instance profile `fleet-ssm-profile`** (`AmazonSSMManagedInstanceCore`). Without it the preinstalled SSM agent never registers and the host is invisible to Systems Manager, tag or no tag.
- **Security group `fleet-ec2-sg`.** Not just any group that opens 80 and 22 — specifically the one the RDS and ElastiCache groups name as a source. A host in the wrong group provisions cleanly, starts cleanly, and then hangs on its first database connection, with nothing in the provisioning output to suggest why. The script checks this now and warns; `-SecurityGroupName` overrides it.
- **User-data bootstrap.** Installs Docker with the Compose plugin, adds 1 GB of swap on the 1 GB box, and clones the repository to `/opt/fleetops` — the directory the rollout `cd`s into.

The script is re-runnable, and repairs as well as creates. It reuses an instance already tagged `role=fleet-app`; failing that, it adopts an untagged one launched with the `fleet-key` pair — the shape a host provisioned before this script applied tags — by tagging it and attaching the instance profile, which EC2 permits on a running instance. So a host that predates any of this becomes SSM-manageable without being rebuilt. Only when neither exists does it launch. Pass `-ForceNewInstance` to add a second host deliberately, remembering that both will then match the CD job's tag target.

User-data runs once, at first boot, so an adopted host never sees it. [`ec2_user_data.sh`](../scripts/ec2_user_data.sh) is written to be idempotent for exactly that case — copy it over and run it with `sudo` to install Docker and create the checkout. The script prints the two commands.

Two steps stay manual, because both involve secrets that are deliberately not in the repository: writing `backend/.env` on the host, and `docker login ghcr.io` if the GHCR packages are private. Without the first the stack starts unconfigured; without the second `docker compose pull` fails with `denied`.

The security group opens 22 (for the Prometheus tunnel below) and 80. Not 8000 — the backend port is never published, since nginx reaches it over the internal Compose network.

Nginx serves the single-page application and proxies REST and WebSocket traffic to the backend container.

```
Viewer --HTTPS--> CloudFront --HTTP--> EC2 (nginx :80 -> FastAPI :8000)
                                          |--> Amazon RDS (PostgreSQL)
                                          '--> Amazon ElastiCache (Redis)
```

This is the cost-optimized single-node topology. The multi-node high-availability version (ALB, Auto Scaling Group, Multi-AZ RDS) is described under [Scaling roadmap](#scaling-roadmap); the application tier is already stateless and ready for it.

`CORS_ORIGINS` and `TRUSTED_PROXY_COUNT` both come from `backend/.env` on the host — Compose interpolation cannot read `env_file`, so the deploy has no way to enforce them and will start without either. Set them there. `APP_ENV=production` rejects `CORS_ORIGINS=*` at startup, and `TRUSTED_PROXY_COUNT=2` accounts for nginx and CloudFront; leaving it at `0` makes the rate limiter trust a client-supplied `X-Forwarded-For`.

Every container caps its logs at 30 MB (`10m` × 3 files). The json-file driver is unbounded by default, which on a 7.6 GB disk shared with a simulator that posts around the clock is a disk-full outage waiting for a long enough uptime.

Backend, worker, and frontend each declare a healthcheck. The worker's probe reads the mtime of the heartbeat file its own task rewrites every 10 seconds, so a heartbeat that dies inside a live process is caught rather than passing forever. Worker and simulator wait on `condition: service_healthy` — the backend owns the migrations, and 24 robots posting at a backend still running Alembic is the startup race that gating removes. The frontend deliberately does not wait: `depends_on` only orders startup, so gating it would trade nothing at runtime for a site that fails to load at all when the backend is down.

### Free-tier deployment (the live demo)

No EC2, no Docker host to manage: **Neon** (Postgres), **Render** (two separate Docker web services — API and simulator), **Upstash** (Redis), **Vercel** (the static frontend build), and **UptimeRobot** pinging both Render `/health` endpoints every 5 minutes so neither free instance spins down after 15 minutes idle. Costs nothing to run. Every one of the following was a real outage while setting this up, not a hypothetical:

- **`DATABASE_URL` needs the `sslmode`/`channel_binding` query params stripped.** Neon's dashboard hands you a connection string shaped for `psycopg2`/`libpq`. This app's driver is `asyncpg`, and SQLAlchemy's asyncpg dialect passes URL query params straight through as connection kwargs — `asyncpg.connect()` doesn't accept `sslmode` and throws `TypeError` before a single migration runs. Drop both params; asyncpg negotiates TLS with Neon automatically.
- **`/health` needs to answer `HEAD`, not just `GET`.** UptimeRobot (and most uptime monitors, and load balancer health probes generally) send `HEAD` by default to skip the response body. A route declared with `@app.get(...)` only registers `GET` in FastAPI — `HEAD` comes back `405`, which every monitor reports as "down" even though the service is healthy. Declare health/root routes with `@app.api_route(path, methods=["GET", "HEAD"])`.
- **`CORS_ORIGINS` must match the browser's `Origin` header exactly — no trailing slash.** `Origin` is always scheme+host+port, never a path. `https://your-app.vercel.app/` (with the slash) will never match, and every request gets silently rejected with no indication beyond a `400` on the OPTIONS preflight and a browser console CORS error.
- **The direct (non-buffered) ingest path needs its own roster registration.** Fleet status is the robot roster `LEFT JOIN`ed onto telemetry (see [Architecture](../README.md#architecture)) — a robot that never gets a roster row never appears in `/robots/status`, no matter how much telemetry it's sent. `RobotRepository.mark_seen()` was only ever called from the worker's buffered-path batch processing; running `OPT_REDIS_BUFFER=false` with no worker meant telemetry accumulated for real while the roster stayed empty. Call the same upsert from `TelemetryService`'s direct-path branches.
- **The direct batch-ingest path needs one commit per batch, not one per row.** The naive direct-path implementation committed per telemetry row inside the batch loop — up to 50 sequential round trips to a remote Postgres instance per simulator POST, easily slow enough to exceed the simulator's own POST timeout and have the whole batch silently dropped client-side with nothing to retry it. Use one multi-row `INSERT` and one `commit()` for the whole batch (`TelemetryRepository.insert_many()`).
- **Redis is not actually optional, even with both `OPT_*` buffering flags off.** Those flags only gate telemetry-write buffering and the analytics read cache. WebSocket fan-out (`websocket_manager.py`) is built on Redis Streams unconditionally — with no `REDIS_URL`, the app falls back to `localhost:6379` and retries a failed connection forever, filling logs and leaving the dashboard on REST-poll-only. A free Upstash instance is enough; use the `rediss://` (TLS) scheme, since `redis-py`'s `ConnectionPool.from_url()` selects TLS from the URL scheme alone.
- **CartoDB's free anonymous basemap CDN (`basemaps.cartocdn.com`) was discontinued.** Every tile request now returns an "API KEY REQUIRED" placeholder image regardless of referrer. Switched to OpenStreetMap's standard tiles (free, no key); the dashboard's dark theme is approximated with a CSS filter on the Leaflet tile pane rather than a second CartoDB-shaped tile source.
- **A Render free-tier container's clock can drift hours behind real time** after being idle/frozen and resumed. Telemetry timestamped hours in the past passes the backend's clock-skew guard unclamped — it only clamps *future*-dated readings, since backdated ones are legitimate store-and-forward — so every reading looked "old" and every robot showed `OFFLINE` despite telemetry actively flowing. A redeploy resets the container and its clock; there's no code-level fix for host clock drift.

### CI/CD

On every push and pull request to `main`:

1. **`backend-test`** — `ruff check` across `backend/`, `scripts/`, and `simulator/`, plus `ruff format --check`. Both are blocking. Then pytest with coverage against real PostgreSQL and Redis service containers.
2. **`frontend-test`** — ESLint (zero errors, zero warnings), Vitest with coverage, and a production Vite build. All blocking.
3. **`publish-images`** — builds and pushes `backend`, `frontend`, and `simulator` images to GHCR on `main`, tagged both `latest` and the commit SHA. These are the images the host pulls; nothing is built on the instance.
4. **`deploy-aws`** — gated SSM-based rollout, opt-in via a repository variable. The AWS identity it assumes is created by [`scripts/setup_oidc_role.ps1`](../scripts/setup_oidc_role.ps1): a GitHub OIDC provider and a role trusted only by this repository on `main`, so the pipeline holds no long-lived key. Its one permission is `ssm:SendCommand`, conditioned on `ssm:resourceTag/role = fleet-app` — without that condition the role could run shell on every instance in the account. It resolves the tagged, online instances before sending anything and fails when there are none, then polls each invocation to completion and reports the host's stdout into the job log. `send-command` on its own is fire-and-forget against a target that need not exist, which is how a deploy that ran nowhere could report success. The host is reset to the deployed SHA rather than to `origin/main`, so the compose file and `prometheus.yml` on the box come from the same commit as the images `IMAGE_TAG` pins.

---

## Logging

One configuration for the API and the worker (`app/logging_config.py`), replacing two `basicConfig` calls that used different formats despite shipping in the same image.

Every line carries the `request_id` that `RequestIDMiddleware` returns as `X-Request-ID`, propagated through a `ContextVar` rather than threaded through call signatures — under asyncio that is per-task, so concurrent requests cannot see each other's value and a service function five frames deep gets correlation for free. An inbound `X-Request-ID` is honoured rather than replaced, so a trace started by nginx survives into the service.

`LOG_FORMAT=json` emits one JSON object per line; fields passed via `extra=` become queryable keys instead of text a dashboard has to regex. Production defaults to it.

Each request also produces a structured access line (`http_method`, `http_path`, `http_status`, `duration_ms`). Uvicorn runs with `--no-access-log` in production, so without this a deployed request left no trace at all.

---

## Monitoring

The backend exports Prometheus metrics at `/metrics` in every environment, and until recently nothing scraped them in production — including `telemetry_stream_length` and `telemetry_stream_pending`, the two the bounded ingest buffer depends on being watched. A Prometheus container now runs alongside the stack and scrapes the backend over the internal network.

It is bound to loopback rather than published. Metrics name internal hosts and expose request volumes, and nothing authenticates port 9090 on that box. Reach it through a tunnel:

```bash
ssh -L 9090:localhost:9090 ubuntu@<host>
```

Then open `http://localhost:9090`. Retention is capped at 15 days *and* 512 MB, whichever binds first, with a 256 MB memory ceiling on the container so a cardinality surprise cannot push the application into the OOM killer.

Grafana runs in the local compose stack only. On a 1 GB box it roughly doubles the memory cost of observability, and Prometheus' own expression browser answers the questions this deployment raises; point a local Grafana at the tunnel if you want dashboards. Alerting is still absent — see [Scaling roadmap](#scaling-roadmap).

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
