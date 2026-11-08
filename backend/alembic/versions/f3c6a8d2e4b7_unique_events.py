"""Make events unique by robot, time and message

Over MQTT an event can be delivered more than once. With a unique key the
insert skips a redelivered event instead of storing it again. Existing
duplicates are removed first (keeping the earliest row).

Revision ID: f3c6a8d2e4b7
Revises: e9a3c5b7d1f2
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3c6a8d2e4b7"
down_revision: str | None = "e9a3c5b7d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM events a
        USING events b
        WHERE a.robot_id = b.robot_id
          AND a.timestamp = b.timestamp
          AND a.message = b.message
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_events_robot_time_message", "events", ["robot_id", "timestamp", "message"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_events_robot_time_message", "events", type_="unique")
