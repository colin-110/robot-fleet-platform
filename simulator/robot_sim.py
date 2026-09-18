import argparse
import asyncio
import contextlib
import logging
import math
import multiprocessing
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import aiohttp

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

MISSION_TYPES = ["PATROL", "DELIVERY", "INSPECTION"]



@dataclass
class MissionStep:
    x: float
    y: float
    label: str
    pause_s: float = 0.0


@dataclass
class Mission:
    mission_id: str
    mission_type: str
    steps: list[MissionStep]
    home_x: float
    home_y: float
    progress_weight: float
    current_step: int = 0


@dataclass
class RobotState:
    robot_id: int
    battery: float = 100.0
    temperature: float = 33.0
    speed: float = 0.0
    status: str = "ACTIVE"
    online: bool = True
    mission: Mission | None = None
    mission_id: str | None = None
    mission_progress: float | None = None
    mission_start_time: str | None = None
    battery_health: float = 100.0
    motor_health: float = 100.0
    sensor_health: float = 100.0
    network_health: float = 100.0
    dead_printed: bool = False
    x: float = 0.0
    y: float = 0.0
    home_x: float = 0.0
    home_y: float = 0.0
    last_update_s: float = 0.0
    pause_until_s: float = 0.0
    completion_count: int = 0
    blackout_until: float = 0.0
    charging_suspended: bool = False
    dead_since: float = 0.0
    returning_to_charge: bool = False
    processed_command_ids: list[str] = field(default_factory=list)
    in_fence: bool = False
    fence_cooldown_until: float = 0.0


def clamp(value: float, lo: float, hi: float):
    return max(lo, min(hi, value))


def get_base_api(api_url: str) -> str:
    parsed = urlparse(api_url)
    path = parsed.path
    if path.endswith('/telemetry'):
        path = path[:-len('/telemetry')]
    elif path.endswith('/telemetry/'):
        path = path[:-len('/telemetry/')]
    return urlunparse(parsed._replace(path=path))


def lerp(a: float, b: float, t: float):
    return a + (b - a) * t


# Multiplies the per-tick component wear rates. Wear is slow in absolute terms
# — a real robot degrades over months — but a demo that has to be left running
# for twenty hours before anything moves off 100% is not demonstrating anything.
WEAR_ACCELERATION = 6.0


def initial_component_health(rng: random.Random, service_age: float | None = None):
    """Component health for a robot entering the fleet.

    Every robot used to start at exactly 100.0. Combined with a wear rate of
    roughly one percent per hour, that meant all four health readouts sat at a
    flat 100 for the first several hours of uptime: the colour thresholds never
    fired, the per-component bars were indistinguishable, and the maintenance
    view had nothing to rank. Real fleets are mixed-age, so this seeds one.

    A single service-age factor drives all four components, because a unit that
    has done ten thousand hours is worn everywhere rather than in one subsystem.
    Per-component jitter keeps them from moving in visible lockstep, and the
    spreads differ because motors wear faster than network interfaces.

    Returns ``(battery, motor, sensor, network)``.
    """
    # Beta(2, 3) skews toward mid-life: mostly serviceable units, a few nearly
    # new, a few due for replacement.
    age = rng.betavariate(2.0, 3.0) if service_age is None else service_age

    def component(max_wear: float) -> float:
        return clamp(100.0 - age * max_wear * rng.uniform(0.75, 1.25), 25.0, 100.0)

    return component(55.0), component(70.0), component(45.0), component(40.0)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("simulator")

def safe_print(message: str):
    logger.info(message)


def random_point(rng: random.Random, radius: float):
    angle = rng.random() * math.tau
    distance = radius * math.sqrt(rng.random())
    return (math.cos(angle) * distance, math.sin(angle) * distance)


def iso_utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_mission(mission_id: str, mission_type: str, *, rng: random.Random, radius: float):
    if mission_type == "PATROL":
        checkpoint_count = rng.randint(3, 5)
        checkpoints = [
            MissionStep(*random_point(rng, radius), label=f"Checkpoint {index + 1}")
            for index in range(checkpoint_count)
        ]
        checkpoints.append(MissionStep(0.0, 0.0, label="Return to base"))
        return Mission(
            mission_id=mission_id,
            mission_type=mission_type,
            steps=checkpoints,
            home_x=0.0,
            home_y=0.0,
            progress_weight=100.0 / len(checkpoints),
        )

    if mission_type == "DELIVERY":
        pickup = MissionStep(*random_point(rng, radius * 0.8), label="Pickup")
        dropoff = MissionStep(*random_point(rng, radius), label="Delivery")
        steps = [pickup, dropoff, MissionStep(0.0, 0.0, label="Return to base")]
        return Mission(
            mission_id=mission_id,
            mission_type=mission_type,
            steps=steps,
            home_x=0.0,
            home_y=0.0,
            progress_weight=100.0 / len(steps),
        )

    inspection_count = rng.randint(2, 4)
    steps = [
        MissionStep(*random_point(rng, radius * 0.9), label=f"Inspection {index + 1}", pause_s=rng.uniform(2.0, 4.0))
        for index in range(inspection_count)
    ]
    return Mission(
        mission_id=mission_id,
        mission_type=mission_type,
        steps=steps,
        home_x=0.0,
        home_y=0.0,
        progress_weight=100.0 / len(steps),
    )


def assign_mission(robot: RobotState, mission: Mission):
    robot.mission = mission
    robot.mission_id = mission.mission_id
    robot.mission_progress = 0.0
    robot.mission_start_time = iso_utc_now()
    robot.status = "ACTIVE"
    robot.pause_until_s = 0.0
    robot.returning_to_charge = False


def clear_mission(robot: RobotState):
    robot.mission = None
    robot.mission_id = None
    robot.mission_progress = None
    robot.mission_start_time = None
    robot.pause_until_s = 0.0


def effective_speed(robot: RobotState, mission_type: str, rng: random.Random):
    base = {
        "PATROL": 1.1,
        "DELIVERY": 1.8,
        "INSPECTION": 0.95,
        "RETURN": 1.4,
    }.get(mission_type, 1.0)
    degraded_cap = base * clamp(robot.motor_health / 100.0, 0.45, 1.0)
    return clamp(degraded_cap + rng.uniform(-0.08, 0.12), 0.2, 2.2)


def apply_sensor_noise(robot: RobotState, value: float, *, kind: str, rng: random.Random):
    noise_scale = (100.0 - robot.sensor_health) / 100.0
    if noise_scale <= 0:
        return value

    if kind == "temperature":
        value += rng.uniform(-2.5, 2.5) * noise_scale
    elif kind == "speed":
        value += rng.uniform(-0.18, 0.18) * noise_scale
    else:
        value += rng.uniform(-1.8, 1.8) * noise_scale

    if rng.random() < noise_scale * 0.05:
        value += rng.uniform(-6.0, 6.0)

    return value


async def post_telemetry(client: aiohttp.ClientSession, api_url: str, payload: dict, timeout_s: float):
    for attempt in range(3):
        try:
            response = await client.post(api_url, json=payload, timeout=timeout_s)
            response.raise_for_status()
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == 2:
                raise exc
            await asyncio.sleep(0.5 * (2 ** attempt))


async def patch_command_status(
    client: aiohttp.ClientSession, base_api: str, cmd_id: str, status: str, **extra
) -> None:
    """Best-effort command status transition.

    Failures are logged and swallowed: a robot that can't report its progress
    should keep executing the command, and the backend's timeout sweeper will
    reconcile anything left dangling.
    """
    payload = {"status": status, **extra}
    try:
        async with client.patch(
            f"{base_api}/commands/{cmd_id}/status", json=payload
        ) as response:
            await response.read()
    except Exception as exc:
        logger.debug("Failed to patch command %s to %s: %s", cmd_id, status, exc)


async def dispatcher_loop(*, robots: list[RobotState], queue: list[Mission], rng: random.Random, radius: float):
    mission_counter = 1
    while True:
        if len(queue) < 4:
            mission_type = rng.choice(MISSION_TYPES)
            mission_id = f"M-{mission_counter:05d}"
            mission_counter += 1
            queue.append(build_mission(mission_id, mission_type, rng=rng, radius=radius))

        for mission in list(queue):
            candidates = [
                robot
                for robot in robots
                if robot.online
                # STOPPED robots stay parked until an explicit RESUME command —
                # the dispatcher must not re-task an emergency-stopped unit.
                and robot.status not in {"DEAD", "CHARGING", "OVERHEATING", "STOPPED"}
                and robot.mission is None
                and not robot.returning_to_charge
                and robot.battery > 20.0
            ]
            if not candidates:
                break

            candidates.sort(
                key=lambda robot: math.hypot(
                    mission.steps[0].x - robot.x,
                    mission.steps[0].y - robot.y,
                )
            )
            selected = candidates[0]
            assign_mission(selected, mission)
            queue.remove(mission)
            safe_print(
                f"[DISPATCH] assigned {mission.mission_type} {mission.mission_id} "
                f"to R{selected.robot_id:02d}"
            )

        await asyncio.sleep(3.0)


async def robot_loop(
    robot: RobotState,
    *,
    client: aiohttp.ClientSession,
    api_url: str,
    queue: asyncio.Queue,
    rng: random.Random,
    ambient_c: float,
    tick_min_s: float,
    tick_max_s: float,
    post_timeout_s: float,
):
    base_api = get_base_api(api_url)
    robot.last_update_s = time.time()

    try:
        while True:
            now = time.time()
            dt = clamp(now - robot.last_update_s, 0.1, 2.5)
            robot.last_update_s = now
            
            # Check simulated network blackout
            is_blacked_out = robot.blackout_until > now
            
            # Trigger network blackout with random probability
            if not is_blacked_out and robot.status not in ("DEAD", "STOPPED"):
                blackout_chance = 0.0015 + ((100.0 - robot.network_health) / 100.0) * 0.008
                if rng.random() < blackout_chance:
                    blackout_duration = rng.uniform(10.0, 25.0)
                    robot.blackout_until = now + blackout_duration
                    is_blacked_out = True
                    safe_print(f"[R{robot.robot_id:02d}] Telemetry drop: Network blackout started for {blackout_duration:.1f}s (network_health={robot.network_health:.1f}%)")

            # Commands still poll during a blackout — a blackout drops this
            # robot's telemetry uplink, not its ability to receive control
            # commands. Gating RETURN_TO_BASE/EMERGENCY_STOP on the same flag
            # meant an operator's Stop could sit unapplied for up to 90s,
            # which reads as a stuck button rather than realistic degraded
            # comms.
            try:
                # POST /claim, not GET: claiming transitions commands to
                # DISPATCHED, so it is not a safe method.
                cmd_url = f"{base_api}/commands/{robot.robot_id}/claim"
                async with client.post(cmd_url, timeout=10.0) as cmd_resp:
                    if cmd_resp.status == 200:
                        data = await cmd_resp.json()
                        for cmd_obj in data:
                            cmd_id = cmd_obj["id"]
                            cmd_action = cmd_obj.get("command_type") or cmd_obj.get("action")

                            if cmd_id in robot.processed_command_ids:
                                # End-to-end idempotency: replay the result
                                # rather than executing the command twice.
                                await patch_command_status(
                                    client, base_api, cmd_id, "COMPLETED",
                                    result={"message": "Already processed"},
                                )
                                continue

                            robot.processed_command_ids.append(cmd_id)
                            if len(robot.processed_command_ids) > 100:
                                robot.processed_command_ids.pop(0)

                            await patch_command_status(client, base_api, cmd_id, "ACKNOWLEDGED")
                            await patch_command_status(client, base_api, cmd_id, "EXECUTING")

                            status_to_patch = "COMPLETED"

                            if cmd_action == "RETURN_TO_BASE":
                                clear_mission(robot)
                                robot.returning_to_charge = True
                                robot.status = "ACTIVE"
                                robot.online = True
                                safe_print(f"[R{robot.robot_id:02d}] Executing RETURN_TO_BASE command (id={cmd_id})")
                            elif cmd_action == "EMERGENCY_STOP":
                                robot.status = "STOPPED"
                                robot.speed = 0.0
                                clear_mission(robot)
                                robot.returning_to_charge = False
                                safe_print(f"[R{robot.robot_id:02d}] EMERGENCY STOP ACTIVATED (id={cmd_id})")
                            elif cmd_action == "RESUME":
                                robot.status = "ACTIVE"
                                robot.online = True
                                safe_print(f"[R{robot.robot_id:02d}] RESUMED (id={cmd_id})")
                            else:
                                status_to_patch = "FAILED"

                            await patch_command_status(
                                client, base_api, cmd_id, status_to_patch
                            )
            except Exception as e:
                logger.error(f"[R{robot.robot_id:02d}] Command poll error: {e}", exc_info=True)

            # Meltdown / Battery exhaustion DEAD checks
            is_dead = (
                robot.battery <= 5.0 
                or robot.temperature >= 95.0 
                or robot.battery_health < 10.0 
                or robot.motor_health < 10.0
            )
            
            if is_dead:
                if robot.status != "DEAD":
                    robot.status = "DEAD"
                    robot.online = False
                    robot.speed = 0.0
                    robot.dead_since = now
                    clear_mission(robot)
                    robot.returning_to_charge = False
                    safe_print(f"[R{robot.robot_id:02d}] SHUTDOWN: DEAD state reached (bat={robot.battery:.1f}%, temp={robot.temperature:.1f}C, motor_h={robot.motor_health:.1f}%)")
                
                # Emit dead telemetry if not blacked out
                if not is_blacked_out:
                    await emit_telemetry(robot, queue, rng)
                
                # Maintenance repair crew event (45s to 90s delay)
                if now - robot.dead_since >= rng.uniform(45.0, 90.0):
                    robot.battery = 100.0
                    robot.temperature = ambient_c
                    # A replacement unit, not a magically restored one. Reviving
                    # everything to a flat 100 pulled the whole fleet toward
                    # perfect health over a long run, undoing the age spread.
                    (
                        robot.battery_health,
                        robot.motor_health,
                        robot.sensor_health,
                        robot.network_health,
                    ) = initial_component_health(rng, service_age=rng.uniform(0.02, 0.15))
                    robot.status = "ACTIVE"
                    robot.online = True
                    robot.charging_suspended = False
                    safe_print(f"[R{robot.robot_id:02d}] MAINTENANCE COMPLETE: Robot fully revived and operational")
                
                await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
                continue

            if robot.status == "STOPPED":
                if not is_blacked_out:
                    await emit_telemetry(robot, queue, rng)
                await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
                continue

            # Low power / Return to charge triggers
            if robot.battery <= 30.0 and robot.status not in ("CHARGING", "RETURNING_TO_CHARGE"):
                robot.status = "LOW POWER"
                if robot.battery <= 25.0 and robot.mission is not None:
                    # Abort active mission to top up
                    safe_print(f"[R{robot.robot_id:02d}] Aborting mission {robot.mission_id} due to low charge ({robot.battery:.1f}%)")
                    clear_mission(robot)
                    robot.returning_to_charge = True

            # Proactive top up if idle
            if robot.mission is None and robot.battery <= 50.0 and not robot.returning_to_charge and robot.status != "CHARGING":
                robot.returning_to_charge = True

            # ── STATE PHYSICS ──

            # 1. Returning to charge base
            if robot.returning_to_charge:
                robot.status = "LOW POWER" if robot.battery <= 20.0 else "ACTIVE"
                dx = 0.0 - robot.x
                dy = 0.0 - robot.y
                dist = math.hypot(dx, dy)
                if dist < 0.5:
                    robot.returning_to_charge = False
                    robot.status = "CHARGING"
                    robot.x = 0.0
                    robot.y = 0.0
                    robot.speed = 0.0
                    safe_print(f"[R{robot.robot_id:02d}] Arrived at Base charging pad.")
                else:
                    target_speed = effective_speed(robot, "RETURN", rng)
                    robot.speed = lerp(robot.speed, target_speed, 0.22)
                    step_distance = robot.speed * dt
                    robot.x += (dx / dist) * step_distance
                    robot.y += (dy / dist) * step_distance
                    
                    motor_penalty = 1.0 + ((100.0 - robot.motor_health) / 100.0) * 0.45
                    battery_penalty = 1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.65
                    drain_per_s = (0.008 + 0.014 * robot.speed) * motor_penalty * battery_penalty
                    robot.battery -= drain_per_s * dt * 20.0
                    
                    load_heat = 0.055 + 0.055 * robot.speed + ((100.0 - robot.motor_health) / 100.0) * 0.035
                    cooling = 0.02 * max(0.0, robot.temperature - ambient_c)
                    robot.temperature += (load_heat - cooling) * dt

            # 2. Charging (with thermal suspension)
            elif robot.status == "CHARGING":
                robot.speed = 0.0
                
                if robot.temperature >= 80.0 and not robot.charging_suspended:
                    robot.charging_suspended = True
                    safe_print(f"[R{robot.robot_id:02d}] Thermal safety: Charging suspended due to overheating ({robot.temperature:.1f}C)")
                
                if robot.charging_suspended:
                    robot.status = "OVERHEATING"
                    # Cool down
                    robot.temperature -= 0.08 * (robot.temperature - ambient_c) * dt
                    if robot.temperature <= 60.0:
                        robot.charging_suspended = False
                        robot.status = "CHARGING"
                        safe_print(f"[R{robot.robot_id:02d}] Battery cooled to safe levels. Resuming charge cycle.")
                else:
                    charge_rate = 0.35 * clamp(robot.battery_health / 100.0, 0.55, 1.0)
                    robot.battery = clamp(robot.battery + charge_rate * dt * 10.0, 0.0, 100.0)
                    
                    # Charging creates heat
                    charging_heat = 0.12 * (1.0 + (100.0 - robot.battery_health) / 100.0)
                    cooling = 0.035 * (robot.temperature - ambient_c)
                    robot.temperature += (charging_heat - cooling) * dt
                    
                    if robot.battery >= 100.0:
                        robot.status = "ACTIVE"
                        safe_print(f"[R{robot.robot_id:02d}] Fully charged to 100%.")

            # 3. Overheating operation (speed halved)
            elif robot.temperature >= 80.0:
                robot.status = "OVERHEATING"
                if robot.mission is not None:
                    mission = robot.mission
                    step = mission.steps[mission.current_step]
                    dx = step.x - robot.x
                    dy = step.y - robot.y
                    dist = math.hypot(dx, dy)
                    
                    # Slow down by half to cool down
                    target_speed = effective_speed(robot, mission.mission_type, rng) / 2.0
                    robot.speed = lerp(robot.speed, target_speed, 0.22)
                    
                    if dist < 0.45:
                        mission.current_step += 1
                        robot.mission_progress = round(min(100.0, mission.current_step * mission.progress_weight), 1)
                        if mission.current_step >= len(mission.steps):
                            robot.completion_count += 1
                            robot.mission_progress = 100.0
                            safe_print(f"[R{robot.robot_id:02d}] Completed mission {mission.mission_id} while overheating.")
                            if not is_blacked_out:
                                await emit_telemetry(robot, queue, rng)
                            clear_mission(robot)
                        else:
                            next_step = mission.steps[mission.current_step]
                    else:
                        step_distance = robot.speed * dt
                        robot.x += (dx / dist) * step_distance
                        robot.y += (dy / dist) * step_distance
                        
                    # Lower battery drain
                    motor_penalty = 1.0 + ((100.0 - robot.motor_health) / 100.0) * 0.45
                    battery_penalty = 1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.65
                    drain_per_s = (0.008 + 0.014 * robot.speed) * motor_penalty * battery_penalty
                    robot.battery -= drain_per_s * dt * 20.0
                    
                    load_heat = 0.02 + 0.02 * robot.speed
                    cooling = 0.045 * (robot.temperature - ambient_c)
                    robot.temperature += (load_heat - cooling) * dt
                else:
                    robot.speed = lerp(robot.speed, 0.0, 0.35)
                    robot.temperature -= 0.06 * (robot.temperature - ambient_c) * dt

            # 4. Normal Mission Operation
            elif robot.mission is not None:
                mission = robot.mission
                step = mission.steps[mission.current_step]
                dx = step.x - robot.x
                dy = step.y - robot.y
                dist = math.hypot(dx, dy)

                if robot.pause_until_s > now:
                    robot.speed = lerp(robot.speed, 0.0, 0.4)
                else:
                    target_speed = effective_speed(robot, mission.mission_type, rng)
                    robot.speed = lerp(robot.speed, target_speed, 0.22)

                    if dist < 0.45:
                        mission.current_step += 1
                        robot.mission_progress = round(
                            min(100.0, mission.current_step * mission.progress_weight),
                            1,
                        )

                        if step.pause_s > 0:
                            robot.pause_until_s = now + step.pause_s

                        if mission.current_step >= len(mission.steps):
                            robot.completion_count += 1
                            robot.mission_progress = 100.0
                            safe_print(
                                f"[R{robot.robot_id:02d}] completed "
                                f"{mission.mission_type} {mission.mission_id}"
                            )
                            if not is_blacked_out:
                                await emit_telemetry(robot, queue, rng)
                                try:
                                    async with client.post(f"{base_api}/events", json={
                                        "robot_id": robot.robot_id,
                                        "message": f"Completed {mission.mission_type} mission {mission.mission_id}",
                                    }, timeout=10.0) as _r:
                                        await _r.read()
                                except Exception:
                                    pass
                            clear_mission(robot)
                            robot.status = "ACTIVE"
                        else:
                            next_step = mission.steps[mission.current_step]
                            safe_print(
                                f"[R{robot.robot_id:02d}] {mission.mission_id} -> {next_step.label}"
                            )
                    else:
                        step_distance = robot.speed * dt
                        robot.x += (dx / dist) * step_distance
                        robot.y += (dy / dist) * step_distance

                motor_penalty = 1.0 + ((100.0 - robot.motor_health) / 100.0) * 0.45
                battery_penalty = 1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.65
                drain_per_s = (0.008 + 0.014 * robot.speed) * motor_penalty * battery_penalty
                robot.battery -= drain_per_s * dt * 20.0

                load_heat = 0.055 + 0.055 * robot.speed + ((100.0 - robot.motor_health) / 100.0) * 0.035
                cooling = 0.02 * max(0.0, robot.temperature - ambient_c)
                robot.temperature += (load_heat - cooling) * dt

            # 5. Standby
            else:
                robot.speed = lerp(robot.speed, 0.0, 0.35)
                standby_drain = 0.0015 * (1.0 + ((100.0 - robot.battery_health) / 100.0) * 0.25)
                robot.battery -= standby_drain * dt * 20.0
                robot.temperature -= 0.04 * (robot.temperature - ambient_c) * dt

            # Wear down components. Scaled by WEAR_ACCELERATION so degradation
            # is observable across a demo session rather than a working week.
            wear = WEAR_ACCELERATION
            robot.battery_health = clamp(
                robot.battery_health - rng.uniform(0.0006, 0.0012) * wear, 10.0, 100.0
            )
            robot.motor_health = clamp(
                robot.motor_health
                - rng.uniform(0.0008, 0.0014) * (1.3 if robot.mission else 0.5) * wear,
                10.0,
                100.0,
            )
            robot.sensor_health = clamp(
                robot.sensor_health - rng.uniform(0.0005, 0.0010) * wear, 10.0, 100.0
            )
            robot.network_health = clamp(
                robot.network_health - rng.uniform(0.0006, 0.0011) * wear, 10.0, 100.0
            )

            if rng.random() < 0.0025:
                robot.temperature += rng.uniform(4.0, 9.0)

            # Check geofence. Hysteresis (enter <5m, clear only after >7m) stops a
            # robot hovering at the boundary from oscillating, and a per-robot
            # cooldown guarantees we never spam the same entry event.
            dist_to_fence = math.hypot(robot.x - 15.0, robot.y - 10.0)
            if dist_to_fence < 5.0 and not robot.in_fence:
                robot.in_fence = True
                if now >= robot.fence_cooldown_until and not is_blacked_out:
                    robot.fence_cooldown_until = now + 45.0
                    safe_print(f"[R{robot.robot_id:02d}] ENTERED RESTRICTED ZONE")
                    try:
                        async with client.post(f"{base_api}/events", json={
                            "robot_id": robot.robot_id,
                            "message": "Entered Restricted Zone!"
                        }, timeout=10.0) as _r:
                            await _r.read()
                    except Exception:
                        pass
            elif dist_to_fence > 7.0 and robot.in_fence:
                robot.in_fence = False

            robot.battery = clamp(robot.battery, 0.0, 100.0)
            robot.temperature = clamp(robot.temperature, 22.0, 99.0)
            robot.speed = clamp(robot.speed, 0.0, 2.2)

            # Emit telemetry if online and not in blackout
            if not is_blacked_out:
                await emit_telemetry(robot, queue, rng)

            await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
            
    except asyncio.CancelledError:
        safe_print(f"[R{robot.robot_id:02d}] Decommissioned and shutting down.")


async def emit_telemetry(robot: RobotState, queue: asyncio.Queue, rng: random.Random):
    payload = {
        "robot_id": robot.robot_id,
        # The robot stamps its own reading. Readings sit in an outbound queue
        # and are posted in batches, so a server-side timestamp would record
        # when the batch was uploaded rather than when each was measured —
        # collapsing readings taken seconds apart onto one instant.
        "timestamp": iso_utc_now(),
        "battery": round(clamp(apply_sensor_noise(robot, robot.battery, kind="battery", rng=rng), 0.0, 100.0), 2),
        "temperature": round(clamp(apply_sensor_noise(robot, robot.temperature, kind="temperature", rng=rng), 0.0, 120.0), 2),
        "speed": round(clamp(apply_sensor_noise(robot, robot.speed, kind="speed", rng=rng), 0.0, 3.0), 2),
        "status": robot.status,
        "mission_id": robot.mission_id,
        "mission_type": robot.mission.mission_type if robot.mission else None,
        "mission_progress": robot.mission_progress,
        "mission_start_time": robot.mission_start_time,
        "battery_health": round(robot.battery_health, 2),
        "motor_health": round(robot.motor_health, 2),
        "sensor_health": round(robot.sensor_health, 2),
        "network_health": round(robot.network_health, 2),
        "x": round(robot.x, 2),
        "y": round(robot.y, 2),
    }

    try:
        await queue.put(payload)
        mission_label = robot.mission.mission_type if robot.mission else ("RTB" if robot.returning_to_charge else "IDLE")
        progress = f"{robot.mission_progress:5.1f}%" if robot.mission_progress is not None else "  n/a"
        safe_print(
            f"[R{robot.robot_id:02d}] {robot.status:<10} {mission_label:<10} "
            f"bat={robot.battery:5.1f}% temp={robot.temperature:5.1f}C spd={robot.speed:4.2f} "
            f"mission={progress} comp=({robot.battery_health:4.0f}/{robot.motor_health:4.0f}/"
            f"{robot.sensor_health:4.0f}/{robot.network_health:4.0f}) pos=({robot.x:5.1f},{robot.y:5.1f})"
        )
    except Exception as exc:
        safe_print(f"[R{robot.robot_id:02d}] Queue put failed: {exc}")


async def telemetry_batcher(client: aiohttp.ClientSession, api_url: str, post_timeout_s: float, queue: asyncio.Queue):
    batch = []
    base_api = get_base_api(api_url)
    batch_url = f"{base_api}/telemetry/batch"
    while True:
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=1.0)
            batch.append(payload)
            if len(batch) >= 50:
                try:
                    await post_telemetry(client, batch_url, batch, post_timeout_s)
                except Exception as exc:
                    safe_print(f"[BATCHER] POST failed: {exc}")
                batch.clear()
        except asyncio.TimeoutError:
            if batch:
                try:
                    await post_telemetry(client, batch_url, batch, post_timeout_s)
                except Exception as exc:
                    safe_print(f"[BATCHER] POST failed: {exc}")
                batch.clear()
        except asyncio.CancelledError:
            break


async def main_async(args, worker_index=0, total_workers=1):


    rng = random.Random(args.seed)
    
    robots_count = args.robots
    if robots_count <= 0:
        robots_count = rng.randint(35, 55)
    
    # Calculate chunk for this worker
    chunk_size = robots_count // total_workers
    remainder = robots_count % total_workers
    my_robots = chunk_size + (1 if worker_index < remainder else 0)
    
    start_id = sum(chunk_size + (1 if i < remainder else 0) for i in range(worker_index)) + 1
    end_id = start_id + my_robots
    
    safe_print(f"[Worker {worker_index}] Initializing fleet with {my_robots} robots (IDs {start_id} to {end_id - 1}).")

    robots = []
    for rid in range(start_id, end_id):
        start_x, start_y = random_point(rng, args.radius * 0.2)
        battery_h, motor_h, sensor_h, network_h = initial_component_health(rng)
        robots.append(
            RobotState(
                robot_id=rid,
                battery=clamp(100.0 - rng.uniform(0, 12), 65.0, 100.0),
                temperature=clamp(args.ambient + rng.uniform(1.0, 6.0), 24.0, 50.0),
                battery_health=battery_h,
                motor_health=motor_h,
                sensor_health=sensor_h,
                network_health=network_h,
                x=start_x,
                y=start_y,
                home_x=0.0,
                home_y=0.0,
            )
        )

    mission_queue: list[Mission] = []
    
    # Start dispatcher loop
    dispatcher_task = asyncio.create_task(
        dispatcher_loop(
            robots=robots,
            queue=mission_queue,
            rng=random.Random(args.seed + 999),
            radius=args.radius,
        )
    )

    active_robot_tasks = {}
    headers = {"X-API-Key": args.api_key}
    connector = aiohttp.TCPConnector(limit=5000)
    telemetry_queue = asyncio.Queue()
    timeout_obj = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout_obj) as client:
        # Start batcher
        batcher_task = asyncio.create_task(
            telemetry_batcher(client, args.api_url, args.timeout, telemetry_queue)
        )
        
        # Start robot loops
        for robot in robots:
            task = asyncio.create_task(
                robot_loop(
                    robot,
                    client=client,
                    api_url=args.api_url,
                    queue=telemetry_queue,
                    rng=random.Random((args.seed * 1000) + robot.robot_id * 17),
                    ambient_c=args.ambient,
                    tick_min_s=args.tick_min,
                    tick_max_s=args.tick_max,
                    post_timeout_s=args.timeout,
                )
            )
            active_robot_tasks[robot.robot_id] = task

        try:
            while True:
                await asyncio.sleep(1.0)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            dispatcher_task.cancel()
            batcher_task.cancel()
            for task in list(active_robot_tasks.values()):
                task.cancel()



def worker_process(args, worker_index, total_workers):
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async(args, worker_index, total_workers))

def main():
    parser = argparse.ArgumentParser(description="Mission-based robot fleet simulator")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000/api/v1/telemetry",
    )
    parser.add_argument("--local", action="store_true", help="Use local API URL")
    parser.add_argument("--robots", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    # Read from the environment, never a flag. A default here was a working key
    # published in a public repo, and passing one on the command line puts it in
    # `ps aux` and `docker inspect` for anyone with host access.
    parser.add_argument(
        "--api-key",
        default=os.environ.get("TELEMETRY_API_KEY"),
        help="API key. Prefer the TELEMETRY_API_KEY environment variable.",
    )
    parser.add_argument("--ambient", type=float, default=30.0)
    parser.add_argument("--radius", type=float, default=20.0)
    parser.add_argument("--tick-min", type=float, default=2.0)
    parser.add_argument("--tick-max", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if not args.api_key:
        parser.error(
            "No API key. Set TELEMETRY_API_KEY in the environment "
            "(the compose file already passes backend/.env through)."
        )

    if args.local:
        args.api_url = "http://localhost:8000/api/v1/telemetry"

    if args.workers > 1:
        processes = []
        for i in range(args.workers):
            p = multiprocessing.Process(target=worker_process, args=(args, i, args.workers))
            p.start()
            processes.append(p)
        
        try:
            for p in processes:
                p.join()
        except KeyboardInterrupt:
            safe_print("\nSimulator stopped.")
            for p in processes:
                p.terminate()
    else:
        try:
            asyncio.run(main_async(args, 0, 1))
        except KeyboardInterrupt:
            safe_print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
