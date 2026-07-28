"""
Robot Fleet Platform — FastAPI application entry point.

Features:
  - Versioned API routes under /api/v1/
  - WebSocket endpoint for real-time telemetry
  - Health check endpoint (reports active optimization flags)
  - Structured logging
  - Rate limiting and request tracing middleware
  - CORS configuration from environment
  - Backward-compatible unversioned routes
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app import metrics
from app.auth import is_valid_api_key
from app.config import get_settings
from app.database import engine
from app.middleware import (
    LegacyRateLimitMiddleware,
    LegacyRequestIDMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
)
from app.routes.analytics import router as analytics_router
from app.routes.commands import router as commands_router
from app.routes.events import router as events_router
from app.routes.robots import router as robots_router
from app.routes.telemetry import router as telemetry_router
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
    # Database schema is created by prestart.py before workers start.
    logger.info(
        "Robot Fleet Platform started  env=%s  cors=%s  optimizations=%s",
        settings.app_env,
        settings.cors_origin_list,
        settings.optimization_flags(),
    )

    manager._listener_task = asyncio.create_task(manager.listen_to_redis())

    yield

    if manager._listener_task:
        manager._listener_task.cancel()

    # Drop this worker's gauge samples so a restart doesn't leave phantom
    # connections summed into the multiprocess registry.
    metrics.mark_worker_exit()
    logger.info("Robot Fleet Platform shutting down")


# ── Application ─────────────────────────────────────────────────────

app = FastAPI(
    title="Robot Fleet Platform API",
    description="Mission dispatch and real-time telemetry ingestion",
    version="1.0.0",
    lifespan=lifespan,
    # orjson is faster than stdlib json on float-heavy payloads like telemetry.
    # Caveat worth knowing: current FastAPI serializes directly via Pydantic
    # whenever a route declares a `response_model`, bypassing this class
    # entirely — so it only affects routes without one (command acks, the
    # telemetry ack, root). The benchmark measures the real effect rather than
    # assuming the library-level claim applies here.
    default_response_class=ORJSONResponse if settings.opt_orjson else JSONResponse,
)

# ── Middleware (order matters: outermost first) ─────────────────────

if settings.opt_asgi_middleware:
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.rate_limit_per_minute,
        window_seconds=60,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
else:
    app.add_middleware(LegacyRequestIDMiddleware)
    app.add_middleware(
        LegacyRateLimitMiddleware,
        max_requests=settings.rate_limit_per_minute,
        window_seconds=60,
        trusted_proxy_count=settings.trusted_proxy_count,
    )

# A wildcard origin cannot be combined with credentials — the spec forbids it
# and browsers reject the response. cors_allow_credentials drops credentials
# automatically when the origin list is "*". APP_ENV=production rejects the
# wildcard outright at config load.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──────────────────────────────────────────────────────────

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
    """Health check — verifies database connectivity and reports active flags.

    The optimization flags are exposed so the benchmark harness can assert the
    stack actually came up in the configuration it intended to measure.
    """
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
        optimizations=settings.optimization_flags(),
    )


@app.get("/metrics", tags=["observability"])
async def prometheus_metrics():
    """Prometheus metrics endpoint (multiprocess-aware)."""
    return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)


# ── WebSocket ───────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    api_key: str = Query(None),
):
    """WebSocket endpoint for real-time telemetry broadcast.

    Requires ``?api_key=<key>`` query parameter for authentication.
    """
    if not is_valid_api_key(api_key):
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
