"""On-disk outbox: everything a robot sends, kept until the server has stored it.

A bounded queue in SQLite. Each item gets a sequence number when it is added;
numbers are never reused, even after the outbox is emptied or the process
restarts (``AUTOINCREMENT``), because the server's confirmations refer to
them. An item leaves the outbox only when the server confirms it stored
everything up to that number, or when the outbox is full and the oldest items
make room for new ones. Either way the lowest number still held, the floor,
tells the server what the robot no longer has.

The outbox file names a stream: a random id created with the file. A new file
(a replaced disk, a wiped robot) is a new stream, and the server starts
counting it from scratch instead of confusing its numbers with the old ones.

The database runs in WAL mode with ``synchronous=NORMAL`` by default, which
survives the robot process being killed at any point. Losing power can undo
the last few writes; ``synchronous="FULL"`` closes that at the cost of an
fsync per write.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid

import cbor2

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    body BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_commands (
    id TEXT PRIMARY KEY,
    at INTEGER NOT NULL
);
"""

MAX_SEEN_COMMANDS = 1000


class Outbox:
    def __init__(self, path: str, *, max_items: int = 100_000, synchronous: str = "NORMAL") -> None:
        if synchronous not in ("NORMAL", "FULL"):
            raise ValueError("synchronous must be NORMAL or FULL")
        self.max_items = max_items
        self.dropped = 0
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(f"PRAGMA synchronous={synchronous}")
        self._db.executescript(SCHEMA)
        stream = self.get_meta("stream")
        if stream is None:
            stream = uuid.uuid4().hex[:16]
            self.set_meta("stream", stream)
        self.stream: str = stream
        self._count = self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    # ── items ───────────────────────────────────────────────────────

    def put(self, channel: str, item: dict) -> int:
        """Add one packed item and return its sequence number."""
        return self.put_many(channel, [item])[-1]

    def put_many(self, channel: str, items: list[dict]) -> list[int]:
        rows = [(channel, cbor2.dumps(item)) for item in items]
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                seqs = [
                    self._db.execute(
                        "INSERT INTO items (channel, body) VALUES (?, ?)", row
                    ).lastrowid
                    for row in rows
                ]
                self._count += len(rows)
                overflow = self._count - self.max_items
                if overflow > 0:
                    # Oldest first: the newest readings matter most.
                    self._db.execute(
                        "DELETE FROM items WHERE seq IN "
                        "(SELECT seq FROM items ORDER BY seq LIMIT ?)",
                        (overflow,),
                    )
                    self._count -= overflow
                    self.dropped += overflow
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return seqs

    def read(self, after: int, limit: int) -> list[tuple[int, str, dict]]:
        """Up to ``limit`` items with a sequence number above ``after``, in order."""
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, channel, body FROM items WHERE seq > ? ORDER BY seq LIMIT ?",
                (after, limit),
            ).fetchall()
        return [(seq, channel, cbor2.loads(body)) for seq, channel, body in rows]

    def confirm(self, upto: int) -> int:
        """Forget every item up to ``upto``; the server has stored them."""
        with self._lock:
            removed = self._db.execute("DELETE FROM items WHERE seq <= ?", (upto,)).rowcount
            self._count -= removed
        return removed

    def floor(self) -> int:
        """The lowest sequence number still held (the next one if empty)."""
        with self._lock:
            lowest = self._db.execute("SELECT MIN(seq) FROM items").fetchone()[0]
            if lowest is not None:
                return lowest
            row = self._db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'items'"
            ).fetchone()
        return (row[0] if row else 0) + 1

    def __len__(self) -> int:
        return self._count

    # ── commands already run ───────────────────────────────────────

    def first_sighting(self, command_id: str, now_ms: int) -> bool:
        """Record a command id; False if it was recorded before.

        Kept on disk so a command delivered again after a restart is not run
        a second time.
        """
        with self._lock:
            inserted = self._db.execute(
                "INSERT OR IGNORE INTO seen_commands (id, at) VALUES (?, ?)",
                (command_id, now_ms),
            ).rowcount
            if inserted:
                self._db.execute(
                    "DELETE FROM seen_commands WHERE id NOT IN "
                    "(SELECT id FROM seen_commands ORDER BY at DESC LIMIT ?)",
                    (MAX_SEEN_COMMANDS,),
                )
        return bool(inserted)

    # ── small persistent values ────────────────────────────────────

    def get_meta(self, key: str):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return cbor2.loads(row[0]) if row else None

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, cbor2.dumps(value)),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()
