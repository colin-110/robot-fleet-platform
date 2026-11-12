"""Add telemetry_states: status and mission fields as their changes

The non-numeric companion of telemetry_lines, with the same hypertable and
compression settings.

Revision ID: b8e4a0d2f6c9
Revises: a7d3f9c1e5b8
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4a0d2f6c9"
down_revision: str | None = "a7d3f9c1e5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telemetry_states",
        sa.Column("robot_id", sa.Integer(), primary_key=True),
        sa.Column("field", sa.SmallInteger(), primary_key=True),
        sa.Column("t", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("value", sa.String(64), nullable=True),
    )
    op.execute("SELECT create_hypertable('telemetry_states', by_range('t', INTERVAL '7 days'))")
    op.execute(
        """
        ALTER TABLE telemetry_states SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'robot_id, field',
            timescaledb.compress_orderby = 't'
        )
        """
    )
    op.execute("SELECT add_compression_policy('telemetry_states', INTERVAL '7 days')")


def downgrade() -> None:
    op.drop_table("telemetry_states")
