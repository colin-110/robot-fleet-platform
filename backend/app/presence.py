"""Which robots are connected to the broker right now.

A robot publishes a retained ``online`` to ``fleet/<id>/presence`` when it
connects and registers a retained ``offline`` as its last will, which the
broker publishes the moment the connection drops. Because both are retained,
a fresh subscription receives the current presence of every robot, so this
state lives in memory and is rebuilt on every (re)connect without a query.

Payloads are JSON, ``{"online": true, "conn": "<connection id>"}``, or the bare
strings ``online`` and ``offline``. The connection id resolves a race: when a
robot reconnects before the broker has noticed its old connection died, the
old connection's will can arrive *after* the new ``online``. An ``offline`` is
therefore ignored if it names a different connection than the one last seen
coming online. Any telemetry from a robot also marks it online, which repairs
the state if a stale will ever wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import orjson


@dataclass(frozen=True)
class Presence:
    online: bool
    conn: str | None
    since: datetime


_state: dict[int, Presence] = {}


def parse(payload: bytes) -> tuple[bool, str | None] | None:
    """``(online, conn)`` from a presence payload, or None if malformed."""
    text = payload.strip()
    if text in (b"online", b"offline"):
        return text == b"online", None
    try:
        body = orjson.loads(text)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("online"), bool):
        return None
    conn = body.get("conn")
    return body["online"], conn if isinstance(conn, str) else None


def apply(robot_id: int, online: bool, conn: str | None, now: datetime | None = None) -> bool:
    """Record a presence message. Returns True if the robot's state changed."""
    now = now or datetime.now(timezone.utc)
    current = _state.get(robot_id)
    if not online and current and current.online and current.conn and conn and current.conn != conn:
        return False  # the will of an older connection
    if current and current.online == online and (current.conn == conn or not online):
        return False
    _state[robot_id] = Presence(online, conn, now)
    return current is None or current.online != online


def saw_traffic(robot_id: int, now: datetime | None = None) -> bool:
    """A message from the robot proves it is connected. True if that is news."""
    current = _state.get(robot_id)
    if current is not None and current.online:
        return False
    _state[robot_id] = Presence(
        True, current.conn if current else None, now or datetime.now(timezone.utc)
    )
    return current is not None


def is_offline(robot_id: int) -> bool:
    """True only when the broker has told us the robot disconnected."""
    current = _state.get(robot_id)
    return current is not None and not current.online


def get(robot_id: int) -> Presence | None:
    return _state.get(robot_id)


def reset() -> None:
    _state.clear()
