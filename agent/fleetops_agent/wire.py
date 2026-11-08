"""Wire format between robots and the server, version 1.

This module is the single definition of the format. The server keeps an
identical copy (``backend/app/mqtt/wire.py``) so its image does not depend on
the agent package; a backend test fails if the two ever differ.

Every message a robot sends on ``telemetry``, ``events`` or
``commands/status`` is a CBOR map, the envelope::

    s   stream id: names the robot's outbox; a new outbox is a new stream
    q   sequence number of the first item
    f   floor: the lowest sequence number the robot still holds. It no longer
        has anything below it, either because the server confirmed it or
        because the outbox overflowed.
    c   when the message was sent, device clock, milliseconds since the epoch
    d   the items, with consecutive sequence numbers starting at ``q``

and is published with the MQTT 5 content type ``CONTENT_TYPE``.

Items are small maps with one- or two-letter keys (the tables below). Numbers
are sent as integers in fixed units (``scale``), so a battery of 87.43 % is
the integer 8743 and costs three bytes instead of nine. Times are milliseconds
relative to ``c``, which is a couple of bytes for a reading taken just before
sending. After the first item, an item carries only the keys whose value
changed from the item before it (``None`` for a value that went away), which
removes status, mission and component health from most readings. A reading
sent on its own is about 130 bytes with the envelope, against about 450 as
JSON; each further reading in the same message adds 20 to 35.

The server answers with ``fleet/<id>/stored``: a retained CBOR map ``{s, q}``
saying that everything up to sequence ``q`` of stream ``s`` is committed. The
robot deletes those items from its outbox. Until then it keeps them on disk
and sends them again if no confirmation comes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import cbor2

CONTENT_TYPE = "application/vnd.fleetops.v1+cbor"

NUM, STR, TIME, ANY = "num", "str", "time", "any"


@dataclass(frozen=True)
class Field:
    key: str
    name: str
    kind: str
    # NUM only: the value is sent as round(value * scale).
    scale: int = 1


TELEMETRY_FIELDS = (
    Field("t", "timestamp", TIME),
    Field("b", "battery", NUM, 100),
    Field("k", "temperature", NUM, 100),
    Field("v", "speed", NUM, 100),
    Field("st", "status", STR),
    Field("mi", "mission_id", STR),
    Field("mt", "mission_type", STR),
    Field("mp", "mission_progress", NUM, 100),
    Field("ms", "mission_start_time", TIME),
    Field("hb", "battery_health", NUM, 100),
    Field("hm", "motor_health", NUM, 100),
    Field("hs", "sensor_health", NUM, 100),
    Field("hn", "network_health", NUM, 100),
    Field("x", "x", NUM, 100),
    Field("y", "y", NUM, 100),
)

EVENT_FIELDS = (
    Field("t", "timestamp", TIME),
    Field("m", "message", STR),
)

COMMAND_STATUS_FIELDS = (
    Field("id", "id", STR),
    Field("st", "status", STR),
    Field("ec", "error_code", STR),
    Field("em", "error_message", STR),
    Field("r", "result", ANY),
)

CHANNELS = {
    "telemetry": TELEMETRY_FIELDS,
    "events": EVENT_FIELDS,
    "commands/status": COMMAND_STATUS_FIELDS,
}

MAX_STREAM_ID = 64


class WireError(ValueError):
    """A message or value that does not follow the format."""


def _fields(channel: str) -> tuple[Field, ...]:
    try:
        return CHANNELS[channel]
    except KeyError:
        raise WireError(f"no wire format for channel {channel!r}") from None


def _time_keys(channel: str) -> frozenset[str]:
    return frozenset(f.key for f in _fields(channel) if f.kind == TIME)


def epoch_ms(value) -> int:
    """Milliseconds since the epoch from a datetime (naive means UTC) or seconds."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return round(value.timestamp() * 1000)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return round(value * 1000)
    raise WireError(f"not a time: {value!r}")


# ── Items ─────────────────────────────────────────────────────────


def pack_item(channel: str, values: dict) -> dict:
    """Short-keyed item from full field names. ``None`` values are left out."""
    fields = _fields(channel)
    unknown = set(values) - {f.name for f in fields}
    if unknown:
        raise WireError(f"unknown {channel} fields: {sorted(unknown)}")
    packed = {}
    for f in fields:
        value = values.get(f.name)
        if value is None:
            continue
        if f.kind == NUM:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise WireError(f"{f.name} must be a finite number, got {value!r}")
            packed[f.key] = round(value * f.scale)
        elif f.kind == TIME:
            packed[f.key] = epoch_ms(value)
        elif f.kind == STR:
            if not isinstance(value, str):
                raise WireError(f"{f.name} must be a string, got {value!r}")
            packed[f.key] = value
        else:
            packed[f.key] = value
    return packed


def unpack_item(channel: str, packed: dict) -> dict:
    """Full field names from a short-keyed item. Times become UTC datetimes.

    Keys this version does not know are ignored, so a newer robot can add a
    field without breaking an older server.
    """
    values = {}
    for f in _fields(channel):
        value = packed.get(f.key)
        if value is None:
            continue
        if f.kind == NUM:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WireError(f"{f.name} must be a number, got {value!r}")
            values[f.name] = value / f.scale if f.scale != 1 else value
        elif f.kind == TIME:
            if isinstance(value, bool) or not isinstance(value, int):
                raise WireError(f"{f.name} must be integer milliseconds, got {value!r}")
            try:
                values[f.name] = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                raise WireError(f"{f.name} is out of range: {value!r}") from None
        elif f.kind == STR:
            if not isinstance(value, str):
                raise WireError(f"{f.name} must be a string, got {value!r}")
            values[f.name] = value
        else:
            values[f.name] = value
    return values


def shift_times(channel: str, items: list[dict], delta_ms: int) -> list[dict]:
    """Items with every time moved by ``delta_ms`` (used to correct a fast clock)."""
    keys = _time_keys(channel)
    return [
        {k: (v + delta_ms if k in keys and v is not None else v) for k, v in i.items()}
        for i in items
    ]


# ── Envelopes ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Envelope:
    stream: str
    seq: int
    floor: int
    sent_ms: int
    # Packed items with absolute times, in sequence order.
    items: list[dict]

    @property
    def last_seq(self) -> int:
        return self.seq + len(self.items) - 1


def encode_envelope(channel: str, envelope: Envelope) -> bytes:
    if not envelope.items:
        raise WireError("an envelope needs at least one item")
    time_keys = _time_keys(channel)
    data = []
    previous: dict = {}
    for item in envelope.items:
        relative = {k: (v - envelope.sent_ms if k in time_keys else v) for k, v in item.items()}
        delta = {k: v for k, v in relative.items() if k not in previous or previous[k] != v}
        for k in previous:
            if k not in relative:
                delta[k] = None
        data.append(delta)
        previous = relative
    return cbor2.dumps(
        {
            "s": envelope.stream,
            "q": envelope.seq,
            "f": envelope.floor,
            "c": envelope.sent_ms,
            "d": data,
        }
    )


def _int(body: dict, key: str, minimum: int | None = None) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WireError(f"envelope field {key!r} must be an integer")
    if minimum is not None and value < minimum:
        raise WireError(f"envelope field {key!r} must be at least {minimum}")
    return value


def _stream(body: dict) -> str:
    stream = body.get("s")
    if not isinstance(stream, str) or not 0 < len(stream) <= MAX_STREAM_ID:
        raise WireError("envelope needs a stream id")
    return stream


def decode_envelope(channel: str, payload: bytes) -> Envelope:
    """The envelope with items restored: deltas applied, absolute times."""
    time_keys = _time_keys(channel)
    try:
        body = cbor2.loads(payload)
    except Exception as exc:
        raise WireError(f"not CBOR: {exc}") from None
    if not isinstance(body, dict):
        raise WireError("an envelope must be a map")
    stream = _stream(body)
    seq = _int(body, "q", minimum=1)
    floor = _int(body, "f", minimum=1)
    sent_ms = _int(body, "c")
    data = body.get("d")
    if not isinstance(data, list) or not data:
        raise WireError("an envelope needs a non-empty item list")

    items = []
    current: dict = {}
    for delta in data:
        if not isinstance(delta, dict):
            raise WireError("each item must be a map")
        current = {**current, **delta}
        current = {k: v for k, v in current.items() if v is not None}
        absolute = {}
        for k, v in current.items():
            if k in time_keys:
                if isinstance(v, bool) or not isinstance(v, int):
                    raise WireError(f"time {k!r} must be integer milliseconds")
                v = v + sent_ms
            absolute[k] = v
        items.append(absolute)
    return Envelope(stream, seq, floor, sent_ms, items)


# ── Stored confirmations (server -> robot) ────────────────────────


def encode_stored(stream: str, seq: int) -> bytes:
    return cbor2.dumps({"s": stream, "q": seq})


def decode_stored(payload: bytes) -> tuple[str, int]:
    try:
        body = cbor2.loads(payload)
    except Exception as exc:
        raise WireError(f"not CBOR: {exc}") from None
    if not isinstance(body, dict):
        raise WireError("a stored confirmation must be a map")
    return _stream(body), _int(body, "q", minimum=0)
