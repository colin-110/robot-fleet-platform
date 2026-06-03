from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.schemas import TelemetryCreate
from app.models import Telemetry
from app.database import SessionLocal
from app.websocket_manager import manager
from app.ml.anomaly_detector import detect_anomalies

router = APIRouter()


@router.post("/telemetry")
async def create_telemetry(data: TelemetryCreate):

    db: Session = SessionLocal()

    try:

        telemetry = Telemetry(
            robot_id=data.robot_id,
            battery=data.battery,
            temperature=data.temperature,
            speed=data.speed
        )

        db.add(telemetry)

        db.commit()

        db.refresh(telemetry)

        await manager.broadcast({
            "robot_id": telemetry.robot_id,
            "battery": telemetry.battery,
            "temperature": telemetry.temperature,
            "speed": telemetry.speed
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
            .order_by(Telemetry.id.desc())
            .all()
        )

        latest_robots = {}

        for t in telemetry:

            if t.robot_id not in latest_robots:

                latest_robots[t.robot_id] = {
                    "robot_id": t.robot_id,
                    "battery": t.battery,
                    "temperature": t.temperature,
                    "speed": t.speed,
                    "status": (
                        "LOW POWER"
                        if t.battery < 25
                        else "OVERHEATING"
                        if t.temperature > 70
                        else "ACTIVE"
                    )
                }

        return list(latest_robots.values())

    finally:

        db.close()
        
@router.get("/robots/anomalies")
def robot_anomalies():

    db: Session = SessionLocal()

    try:

        telemetry = (
            db.query(Telemetry)
            .order_by(Telemetry.id.desc())
            .limit(100)
            .all()
        )

        anomalies = detect_anomalies(
            telemetry
        )

        return anomalies

    finally:

        db.close()