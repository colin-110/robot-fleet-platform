"""Per-robot device keys.

Each robot gets its own API key, so a leaked key exposes one robot instead of
the whole fleet, and a key can only report as the robot it was issued to.

Format::

    rk_<robot_id>_<43 random url-safe characters>

Keys are random, not derived from anything, and only their SHA-256 hash is
stored (``robots.key_hash``). Revoking clears the hash and issuing a new key
replaces it, so an old key can never become valid again. A database leak
exposes hashes, not usable keys, and rotating the master key does not affect
device keys.

The hashes are cached in memory per process, so checking a key does no database
work. With several instances, a key issued by another instance is picked up by
one rate-limited database check, and ``refresh_loop`` propagates revocations.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
from hashlib import sha256

from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Robot

logger = logging.getLogger(__name__)
settings = get_settings()

PREFIX = "rk"

# Minimum seconds between database checks for the same robot. Only used when
# several instances share the database (websocket_backend=redis).
RECHECK_INTERVAL_SECONDS = 30.0

_hashes: dict[int, str] = {}
_last_recheck: dict[int, float] = {}


def generate(robot_id: int) -> str:
    """Create a new random key for ``robot_id``."""
    return f"{PREFIX}_{robot_id}_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return sha256(key.encode("utf-8")).hexdigest()


def looks_like_device_key(candidate: str | None) -> bool:
    return bool(candidate) and candidate.startswith(f"{PREFIX}_")


def robot_id_of(candidate: str | None) -> int | None:
    """The robot id a key claims to belong to, or None if it is malformed."""
    if not looks_like_device_key(candidate):
        return None
    parts = candidate.split("_", 2)
    if len(parts) != 3 or not parts[1].isdigit() or len(parts[2]) < 32:
        return None
    return int(parts[1])


async def verify(candidate: str | None) -> int | None:
    """Return the robot id the key belongs to, or None if it is not valid."""
    robot_id = robot_id_of(candidate)
    if robot_id is None:
        return None
    digest = hash_key(candidate)

    if _matches(_hashes.get(robot_id), digest):
        return robot_id

    # A single instance updates its own cache on every issue and revoke, so
    # the cache is authoritative. With several instances, another one may have
    # issued this key since our last refresh.
    if settings.websocket_backend == "redis" and _may_recheck(robot_id):
        current = await _fetch_hash(robot_id)
        set_hash(robot_id, current)
        if _matches(current, digest):
            return robot_id
    return None


def _matches(stored: str | None, digest: str) -> bool:
    return stored is not None and hmac.compare_digest(stored, digest)


def set_hash(robot_id: int, key_hash: str | None) -> None:
    if key_hash:
        _hashes[robot_id] = key_hash
    else:
        _hashes.pop(robot_id, None)


def has_key(robot_id: int) -> bool:
    """Whether the robot currently holds a key (it has not been revoked)."""
    return robot_id in _hashes


def reset() -> None:
    """Forget all cached hashes (used by tests)."""
    _hashes.clear()
    _last_recheck.clear()


def _may_recheck(robot_id: int) -> bool:
    now = time.monotonic()
    if now - _last_recheck.get(robot_id, float("-inf")) < RECHECK_INTERVAL_SECONDS:
        return False
    _last_recheck[robot_id] = now
    return True


async def _fetch_hash(robot_id: int) -> str | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Robot.key_hash).where(Robot.id == robot_id))
        return result.scalar_one_or_none()


async def load_hashes() -> int:
    """Replace the cache with the current key hashes from the database."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Robot.id, Robot.key_hash).where(Robot.key_hash.isnot(None))
        )
        rows = result.all()
    _hashes.clear()
    _hashes.update({row.id: row.key_hash for row in rows})
    return len(rows)


async def refresh_loop(interval_seconds: float) -> None:
    """Reload hashes periodically so revocations reach every instance."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await load_hashes()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Failed to refresh device key hashes", exc_info=True)
