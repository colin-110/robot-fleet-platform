import argparse
import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp
import multiprocessing
import sys
from urllib.parse import urlparse, urlunparse


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
    processed_command_ids: set[str] = field(default_factory=set)


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
                and robot.status not in {"DEAD", "CHARGING", "OVERHEATING"}
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
                blackout_chance = 0.003 + ((100.0 - robot.network_health) / 100.0) * 0.015
                if rng.random() < blackout_chance:
                    blackout_duration = rng.uniform(30.0, 90.0)
                    robot.blackout_until = now + blackout_duration
                    is_blacked_out = True
                    safe_print(f"[R{robot.robot_id:02d}] Telemetry drop: Network blackout started for {blackout_duration:.1f}s (network_health={robot.network_health:.1f}%)")

            # Poll commands if not blacked out
            if not is_blacked_out:
                try:
                    cmd_url = f"{base_api}/commands/{robot.robot_id}"
                    async with client.get(cmd_url, timeout=2.0) as cmd_resp:
                        if cmd_resp.status == 200:
                            data = await cmd_resp.json()
                            for cmd_obj in data:
                                cmd_id = cmd_obj["id"]
                                cmd_action = cmd_obj.get("command_type") or cmd_obj.get("action")
                                
                                if cmd_id in robot.processed_command_ids:
                                    # End-to-end idempotency: return existing result
                                    try:
                                        async with client.patch(f"{base_api}/commands/{cmd_id}/status", json={"status": "COMPLETED", "result": {"message": "Already processed"}}) as _r: await _r.read()
                                    except Exception:
                                        pass
                                    continue
                                    
                                robot.processed_command_ids.add(cmd_id)
                                
                                # Acknowledge command receipt
                                try:
                                    async with client.patch(f"{base_api}/commands/{cmd_id}/status", json={"status": "ACKNOWLEDGED"}) as _r: await _r.read()
                                except Exception:
                                    pass
                                    
                                # Start execution
                                try:
                                    async with client.patch(f"{base_api}/commands/{cmd_id}/status", json={"status": "EXECUTING"}) as _r: await _r.read()
                                except Exception:
                                    pass
                                    
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
                                    
                                # Complete command
                                try:
                                    async with client.patch(f"{base_api}/commands/{cmd_id}/status", json={"status": status_to_patch}) as _r: await _r.read()
                                except Exception:
                                    pass
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
                    await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
                
                # Maintenance repair crew event (45s to 90s delay)
                if now - robot.dead_since >= rng.uniform(45.0, 90.0):
                    robot.battery = 100.0
                    robot.temperature = ambient_c
                    robot.battery_health = 100.0
                    robot.motor_health = 100.0
                    robot.sensor_health = 100.0
                    robot.network_health = 100.0
                    robot.status = "ACTIVE"
                    robot.online = True
                    robot.charging_suspended = False
                    safe_print(f"[R{robot.robot_id:02d}] MAINTENANCE COMPLETE: Robot fully revived and operational")
                
                await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
                continue

            if robot.status == "STOPPED":
                if not is_blacked_out:
                    await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
                await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
                continue

            # Low power / Return to charge triggers
            if robot.battery <= 20.0 and robot.status not in ("CHARGING", "RETURNING_TO_CHARGE"):
                robot.status = "LOW POWER"
                if robot.battery <= 10.0 and robot.mission is not None:
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
                
                if robot.temperature >= 80.0:
                    if not robot.charging_suspended:
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
                                await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
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
                                await emit_telemetry(robot, client, api_url, rng, post_timeout_s)
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

            # Wear down components
            robot.battery_health = clamp(robot.battery_health - rng.uniform(0.0006, 0.0012), 10.0, 100.0)
            robot.motor_health = clamp(
                robot.motor_health - rng.uniform(0.0008, 0.0014) * (1.3 if robot.mission else 0.5),
                10.0,
                100.0,
            )
            robot.sensor_health = clamp(robot.sensor_health - rng.uniform(0.0005, 0.0010), 10.0, 100.0)
            robot.network_health = clamp(robot.network_health - rng.uniform(0.0006, 0.0011), 10.0, 100.0)

            if rng.random() < 0.0025:
                robot.temperature += rng.uniform(4.0, 9.0)

            # Check geofence
            dist_to_fence = math.hypot(robot.x - 15.0, robot.y - 10.0)
            if dist_to_fence < 5.0:
                if not getattr(robot, 'in_fence', False):
                    robot.in_fence = True
                    safe_print(f"[R{robot.robot_id:02d}] ENTERED RESTRICTED ZONE")
                    if not is_blacked_out:
                        try:
                            async with client.post(f"{base_api}/events", json={
                                "robot_id": robot.robot_id,
                                "message": "Entered Restricted Zone!"
                            }, timeout=2.0) as _r:
                                await _r.read()
                        except Exception:
                            pass
            else:
                if getattr(robot, 'in_fence', False):
                    robot.in_fence = False
                    safe_print(f"[R{robot.robot_id:02d}] EXITED RESTRICTED ZONE")

            robot.battery = clamp(robot.battery, 0.0, 100.0)
            robot.temperature = clamp(robot.temperature, 22.0, 99.0)
            robot.speed = clamp(robot.speed, 0.0, 2.2)

            # Emit telemetry if online and not in blackout
            if not is_blacked_out:
                await emit_telemetry(robot, client, api_url, rng, post_timeout_s)

            await asyncio.sleep(rng.uniform(tick_min_s, tick_max_s))
            
    except asyncio.CancelledError:
        safe_print(f"[R{robot.robot_id:02d}] Decommissioned and shutting down.")


async def emit_telemetry(robot: RobotState, client: aiohttp.ClientSession, api_url: str, rng: random.Random, post_timeout_s: float):
    payload = {
        "robot_id": robot.robot_id,
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
        await post_telemetry(client, api_url, payload, post_timeout_s)
        mission_label = robot.mission.mission_type if robot.mission else ("RTB" if robot.returning_to_charge else "IDLE")
        progress = f"{robot.mission_progress:5.1f}%" if robot.mission_progress is not None else "  n/a"
        safe_print(
            f"[R{robot.robot_id:02d}] {robot.status:<10} {mission_label:<10} "
            f"bat={robot.battery:5.1f}% temp={robot.temperature:5.1f}C spd={robot.speed:4.2f} "
            f"mission={progress} comp=({robot.battery_health:4.0f}/{robot.motor_health:4.0f}/"
            f"{robot.sensor_health:4.0f}/{robot.network_health:4.0f}) pos=({robot.x:5.1f},{robot.y:5.1f})"
        )
    except Exception as exc:
        safe_print(f"[R{robot.robot_id:02d}] POST failed: {exc}")


async def fleet_scaling_loop(
    robots: list[RobotState],
    active_robot_tasks: dict[int, asyncio.Task],
    client: aiohttp.ClientSession,
    api_url: str,
    rng: random.Random,
    ambient: float,
    tick_min: float,
    tick_max: float,
    timeout: float,
    radius: float,
    initial_robots_count: int,
    total_workers: int,
    worker_index: int,
):
    max_robot_id = initial_robots_count * total_workers + worker_index * 1000000
    
    while True:
        # Check size constraints
        # Keep fleet size between 40 and 60.
        # Random choice to scale up or down
        await asyncio.sleep(rng.uniform(60.0, 120.0))
        
        current_count = len(robots)
        action = None
        if current_count < 40:
            action = "deploy"
        elif current_count > 60:
            action = "retire"
        else:
            action = rng.choice(["deploy", "retire", "none"])
            
        if action == "deploy":
            max_robot_id += 1
            new_id = max_robot_id
            start_x, start_y = random_point(rng, radius * 0.2)
            new_robot = RobotState(
                robot_id=new_id,
                battery=100.0,
                temperature=ambient,
                x=start_x,
                y=start_y,
                home_x=0.0,
                home_y=0.0,
            )
            robots.append(new_robot)
            
            # Start loop
            task = asyncio.create_task(
                robot_loop(
                    new_robot,
                    client=client,
                    api_url=api_url,
                    rng=random.Random(rng.randint(0, 1000000)),
                    ambient_c=ambient,
                    tick_min_s=tick_min,
                    tick_max_s=tick_max,
                    post_timeout_s=timeout,
                )
            )
            active_robot_tasks[new_id] = task
            safe_print(f"[FLEET] Deploying new robot R{new_id:02d} to the field.")
            
        elif action == "retire" and current_count > 10:
            candidates = [r for r in robots if r.status != "DEAD"]
            if not candidates:
                candidates = robots
                
            selected = rng.choice(candidates)
            rid = selected.robot_id
            
            # Remove mission
            clear_mission(selected)
            
            # Stop task
            if rid in active_robot_tasks:
                task = active_robot_tasks[rid]
                task.cancel()
                del active_robot_tasks[rid]
                
            # Remove from list
            robots.remove(selected)
            safe_print(f"[FLEET] Retired robot R{rid:02d}. Recalled to workshop.")


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
        robots.append(
            RobotState(
                robot_id=rid,
                battery=clamp(100.0 - rng.uniform(0, 12), 65.0, 100.0),
                temperature=clamp(args.ambient + rng.uniform(1.0, 6.0), 24.0, 50.0),
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
    async with aiohttp.ClientSession(headers=headers, connector=connector) as client:
        # Start robot loops
        for robot in robots:
            task = asyncio.create_task(
                robot_loop(
                    robot,
                    client=client,
                    api_url=args.api_url,
                    rng=random.Random((args.seed * 1000) + robot.robot_id * 17),
                    ambient_c=args.ambient,
                    tick_min_s=args.tick_min,
                    tick_max_s=args.tick_max,
                    post_timeout_s=args.timeout,
                )
            )
            active_robot_tasks[robot.robot_id] = task

        # Start fleet scaling daemon task
        scaling_task = asyncio.create_task(
            fleet_scaling_loop(
                robots=robots,
                active_robot_tasks=active_robot_tasks,
                client=client,
                api_url=args.api_url,
                rng=random.Random(args.seed + 777),
                ambient=args.ambient,
                tick_min=args.tick_min,
                tick_max=args.tick_max,
                timeout=args.timeout,
                radius=args.radius,
                initial_robots_count=args.robots if args.robots > 0 else 55,
                total_workers=total_workers,
                worker_index=worker_index,
            )
        )

        try:
            while True:
                await asyncio.sleep(1.0)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            dispatcher_task.cancel()
            scaling_task.cancel()
            for task in list(active_robot_tasks.values()):
                task.cancel()



def worker_process(args, worker_index, total_workers):
    try:
        asyncio.run(main_async(args, worker_index, total_workers))
    except KeyboardInterrupt:
        pass

def main():
    parser = argparse.ArgumentParser(description="Mission-based robot fleet simulator")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000/api/v1/telemetry",
    )
    parser.add_argument("--local", action="store_true", help="Use local API URL")
    parser.add_argument("--robots", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--api-key", default="fleet-secret-key-2026", help="API authentication key")
    parser.add_argument("--ambient", type=float, default=30.0)
    parser.add_argument("--radius", type=float, default=20.0)
    parser.add_argument("--tick-min", type=float, default=2.0)
    parser.add_argument("--tick-max", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

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
