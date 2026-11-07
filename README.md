# FleetOps

Real-time robot fleet monitoring and control: telemetry ingestion, live
dashboards over WebSocket, and command dispatch, built on FastAPI and
TimescaleDB.

[Live demo](https://robot-fleet-platform-eight.vercel.app)

![Fleet dashboard](docs/images/dashboard-overview.png)

## What it does

Robots report telemetry (battery, position, component health, mission
progress). Operators see the fleet live on a map, get each robot's status and
remaining battery runtime, and send commands such as return-to-base or
emergency stop.

## Architecture

~~~text
Robot simulator ──HTTP──► FastAPI ──► TimescaleDB
  (per-robot keys,          │          telemetry   (hypertable, compressed)
   outbox + retries)        │          robot_state (latest reading per robot)
                            │          robots, commands, events, users
                            ▼
                   WebSocket fan-out ──► Dashboard (React, Vercel)
~~~

Every reading goes through one write path that inserts it, registers the
robot and updates its current state in a single transaction.

The system is partway through a redesign toward MQTT transport and
error-budgeted telemetry, aimed at running a fleet on the smallest possible
server. See [ADR 0001](docs/adr/0001-mqtt-and-timescale.md) and
[ADR 0002](docs/adr/0002-efficiency-and-error-budgeted-telemetry.md).

## Engineering decisions

- **Idempotent writes.** A reading is identified by `(robot_id, timestamp)`,
  the table's primary key, so a retried upload is never stored twice.
- **Current state on write.** `robot_state` holds one row per robot, updated
  only by newer readings, so fleet status never scans history.
- **TimescaleDB.** One-day chunks, compression after a day, and a 30-day
  retention policy that drops whole chunks instead of deleting rows.
- **Per-robot device keys.** Random keys stored only as hashes; a key can only
  report as its own robot, and checking one does no database work.
- **Compare-and-set command transitions.** Concurrent pollers or status
  updates cannot both win a transition.
- **Bounded WebSocket queues.** A slow client drops its oldest frames instead of
  stalling fan-out for everyone else.
- **Short-lived console tickets.** The browser never holds the master API key.

## Testing

Tests cover:

- telemetry ingestion, idempotency and fleet-state derivation
- device keys, rotation and revocation
- command concurrency, idempotency and timeouts
- WebSocket lifecycle and reconnect behavior
- authentication and command dispatch
- structured logging

Backend:

~~~bash
cd backend
DATABASE_URL=postgresql://postgres:<password>@localhost:5432/fleet_test_db pytest tests/ -v --cov=app
~~~

Frontend:

~~~bash
cd frontend/robot-fleet-dashboard
npm ci
npm run test:coverage
~~~

## Run locally

Prerequisites: Docker and Docker Compose.

~~~bash
git clone https://github.com/colin-110/robot-fleet-platform.git
cd robot-fleet-platform
cp backend/.env.example backend/.env
docker compose up --build -d
~~~

The stack starts the API, dashboard, simulator, TimescaleDB and Redis (used for WebSocket fan-out until the MQTT phase).

## Repository structure

- `backend/app/` - API routes, services, repositories and authentication
- `backend/tests/` - backend test suite
- `frontend/robot-fleet-dashboard/` - React dashboard
- `simulator/` - asynchronous robot simulator
- `scripts/` - EC2 provisioning and CI deploy-role setup
- `docs/` - architecture, operations, and design decisions (`docs/adr/`)
- `docker-compose.yml` - full local stack; `docker-compose.ec2.yml` - production

## Limitations

This is a portfolio-scale system using synthetic robot data. The hosted demo intentionally uses simplified authentication, and the deployment is single-node without high availability. The live demo still runs the previous version until the redesign is deployed.

## License

MIT
