"""Make (robot_id, timestamp) unique on telemetry

Delivery from the Redis stream is at-least-once: if the worker commits a batch
and then fails to XACK, the entries are redelivered and were inserted a second
time. Client retries had the same effect on the direct path. A reading is
identified by its robot and measurement instant, so that pair is now unique and
every writer uses ON CONFLICT DO NOTHING.

The unique index replaces ix_telemetry_robot_timestamp rather than sitting next
to it: it serves the same lookups, so there is no extra index to store.

Steps, in order:
  1. Delete existing duplicates, keeping the lowest id of each pair. Without
     this the unique index cannot be built. The deleted rows are exact repeats
     of a (robot, instant), so no distinct reading is lost, but the deletion is
     not reversible.
  2. Build the unique index CONCURRENTLY so ingest is not blocked. That cannot
     run inside a transaction, hence the autocommit block. A failed concurrent
     build leaves an INVALID index behind, so any leftover is dropped first and
     the migration can simply be re-run.
  3. Drop the old non-unique index.

Deploy order: every writer must already tolerate conflicts (worker, direct
path, buffer flush) before this runs, or a duplicate insert becomes an error.

Revision ID: a7c4e2d91f53
Revises: f1a9c3e7b2d4
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7c4e2d91f53"
down_revision: str | None = "f1a9c3e7b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_INDEX = "uq_telemetry_robot_id_timestamp"
OLD_INDEX = "ix_telemetry_robot_timestamp"


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM telemetry AS t
        USING telemetry AS d
        WHERE t.robot_id = d.robot_id
          AND t.timestamp = d.timestamp
          AND t.id > d.id
        """
    )

    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {UNIQUE_INDEX}")
        op.create_index(
            UNIQUE_INDEX,
            "telemetry",
            ["robot_id", "timestamp"],
            unique=True,
            postgresql_concurrently=True,
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {OLD_INDEX}")


def downgrade() -> None:
    # Removed duplicates are not restored.
    with op.get_context().autocommit_block():
        op.create_index(
            OLD_INDEX,
            "telemetry",
            ["robot_id", "timestamp"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {UNIQUE_INDEX}")
