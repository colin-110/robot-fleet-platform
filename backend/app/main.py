import asyncio

from fastapi import FastAPI
from fastapi import WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import inspect, text

from app.database import engine
from app.models import Base
from app.routes.telemetry import router as telemetry_router
from app.websocket_manager import manager

Base.metadata.create_all(bind=engine)


def _ensure_telemetry_timestamp_column():

    inspector = inspect(engine)

    tables = inspector.get_table_names()

    if "telemetry" not in tables:
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("telemetry")
    }

    if "timestamp" in columns:
        return

    with engine.begin() as connection:

        connection.execute(
            text(
                "ALTER TABLE telemetry "
                "ADD COLUMN timestamp TIMESTAMP WITH TIME ZONE"
            )
        )

        connection.execute(
            text(
                "UPDATE telemetry "
                "SET timestamp = CURRENT_TIMESTAMP "
                "WHERE timestamp IS NULL"
            )
        )


_ensure_telemetry_timestamp_column()

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

    return {
        "message": "Robot Fleet Platform Running"
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    await manager.connect(websocket)

    try:

        while True:
            try:
                await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30
                )
            except asyncio.TimeoutError:
                await websocket.send_text("ping")

    except WebSocketDisconnect:

        manager.disconnect(websocket)

    except Exception as e:

        print("WebSocket error:", e)

        manager.disconnect(websocket)
