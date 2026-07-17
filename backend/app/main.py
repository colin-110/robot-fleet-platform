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
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, insert
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.database import Base, engine, AsyncSessionLocal
from app.middleware import RateLimitMiddleware, RequestIdMiddleware
from app.routes.telemetry import router as v1_router
from app.schemas import HealthResponse
from app.websocket_manager import manager
from app.models import Telemetry

# ── Logging Setup ───────────────────────────────────────────────────

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Redis to DB Worker ───────────────────────────────────────────────


async def redis_to_db_sync_worker():
    """Background worker that batch-saves telemetry from Redis to PostgreSQL."""
    logger.info("Starting Redis-to-DB sync worker...")
    redis = manager.redis
    
    while True:
        try:
            payloads = []
            for _ in range(100):
                data = await redis.rpop("telemetry_queue")
                if not data:
                    break
                payloads.append(json.loads(data.decode("utf-8") if isinstance(data, bytes) else data))
                
            if payloads:
                insert_dicts = []
                for p in payloads:
                    ts = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
                    mst = datetime.fromisoformat(p["mission_start_time"].replace("Z", "+00:00")) if p["mission_start_time"] else None
                    
                    insert_dicts.append({
                        "robot_id": p["robot_id"],
                        "battery": p["battery"],
                        "temperature": p["temperature"],
                        "speed": p["speed"],
                        "status": p["status"],
                        "mission_id": p["mission_id"],
                        "mission_type": p["mission_type"],
                        "mission_progress": p["mission_progress"],
                        "mission_start_time": mst,
                        "battery_health": p["battery_health"],
                        "motor_health": p["motor_health"],
                        "sensor_health": p["sensor_health"],
                        "network_health": p["network_health"],
                        "x": p["x"],
                        "y": p["y"],
                        "timestamp": ts
                    })
                
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        stmt = insert(Telemetry).values(insert_dicts)
                        await session.execute(stmt)
                        
                logger.debug("Successfully batched and saved %d telemetry records to database.", len(insert_dicts))
            
            if not payloads:
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0.05)
                
        except asyncio.CancelledError:
            logger.info("Stopping Redis-to-DB sync worker...")
            break
        except Exception:
            logger.exception("Error in Redis-to-DB sync worker")
            await asyncio.sleep(2.0)


# ── Lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    # Startup: ensure tables exist (safe for first run)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info(
        "Robot Fleet Platform started  env=%s  cors=%s",
        settings.app_env,
        settings.cors_origin_list,
    )
    # Start Redis Pub/Sub listener
    manager._listener_task = asyncio.create_task(manager.listen_to_redis())
    
    # Start Redis Ingestion DB Sync worker
    app.state.db_sync_task = asyncio.create_task(redis_to_db_sync_worker())
    
    yield
    
    # Shutdown
    if manager._listener_task:
        manager._listener_task.cancel()
    
    if hasattr(app.state, "db_sync_task") and app.state.db_sync_task:
        app.state.db_sync_task.cancel()
        
    logger.info("Robot Fleet Platform shutting down")


# ── Application ─────────────────────────────────────────────────────

app = FastAPI(
    title="Robot Fleet Platform API",
    description="Mission dispatch, telemetry ingestion, and predictive maintenance",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware (order matters: outermost first) ─────────────────────

app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    max_per_minute=settings.rate_limit_per_minute,
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
app.include_router(v1_router)

# Backward-compatible unversioned routes (so existing simulator works)
app.include_router(v1_router, prefix="", include_in_schema=False)


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


# ── WebSocket ───────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time telemetry broadcast."""
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
