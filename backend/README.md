<div align="center">
  <h1>🛠️ Robot Fleet Platform: Backend Service</h1>
  <p><strong>A high-performance, asynchronous REST API and WebSocket engine built with FastAPI, Redis Streams, and PostgreSQL.</strong></p>
</div>

---

## 📖 Overview

The backend service is the central nervous system of the Robot Fleet Platform. It handles thousands of incoming telemetry events per second from the robot simulators, broadcasts real-time updates to connected frontend clients via WebSockets, and safely persists data to PostgreSQL using a decoupled background worker approach.

## 🏗️ Architecture & Core Concepts

### 1. The Ingestion Pipeline (Redis Buffered)
Synchronously writing to a relational database during high-traffic spikes is a common architectural bottleneck. To solve this, our FastAPI ingestion endpoints (`POST /api/v1/telemetry`) immediately push telemetry data into a **Redis Stream** (`XADD`) and return a fast `200 OK`. 

A standalone Python worker (`worker.py`) constantly consumes this stream (`XREADGROUP`) and performs batched `INSERT` operations into PostgreSQL. This completely decouples the HTTP request lifecycle from database I/O latency.

### 2. High-Performance Asynchronous I/O
The API leverages the full asynchronous ecosystem:
*   **FastAPI & Uvicorn**: Utilizing `uvloop` for blazing-fast event loop execution.
*   **SQLAlchemy 2.0 + Asyncpg**: Fully asynchronous database queries and connection pooling (`db_pool_size` tuned for concurrency).

### 3. Real-Time WebSockets
The stateful `websocket_manager.py` maintains concurrent WebSocket connections to dashboard clients. As telemetry is ingested into the system, it is concurrently pushed to Redis Streams (for the DB worker) and broadcasted directly to active WebSockets, achieving sub-50ms latency.

---

## 🛠️ Tech Stack
*   **Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Pydantic, Starlette)
*   **Database Engine**: [PostgreSQL 15](https://www.postgresql.org/)
*   **ORM & Async Driver**: [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + `asyncpg`
*   **Cache & Message Broker**: [Redis](https://redis.io/)
*   **Migrations**: [Alembic](https://alembic.sqlalchemy.org/)
*   **Testing**: Pytest, httpx, aiosqlite (for in-memory isolation)

---

## 💻 Local Development Setup

### 1. Virtual Environment & Dependencies
We recommend using Python 3.10+. Create a virtual environment and install dependencies:

```bash
cd backend
python -m venv .venv
# Activate (Windows):
.\.venv\Scripts\activate
# Activate (macOS/Linux):
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment Configuration
Copy the template and modify variables if needed (e.g., adding your specific `DATABASE_URL`):

```bash
cp .env.example .env
```

### 3. Database Initialization (Alembic)
Ensure PostgreSQL is running (can be started via Docker Compose in the root folder). Apply schema migrations to create the tables:

```bash
alembic upgrade head
```

### 4. Running the Development Server
Launch the FastAPI application with live-reloading:

```bash
uvicorn app.main:app --reload --port 8000
```
*   **Interactive API Docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
*   **Redoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## 🗄️ Database Migrations

This project uses **Alembic** to manage structural changes to the database. Whenever you modify the ORM models in `models.py`, you must generate a new migration script:

```bash
# 1. Auto-generate the migration script
alembic revision --autogenerate -m "Added battery_health column to telemetry"

# 2. Apply the migration to the database
alembic upgrade head
```

---

## 🧪 Comprehensive Testing Suite

The backend ships with a comprehensive integration and unit testing suite using `pytest`. Tests run against an isolated in-memory `aiosqlite` database to ensure they are fast, deterministic, and safe.

```bash
pytest tests/ -v
```

## 🔒 Security & Best Practices
- **API Key Authentication**: Ingestion endpoints are protected via `X-API-Key` headers.
- **CORS Management**: Configurable via `.env` to strict domain allow-lists in production.
- **Rate Limiting**: Custom middleware tracks and throttles excessive requests to prevent abuse.
