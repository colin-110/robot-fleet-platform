"""Topic layout and per-robot access rules.

Every robot owns the topics under ``fleet/<robot_id>/``::

    robot -> server   fleet/<id>/telemetry         readings
                      fleet/<id>/events            mission and fault events
                      fleet/<id>/presence          "online", or "offline" as the last will
                      fleet/<id>/commands/status   command progress
                      fleet/<id>/config/reported   the config the robot applied
    server -> robot   fleet/<id>/commands          commands to run
                      fleet/<id>/config            desired config (retained)

A robot may publish only to its own robot -> server topics and subscribe only
to its own server -> robot topics. Wildcards are never granted to robots, so a
robot cannot read or impersonate another one. The API's own client is a
superuser and is not checked here.
"""

from __future__ import annotations

from enum import IntEnum

ROOT = "fleet"

TELEMETRY = "telemetry"
EVENTS = "events"
PRESENCE = "presence"
COMMAND_STATUS = "commands/status"
CONFIG_REPORTED = "config/reported"
COMMANDS = "commands"
CONFIG = "config"

ROBOT_PUBLISHES = frozenset({TELEMETRY, EVENTS, PRESENCE, COMMAND_STATUS, CONFIG_REPORTED})
ROBOT_RECEIVES = frozenset({COMMANDS, CONFIG})

USERNAME_PREFIX = "robot-"

# Live dashboard updates relayed between API instances. Outside the fleet/
# tree, so no robot can read it.
LIVE = "fleetops/live"


class Access(IntEnum):
    """Access levels as mosquitto-go-auth sends them."""

    READ = 1
    WRITE = 2
    READWRITE = 3
    SUBSCRIBE = 4


def topic(robot_id: int, channel: str) -> str:
    return f"{ROOT}/{robot_id}/{channel}"


def shared_subscription(group: str, channel: str) -> str:
    """Subscription for one channel of every robot, load-balanced across a group."""
    return f"$share/{group}/{ROOT}/+/{channel}"


def parse(name: str) -> tuple[int, str] | None:
    """Split ``fleet/<id>/<channel>`` into ``(id, channel)``, or None."""
    parts = name.split("/", 2)
    if len(parts) != 3 or parts[0] != ROOT or not parts[1].isdigit():
        return None
    return int(parts[1]), parts[2]


def robot_username(robot_id: int) -> str:
    return f"{USERNAME_PREFIX}{robot_id}"


def robot_id_from_username(username: str | None) -> int | None:
    if not username or not username.startswith(USERNAME_PREFIX):
        return None
    suffix = username[len(USERNAME_PREFIX) :]
    return int(suffix) if suffix.isdigit() else None


def robot_may(robot_id: int, name: str, access: int) -> bool:
    """Whether ``robot_id`` may perform ``access`` on topic ``name``."""
    parsed = parse(name)
    if parsed is None or parsed[0] != robot_id:
        return False
    channel = parsed[1]
    if access == Access.WRITE:
        return channel in ROBOT_PUBLISHES
    if access in (Access.READ, Access.SUBSCRIBE):
        return channel in ROBOT_RECEIVES
    return False
