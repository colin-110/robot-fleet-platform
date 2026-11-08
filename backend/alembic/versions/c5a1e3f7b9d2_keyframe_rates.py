"""Add keyframe rates (vx, vy, battery_rate) to telemetry and robot_state

Nullable and without a default, which TimescaleDB allows on a hypertable with
compression enabled.

Revision ID: c5a1e3f7b9d2
Revises: b4f7c9e1a2d5
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a1e3f7b9d2"
down_revision: str | None = "b4f7c9e1a2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("vx", "vy", "battery_rate")


def upgrade() -> None:
    for table in ("telemetry", "robot_state"):
        for name in COLUMNS:
            op.add_column(table, sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for table in ("telemetry", "robot_state"):
        for name in COLUMNS:
            op.drop_column(table, name)
