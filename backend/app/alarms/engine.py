"""Evaluating alarm rules for every robot and recording the outcome.

The engine runs every ``alarm_interval_seconds`` over what is in memory (the
fleet model, presence, peer scores), so it costs no queries until something
changes. Raising inserts a row, guarded by the partial unique index on open
alarms; clearing updates it. Listeners (WebSocket broadcast, webhooks) are
told about each change after it is committed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import metrics as prom
from app import presence
from app.alarms.rules import RULES, Tracker
from app.database import AsyncSessionLocal
from app.intelligence import anomaly, fleet
from app.models import Alarm
from app.mqtt import predict
from app.repositories.config_repo import applied_settings
from app.repositories.robot_repo import RobotRepository

logger = logging.getLogger(__name__)

ACTIVE, ACKNOWLEDGED, CLEARED = "ACTIVE", "ACKNOWLEDGED", "CLEARED"
# Readings older than this no longer raise or clear anything.
STALE_SECONDS = 300.0
APPLIED_REFRESH_SECONDS = 30.0

Listener = Callable[[str, dict], Awaitable[None]]

# Set by the app lifespan.
engine: AlarmEngine | None = None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def alarm_dict(alarm: Alarm) -> dict:
    return {
        "id": alarm.id,
        "robot_id": alarm.robot_id,
        "kind": alarm.kind,
        "severity": alarm.severity,
        "state": alarm.state,
        "message": alarm.message,
        "value": alarm.value,
        "raised_at": _iso(alarm.raised_at),
        "acknowledged_at": _iso(alarm.acknowledged_at),
        "acknowledged_by": alarm.acknowledged_by,
        "cleared_at": _iso(alarm.cleared_at),
    }


def metrics(
    robot_id: int, peers: dict, now: datetime, applied: dict | None = None
) -> tuple[dict, str | None]:
    """Each rule's metric for one robot; None where it is unknown or stale.

    ``applied`` is the settings the robot reported applying (its heartbeat
    says how long silence is normal).
    """
    heartbeat = predict.tolerances(applied)["heartbeat_s"]
    t = now.timestamp()
    model = fleet.get(robot_id)
    last = model.last if model else {}
    fresh = bool(last) and t - last.get("t", 0.0) <= STALE_SECONDS
    state = presence.get(robot_id)
    offline = presence.is_offline(robot_id)
    peer = peers.get(robot_id)
    values = {
        "battery": last.get("battery") if fresh else None,
        "temperature": last.get("temperature") if fresh else None,
        "health": last.get("health") if fresh else None,
        "anomaly": (peer.score if peer else 0.0) if fresh else None,
        # A disconnected robot is the OFFLINE alarm's business, not SILENT's.
        "suspicion": None if offline else fleet.suspicion(robot_id, t, heartbeat),
        "offline": None if state is None else float(offline),
        "connections_per_minute": float(presence.connections_within(robot_id, 60, now)),
    }
    return values, peer.feature if peer else None


class AlarmEngine:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory
        self.tracker = Tracker()
        self.listeners: list[Listener] = []
        self._open: dict[tuple[int, str], int] | None = None
        # Robots' applied settings, reread at most every APPLIED_REFRESH seconds.
        self._applied: dict[int, dict] = {}
        self._applied_at = float("-inf")
        # Retired robots raise nothing (reread with the applied settings).
        self._retired: set[int] = set()
        # Alarms ``retire`` cleared, told to listeners once the caller commits.
        self._pending_events: list[tuple[str, dict]] = []

    async def _load(self) -> None:
        async with self.session_factory() as db:
            rows = await db.execute(
                select(Alarm.robot_id, Alarm.kind, Alarm.id).where(Alarm.state != CLEARED)
            )
            self._open = {(robot_id, kind): alarm_id for robot_id, kind, alarm_id in rows}

    @property
    def open_count(self) -> int | None:
        """Alarms not yet cleared; None before the first pass loads them."""
        return None if self._open is None else len(self._open)

    async def evaluate(self, now: datetime | None = None) -> list[tuple[str, dict]]:
        """One pass over every known robot. Returns the changes made."""
        started = time.perf_counter()
        try:
            return await self._evaluate(now)
        finally:
            prom.ALARM_PASS_SECONDS.observe(time.perf_counter() - started)

    async def _evaluate(self, now: datetime | None) -> list[tuple[str, dict]]:
        now = now or datetime.now(timezone.utc)
        if self._open is None:
            await self._load()
        if now.timestamp() - self._applied_at >= APPLIED_REFRESH_SECONDS:
            async with self.session_factory() as db:
                self._applied = await applied_settings(db)
                self._retired = await RobotRepository(db).retired_ids()
            self._applied_at = now.timestamp()
        peers = anomaly.scores(fleet.robots(), now.timestamp())
        changes = []
        for robot_id in sorted((set(fleet.robots()) | set(presence.robot_ids())) - self._retired):
            values, feature = metrics(robot_id, peers, now, self._applied.get(robot_id))
            for rule in RULES:
                value = values.get(rule.metric)
                is_open = (robot_id, rule.kind) in self._open
                decision = self.tracker.step(rule, robot_id, value, now.timestamp(), is_open)
                if decision:
                    changes.append((decision, rule, robot_id, value, feature))
        if not changes:
            return []
        events = await self._apply(changes, now)
        await self._notify(events)
        return events

    async def _apply(self, changes, now: datetime) -> list[tuple[str, dict]]:
        events = []
        async with self.session_factory() as db:
            for decision, rule, robot_id, value, feature in changes:
                key = (robot_id, rule.kind)
                if decision == "raise":
                    stmt = (
                        pg_insert(Alarm)
                        .values(
                            robot_id=robot_id,
                            kind=rule.kind,
                            severity=rule.severity,
                            state=ACTIVE,
                            message=rule.message.format(value=value, feature=feature)[:255],
                            value=value,
                            raised_at=now,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["robot_id", "kind"],
                            index_where=text("state <> 'CLEARED'"),
                        )
                        .returning(Alarm)
                    )
                    alarm = (await db.execute(stmt)).scalar_one_or_none()
                    if alarm is not None:
                        self._open[key] = alarm.id
                        events.append(("raised", alarm_dict(alarm)))
                else:
                    stmt = (
                        update(Alarm)
                        .where(Alarm.id == self._open[key], Alarm.state != CLEARED)
                        .values(state=CLEARED, cleared_at=now)
                        .returning(Alarm)
                    )
                    alarm = (await db.execute(stmt)).scalar_one_or_none()
                    self._open.pop(key, None)
                    if alarm is not None:
                        events.append(("cleared", alarm_dict(alarm)))
            await db.commit()
        for event, alarm in events:
            logger.info("Alarm %s: robot %d %s", event, alarm["robot_id"], alarm["kind"])
        return events

    async def _notify(self, events: list[tuple[str, dict]]) -> None:
        for event, alarm in events:
            for listener in self.listeners:
                try:
                    await listener(event, alarm)
                except Exception:
                    logger.exception("Alarm listener failed")

    async def retire(self, db, robot_id: int) -> int:
        """Clear a retired robot's open alarms and stop watching it. The
        caller commits. Returns how many were cleared."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(Alarm)
            .where(Alarm.robot_id == robot_id, Alarm.state != CLEARED)
            .values(state=CLEARED, cleared_at=now)
            .returning(Alarm)
        )
        cleared = [alarm_dict(a) for a in (await db.execute(stmt)).scalars()]
        self._retired.add(robot_id)
        if self._open is not None:
            for key in [k for k in self._open if k[0] == robot_id]:
                del self._open[key]
        self.tracker.pending = {k: v for k, v in self.tracker.pending.items() if k[0] != robot_id}
        self._pending_events = [("cleared", a) for a in cleared]
        return len(cleared)

    async def notify_pending(self) -> None:
        """Tell listeners about alarms ``retire`` cleared, once committed."""
        events, self._pending_events = self._pending_events, []
        await self._notify(events)

    async def acknowledge(self, db, alarm_id: int, by: str) -> dict | None:
        """ACTIVE to ACKNOWLEDGED. None if the alarm is not ACTIVE."""
        stmt = (
            update(Alarm)
            .where(Alarm.id == alarm_id, Alarm.state == ACTIVE)
            .values(
                state=ACKNOWLEDGED,
                acknowledged_at=datetime.now(timezone.utc),
                acknowledged_by=by[:64],
            )
            .returning(Alarm)
        )
        alarm = (await db.execute(stmt)).scalar_one_or_none()
        await db.commit()
        if alarm is None:
            return None
        payload = alarm_dict(alarm)
        await self._notify([("acknowledged", payload)])
        return payload

    async def run(self, interval: float) -> None:
        while True:
            try:
                await self.evaluate()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Alarm evaluation failed")
            await asyncio.sleep(interval)


async def broadcast(event: str, alarm: dict) -> None:
    """Listener: tell connected dashboards."""
    from app.websocket_manager import manager

    await manager.broadcast({"type": "ALARM", "event": event, "alarm": alarm})
