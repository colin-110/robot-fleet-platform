"""Add the 1-minute and 1-hour rollups, with their retention tiers

Both are hypertables with compression. Raw keyframes stay 30 days (set in
e9a3c5b7d1f2), minutes a year, hours indefinitely.

Revision ID: f8d4b6c0e2a5
Revises: e7c3a5b9d1f4
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8d4b6c0e2a5"
down_revision: str | None = "e7c3a5b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = {
    # table: (chunk interval, compress after, retention or None)
    "telemetry_1m": ("7 days", "7 days", "365 days"),
    "telemetry_1h": ("90 days", "90 days", None),
}
FLOATS = (
    "seconds_covered",
    "distance_m",
    "battery_avg",
    "battery_min",
    "battery_max",
    "temperature_avg",
    "temperature_max",
    "speed_avg",
    "battery_health_avg",
    "motor_health_avg",
    "sensor_health_avg",
    "network_health_avg",
)
REQUIRED = {"seconds_covered", "distance_m"}


def upgrade() -> None:
    for table, (chunk, compress_after, retention) in TABLES.items():
        op.create_table(
            table,
            sa.Column("robot_id", sa.Integer(), primary_key=True),
            sa.Column("bucket", sa.DateTime(timezone=True), primary_key=True),
            sa.Column("keyframes", sa.Integer(), nullable=False),
            *(sa.Column(name, sa.Float(), nullable=name not in REQUIRED) for name in FLOATS),
        )
        op.execute(f"SELECT create_hypertable('{table}', by_range('bucket', INTERVAL '{chunk}'))")
        op.execute(
            f"""
            ALTER TABLE {table} SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'robot_id',
                timescaledb.compress_orderby = 'bucket DESC'
            )
            """
        )
        op.execute(f"SELECT add_compression_policy('{table}', INTERVAL '{compress_after}')")
        if retention:
            op.execute(f"SELECT add_retention_policy('{table}', INTERVAL '{retention}')")


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
