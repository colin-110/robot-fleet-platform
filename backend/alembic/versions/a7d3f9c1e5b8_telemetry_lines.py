"""Add telemetry_lines: each field as straight-line segments

A hypertable compressed per robot and field, so its own column compression
applies on top of the segments. Segments older than a week are compressed;
none are dropped, since they are meant to outlive the raw keyframes.

Revision ID: a7d3f9c1e5b8
Revises: f8d4b6c0e2a5
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3f9c1e5b8"
down_revision: str | None = "f8d4b6c0e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telemetry_lines",
        sa.Column("robot_id", sa.Integer(), primary_key=True),
        sa.Column("field", sa.SmallInteger(), primary_key=True),
        sa.Column("t0", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("t1", sa.DateTime(timezone=True), nullable=False),
        sa.Column("v0", sa.Float(), nullable=False),
        sa.Column("slope", sa.Float(), nullable=False),
    )
    op.execute("SELECT create_hypertable('telemetry_lines', by_range('t0', INTERVAL '7 days'))")
    op.execute(
        """
        ALTER TABLE telemetry_lines SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'robot_id, field',
            timescaledb.compress_orderby = 't0'
        )
        """
    )
    op.execute("SELECT add_compression_policy('telemetry_lines', INTERVAL '7 days')")


def downgrade() -> None:
    op.drop_table("telemetry_lines")
