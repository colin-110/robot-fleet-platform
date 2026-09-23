# FleetOps

Real-time robot fleet monitoring and control platform built around asynchronous telemetry ingestion, Redis Streams, PostgreSQL, and WebSockets.

[Live demo](https://robot-fleet-platform-eight.vercel.app)

![Fleet dashboard](docs/images/dashboard-overview.png)

## What it does

A simulated fleet sends telemetry for battery, temperature, speed, position, component health, and mission progress. The backend ingests the data, maintains fleet state, streams updates to connected dashboards, and accepts operator commands such as Return to Base, Emergency Stop, and Resume.

The main architectural decision is separating the write path from database persistence:

~~~text
Robot Simulator
      |
      v
  FastAPI API
      |
      | XADD
      v
 Redis Streams
   |        |
   |        +----> WebSocket fan-out ----> Dashboard
   |
   +----> Async Worker ----> PostgreSQL
~~~

An ingest request does not wait for a PostgreSQL write. A worker consumes the Redis stream and performs batched inserts.

## Key engineering work

- Async FastAPI backend with SQLAlchemy 2.0 and PostgreSQL.
- Redis Streams with consumer groups for decoupled persistence.
- WebSocket fan-out with bounded per-client queues so slow clients do not block the fleet.
- Idempotent robot command state machine using atomic compare-and-set updates.
- JWT authentication with viewer/operator/admin roles and short-lived WebSocket console tickets.
- Structured JSON logging with request correlation.
- Prometheus/Grafana observability.
- Docker Compose and GitHub Actions CI/CD with container publishing to GHCR.

## Measured performance

Benchmarks are run with feature flags that isolate individual optimizations.

| Change | Baseline | Optimized |
|---|---:|---:|
| Redis Stream ingest buffer | 1,271 ms p99 | 158 ms p99 |
| Read-through fleet-status cache | 11,172 ms p99 | 265 ms p99 |
| Worker bulk insert | 242 rows/s | 985 rows/s |
| Bounded WebSocket fan-out | 5,977 msg/s | 14,352 msg/s |

The load test also reached about **2,000 concurrent WebSocket clients** at roughly **15,000–18,000 messages/s** with >99% delivery on a single node.

See [the full performance methodology](docs/performance.md) for the benchmark harness and limitations.

## Testing

The repository contains backend and frontend tests covering telemetry ingestion, fleet-state derivation, command concurrency/idempotency, worker behavior, WebSocket lifecycle, authentication, logging, reconnect behavior, and command dispatch.

Run the backend tests with:

~~~bash
cd backend
DATABASE_URL=postgresql://postgres:<password>@localhost:5432/fleet_test_db pytest tests/ -v --cov=app
~~~

Run the frontend tests with:

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

The stack starts the API, worker, dashboard, simulator, PostgreSQL, and Redis.

## Repository structure

- `backend/app/` — FastAPI routes, services, repositories, authentication, metrics, and worker.
- `backend/tests/` — backend test suite.
- `frontend/robot-fleet-dashboard/` — React dashboard.
- `simulator/` — asynchronous robot simulator.
- `scripts/` — benchmark and load-test tooling.
- `docs/` — architecture, performance, and operations documentation.

## Limitations

This is a portfolio-scale system, not a production fleet-control platform. The hosted demo uses synthetic data and deliberately runs with open authentication. The production configuration enables authentication, but the project still has single-node/high-availability and time-series-storage limitations documented in [operations.md](docs/operations.md).

## License

MIT
