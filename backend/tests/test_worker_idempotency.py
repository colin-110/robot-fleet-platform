"""Worker redelivery must not duplicate telemetry rows.

Reproduces the failure mode: the worker commits a batch, then XACK fails (or the
process dies in between). The entries stay pending, the next loop re-reads them
with XREADGROUP id "0", and process_batch runs again on the same stream entries.
"""

import orjson
from sqlalchemy import func, select

from app.models import Telemetry
from app.worker import process_batch


def _msg(msg_id: str, robot_id: int, ts: str):
    payload = {
        "robot_id": robot_id,
        "battery": 90.0,
        "temperature": 25.0,
        "speed": 1.0,
        "timestamp": ts,
    }
    return (msg_id, {"payload": orjson.dumps(payload).decode("utf-8")})


async def _row_count(db) -> int:
    result = await db.execute(select(func.count()).select_from(Telemetry))
    return result.scalar_one()


async def test_redelivered_batch_does_not_duplicate_rows(db):
    batch = [_msg("1-0", 1, "2026-07-22T00:00:00Z"), _msg("1-1", 2, "2026-07-22T00:00:01Z")]

    await process_batch(db, batch)  # first delivery commits; the ack is "lost"
    await process_batch(db, batch)  # redelivery of the same entries

    assert await _row_count(db) == 2


async def test_redelivery_overlapping_new_entries_inserts_only_new(db):
    first = [_msg("1-0", 1, "2026-07-22T00:00:00Z"), _msg("1-1", 2, "2026-07-22T00:00:01Z")]
    overlap = [_msg("1-1", 2, "2026-07-22T00:00:01Z"), _msg("1-2", 3, "2026-07-22T00:00:02Z")]

    await process_batch(db, first)
    await process_batch(db, overlap)  # one replayed entry plus one genuinely new one

    assert await _row_count(db) == 3


async def test_distinct_readings_from_same_robot_are_all_kept(db):
    batch = [_msg(f"1-{i}", 1, f"2026-07-22T00:00:0{i}Z") for i in range(3)]

    await process_batch(db, batch)

    assert await _row_count(db) == 3
