from datetime import datetime, timezone

import cbor2
import orjson
import pytest
from fleetops_agent import wire
from fleetops_agent.wire import Envelope, WireError

NOW_MS = 1_790_000_000_000


def reading(i: int, **overrides) -> dict:
    values = {
        "timestamp": (NOW_MS - 3000 + i * 1000) / 1000,
        "battery": 87.43 - i * 0.01,
        "temperature": 41.2,
        "speed": 1.25,
        "status": "ACTIVE",
        "mission_id": "M-00042",
        "mission_type": "PATROL",
        "mission_progress": 12.5 + i,
        "mission_start_time": datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        "battery_health": 97.1,
        "motor_health": 88.0,
        "sensor_health": 99.5,
        "network_health": 93.25,
        "x": 3.21 + i * 0.5,
        "y": -7.5,
    }
    values.update(overrides)
    return values


def roundtrip(channel, items, sent_ms=NOW_MS):
    packed = [wire.pack_item(channel, i) for i in items]
    payload = wire.encode_envelope(channel, Envelope("abc123", 41, 30, sent_ms, packed))
    return payload, wire.decode_envelope(channel, payload)


def test_telemetry_roundtrip_keeps_every_value():
    items = [reading(i) for i in range(3)]
    _, envelope = roundtrip("telemetry", items)
    assert (envelope.stream, envelope.seq, envelope.floor, envelope.last_seq) == (
        "abc123",
        41,
        30,
        43,
    )
    for original, packed in zip(items, envelope.items, strict=True):
        values = wire.unpack_item("telemetry", packed)
        assert values["timestamp"] == datetime.fromtimestamp(original["timestamp"], timezone.utc)
        assert values["mission_start_time"] == original["mission_start_time"]
        for name in ("battery", "temperature", "x", "y", "mission_progress", "network_health"):
            assert values[name] == pytest.approx(original[name], abs=0.005)
        assert values["status"] == "ACTIVE"


def test_later_items_carry_only_what_changed():
    items = [reading(0), reading(1, status="CHARGING")]
    payload, _ = roundtrip("telemetry", items)
    second = cbor2.loads(payload)["d"][1]
    # Time, battery, progress, x and the status changed; nothing else is resent.
    assert set(second) == {"t", "b", "mp", "x", "st"}


def test_a_value_that_goes_away_is_cleared():
    items = [reading(0), reading(1, mission_id=None, mission_type=None)]
    _, envelope = roundtrip("telemetry", items)
    values = wire.unpack_item("telemetry", envelope.items[1])
    assert "mission_id" not in values and "mission_type" not in values
    assert values["status"] == "ACTIVE"


def test_much_smaller_than_json():
    items = [reading(i) for i in range(10)]
    payload, _ = roundtrip("telemetry", items)
    as_json = orjson.dumps(
        [{**i, "mission_start_time": i["mission_start_time"].isoformat()} for i in items]
    )
    assert len(payload) / len(items) < 40
    assert len(payload) * 8 < len(as_json)


def test_events_and_command_status():
    _, envelope = roundtrip("events", [{"timestamp": NOW_MS / 1000, "message": "Fault: motor"}])
    assert wire.unpack_item("events", envelope.items[0])["message"] == "Fault: motor"

    status = {"id": "c-1", "status": "COMPLETED", "result": {"ok": True}}
    _, envelope = roundtrip("commands/status", [status])
    assert wire.unpack_item("commands/status", envelope.items[0]) == status


def test_shift_times_moves_only_times():
    packed = [wire.pack_item("telemetry", reading(0))]
    shifted = wire.shift_times("telemetry", packed, -60_000)
    assert shifted[0]["t"] == packed[0]["t"] - 60_000
    assert shifted[0]["ms"] == packed[0]["ms"] - 60_000
    assert shifted[0]["b"] == packed[0]["b"]


def test_unknown_keys_from_a_newer_robot_are_ignored():
    assert wire.unpack_item("events", {"t": NOW_MS, "m": "hi", "zz": 1}) == {
        "timestamp": datetime.fromtimestamp(NOW_MS / 1000, timezone.utc),
        "message": "hi",
    }


@pytest.mark.parametrize(
    "values",
    [
        {"battery": float("nan")},
        {"battery": True},
        {"status": 3},
        {"nonsense": 1},
        {"timestamp": "yesterday"},
    ],
)
def test_pack_rejects_bad_values(values):
    with pytest.raises(WireError):
        wire.pack_item("telemetry", values)


@pytest.mark.parametrize(
    "body",
    [
        b"\xff\xff",
        cbor2.dumps([1, 2]),
        cbor2.dumps({"s": "a", "q": 0, "f": 1, "c": 0, "d": [{}]}),
        cbor2.dumps({"s": "", "q": 1, "f": 1, "c": 0, "d": [{}]}),
        cbor2.dumps({"s": "a", "q": 1, "f": 1, "c": 0, "d": []}),
        cbor2.dumps({"s": "a", "q": 1, "f": 1, "c": 0, "d": [1]}),
        cbor2.dumps({"s": "a", "q": 1, "f": 1, "c": 0, "d": [{"t": "x"}]}),
    ],
)
def test_decode_rejects_malformed_envelopes(body):
    with pytest.raises(WireError):
        wire.decode_envelope("telemetry", body)


def test_unknown_channel():
    with pytest.raises(WireError):
        wire.pack_item("presence", {})


def test_stored_confirmation_roundtrip():
    assert wire.decode_stored(wire.encode_stored("abc", 17)) == ("abc", 17)
    with pytest.raises(WireError):
        wire.decode_stored(cbor2.dumps({"s": "abc", "q": -1}))


def test_check_config_accepts_known_settings_in_range():
    accepted, rejected = wire.check_config(
        {"report_interval_s": 5, "warp": 9, "report_interval": "fast"}
    )
    assert accepted == {"report_interval_s": 5.0}
    assert set(rejected) == {"warp", "report_interval"}
    _, rejected = wire.check_config({"report_interval_s": 0})
    assert rejected == {"report_interval_s": "must be between 0.1 and 3600"}
    _, rejected = wire.check_config({"report_interval_s": True})
    assert rejected == {"report_interval_s": "must be a number"}


def test_config_messages_roundtrip():
    assert wire.decode_config(wire.encode_config(3, {"report_interval_s": 2.0})) == (
        3,
        {"report_interval_s": 2.0},
    )
    reported = wire.encode_reported(3, {"report_interval_s": 2.0}, {"warp": "unknown setting"})
    assert wire.decode_reported(reported) == (
        3,
        {"report_interval_s": 2.0},
        {"warp": "unknown setting"},
    )


@pytest.mark.parametrize(
    "payload", [b"nope", b"[]", b'{"version": -1}', b'{"version": 1, "values": []}']
)
def test_decode_config_rejects_malformed(payload):
    with pytest.raises(WireError):
        wire.decode_config(payload)
