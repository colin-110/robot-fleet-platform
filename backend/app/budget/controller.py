"""Running the allocator: every ``INTERVAL`` seconds, new tolerances where needed.

Weights: an open alarm makes a robot matter four times as much, an anomaly
score above 2 twice as much. A robot's new tolerances are pushed as desired
config only when its scale moves by more than ``HYSTERESIS``, so a steady
fleet sends no config at all. The allocator owns the ``tol_*`` settings while
the budget is on; anything else in a robot's desired config is kept.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.alarms import engine as alarm_engine
from app.budget.allocator import Demand, allocate, predicted_rate
from app.database import AsyncSessionLocal
from app.intelligence import anomaly, fleet
from app.models import FleetSetting
from app.mqtt import config as mqtt_config
from app.mqtt import predict
from app.repositories import config_repo

logger = logging.getLogger(__name__)

INTERVAL = 30.0
HYSTERESIS = 0.15
BUDGET_KEY = "budget"
TOLERANCE_KEYS = tuple(k for k in predict.DEFAULTS if k.startswith("tol_"))


def scale_of(applied: dict | None) -> float:
    """The scale a robot's applied tolerances amount to (position is the reference)."""
    tol = (applied or {}).get("tol_position_m")
    return tol / predict.DEFAULTS["tol_position_m"] if tol else 1.0


def tolerances_for(scale: float) -> dict:
    return {key: round(predict.DEFAULTS[key] * scale, 4) for key in TOLERANCE_KEYS}


async def get_budget(db) -> dict:
    row = await db.get(FleetSetting, BUDGET_KEY)
    return row.value if row else {"enabled": False, "messages_per_minute": None}


async def set_budget(db, value: dict) -> dict:
    from datetime import datetime, timezone

    stmt = pg_insert(FleetSetting).values(
        key=BUDGET_KEY, value=value, updated_at=datetime.now(timezone.utc)
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[FleetSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    await db.execute(stmt)
    await db.commit()
    return value


def demands(applied: dict[int, dict], now: float) -> list[Demand]:
    open_alarms = (
        {robot for robot, _ in (alarm_engine.engine._open or {})}
        if (alarm_engine.engine)
        else set()
    )
    peers = anomaly.scores(fleet.robots(), now)
    out = []
    for robot_id, model in fleet.robots().items():
        rate = model.rate_per_minute(now)
        if rate is None or now - model.last.get("t", 0.0) > 120:
            continue
        settings = applied.get(robot_id)
        weight = 1.0
        if robot_id in open_alarms:
            weight *= 4
        peer = peers.get(robot_id)
        if peer and peer.score > 2:
            weight *= 2
        heartbeat = predict.tolerances(settings)["heartbeat_s"]
        out.append(Demand(robot_id, rate, scale_of(settings), weight, 60 / heartbeat))
    return out


class BudgetController:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory
        self.last: dict = {}

    async def step(self, now: float | None = None) -> dict[int, float]:
        """One allocation. Returns the robots whose tolerances were changed."""
        now = time.time() if now is None else now
        async with self.session_factory() as db:
            budget = await get_budget(db)
            if not budget.get("enabled") or not budget.get("messages_per_minute"):
                return {}
            applied = await config_repo.applied_settings(db)
            wanted = demands(applied, now)
            scales = allocate(wanted, float(budget["messages_per_minute"]))
            changed = {}
            for d in wanted:
                new = scales[d.robot_id]
                if abs(new / d.scale - 1) <= HYSTERESIS:
                    continue
                current = await config_repo.get_config(db, d.robot_id)
                desired = {
                    **{
                        k: v
                        for k, v in (current.desired if current else {}).items()
                        if not k.startswith("tol_")
                    },
                    **tolerances_for(new),
                }
                row = await config_repo.set_desired(db, d.robot_id, desired)
                changed[d.robot_id] = (row.version, row.desired, new)
            await db.commit()
        for robot_id, (version, desired, _) in changed.items():
            if mqtt_config.publisher is not None:
                mqtt_config.publisher.publish(robot_id, version, desired)
        self.last = {
            "robots": len(wanted),
            "observed_per_min": round(sum(d.rate_per_min for d in wanted), 1),
            "planned_per_min": round(predicted_rate(wanted, scales), 1) if wanted else 0.0,
            "changed": len(changed),
        }
        if changed:
            logger.info("Budget: %s", self.last)
        return {robot_id: scale for robot_id, (_, _, scale) in changed.items()}

    async def run(self, interval: float = INTERVAL) -> None:
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Budget allocation failed")
            await asyncio.sleep(interval)


# Set by the app lifespan.
controller: BudgetController | None = None
