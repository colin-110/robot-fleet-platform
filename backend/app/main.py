"""
Robot Fleet Platform — FastAPI application entry point.

Features:
  - Versioned API routes under /api/v1/
  - WebSocket endpoint for real-time telemetry
  - Health check endpoint
  - Structured logging
  - Rate limiting and request tracing middleware
  - CORS configuration from environment
  - Backward-compatible unversioned routes
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Gauge, Histogram
from fastapi import FastAPI, WebSocket, Query, Response
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.database import Base, engine
from app.middleware import RateLimitMiddleware, RequestIDMiddleware
from app.routes.telemetry import router as telemetry_router
from app.routes.commands import router as commands_router
from app.routes.robots import router as robots_router
from app.routes.analytics import router as analytics_router
from app.routes.events import router as events_router
from app.schemas import HealthResponse
from app.websocket_manager import manager

# ── Logging Setup ───────────────────────────────────────────────────

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)





# ── Lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    # Database initialization is now handled by prestart.py

    logger.info(
        "Robot Fleet Platform started  env=%s  cors=%s",
        settings.app_env,
        settings.cors_origin_list,
    )
    # Start Redis Pub/Sub listener
    manager._listener_task = asyncio.create_task(manager.listen_to_redis())
    
    yield
    
    # Shutdown
    if manager._listener_task:
        manager._listener_task.cancel()
        
    logger.info("Robot Fleet Platform shutting down")


# ── Application ─────────────────────────────────────────────────────

app = FastAPI(
    title="Robot Fleet Platform API",
    description="Mission dispatch and real-time telemetry ingestion",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)

# ── Middleware (order matters: outermost first) ─────────────────────

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    max_requests=settings.rate_limit_per_minute,
    window_seconds=60,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──────────────────────────────────────────────────────────

# Versioned API
app.include_router(telemetry_router)
app.include_router(commands_router)
app.include_router(robots_router)
app.include_router(analytics_router)
app.include_router(events_router)

# Backward-compatible unversioned routes (so existing simulator works)
app.include_router(telemetry_router, prefix="", include_in_schema=False)
app.include_router(commands_router, prefix="", include_in_schema=False)


@app.get("/", tags=["root"])
def root():
    """Root endpoint — confirms the API is running."""
    return {"message": "Robot Fleet Platform Running", "version": "1.0.0"}


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint — verifies database connectivity."""
    db_status = "healthy"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"
        logger.exception("Health check: database connection failed")

    return HealthResponse(
        status="ok" if db_status == "healthy" else "degraded",
        database=db_status,
    )


# ── Metrics Definitions ─────────────────────────────────────────────

telemetry_ingested_total = Counter(
    "telemetry_ingested_total", "Total number of telemetry readings ingested"
)
websocket_connections_active = Gauge(
    "websocket_connections_active", "Number of active WebSocket connections"
)
db_write_latency_seconds = Histogram(
    "db_write_latency_seconds", "Latency of database writes in seconds"
)

@app.get("/metrics", tags=["observability"])
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── WebSocket ───────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    api_key: str = Query(None),
):
    """WebSocket endpoint for real-time telemetry broadcast.

    Requires ``?api_key=<key>`` query parameter for authentication.
    """
    if api_key != settings.telemetry_api_key:
        await websocket.close(code=4001, reason="Invalid or missing API key")
        return

    await manager.connect(websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_text("ping")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        logger.exception("WebSocket error")
        manager.disconnect(websocket)
