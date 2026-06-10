from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ml.predictive_maintenance import (
    build_fleet_analytics,
    build_predictive_maintenance,
    summarize_robot_history,
)
from app.models import Telemetry
from app.schemas import TelemetryCreate
from app.websocket_manager import manager

router = APIRouter()


def _to_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@router.post("/telemetry")
async def create_telemetry(data: TelemetryCreate):
    db: Session = SessionLocal()
    try:
        telemetry = Telemetry(
            robot_id=data.robot_id,
            battery=data.battery,
            temperature=data.temperature,
            speed=data.speed,
            status=data.status,
            mission_id=data.mission_id,
            mission_type=data.mission_type,
            mission_progress=data.mission_progress,
            mission_start_time=data.mission_start_time,
            battery_health=data.battery_health,
            motor_health=data.motor_health,
            sensor_health=data.sensor_health,
            network_health=data.network_health,
            x=data.x,
            y=data.y,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(telemetry)
        db.commit()
        db.refresh(telemetry)

        await manager.broadcast(
            {
                "robot_id": telemetry.robot_id,
                "battery": telemetry.battery,
                "temperature": telemetry.temperature,
                "speed": telemetry.speed,
                "status": telemetry.status,
                "mission_id": telemetry.mission_id,
                "mission_type": telemetry.mission_type,
                "mission_progress": telemetry.mission_progress,
                "mission_start_time": _to_iso(telemetry.mission_start_time),
                "battery_health": telemetry.battery_health,
                "motor_health": telemetry.motor_health,
                "sensor_health": telemetry.sensor_health,
                "network_health": telemetry.network_health,
                "x": telemetry.x,
                "y": telemetry.y,
                "timestamp": _to_iso(telemetry.timestamp),
            }
        )

        return {
            "message": "Telemetry received",
            "id": telemetry.id,
        }
    finally:
        db.close()


@router.get("/telemetry")
def get_telemetry():
    db: Session = SessionLocal()
    try:
        return db.query(Telemetry).order_by(Telemetry.id.desc()).limit(50).all()
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
                Telemetry.id.asc(),
            )
            .all()
        )

        grouped = {}
        for row in telemetry:
            grouped.setdefault(row.robot_id, []).append(row)

        robots = []
        for robot_id in sorted(grouped):
            summary = summarize_robot_history(grouped[robot_id])
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
                Telemetry.id.asc(),
            )
            .all()
        )

        grouped = {}
        for row in telemetry:
            grouped.setdefault(row.robot_id, []).append(row)

        predictions = []
        for robot_id in sorted(grouped):
            prediction = build_predictive_maintenance(grouped[robot_id])
            if prediction is not None:
                predictions.append(prediction)

        predictions.sort(key=lambda item: (-item["failure_risk"], item["robot_id"]))
        return predictions
    finally:
        db.close()


@router.get("/robots/anomalies")
def robot_anomalies():
    return predictive_maintenance()


@router.get("/analytics/fleet")
def fleet_analytics():
    db: Session = SessionLocal()
    try:
        telemetry = (
            db.query(Telemetry)
            .order_by(Telemetry.timestamp.asc(), Telemetry.id.asc())
            .all()
        )
        return build_fleet_analytics(telemetry)
    finally:
        db.close()
