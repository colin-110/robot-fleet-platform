# Operations

[← Back to README](../README.md)

Configuration, CI, security posture, and the scaling roadmap. For local setup see [Running Locally](../README.md#running-locally) in the README.

---

## Configuration

All settings are environment variables, loaded and validated by `pydantic-settings` at startup. See [`backend/.env.example`](../backend/.env.example) for the full list.

### Operational settings

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DATABASE_URL` | *required* | PostgreSQL connection string |
| `TELEMETRY_API_KEY` | *required* | Master key: the API's broker password, accepted on robot HTTP endpoints while `ALLOW_SHARED_INGEST_KEY` is on, and the signing key that console tickets are derived from |
| `MQTT_ENABLED` | `false` (`true` in Compose) | Run the ingest consumer and command publisher in the API |
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | Broker address for the API's own connection (plain, inside the Docker network) |
| `MQTT_SERVICE_USERNAME` | `fleetops` | The API's broker account (it and `fleetops-<name>` are the only superusers) |
| `MQTT_BATCH_SIZE` / `MQTT_BATCH_WAIT_MS` | `200` / `250` | A batch is written when it reaches this size or age |
| `MQTT_INGEST_GROUP` | unset | Use shared subscriptions in this group, for several API processes. Mosquitto does not restore shared subscriptions after a restart, so leave unset unless you need it |
| `TICKET_TTL_SECONDS` | `300` | Lifetime of a browser console ticket |
| `ALLOW_SHARED_INGEST_KEY` | `true` (`false` in `.env.example`) | Whether the master key still works on robot endpoints. `false` once every robot has a device key; the simulator issues its own |
| `DEVICE_KEY_REFRESH_SECONDS` | `60` | How often each instance reloads device keys (multi-instance only), which bounds how long a revoked key keeps working on other instances |
| `AUTH_MODE` | `open` (`required` in production and in `.env.example`) | `open` = public console, no accounts; `required` = every console action, and every read, needs a JWT |
| `JWT_SECRET` | derived from `TELEMETRY_API_KEY` | Access-token signing key. 32+ chars required in production |
| `JWT_TTL_SECONDS` | `3600` | Access-token lifetime |
| `BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | unset | Creates the first admin at startup, only while the users table is empty |
| `APP_ENV` | `development` | `production` additionally rejects wildcard CORS and API keys shorter than 16 characters |
| `CORS_ORIGINS` | localhost origins | Comma-separated allowed origins |
| `TRUSTED_PROXY_COUNT` | `0` | Number of reverse proxies in front of the app; controls how the rate limiter resolves the client IP |
| `RATE_LIMIT_PER_MINUTE` | `600` | Ingest requests permitted per client per minute |
| `CONSOLE_RATE_LIMIT_PER_MINUTE` | `60` | Separate, tighter budget for what a browser can reach without the master key: minting console tickets and dispatching commands |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | Sign-in attempts per client IP. bcrypt slows each password guess; this caps how many there are |
| `REQUIRE_AUTH_FOR_READS` | follows `AUTH_MODE` | Whether fleet status, positions, history, analytics, events and alarms need a signed-in user (any role) or the master key. Set `false` explicitly for a public read-only demo |
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

`GET /api/v1/robots/<id>/history?start=&end=&resolution=minute|hour` returns exact time-weighted rollups: averages, minimum and maximum battery, distance and how many seconds of the bucket the robot was heard from. A job recomputes the last ten minutes every minute, and further back for keyframes that arrive late.

`GET /api/v1/robots/<id>/series?field=&start=&end=` returns one field as line segments (`value(t) = v0 + slope * (t - t0)`), or status and mission as their changes, with the error bound: the robot's tolerance plus the server's. An hourly job writes the segments, about half the disk of the raw keyframes after compression at the default tolerance ([ADR 0003](adr/0003-line-segment-storage.md)).

| Data | Kept |
| :--- | :--- |
| Raw keyframes | `RAW_RETENTION_DAYS` (30), and only once their hours' segments are done and verified |
| Keyframes around alarms (`telemetry_incidents`) | A year |
| Line segments and state changes | Indefinitely (compressed after a week) |
| Minute rollups / hour rollups | A year / indefinitely |

| Setting | Default | Description |
| :--- | :--- | :--- |
| `LINE_TOLERANCE_SCALE` | `1.0` | Segments stay within the robot's own tolerance times this. 1 stores 62% fewer numbers than the keyframe rows, 2 stores 74%, 3 stores 80% (simulated fleet) |
| `RAW_RETENTION_DAYS` | `30` | Raw keyframes older than this are dropped, subject to the conditions above. History beyond it comes from the segments |
| `INCIDENT_MARGIN_MINUTES` | `10` | How far either side of an alarm counts as part of the incident kept in `telemetry_incidents` |

### Robot settings

`PUT /api/v1/robots/<id>/config` with `{"values": {"report_interval_s": 5}}` (operator role) replaces a robot's desired settings, bumps their version and publishes them to the robot's retained `fleet/<id>/config` topic. `GET` the same path to see the desired settings next to what the robot last reported applying; `in_sync` is true once the versions match. Unknown settings and out-of-range values are refused with `422`, using the same table the agent checks (`CONFIG_FIELDS` in `wire.py`). The API republishes every desired config when it starts, in case the broker lost its retained messages.

### Robots

Robots run [`fleetops-agent`](../agent/README.md). Give each one a device key (`POST /api/v1/robots/<id>/key`) and a persistent path for its outbox. The agent keeps everything it sends until the API confirms it is committed, so a robot can lose its network or its process and resend afterwards; the outbox holds 100,000 items by default and drops the oldest when full, which the API counts as `given_up` in its ingest stats.

The API runs as **one process**. The broker replaces each client id with the username, so two processes logging in as the service account would keep disconnecting each other.

---

## CI

On every push and pull request to `main`:

1. **`backend-test`** - `ruff check` across `backend/`, `simulator/` and `agent/`, plus `ruff format --check`. Both are blocking. Then pytest with coverage against a real TimescaleDB service container and an anonymous Mosquitto broker for the MQTT integration tests, and the agent's own tests (wire format, outbox, and the client against the broker, including a robot killed with `-9`).
2. **`frontend-test`** - `npm audit` (fails on a moderate or worse known vulnerability), ESLint (zero errors, zero warnings), Vitest with coverage, and a production Vite build. All blocking.
3. **`security`** - all blocking:
   - **gitleaks** over the whole git history, since a secret deleted from the files is still in it (`.gitleaks.toml` allows two known false positives, nothing else).
   - **pip-audit** on both Python lockfiles, checked by hash.
   - **Trivy** on the API, simulator, database, dashboard and broker images as built: fails on any high or critical vulnerability that has a fix available. Ones the distribution has not fixed yet are reported but cannot fail the build, since there is nothing to upgrade to.
   - **govulncheck** on the broker's go-auth plugin, which Trivy cannot read (it is a shared library, not an executable). Fails on any vulnerability in code the plugin calls, except one with no fixed release that only go-auth's unused JWT backend reaches (`mosquitto/go-updates.txt`).

Actions are pinned to commits rather than tags. Dependabot (`.github/dependabot.yml`) proposes weekly updates for npm packages, Docker base images and the actions; Python packages are pinned with hashes in `requirements.lock` files and regenerated with `pip-compile` (command in each Dockerfile).

---

## Logging

One logging configuration for the whole app (`app/logging_config.py`).

Every line carries the `request_id` that `RequestIDMiddleware` returns as `X-Request-ID`, propagated through a `ContextVar` rather than threaded through call signatures - under asyncio that is per-task, so concurrent requests cannot see each other's value and a service function five frames deep gets correlation for free. An inbound `X-Request-ID` is honoured rather than replaced, so a trace started by nginx survives into the service.

`LOG_FORMAT=json` emits one JSON object per line; fields passed via `extra=` become queryable keys instead of text a dashboard has to regex. Production defaults to it.

Each request also produces a structured access line (`http_method`, `http_path`, `http_status`, `duration_ms`). Uvicorn runs with `--no-access-log` in production, so without this a deployed request left no trace at all.

---

## Monitoring

- `GET /health/live` - process liveness, no database round trip. This is what Docker polls.
- `GET /health` - checks the database and reports the broker connection (`mqtt`: `connected`, `disconnected` or `disabled`); status is `degraded` if either is down. Use it by hand; Docker polls `/health/live`.
- `GET /metrics` - Prometheus metrics, behind the master key (`X-API-Key`, or a bearer token for Prometheus' `authorization` scrape setting). Most values are counters the components already keep, read at scrape time, so collecting them costs the ingest path nothing.
- Structured logs (above).

| Metric | What it shows |
| :--- | :--- |
| `fleetops_ingest_stored_total`, `_duplicates_total`, `_rejected_total` | Readings stored, redeliveries skipped, messages dropped |
| `fleetops_ingest_batch_seconds`, `fleetops_ingest_batch_messages` | Time per batch and messages per batch; batches at the size limit mean ingest is running flat out |
| `fleetops_ingest_queue_depth` | Messages received and waiting for a batch: growing means ingest is behind |
| `fleetops_reading_age_seconds` | From a reading's device time to its commit (network, broker queue and outbox together) |
| `fleetops_websocket_clients`, `_frames_sent_total`, `_frames_dropped_total`, `_queued_frames` | Dashboard fan-out; drops mean a dashboard cannot keep up |
| `fleetops_alarm_pass_seconds`, `fleetops_alarms_open` | One alarm pass over the fleet, and alarms not yet cleared |
| `fleetops_rollup_run_seconds`, `fleetops_rollup_minutes_written_total` | The rollup job |
| `fleetops_robots_online`, `fleetops_robots_modelled` | Robots connected, and robots with an in-memory model |
| `process_*` | CPU, memory and open files of the API process |

Alerting is absent - see [Scaling roadmap](#scaling-roadmap).

---

## Load testing

`loadtest/run.py` runs the local stack (`docker compose up`) with any number of simulated robots, each on its own agent, and reports where it strains. It stops the regular simulator, waits for the robots to come online, samples `/metrics` and `docker stats` every 10 seconds, then puts the regular simulator back. It needs only Python's standard library.

```bash
python3 loadtest/run.py --robots 500 --minutes 5
python3 loadtest/run.py --robots 2000 --minutes 5 --json run-2000.json
```

The report gives readings and messages per second, batch time and fill, how busy ingest is, the ingest queue, reading age (p50 and p95), alarm pass time, WebSocket frames, database growth per hour, and CPU and memory per container, the simulator included, so a saturated simulator is not mistaken for a slow server. The simulator runs at most 120 robots per process (`ROBOTS_PER_PROCESS` in `robot_sim.py`) and starts more processes as needed: paho waits on sockets with `select()`, which cannot watch a file descriptor above 1023.

---

## Backups

**None are set up, on purpose.** Every reading in the local stack and the demo comes from the simulator, so a lost database costs nothing: restart the stack and new data arrives within minutes.

What is and is not protected today:

| Failure | Outcome |
| :--- | :--- |
| A process crashes (API, broker, database restart) | Nothing lost: robots keep readings until the API confirms they are committed (see [Architecture](architecture.md#message-flow)) |
| The database's disk is lost | All history is gone. There is no replica and no backup |

A manual snapshot, and its restore, if you want to keep one:

```bash
docker compose exec -T db pg_dump -U postgres -d robotfleet -Fc > robotfleet.dump
docker compose exec -T db pg_restore -U postgres -d robotfleet --clean < robotfleet.dump
```

A deployment with real fleet data would need, before anything else:

1. **Continuous WAL archiving** (for example with pgBackRest or a managed database's point-in-time recovery) to object storage, so the database can be restored to any moment, not only to the last nightly dump.
2. **A daily full backup** kept for an agreed period, stored in a different account or region from the database.
3. **A tested restore**: run one on a schedule and time it. A backup that has never been restored is not known to work.
4. **A replica** if downtime matters as well as data, so a failed disk is a failover rather than a restore.

---

## TLS

| Connection | Encrypted | How |
| :--- | :--- | :--- |
| Robots to the broker | Yes, port 8883 | TLS 1.2 or later; robots verify the broker's certificate. The simulator connects this way |
| Browsers to the dashboard and its API and WebSocket | Yes, port 443 | TLS 1.2 or 1.3; port 80 only redirects. HSTS for a day |
| The API to the broker, database and itself | No | Inside the Docker network only; plain MQTT on 1883 is not published |

The one-off compose service `certs` runs `tls/make-certs.sh` on every start: on the first it creates a local certificate authority and a server certificate (localhost, 127.0.0.1, and the service names `mqtt` and `frontend`, plus anything in `TLS_EXTRA_SANS`); afterwards it keeps both and renews the server certificate when under 30 days remain. The CA's private key lives in its own volume (`tls_ca`) that no running service mounts. A public deployment would replace the local CA with certificates from a trusted authority (for example Let's Encrypt) and raise HSTS to a year.

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
| Read endpoints | Protected whenever sign-in is required: a signed-in user of any role, or the master key. A public read-only demo must set `REQUIRE_AUTH_FOR_READS=false` on purpose. |
| Secrets in the example file | Every secret in `.env.example` is a `change-me` placeholder, and the API refuses to start while one remains. `python3 backend/make_env.py` fills them with random values and writes `.env` readable by its owner only. |
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
7. **Observability.** Metrics are at `/metrics` (above); still missing are alerting on them and distributed tracing (OpenTelemetry) across the ingest and database path.
