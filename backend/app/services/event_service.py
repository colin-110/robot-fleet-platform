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
        # Events get their own stream so a burst of telemetry cannot push them
        # out of the capped telemetry stream before listeners read them.
        await manager.broadcast(payload, stream=manager.event_stream)
