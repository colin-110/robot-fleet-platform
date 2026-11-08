"""Running the allocator: every ``INTERVAL`` seconds, new tolerances where needed.

A robot whose tolerances just changed is measured afresh for a minute. Until
then it is not reallocated, but it still uses its share: its expected rate,
from the demand last learned and the scale just assigned, is taken off the
budget before the measured robots divide the rest.

The model behind the allocation is only approximately right for any real
robot, and the errors add up across a fleet. So the budget handed to the
allocator is corrected by feedback on what the fleet actually sends: every
step it is multiplied by ``(budget / measured) ** FEEDBACK_GAIN``, within
limits. The allocator still decides who gets which share; the feedback makes
the total come out right.

Weights: an open alarm makes a robot matter four times as much, a dashboard
looking at it closely three times, an anomaly score above 2 twice as much. A robot's new tolerances are pushed as desired
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
from app.budget.allocator import Demand, allocate, feasible, fit, predicted_rate
from app.database import AsyncSessionLocal
from app.intelligence import anomaly, fleet
from app.models import FleetSetting
from app.mqtt import config as mqtt_config
from app.mqtt import predict
from app.repositories import config_repo

logger = logging.getLogger(__name__)

INTERVAL = 30.0
HYSTERESIS = 0.15
FEEDBACK_GAIN = 0.5
# Within this of the budget the correction is left alone, or it keeps
# nudging tolerances back and forth across the hysteresis.
FEEDBACK_DEADBAND = 0.05
# The corrected budget stays within these multiples of the real one.
CORRECTION_LIMITS = (0.2, 3.0)
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


def demands(
    applied: dict[int, dict],
    now: float,
    assigned: dict | None = None,
    points: dict | None = None,
) -> list[Demand]:
    """What each measured robot needs. Its scale is the one it was last given
    (the agent applies desired settings at once), else what it reports.

    ``points`` collects each robot's ``(scale, rate)`` observations at up to
    two different scales; with two, its tolerance-independent floor is
    fitted instead of assumed to be just the heartbeat.
    """
    assigned = assigned or {}
    points = {} if points is None else points
    open_alarms = (
        {robot for robot, _ in (alarm_engine.engine._open or {})}
        if (alarm_engine.engine)
        else set()
    )
    peers = anomaly.scores(fleet.robots(), now)
    from app.websocket_manager import manager

    watched = manager.watched()
    out = []
    for robot_id, model in fleet.robots().items():
        rate = model.rate_per_minute(now)
        if rate is None or now - model.last.get("t", 0.0) > 120:
            continue
        settings = applied.get(robot_id)
        weight = 1.0
        if robot_id in open_alarms:
            weight *= 4
        if robot_id in watched:
            weight *= 3
        peer = peers.get(robot_id)
        if peer and peer.score > 2:
            weight *= 2
        heartbeat = predict.tolerances(settings)["heartbeat_s"]
        scale = assigned.get(robot_id) or scale_of(settings)
        seen = points.setdefault(robot_id, [])
        if seen and abs(seen[-1][0] / scale - 1) <= HYSTERESIS:
            seen[-1] = (scale, rate)  # the same operating point, measured again
        else:
            seen.append((scale, rate))
            del seen[:-2]
        floor = 60 / heartbeat
        fitted = fit(seen[0], seen[1], floor) if len(seen) == 2 else None
        if fitted:
            floor = min(fitted[1], rate)
        out.append(Demand(robot_id, rate, scale, weight, floor))
    return out


class BudgetController:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self.session_factory = session_factory
        self.last: dict = {}
        # The last measured demand of each robot, and the scale it was given.
        self.known: dict[int, Demand] = {}
        self.assigned: dict[int, float] = {}
        self.points: dict[int, list[tuple[float, float]]] = {}
        self.correction = 1.0  # multiplies the budget given to the allocator
        self.weights: dict[int, float] = {}
        self.enabled_since: float | None = None

    async def step(self, now: float | None = None) -> dict[int, float]:
        """One allocation. Returns the robots whose tolerances were changed."""
        now = time.time() if now is None else now
        async with self.session_factory() as db:
            budget = await get_budget(db)
            if not budget.get("enabled") or not budget.get("messages_per_minute"):
                self.enabled_since = None
                self.correction = 1.0
                return {}
            target = float(budget["messages_per_minute"])
            if self.enabled_since is None:
                self.enabled_since = now
            measured_total = fleet.total_rate(now)
            # Feedback only once the window holds traffic sent under the budget.
            settled = abs(measured_total / target - 1) <= FEEDBACK_DEADBAND
            if now - self.enabled_since >= 120 and measured_total > 0 and not settled:
                lo, hi = CORRECTION_LIMITS
                self.correction = min(
                    hi, max(lo, self.correction * (target / measured_total) ** FEEDBACK_GAIN)
                )
            applied = await config_repo.applied_settings(db)
            wanted = demands(applied, now, self.assigned, self.points)
            for d in wanted:
                self.known[d.robot_id] = d
            measured = {d.robot_id for d in wanted}
            waiting = [
                self.known[robot_id]
                for robot_id, model in fleet.robots().items()
                if robot_id not in measured
                and robot_id in self.known
                and now - model.last.get("t", 0.0) <= 120
            ]
            fixed = sum(
                d.c / self.assigned.get(d.robot_id, d.scale) ** 2 + d.floor_per_min for d in waiting
            )
            available = target * self.correction - fixed
            scales = allocate(wanted, available)
            weights = {d.robot_id: d.weight for d in wanted}
            # On budget and nobody's importance changed: leave the fleet alone.
            # Re-fitting each robot as it is remeasured would otherwise keep
            # moving tolerances around a total that is already right.
            hold = settled and all(self.weights.get(r, w) == w for r, w in weights.items())
            self.weights.update(weights)
            changed = {}
            for d in wanted:
                new = scales[d.robot_id]
                if hold or abs(new / d.scale - 1) <= HYSTERESIS:
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
                self.assigned[d.robot_id] = new
            await db.commit()
        for robot_id, (version, desired, _) in changed.items():
            if mqtt_config.publisher is not None:
                mqtt_config.publisher.publish(robot_id, version, desired)
            # Its rate at the old tolerances says nothing about the new ones,
            # so it is measured afresh (and left alone for a minute).
            fleet.get(robot_id).restart_rate(now)
        planned = (predicted_rate(wanted, scales) if wanted else 0.0) + fixed
        self.last = {
            "robots": len(wanted) + len(waiting),
            "measured": len(wanted),
            "observed_per_min": round(sum(d.rate_per_min for d in wanted), 1),
            "planned_per_min": round(planned, 1),
            "measured_per_min": round(measured_total, 1),
            "correction": round(self.correction, 2),
            # False when even the loosest tolerances cannot get under the
            # budget: the rest of the rate does not depend on tolerance.
            "feasible": feasible(wanted, available) if wanted else True,
            "changed": len(changed),
            "changed_ids": sorted(changed),
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
