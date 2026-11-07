"""
FleetOps - FastAPI application entry point.

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

from fastapi import Depends, FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app import device_keys, durability, telemetry_buffer
from app.auth import is_valid_api_key, verify_api_key
from app.config import get_settings
from app.database import engine
from app.logging_config import configure_logging
from app.middleware import RateLimitMiddleware, RequestIDMiddleware
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

    try:
        loaded = await device_keys.load_hashes()
        logger.info("Loaded device keys for %d robots", loaded)
    except Exception:
        logger.exception("Could not load device keys at startup")

    try:
        await durability.load()
    except Exception:
        # Robots just keep unconfirmed readings a little longer.
        logger.exception("Could not load stored-reading watermarks at startup")
    logger.info("Server id %s", durability.BOOT_ID)

    # A single instance updates its own cache when keys change. Several
    # instances need a periodic reload so a revocation reaches all of them.
    key_refresh_task = (
        asyncio.create_task(device_keys.refresh_loop(settings.device_key_refresh_seconds))
        if settings.websocket_backend == "redis"
        else None
    )

    # Nothing to consume in direct mode: broadcast() already fanned out
    # in-process, so a Redis listener here would just poll an empty stream.
    if settings.websocket_backend == "redis":
        manager._listener_task = asyncio.create_task(manager.listen_to_redis())

    # Retention pruning runs here as well as in the worker, because a
    # deployment without a worker would otherwise never prune. The Redis lock
    # only matters when several instances share the work. A 30-minute
    # interval keeps peak storage close to retention_days even when that
    # window is under a day.
    pruner_task = asyncio.create_task(
        prune_loop(
            use_redis_lock=settings.websocket_backend == "redis",
            initial_delay_seconds=300,
            interval_seconds=1800,
        )
    )

    # Direct-ingest path only.
    buffer_flush_task = (
        asyncio.create_task(
            telemetry_buffer.flush_loop(interval_seconds=settings.deferred_flush_seconds)
        )
        if not settings.opt_redis_buffer and settings.defer_writes_without_viewers
        else None
    )

    yield

    if manager._listener_task:
        manager._listener_task.cancel()
    pruner_task.cancel()
    if buffer_flush_task:
        buffer_flush_task.cancel()
    if key_refresh_task:
        key_refresh_task.cancel()

    logger.info("FleetOps shutting down")


# ── Application ─────────────────────────────────────────────────────

app = FastAPI(
    title="FleetOps API",
    description="Mission dispatch and real-time telemetry ingestion",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware (order matters: outermost first) ─────────────────────

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    max_requests=settings.rate_limit_per_minute,
    console_max_requests=settings.console_rate_limit_per_minute,
    window_seconds=60,
    trusted_proxy_count=settings.trusted_proxy_count,
)

# A wildcard origin cannot be combined with credentials - the spec forbids it
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
    """Root endpoint - confirms the API is running."""
    return {"message": "FleetOps API running", "version": "1.0.0"}


# Process-liveness only, no database round trip. Docker's own healthcheck
# polls every 15s forever - against a serverless Postgres (Neon) that only
# accrues compute-hours while a query is actually running and suspends after
# 5 idle minutes, a healthcheck that queries the DB every 15s is on its own
# enough to keep it permanently awake, well beyond anything ingestion does.
@app.api_route("/health/live", methods=["GET", "HEAD"], tags=["health"])
def liveness():
    return {"status": "ok"}


# GET and HEAD: uptime monitors (UptimeRobot, Render's own probes, load
# balancers) commonly poll health endpoints with HEAD to skip the response
# body. FastAPI's @app.get() only registers GET, so HEAD came back 405 -
# which read as "service down" to monitors even though it was healthy.
@app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check - verifies database connectivity and reports active flags.

    Unlike /health/live this queries the database, so don't point a frequent
    poller at it on a serverless Postgres that bills for awake time.
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


@app.get("/api/v1/viewers", tags=["observability"])
async def viewer_count(_=Depends(verify_api_key)):
    """How many WebSocket clients are currently connected.

    Lets a telemetry source (the simulator) decide whether anyone is actually
    watching before spending bandwidth generating and posting data nobody
    sees - see simulator/robot_sim.py's viewer_watch_loop. API-key gated,
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

    Authenticates with either ``?ticket=<ticket>`` (browsers - short-lived and
    scoped, see ``app/tickets.py``) or ``?api_key=<key>`` (server-side clients
    such as the load harnesses, which legitimately hold the master key).

    Browsers use the ticket because a WebSocket handshake cannot carry a custom
    header, so the credential has to travel in the URL - where it lands in proxy
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
