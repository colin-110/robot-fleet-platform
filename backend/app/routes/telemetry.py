from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.schemas import TelemetryCreate
from app.models import Telemetry
from app.database import SessionLocal
from app.websocket_manager import manager
from app.ml.predictive_maintenance import (
    build_predictive_maintenance,
    summarize_robot_history
)

router = APIRouter()


@router.post("/telemetry")
async def create_telemetry(data: TelemetryCreate):

    db: Session = SessionLocal()

    try:

        telemetry = Telemetry(
            robot_id=data.robot_id,
            battery=data.battery,
            temperature=data.temperature,
            speed=data.speed,
            timestamp=datetime.now(timezone.utc)
        )

        db.add(telemetry)

        db.commit()

        db.refresh(telemetry)

        await manager.broadcast({
            "robot_id": telemetry.robot_id,
            "battery": telemetry.battery,
            "temperature": telemetry.temperature,
            "speed": telemetry.speed,
            "timestamp": telemetry.timestamp.isoformat().replace("+00:00", "Z")
        })

        return {
            "message": "Telemetry received",
            "id": telemetry.id
        }

    finally:

        db.close()


@router.get("/telemetry")
def get_telemetry():

    db: Session = SessionLocal()

    try:

        telemetry = (
            db.query(Telemetry)
            .order_by(Telemetry.id.desc())
            .limit(50)
            .all()
        )

        return telemetry

    finally:

        db.close()


@router.get("/robots/status")
def robot_status():

    db: Session = SessionLocal()

    try:

        telemetry = (
            db.query(Telemetry)
            .order_by(
                Telemetry.robot_id.asc(),
                Telemetry.timestamp.asc(),
                Telemetry.id.asc()
            )
            .all()
        )

        grouped = {}

        for t in telemetry:

            grouped.setdefault(
                t.robot_id,
                []
            ).append(t)

        robots = []

        for robot_id in sorted(grouped):

            summary = summarize_robot_history(
                grouped[robot_id]
            )

            if summary is not None:
                robots.append(summary)

        return robots

    finally:

        db.close()

@router.get("/robots/predictive-maintenance")
def predictive_maintenance():

    db: Session = SessionLocal()

    try:

        telemetry = (
            db.query(Telemetry)
            .order_by(
                Telemetry.robot_id.asc(),
                Telemetry.timestamp.asc(),
                Telemetry.id.asc()
            )
            .all()
        )

        grouped = {}

        for row in telemetry:

            grouped.setdefault(
                row.robot_id,
                []
            ).append(row)

        predictions = []

        for robot_id in sorted(grouped):

            prediction = build_predictive_maintenance(
                grouped[robot_id]
            )

            if prediction is not None:
                predictions.append(prediction)

        predictions.sort(
            key=lambda item: (
                -item["failure_risk"],
                item["robot_id"]
            )
        )

        return predictions

    finally:

        db.close()

@router.get("/robots/anomalies")
def robot_anomalies():

    return predictive_maintenance()
