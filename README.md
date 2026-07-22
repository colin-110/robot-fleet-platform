<div align="center">
  <h1>🤖 Robot Fleet Platform</h1>
  <p><strong>A production-grade, highly-scalable platform for real-time robot fleet telemetry ingestion, monitoring, and predictive maintenance.</strong></p>
  
  [![CI Pipeline](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-blue?style=for-the-badge&logo=github)](https://github.com/placeholder)
  [![Python Version](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
  [![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://reactjs.org/)
  [![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
  [![Redis Streams](https://img.shields.io/badge/Redis-Streams-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io/)
  [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org/)

  [**👉 Live Interactive Demo (S3 + EC2)**](http://robot-fleet-dashboard-349627593894.s3-website-us-east-1.amazonaws.com)

  ![FleetOps Dashboard Dashboard Mockup](https://raw.githubusercontent.com/placeholder/screenshot.png) 
</div>

---

## 📖 Overview

The **Robot Fleet Platform** is a full-stack, distributed system built to solve a critical engineering challenge: **processing high-throughput, high-velocity telemetry data from thousands of concurrent robots while maintaining sub-50ms latencies for real-time dashboard updates.** 

Built with scalability, fault-tolerance, and performance in mind, this architecture leverages modern asynchronous paradigms in Python and React, backed by Redis and PostgreSQL. It acts as a showcase of industry best practices including decoupled ingestion, anomaly detection, horizontal scalability, and idempotent command dispatch.

## ✨ Key Features & Engineering Highlights

*   🚀 **High-Throughput Asynchronous Ingestion Pipeline**
    *   **Challenge:** Synchronous database writes bottleneck under heavy telemetry load.
    *   **Solution:** Implemented **Redis Streams** as a high-speed, persistent buffer. FastAPI handles thousands of concurrent `POST` requests, immediately drops the payload into Redis, and returns a 200 OK. A decoupled Python background worker consumes the stream in batches and efficiently commits to PostgreSQL.
*   ⚡ **Real-Time WebSockets & Reactivity**
    *   **Challenge:** Polling degrades performance and increases latency for live tracking.
    *   **Solution:** Stateful WebSocket management via `wsproto`. Broadcasts telemetry directly to the React dashboard (using Leaflet.js and Recharts) in **< 50ms**, ensuring the UI map and charts update instantly as robots move.
*   🧠 **Statistical Predictive Maintenance (ML/Analytics)**
    *   Continuously calculates real-time Z-Scores and linear extrapolation on thermal and battery metrics to flag hardware degradation *before* catastrophic failure.
*   📈 **High Concurrency & Scalability**
    *   Designed and stress-tested to handle **2,000+ concurrent active robots/clients** per instance utilizing `uvicorn`, `uvloop`, and `asyncpg` connection pooling.
*   🛡️ **Idempotent Command Dispatch**
    *   Fleet managers can dispatch commands (e.g., "Return to Base"). The system uses state machine enforcement (Pending → Executing → Completed) to guarantee idempotency and prevent duplicate executions over spotty networks.

---

## 🏗️ System Architecture

The application adopts a Microservices-inspired API Gateway architecture, strictly separating the ingestion path from the read path.

```mermaid
graph TB
  subgraph "Edge / Clients"
    SIM["🤖 Simulator Fleet<br>(Concurrent Python Agents)"]
    DASH["🖥️ FleetOps Dashboard<br>(React + Leaflet + Vite)"]
  end

  subgraph "API Gateway (FastAPI + Uvicorn)"
    API["API Router"]
    MW["Middleware<br>(Rate Limit, Auth, Trace)"]
  end

  subgraph "Core Services"
    SVC_T["Telemetry Ingestion"]
    SVC_R["Robot State Sync"]
    SVC_A["Aggregated Analytics"]
    SVC_ML["Anomaly Detection Engine"]
  end

  subgraph "Data & Messaging Layer"
    CACHE["Redis<br>(Pub/Sub & Streams)"]
    DB["PostgreSQL 15<br>(Relational Source of Truth)"]
  end
  
  subgraph "Asynchronous Workers"
    Worker["Telemetry Consumer Worker<br>(Batch Processing)"]
  end

  SIM -->|"High-Volume POST"| API
  DASH <-->|"REST + WebSockets"| API
  API --> MW
  MW --> SVC_T & SVC_R & SVC_A & SVC_ML

  SVC_A -->|"Cached Queries (10s TTL)"| CACHE
  SVC_T -->|"XADD (Buffering)"| CACHE
  SVC_T -.->|"WS Broadcast"| DASH
  
  CACHE -->|"XREADGROUP"| Worker
  Worker -->|"Batch INSERT"| DB
  SVC_R & SVC_A -->|"Async SQLAlchemy"| DB
```

---

## 🚀 Quick Start (Docker Compose)

The easiest way to spin up the entire ecosystem (Database, Redis, Backend, Async Worker, React Frontend, and the Simulator) is via Docker.

### 1. Clone & Configure
```bash
git clone https://github.com/your-username/robot-fleet-platform.git
cd robot-fleet-platform
# The system relies on .env files. A local dev .env is mapped automatically in docker-compose.yml
```

### 2. Launch the Stack
```bash
# This builds the frontend, backend, and simulator, pulling Postgres & Redis
docker-compose up --build -d
```

### 3. Access the Services
*   **Web Dashboard:** [http://localhost](http://localhost) (Port 80)
*   **FastAPI Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
*   **Grafana Metrics:** [http://localhost:3000](http://localhost:3000) *(User: admin, Pass: admin)*

### 4. (Optional) Stress Testing
You can run the dedicated stress test suite to benchmark your local hardware:
```bash
pip install aiohttp
python scripts/stress_test.py --base-url http://localhost:8000
```

---

## 📂 Repository Structure

| Directory | Description |
| :--- | :--- |
| [`/backend`](./backend) | FastAPI application, SQLALchemy models, Alembic migrations, and the Redis worker. |
| [`/frontend`](./frontend) | React 18, Vite, Redux, Recharts, and Leaflet.js dashboard. |
| [`/simulator`](./simulator) | High-performance async Python script simulating thousands of robotic agents with physical states (battery, thermal physics, missions). |
| [`/scripts`](./scripts) | CI/CD, AWS EC2 deployment scripts, and intensive load testing tools. |

---

## 📈 Scalability & Load Testing Results

The platform architecture has been rigorously load-tested. Because telemetry writes are decoupled via Redis Streams, the primary bottleneck shifts from I/O bound DB locks to purely CPU bound WebSocket broadcasting.

**Benchmark (Tested on standard hardware):**
- **Concurrent Connections:** Successfully handles **2,000+** concurrent WebSocket clients.
- **Ingestion Rate:** Handles telemetry POST spikes gracefully with Redis buffering.
- **P99 Latency:** Maintained strictly **< 50ms** under sustained mixed load.

---

## 📄 License
This project is licensed under the MIT License.
