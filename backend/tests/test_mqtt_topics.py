"""Topic layout and the per-robot ACL."""

import pytest

from app.mqtt import topics
from app.mqtt.topics import Access


def test_topic_round_trip():
    name = topics.topic(7, topics.COMMAND_STATUS)
    assert name == "fleet/7/commands/status"
    assert topics.parse(name) == (7, "commands/status")


@pytest.mark.parametrize(
    "name", ["fleet", "fleet/7", "fleet/x/telemetry", "other/7/telemetry", "fleet/+/telemetry"]
)
def test_parse_rejects_malformed_topics(name):
    assert topics.parse(name) is None


def test_shared_subscription():
    assert topics.shared_subscription("ingest", "telemetry") == "$share/ingest/fleet/+/telemetry"


def test_username_round_trip():
    assert topics.robot_id_from_username(topics.robot_username(12)) == 12
    for bad in (None, "", "robot-", "robot-x", "fleetops", "robot-1-2"):
        assert topics.robot_id_from_username(bad) is None


@pytest.mark.parametrize("channel", sorted(topics.ROBOT_PUBLISHES))
def test_robot_publishes_only_its_own_uplink(channel):
    assert topics.robot_may(3, f"fleet/3/{channel}", Access.WRITE)
    assert not topics.robot_may(3, f"fleet/4/{channel}", Access.WRITE)
    assert not topics.robot_may(3, f"fleet/3/{channel}", Access.SUBSCRIBE)


@pytest.mark.parametrize("channel", sorted(topics.ROBOT_RECEIVES))
def test_robot_receives_only_its_own_downlink(channel):
    assert topics.robot_may(3, f"fleet/3/{channel}", Access.SUBSCRIBE)
    assert topics.robot_may(3, f"fleet/3/{channel}", Access.READ)
    assert not topics.robot_may(3, f"fleet/4/{channel}", Access.READ)
    # A robot must not be able to forge its own commands or config.
    assert not topics.robot_may(3, f"fleet/3/{channel}", Access.WRITE)


@pytest.mark.parametrize("name", ["fleet/3/#", "fleet/+/commands", "fleet/3/+", "#"])
def test_wildcards_are_never_granted(name):
    assert not topics.robot_may(3, name, Access.SUBSCRIBE)


def test_unknown_channels_and_access_levels_are_denied():
    assert not topics.robot_may(3, "fleet/3/anything", Access.WRITE)
    assert not topics.robot_may(3, "fleet/3/telemetry", Access.READWRITE)
    assert not topics.robot_may(3, "fleet/3/telemetry", 99)
