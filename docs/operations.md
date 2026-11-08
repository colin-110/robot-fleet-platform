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
| `TELEMETRY_API_KEY` | *required* | Master key: the API's broker password, accepted on robot HTTP endpoints while `ALLOW_SHARED_INGEST_KEY` is on, and the signing key that console tickets are derived from |
| `MQTT_ENABLED` | `false` (`true` in Compose) | Run the ingest consumer and command publisher in the API |
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | Broker address |
| `MQTT_SERVICE_USERNAME` | `fleetops` | The API's broker account (it and `fleetops-<name>` are the only superusers) |
| `MQTT_BATCH_SIZE` / `MQTT_BATCH_WAIT_MS` | `200` / `250` | A batch is written when it reaches this size or age |
| `MQTT_INGEST_GROUP` | unset | Use shared subscriptions in this group, for several API processes. Mosquitto does not restore shared subscriptions after a restart, so leave unset unless you need it |
| `TICKET_TTL_SECONDS` | `300` | Lifetime of a browser console ticket |
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
| `MAX_CLOCK_SKEW_SECONDS` | `300` | Plain JSON readings dated further ahead than this are clamped to server time. Agent messages are corrected by the robot's measured clock offset instead |
| `LOG_FORMAT` | `text` (`json` in production) | `json` emits one object per line with the request id and structured fields, for a log aggregator |
| `ACCESS_LOG` | `true` | One structured line per HTTP request; `/health` and `/health/live` are excluded so health polls do not bury real traffic |
| `WEBSOCKET_BACKEND` | `direct` | `direct` fans out in-process (one API process). `mqtt` relays every update through the broker so several processes all see everything |

### Alarms

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `ALARM_INTERVAL_SECONDS` | `2` | How often the alarm rules run (in memory) |
| `ALARM_WEBHOOK_URL` | unset | POST every alarm change here |
| `ALARM_WEBHOOK_SECRET` | unset | Signs webhook bodies; required with the URL, and never the master key, since receivers hold it |

A webhook body is `{"event": "raised" | "acknowledged" | "cleared", "alarm": {...}}`, with `X-FleetOps-Event` and `X-FleetOps-Signature: sha256=<hex>`, the HMAC-SHA256 of the raw body under the secret. Compare it in constant time before trusting the body. Deliveries are retried four times over about 20 seconds, then dropped and counted; the queue holds 1,000.

The rules (`backend/app/alarms/rules.py`): `BATTERY_CRITICAL` below 10 %, `BATTERY_LOW` below 20 % for 10 s, `OVERHEATING` above 75 C for 10 s, `COMPONENT_FAILING` below 30 % health for 30 s, `ANOMALY` beyond 4 robust standard deviations from the fleet for 60 s, `SILENT` at phi 8 while connected, `OFFLINE` after 30 s disconnected, and `DUPLICATE_SESSION` at four new connections in a minute. Each clears at its own, safer threshold.

### Message budget

`PUT /api/v1/fleet/budget` with `{"enabled": true, "messages_per_minute": 200}` (operator role) turns it on. Every 30 seconds the API then sets every robot's tolerances (`tol_*` settings) to keep the fleet's keyframes within it; `GET` the same path for the budget and the last allocation (`measured_per_min`, `planned_per_min`, `correction`, and `feasible`, false when even the loosest tolerances cannot get under it). Other settings in a robot's desired config are kept.

### History

`GET /api/v1/robots/<id>/history?start=&end=&resolution=minute|hour` returns exact time-weighted rollups: averages, minimum and maximum battery, distance and how many seconds of the bucket the robot was heard from. A job recomputes the last ten minutes every minute, and further back for keyframes that arrive late. Retention: keyframes 30 days, minutes a year, hours indefinitely.

### Robot settings

`PUT /api/v1/robots/<id>/config` with `{"values": {"report_interval_s": 5}}` (operator role) replaces a robot's desired settings, bumps their version and publishes them to the robot's retained `fleet/<id>/config` topic. `GET` the same path to see the desired settings next to what the robot last reported applying; `in_sync` is true once the versions match. Unknown settings and out-of-range values are refused with `422`, using the same table the agent checks (`CONFIG_FIELDS` in `wire.py`). The API republishes every desired config when it starts, in case the broker lost its retained messages.

### Robots

Robots run [`fleetops-agent`](../agent/README.md). Give each one a device key (`POST /api/v1/robots/<id>/key`) and a persistent path for its outbox. The agent keeps everything it sends until the API confirms it is committed, so a robot can lose its network or its process and resend afterwards; the outbox holds 100,000 items by default and drops the oldest when full, which the API counts as `given_up` in its ingest stats.

The API runs as **one process**. The broker replaces each client id with the username, so two processes logging in as the service account would keep disconnecting each other.

---

## Deployment

> The live demo still runs the version before the redesign (backend and simulator on a t3.micro, with Neon and Upstash). What follows is the deployment the repository now describes; moving to it is a manual step for the operator.

Production is one small Graviton instance running [`docker-compose.ec2.yml`](../docker-compose.ec2.yml): TimescaleDB, the Mosquitto broker, the API and the demo simulator, with the memory limits [`bench/run.py`](../bench/run.py) measured (480 MB in all; see [the benchmark](benchmark.md)). The dashboard is a static build on Vercel. nginx and Let's Encrypt run on the host.

```
Viewer --HTTPS--> Vercel (static dashboard)
   |
   '--HTTPS/WSS--> nginx (host) --> API :8000 ----> TimescaleDB
                                       |  ^
Robots --MQTT over TLS :8883--> Mosquitto (go-auth asks the API)
```

| Port | Open to | What |
| :--- | :--- | :--- |
| 22 | anywhere (narrow it) | SSH |
| 80, 443 | anywhere | nginx: Let's Encrypt and HTTPS to the API |
| 8883 | anywhere | MQTT over TLS for robots |
| 8000, 1883, 5432 | nobody | bound to localhost or the Docker network |

The host **pulls pre-built images from GHCR rather than building**; CI publishes them for amd64 and arm64 after the tests pass. `IMAGE_TAG` defaults to `latest` and can be pinned to a commit SHA:

```bash
cd /opt/fleetops
IMAGE_TAG=<sha> docker compose -f docker-compose.ec2.yml -p fleetops pull
IMAGE_TAG=<sha> docker compose -f docker-compose.ec2.yml -p fleetops up -d
```

Every container caps its logs at 30 MB (`10m` × 3 files).

### Provisioning the host

[`scripts/deploy_aws.ps1`](../scripts/deploy_aws.ps1) creates (or adopts) the instance (`-InstanceType`, default `t4g.small`; the stack fits a `t4g.nano` by the benchmark, with the swap the bootstrap adds), and [`scripts/ec2_user_data.sh`](../scripts/ec2_user_data.sh) installs Docker, adds 1 GB of swap and clones the repo to `/opt/fleetops`. Two details matter:

- **Tag `role=fleet-app`.** It is the SSM target the `deploy-aws` job selects on. `send-command` treats a target that matches nothing as success, so an untagged host is a silent non-deploy.
- **Instance profile `fleet-ssm-profile`** (`AmazonSSMManagedInstanceCore`). Without it the SSM agent never registers and the host is invisible to the pipeline.

Then, by hand, because each involves a secret or a domain:

1. Write `/opt/fleetops/.env` from [`backend/.env.example`](../backend/.env.example): `POSTGRES_*`, `DATABASE_URL` pointing at host `db`, a long `TELEMETRY_API_KEY`, `APP_ENV=production`, `CORS_ORIGINS`.
2. Issue a certificate for the API and broker host names with certbot, then give the broker a copy it can read: `MQTT_DOMAIN=<host> sudo scripts/mqtt_certs.sh`, and link the script into `/etc/letsencrypt/renewal-hooks/deploy/` so renewals reach it.
3. `docker login ghcr.io` if the GHCR packages are private.
4. Backups, below, and a budget alert: `BUDGET_EMAIL=<you> BUDGET_USD=20 scripts/setup_budget_alert.sh`.

### Backups

[`scripts/backup_db.sh`](../scripts/backup_db.sh) writes a compressed `pg_dump` to S3 or a directory; run it nightly from the host's crontab:

```
15 3 * * * cd /opt/fleetops && BACKUP_TARGET=s3://<bucket>/fleetops scripts/backup_db.sh >> /var/log/fleetops-backup.log 2>&1
```

Give the instance role `s3:PutObject` on that prefix, and expire old dumps with a lifecycle rule:

```bash
aws s3api put-bucket-lifecycle-configuration --bucket <bucket> --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-fleetops-dumps","Status":"Enabled","Filter":{"Prefix":"fleetops/"},"Expiration":{"Days":30}}]}'
```

[`scripts/restore_db.sh`](../scripts/restore_db.sh) restores one (stop the API first). It wraps `pg_restore` in TimescaleDB's `timescaledb_pre_restore()` and `timescaledb_post_restore()`, without which hypertables do not come back.

### Gotchas hit on this stack

- **`DATABASE_URL` must not carry `sslmode`/`channel_binding`.** A managed Postgres hands you a `libpq`-shaped string; asyncpg rejects `sslmode` with a `TypeError` before a single migration runs.
- **Health routes must answer `HEAD`.** Uptime monitors and load balancers default to `HEAD`; `@app.get` only registers `GET`, so they read a healthy service as down. Health/root routes use `@app.api_route(path, methods=["GET", "HEAD"])`.
- **`CORS_ORIGINS` must match the browser's `Origin` exactly - no trailing slash.** `https://your-app.vercel.app/` never matches.
- **Every write registers the robot.** Fleet status is the roster `LEFT JOIN`ed onto `robot_state`, so `persist()` upserts the roster in the same transaction or a robot would never appear.
- **Direct batch ingest commits once per batch.** Per-row commits to a remote Postgres were slow enough to exceed the simulator's POST timeout and silently drop whole batches.
- **CartoDB's free basemap CDN was discontinued.** The map uses OpenStreetMap tiles, with the dark theme approximated by a CSS filter.

### CI/CD

On every push and pull request to `main`:

1. **`backend-test`** - `ruff check` across `backend/`, `simulator/` and `agent/`, plus `ruff format --check`. Both are blocking. Then pytest with coverage against a real TimescaleDB service container and an anonymous Mosquitto broker for the MQTT integration tests, and the agent's own tests (wire format, outbox, and the client against the broker, including a robot killed with `-9`).
2. **`frontend-test`** - ESLint (zero errors, zero warnings), Vitest with coverage, and a production Vite build. All blocking.
3. **`publish-images`** - builds and pushes the `backend` and `simulator` images to GHCR on `main`, tagged both `latest` and the commit SHA. These are the images the host pulls; nothing is built on the instance. The frontend is deployed by Vercel, not from an image.
4. **`deploy-aws`** - gated SSM-based rollout, opt-in via a repository variable. The AWS identity it assumes is created by [`scripts/setup_oidc_role.ps1`](../scripts/setup_oidc_role.ps1): a GitHub OIDC provider and a role trusted only by this repository on `main`, so the pipeline holds no long-lived key. Its one permission is `ssm:SendCommand`, conditioned on `ssm:resourceTag/role = fleet-app` - without that condition the role could run shell on every instance in the account. It resolves the tagged, online instances before sending anything and fails when there are none, then polls each invocation to completion and reports the host's stdout into the job log. `send-command` on its own is fire-and-forget, so without this a deploy that ran nowhere would still report success. The host is reset to the deployed SHA rather than to `origin/main`, then runs `docker-compose.ec2.yml`, so the compose file on the box comes from the same commit as the images `IMAGE_TAG` pins.

---

## Logging

One logging configuration for the whole app (`app/logging_config.py`).

Every line carries the `request_id` that `RequestIDMiddleware` returns as `X-Request-ID`, propagated through a `ContextVar` rather than threaded through call signatures - under asyncio that is per-task, so concurrent requests cannot see each other's value and a service function five frames deep gets correlation for free. An inbound `X-Request-ID` is honoured rather than replaced, so a trace started by nginx survives into the service.

`LOG_FORMAT=json` emits one JSON object per line; fields passed via `extra=` become queryable keys instead of text a dashboard has to regex. Production defaults to it.

Each request also produces a structured access line (`http_method`, `http_path`, `http_status`, `duration_ms`). Uvicorn runs with `--no-access-log` in production, so without this a deployed request left no trace at all.

---

## Monitoring

There is no metrics stack. The signals that exist are:

- `GET /health/live` - process liveness, no database round trip. This is what Docker polls.
- `GET /health` - checks the database and reports the broker connection (`mqtt`: `connected`, `disconnected` or `disabled`); status is `degraded` if either is down. Use it by hand; Docker polls `/health/live`.
- Structured logs (above).

Alerting is absent - see [Scaling roadmap](#scaling-roadmap).

---

## Security posture

| Control | Implementation |
| :--- | :--- |
| Operator identity | `AUTH_MODE=required` puts every console action behind a bcrypt-verified sign-in and an HS256 JWT carrying a signed role. Three roles, ranked: `viewer` reads, `operator` dispatches commands, `admin` covers both. Production defaults to required; the public demo sets `open` explicitly, which resolves every caller to an anonymous operator through the *same* dependency rather than skipping it - so the authenticated path is one code path, not a branch nobody runs. Login returns one message and one timing profile for both "no such user" and "wrong password", so it cannot enumerate accounts. |
| Browser credentials | The dashboard is never built with the master key. It requests a short-lived console ticket at runtime (`POST /api/v1/auth/ticket`) - HMAC-signed over its own scope and expiry, so a holder can neither widen its permissions nor extend its life. Ingest still requires the master key, which now stays server-side, so reading the bundle no longer yields a credential that can forge telemetry for the fleet. Rotating the API key revokes every outstanding ticket, since the signing key is derived from it. |
| Robot credentials | Each robot can have its own device key, `rk_<robot>_<random>`, issued with `POST /api/v1/robots/{id}/key` (master key or an admin account) and revoked with `DELETE` on the same path. A device key can only send telemetry and events for its own robot and only claim or update that robot's commands; anything else is a 403. Keys are random and only their SHA-256 hash is stored, so a revoked key can never become valid again, a database leak exposes no usable keys, and rotating the master key leaves device keys alone. Hashes are cached in memory, so checking a key does no database work. |
| Broker | Mosquitto with mosquitto-go-auth, which delegates every login and ACL decision to the API (`/internal/mqtt/*`) and caches them for 60 seconds. Robots log in as `robot-<id>` with their device key; a robot may publish only its own uplink topics and subscribe only to its own commands, and no wildcard is ever granted. `use_username_as_clientid` stops a robot from connecting under another robot's client id, which would take over that session and its queued commands. After a key is revoked the robot can no longer log in, and a connection it already holds loses its topics once the cached ACL results expire. |
| API key comparison | `secrets.compare_digest`. A plain `!=` short-circuits at the first differing byte, leaking key prefixes through response timing. |
| Secret defaults | None. `TELEMETRY_API_KEY` has no fallback value; the application fails at startup if it is missing. |
| Production guards | `APP_ENV=production` rejects wildcard CORS origins and API keys shorter than 16 characters at configuration load. |
| CORS | Credentials are dropped automatically when the origin list is a wildcard, since the specification forbids that combination and browsers reject the response. |
| Client IP resolution | The rate limiter reads `X-Forwarded-For` using a configured trusted-proxy count, taking the Nth entry from the right. Trusting the leftmost entry would let a client spoof its own address; trusting the socket peer would collapse the entire fleet into one bucket behind a proxy. |
| Rate limiting | In-process sliding window per client IP on the HTTP ingest and console endpoints, exact for one process. Idle clients are pruned once 10,000 are tracked. The broker has no per-client rate limit yet. |
| State transitions | Atomic compare-and-set `UPDATE` statements, so concurrent writers cannot both commit a transition. |
| Read endpoints | Fleet status, analytics, and events are public by default so the hosted demo works without shipping a key to every browser. This is a setting (`REQUIRE_AUTH_FOR_READS`), not a hardcoded exemption; it is the wrong default for real fleet positions. |
| Secret hygiene | `.env`, `*.pem`, `*.key`, and `*.tfvars` are gitignored |

---

## Scaling roadmap

In priority order, if this needed to handle a genuinely large fleet:

1. **Scale the API horizontally.** Give each process its own service username, set `WEBSOCKET_BACKEND=mqtt` so dashboard updates reach every process, and split ingest with `MQTT_INGEST_GROUP`, after solving shared subscriptions not surviving a broker restart (for example by having each process own a fixed range of robots with plain subscriptions).
2. **Telemetry storage (done).** `telemetry` is a TimescaleDB hypertable with one-day chunks, compression after a day (segmented by robot) and a 30-day retention policy that drops whole chunks. See ADR 0001.
3. **A clustered broker** (EMQX or a Mosquitto bridge pair) once one broker is a single point of failure that matters, or Kafka behind it if long replay windows become necessary.
4. **Multi-tenancy.** Per-fleet isolation, OIDC for operators, and per-key rate limiting.
5. **Managed high-availability data tier.** Multi-AZ Postgres with read replicas and PgBouncer for the read path.
6. **Dedicated realtime gateway.** Move WebSocket fan-out to its own horizontally scaled tier so it scales independently of the ingest API.
7. **Observability.** Metrics (Prometheus or a hosted equivalent), alerting, and distributed tracing (OpenTelemetry) across the ingest and database path.
