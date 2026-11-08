"""
FleetOps - FastAPI application entry point.

Features:
  - Versioned API routes under /api/v1/
  - WebSocket endpoint for real-time telemetry
  - MQTT ingest consumer (when MQTT_ENABLED is on)
  - Health check endpoint
  - Structured logging
  - Rate limiting and request tracing middleware
  - CORS configuration from environment
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app import device_keys
from app.alarms import engine as alarm_engine
from app.alarms.webhook import WebhookSender
from app.auth import is_valid_api_key
from app.budget import controller as budget_controller
from app.command_timeouts import timeout_loop
from app.config import get_settings
from app.database import engine
from app.logging_config import configure_logging
from app.middleware import RateLimitMiddleware, RequestIDMiddleware
from app.mqtt import commands as mqtt_commands
from app.mqtt import config as mqtt_config
from app.mqtt.ingest import IngestConsumer, relay_publisher
from app.routes.alarms import router as alarms_router
from app.routes.analytics import router as analytics_router
from app.routes.auth import bootstrap_admin_user
from app.routes.auth import router as auth_router
from app.routes.commands import router as commands_router
from app.routes.events import router as events_router
from app.routes.fleet import router as fleet_router
from app.routes.mqtt_auth import router as mqtt_auth_router
from app.routes.robots import router as robots_router
from app.routes.telemetry import router as telemetry_router
from app.schemas import HealthResponse
from app.storage.job import RollupJob
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
        "FleetOps started  env=%s  auth=%s  cors=%s",
        settings.app_env,
        settings.resolved_auth_mode,
        settings.cors_origin_list,
    )

    await bootstrap_admin_user()

    try:
        loaded = await device_keys.load_hashes()
        logger.info("Loaded device keys for %d robots", loaded)
    except Exception:
        logger.exception("Could not load device keys at startup")

    # A single instance updates its own cache when keys change. Several
    # instances need a periodic reload so a revocation reaches all of them.
    key_refresh_task = (
        asyncio.create_task(device_keys.refresh_loop(settings.device_key_refresh_seconds))
        if settings.websocket_backend == "mqtt"
        else None
    )

    timeout_task = asyncio.create_task(timeout_loop())

    # Alarms are evaluated from memory; changes go to dashboards (and webhooks).
    alarm_engine.engine = alarm_engine.AlarmEngine()
    alarm_engine.engine.listeners.append(alarm_engine.broadcast)
    alarm_task = asyncio.create_task(alarm_engine.engine.run(settings.alarm_interval_seconds))
    # Exact rollups of the keyframes, every minute.
    rollup_task = asyncio.create_task(RollupJob().run())

    # The message budget (off until an operator sets one).
    budget_controller.controller = budget_controller.BudgetController()
    budget_task = asyncio.create_task(budget_controller.controller.run())

    webhook_task = None
    if settings.alarm_webhook_url and not settings.alarm_webhook_secret:
        # Receivers verify the signature with the secret, so it must be one
        # they may hold: never the master key.
        logger.error("ALARM_WEBHOOK_URL is set without ALARM_WEBHOOK_SECRET; webhooks are off")
    elif settings.alarm_webhook_url:
        sender = WebhookSender(settings.alarm_webhook_url, settings.alarm_webhook_secret)
        alarm_engine.engine.listeners.append(sender.listener)
        webhook_task = asyncio.create_task(sender.run())

    # One consumer per process. The broker pins each client id to its
    # username, so this process must be the only one logging in as
    # MQTT_SERVICE_USERNAME: run a single worker.
    app.state.mqtt = IngestConsumer() if settings.mqtt_enabled else None
    mqtt_task = republish_task = None
    if app.state.mqtt:
        # Commands go out on the same connection.
        mqtt_commands.publisher = mqtt_commands.CommandPublisher(app.state.mqtt.client)
        app.state.mqtt.on_robot_online.append(mqtt_commands.publisher.push_pending_soon)
        mqtt_config.publisher = mqtt_config.ConfigPublisher(app.state.mqtt.client)
        republish_task = asyncio.create_task(
            mqtt_config.publisher.republish_all(app.state.mqtt.connected)
        )
        if settings.websocket_backend == "mqtt":
            manager.relay = relay_publisher(app.state.mqtt.client)
        mqtt_task = asyncio.create_task(app.state.mqtt.run())

    yield

    # Anything consumed but not yet committed is unacknowledged, so the broker
    # delivers it again after the restart.
    if mqtt_task:
        mqtt_commands.publisher = None
        mqtt_config.publisher = None
        republish_task.cancel()
        manager.relay = None
        mqtt_task.cancel()
        await asyncio.gather(mqtt_task, return_exceptions=True)
    timeout_task.cancel()
    alarm_task.cancel()
    budget_task.cancel()
    rollup_task.cancel()
    budget_controller.controller = None
    if webhook_task:
        webhook_task.cancel()
    alarm_engine.engine = None
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
app.include_router(alarms_router)
app.include_router(fleet_router)
app.include_router(analytics_router)
app.include_router(events_router)
app.include_router(auth_router)
app.include_router(mqtt_auth_router)


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
    """Health check - verifies database connectivity.

    Unlike /health/live this queries the database, so frequent pollers such as
    the Docker healthcheck should use /health/live instead.
    """
    db_status = "healthy"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"
        logger.exception("Health check: database connection failed")

    consumer = getattr(app.state, "mqtt", None)
    if consumer is None:
        mqtt_status = "disabled"
    else:
        mqtt_status = "connected" if consumer.connected.is_set() else "disconnected"

    healthy = db_status == "healthy" and mqtt_status != "disconnected"
    return HealthResponse(
        status="ok" if healthy else "degraded",
        database=db_status,
        mqtt=mqtt_status,
    )


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
