"""Make telemetry and event writes idempotent

Telemetry: replaces the plain (robot_id, timestamp) index with a unique one,
so every write path can insert with ON CONFLICT DO NOTHING. Before this, any
retry duplicated rows: a simulator POST resent after a timeout, the worker
redelivering a batch it had committed but not yet XACKed, or XAUTOCLAIM handing
a dead consumer's batch to a live one. Existing duplicates are deleted first
(lowest id kept), or the unique index cannot be built.

Events: adds an optional per-robot idempotency key, mirroring robot_commands.

Revision ID: 4b32f2c42653
Revises: f1a9c3e7b2d4
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4b32f2c42653"
down_revision: str | None = "f1a9c3e7b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Uses the existing (robot_id, timestamp) index for the self-join, so it
    # runs before that index is dropped.
    op.execute(
        """
        DELETE FROM telemetry a
        USING telemetry b
        WHERE a.robot_id = b.robot_id
          AND a.timestamp = b.timestamp
          AND a.id > b.id
        """
    )
    op.drop_index("ix_telemetry_robot_timestamp", table_name="telemetry")
    op.create_index(
        "uq_telemetry_robot_timestamp", "telemetry", ["robot_id", "timestamp"], unique=True
    )

    op.add_column("events", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_unique_constraint(
        "uix_events_robot_id_idempotency_key", "events", ["robot_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uix_events_robot_id_idempotency_key", "events", type_="unique")
    op.drop_column("events", "idempotency_key")

    op.drop_index("uq_telemetry_robot_timestamp", table_name="telemetry")
    op.create_index("ix_telemetry_robot_timestamp", "telemetry", ["robot_id", "timestamp"])
