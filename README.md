# FleetOps

Real-time robot fleet monitoring and control platform built around asynchronous telemetry ingestion, Redis Streams, PostgreSQL, and WebSockets.

[Live demo](https://robot-fleet-platform-eight.vercel.app)

![Fleet dashboard](docs/images/dashboard-overview.png)

## Problem

A fleet-control dashboard needs to ingest frequent telemetry without making every request wait for a database write. At the same time, connected operators need near-real-time state updates and reliable command handling.

FleetOps separates the telemetry write path from database persistence while keeping the operator path responsive.

## Architecture

~~~text
Robot Simulator
      |
      v
  FastAPI API
      |
     XADD
      |
      v
 Redis Streams
   |        |
   |        +----> WebSocket fan-out ----> Dashboard
   |
   +----> Async Worker ----> PostgreSQL
~~~

The ingest API acknowledges telemetry after placing it on the Redis stream. A worker consumes the stream and performs batched PostgreSQL writes. WebSocket clients receive state updates independently of the persistence path.

## Engineering decisions

- **Redis Streams + consumer groups:** buffer bursts and decouple ingestion from database writes.
- **Bounded WebSocket queues:** isolate slow clients instead of allowing one connection to stall fan-out.
- **Atomic command state transitions:** prevent duplicate or conflicting robot commands under concurrent requests.
- **Short-lived WebSocket tickets:** avoid putting long-lived access tokens directly into the WebSocket connection flow.
- **Structured logging + correlation IDs:** make asynchronous request/worker behavior traceable.
- **Prometheus/Grafana:** expose operational behavior rather than relying only on application logs.

## Measured performance

Benchmarks isolate individual optimizations and are documented in [the performance methodology](docs/performance.md).

| Workload | Baseline | Optimized |
|---|---:|---:|
| Redis Stream ingest buffer | 1,271 ms p99 | 158 ms p99 |
| Read-through fleet-status cache | 11,172 ms p99 | 265 ms p99 |
| Worker bulk insert | 242 rows/s | 985 rows/s |
| Bounded WebSocket fan-out | 5,977 msg/s | 14,352 msg/s |

A load test reached about **2,000 concurrent WebSocket clients** at roughly **15,000–18,000 messages/s**, with >99% delivery on a single node. These are portfolio-scale measurements, not production capacity guarantees.

## Testing

Tests cover:

- telemetry ingestion and fleet-state derivation
- worker behavior and batch persistence
- command concurrency and idempotency
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

The stack starts the API, worker, dashboard, simulator, PostgreSQL, and Redis.

## Repository structure

- \`backend/app/\` — API routes, services, repositories, authentication, metrics, and worker
- \`backend/tests/\` — backend test suite
- \`frontend/robot-fleet-dashboard/\` — React dashboard
- \`simulator/\` — asynchronous robot simulator
- \`scripts/\` — benchmark and load-test tooling
- \`docs/\` — architecture, performance, and operations documentation

## Limitations

This is a portfolio-scale system using synthetic robot data. The hosted demo intentionally uses simplified authentication. The deployment is single-node and does not provide production-grade high availability or a dedicated time-series database.

## License

MIT
