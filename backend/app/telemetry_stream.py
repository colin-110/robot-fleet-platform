"""Queue telemetry on the Redis stream, skipping readings already queued.

Used when OPT_REDIS_BUFFER is on: the stream feeds both the worker (which
writes to Postgres) and the WebSocket listeners. A retried upload would
otherwise be queued, stored and broadcast twice.

One Lua script per batch claims a short-lived marker per reading with
``SET NX`` and only queues the readings whose marker was new, so the check and
the XADD are atomic and cost one round trip.
"""

from __future__ import annotations

import orjson
import redis.asyncio as aioredis

from app.config import get_settings
from app.redis_pool import get_redis

settings = get_settings()

STREAM_KEY = "telemetry_stream"

# How long a reading's marker is kept. Retries arrive within seconds to
# minutes; an hour covers that with room to spare.
SEEN_TTL_SECONDS = 3600

# KEYS[1] = stream, KEYS[2..] = one marker key per reading
# ARGV[1] = stream maxlen, ARGV[2] = marker TTL, ARGV[3..] = payloads
_ENQUEUE = """
local accepted = {}
for i = 2, #KEYS do
  if redis.call('SET', KEYS[i], '1', 'NX', 'EX', ARGV[2]) then
    redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[1], '*', 'payload', ARGV[i + 1])
    accepted[#accepted + 1] = i - 1
  end
end
return accepted
"""


def seen_key(payload: dict) -> str:
    return f"telemetry:seen:{payload['robot_id']}:{payload['timestamp']}"


async def enqueue(payloads: list[dict], redis: aioredis.Redis | None = None) -> list[dict]:
    """Queue readings that are not already queued. Returns the ones queued.

    Raises if Redis is unavailable, so the caller can fail the request and the
    robot keeps the readings to retry, instead of losing them silently.
    """
    if not payloads:
        return []
    client = redis or get_redis()
    keys = [STREAM_KEY, *(seen_key(p) for p in payloads)]
    args = [
        settings.telemetry_stream_maxlen,
        SEEN_TTL_SECONDS,
        *(orjson.dumps(p).decode() for p in payloads),
    ]
    accepted = await client.eval(_ENQUEUE, len(keys), *keys, *args)
    return [payloads[int(i) - 1] for i in accepted]
