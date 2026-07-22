<div align="center">
  <h1>Robot Fleet Platform: Backend Service</h1>
  <p><strong>A high-performance, asynchronous REST API and WebSocket engine built with FastAPI, Redis Streams, and PostgreSQL.</strong></p>
</div>

---

## Overview

The backend service functions as the central nervous system of the Robot Fleet Platform. It is responsible for handling thousands of incoming telemetry events per second from distributed agents, broadcasting real-time updates to connected dashboard clients via WebSockets, and reliably persisting structured data to PostgreSQL utilizing a decoupled background worker paradigm.

## Architectural Concepts

### 1. The Redis Buffered Ingestion Pipeline
Synchronously writing to a relational database during high-traffic telemetry spikes introduces severe I/O bottlenecks. To mitigate this, the primary ingestion endpoints (`POST /api/v1/telemetry`) are completely decoupled from the database. Payloads are immediately pushed into a **Redis Stream** (`XADD`), allowing the server to return an immediate `200 OK` response. 

A standalone, asynchronous Python worker process (`worker.py`) continuously polls this stream (`XREADGROUP`) and performs highly optimized, batched `INSERT` operations into PostgreSQL.

### 2. High-Performance Asynchronous I/O
The API utilizes the Python asynchronous ecosystem to its full potential:
*   **FastAPI & Uvicorn**: Configured with `uvloop` for high-performance event loop execution, significantly increasing request throughput.
*   **SQLAlchemy 2.0 & Asyncpg**: Utilizes fully asynchronous database queries and rigorous connection pooling (`db_pool_size` and `db_max_overflow` tuned for maximum concurrency).

### 3. Real-Time WebSockets
The stateful `websocket_manager.py` manages concurrent WebSocket connections to edge clients. As telemetry enters the system, it is concurrently dispatched to the Redis Stream (for persistent storage) and broadcasted directly to active WebSockets, achieving sub-50ms propagation delay.

---

## Technology Stack
*   **Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Pydantic, Starlette)
*   **Database Engine**: [PostgreSQL 15](https://www.postgresql.org/)
*   **ORM & Async Driver**: [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + `asyncpg`
*   **Cache & Message Broker**: [Redis](https://redis.io/)
*   **Database Migrations**: [Alembic](https://alembic.sqlalchemy.org/)
*   **Testing Framework**: Pytest, httpx, aiosqlite (for in-memory test isolation)

---

## Local Development Setup

### 1. Virtual Environment Initialization
Python 3.10+ is required. Initialize a virtual environment and install dependencies:

```bash
cd backend
python -m venv .venv
# Activate on Windows:
.\.venv\Scripts\activate
# Activate on macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment Configuration
Duplicate the configuration template and populate required variables:

```bash
cp .env.example .env
```

### 3. Database Initialization (Alembic)
Ensure the PostgreSQL instance is running. Apply the schema migrations to instantiate the database tables:

```bash
alembic upgrade head
```

### 4. Application Execution
Launch the FastAPI application with live-reloading enabled for development:

```bash
uvicorn app.main:app --reload --port 8000
```
*   **Interactive API Documentation:** `http://localhost:8000/docs`
*   **Redoc Alternative:** `http://localhost:8000/redoc`

---

## Database Migrations

This platform utilizes **Alembic** to manage structural database revisions. Whenever modifications are made to the ORM models in `models.py`, a new migration script must be generated and applied:

```bash
# 1. Automatically generate the migration revision
alembic revision --autogenerate -m "Descriptive summary of schema change"

# 2. Apply the revision to the database
alembic upgrade head
```

---

## Comprehensive Testing Suite

The backend includes an extensive integration and unit testing suite powered by `pytest`. Tests are executed against an isolated, in-memory `aiosqlite` database to guarantee rapid, deterministic, and consequence-free execution.

```bash
pytest tests/ -v
```

## Security and Best Practices
- **API Key Authentication**: Ingestion endpoints are protected via strict `X-API-Key` headers.
- **CORS Management**: Cross-Origin Resource Sharing is configurable via `.env` to enforce strict domain allow-lists in production environments.
- **Rate Limiting**: Custom middleware tracks and throttles excessive requests to prevent abuse and ensure service availability.
