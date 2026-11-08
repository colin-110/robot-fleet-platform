"""Event broadcast to dashboards."""

from app.models import Event
from app.websocket_manager import manager


async def broadcast_events(events: list[Event]) -> None:
    for e in events:
        payload = {
            "type": "EVENT",
            "robot_id": e.robot_id,
            "message": e.message,
            "timestamp": e.timestamp.isoformat().replace("+00:00", "Z"),
        }
        await manager.broadcast(payload)
