"""
FleetOps — FastAPI application entry point.

Features:
  - Versioned API routes under /api/v1/
  - WebSocket endpoint for real-time telemetry
  - Health check endpoint (reports active optimization flags)
  - Structured logging
  - Rate limiting and request tracing middleware
  - CORS configuration from environment
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app import metrics
from app.auth import is_valid_api_key, verify_api_key
from app.config import get_settings
from app.database import engine
from app.logging_config import configure_logging
from app.middleware import (
    LegacyRateLimitMiddleware,
    LegacyRequestIDMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
)
from app.retention import prune_loop
from app.routes.analytics import router as analytics_router
from app.routes.auth import bootstrap_admin_user
from app.routes.auth import router as auth_router
from app.routes.commands import router as commands_router
from app.routes.events import router as events_router
from app.routes.robots import router as robots_router
from app.routes.telemetry import router as telemetry_router
from app.schemas import HealthResponse
from app.tickets import SCOPE_CONSOLE, verify_ticket
from app.websocket_manager import manager

# ── Logging Setup ───────────────────────────────────────────────────

settings = get_settings()

configure_logging(service="api")
logger = logging.getLogger(__name__)


# ── Lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    # Database schema is created by prestart.py before workers start.
    logger.info(
        "FleetOps started  env=%s  auth=%s  cors=%s  optimizations=%s",
        settings.app_env,
        settings.resolved_auth_mode,
        settings.cors_origin_list,
        settings.optimization_flags(),
    )

    await bootstrap_admin_user()

    # Nothing to consume in direct mode: broadcast() already fanned out
    # in-process, so a Redis listener here would just poll an empty stream.
    if settings.websocket_backend == "redis":
        manager._listener_task = asyncio.create_task(manager.listen_to_redis())

    # The worker also runs this loop (worker.py's db_pruner_task), but the
    # worker is an optional, separately-deployed process. A deployment that
    # skips it entirely - this app's free-tier Render path - previously
    # never pruned anything: telemetry grew unbounded until it exhausted
    # Neon's storage quota, at which point every INSERT started failing and
    # ingestion silently stopped for hours before anyone noticed. The backend
    # always runs, so the backend always prunes now too. Redis-locked only
    # when Redis is actually meaningful here (multi-instance/AWS path) - a
    # single instance with websocket_backend=direct has nothing to
    # coordinate with.
    pruner_task = asyncio.create_task(
        prune_loop(
            use_redis_lock=settings.websocket_backend == "redis",
            initial_delay_seconds=300,
            interval_seconds=6 * 3600,
        )
    )

    yield

    if manager._listener_task:
        manager._listener_task.cancel()
    pruner_task.cancel()

    # Drop this worker's gauge samples so a restart doesn't leave phantom
    # connections summed into the multiprocess registry.
    metrics.mark_worker_exit()
    logger.info("FleetOps shutting down")


# ── Application ─────────────────────────────────────────────────────

app = FastAPI(
    title="FleetOps API",
    description="Mission dispatch and real-time telemetry ingestion",
    version="1.0.0",
    lifespan=lifespan,
    # No custom response class. orjson was benchmarked here and did help — but
    # only on the handful of routes without a `response_model`, because FastAPI
    # serializes through Pydantic directly whenever one is declared, bypassing
    # the class entirely. FastAPI has since deprecated ORJSONResponse for that
    # exact reason, so the code is gone; the measurement and what it taught us
    # are kept in docs/performance.md.
)

# ── Middleware (order matters: outermost first) ─────────────────────

if settings.opt_asgi_middleware:
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.rate_limit_per_minute,
        console_max_requests=settings.console_rate_limit_per_minute,
        window_seconds=60,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
else:
    app.add_middleware(LegacyRequestIDMiddleware)
    app.add_middleware(
        LegacyRateLimitMiddleware,
        max_requests=settings.rate_limit_per_minute,
        console_max_requests=settings.console_rate_limit_per_minute,
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
app.include_router(auth_router)


@app.api_route("/", methods=["GET", "HEAD"], tags=["root"])
def root():
    """Root endpoint — confirms the API is running."""
    return {"message": "FleetOps API running", "version": "1.0.0"}


# GET and HEAD: uptime monitors (UptimeRobot, Render's own probes, load
# balancers) commonly poll health endpoints with HEAD to skip the response
# body. FastAPI's @app.get() only registers GET, so HEAD came back 405 —
# which read as "service down" to monitors even though it was healthy.
@app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse, tags=["health"])
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


@app.get("/api/v1/viewers", tags=["observability"])
async def viewer_count(_=Depends(verify_api_key)):
    """How many WebSocket clients are currently connected.

    Lets a telemetry source (the simulator) decide whether anyone is actually
    watching before spending bandwidth generating and posting data nobody
    sees — see simulator/robot_sim.py's viewer_watch_loop. API-key gated,
    not console-ticket: this is a fleet-side operational signal, not
    something the dashboard itself needs to read.
    """
    return {"active_connections": manager.connection_count}


# ── WebSocket ───────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    api_key: str = Query(None),
    ticket: str = Query(None),
):
    """WebSocket endpoint for real-time telemetry broadcast.

    Authenticates with either ``?ticket=<ticket>`` (browsers — short-lived and
    scoped, see ``app/tickets.py``) or ``?api_key=<key>`` (server-side clients
    such as the load harnesses, which legitimately hold the master key).

    Browsers use the ticket because a WebSocket handshake cannot carry a custom
    header, so the credential has to travel in the URL — where it lands in proxy
    and access logs. A five-minute scoped ticket is a very different thing to
    leak there than the fleet's ingest key.
    """
    if not (verify_ticket(ticket, SCOPE_CONSOLE) or is_valid_api_key(api_key)):
        await websocket.close(code=4001, reason="Invalid or missing credentials")
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
