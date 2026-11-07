"""Make (robot_id, timestamp) unique on telemetry

Retried uploads and replayed worker batches used to store the same reading
twice. Making the existing composite index unique lets inserts skip
duplicates with ON CONFLICT DO NOTHING, at no extra storage cost.

Existing duplicates are removed first (keeping the earliest row), otherwise
the unique index could not be built.

Revision ID: c4d8e1f6a9b2
Revises: a7c3e9d2b4f1
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d8e1f6a9b2"
down_revision: str | None = "a7c3e9d2b4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        "ix_telemetry_robot_timestamp",
        "telemetry",
        ["robot_id", "timestamp"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_robot_timestamp", table_name="telemetry")
    op.create_index(
        "ix_telemetry_robot_timestamp",
        "telemetry",
        ["robot_id", "timestamp"],
        unique=False,
    )
