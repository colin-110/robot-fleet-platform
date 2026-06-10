import asyncio

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from starlette.websockets import WebSocketDisconnect

from app.database import engine
from app.models import Base
from app.routes.telemetry import router as telemetry_router
from app.websocket_manager import manager

Base.metadata.create_all(bind=engine)


def _ensure_telemetry_schema():
    inspector = inspect(engine)
    if "telemetry" not in inspector.get_table_names():
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("telemetry")
    }

    required_columns = {
        "timestamp": "TIMESTAMP WITH TIME ZONE",
        "status": "VARCHAR(32)",
        "mission_id": "VARCHAR(64)",
        "mission_type": "VARCHAR(32)",
        "mission_progress": "DOUBLE PRECISION",
        "mission_start_time": "TIMESTAMP WITH TIME ZONE",
        "battery_health": "DOUBLE PRECISION",
        "motor_health": "DOUBLE PRECISION",
        "sensor_health": "DOUBLE PRECISION",
        "network_health": "DOUBLE PRECISION",
        "x": "DOUBLE PRECISION",
        "y": "DOUBLE PRECISION",
    }

    with engine.begin() as connection:
        for column_name, column_type in required_columns.items():
            if column_name in columns:
                continue
            connection.execute(
                text(
                    f"ALTER TABLE telemetry "
                    f"ADD COLUMN {column_name} {column_type}"
                )
            )

        if "timestamp" not in columns:
            connection.execute(
                text(
                    "UPDATE telemetry "
                    "SET timestamp = CURRENT_TIMESTAMP "
                    "WHERE timestamp IS NULL"
                )
            )


_ensure_telemetry_schema()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry_router)


@app.get("/")
def root():
    return {"message": "Robot Fleet Platform Running"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_text("ping")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        print("WebSocket error:", exc)
        manager.disconnect(websocket)
