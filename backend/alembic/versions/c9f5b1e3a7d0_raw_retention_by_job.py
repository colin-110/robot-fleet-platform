"""Raw retention by the app, and an incident archive

The fixed 30-day retention policy on telemetry is removed: the retention job
(app/storage/retention.py) drops raw keyframes instead, only for hours whose
line segments are done and verified, after copying the keyframes around
alarms to telemetry_incidents, which keeps them a year.

Revision ID: c9f5b1e3a7d0
Revises: b8e4a0d2f6c9
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9f5b1e3a7d0"
down_revision: str | None = "b8e4a0d2f6c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SELECT remove_retention_policy('telemetry', if_exists => true)")
    op.execute(
        "CREATE TABLE telemetry_incidents "
        "(LIKE telemetry INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)"
    )
    op.execute(
        "SELECT create_hypertable('telemetry_incidents', by_range('timestamp', INTERVAL '7 days'))"
    )
    op.execute(
        """
        ALTER TABLE telemetry_incidents SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'robot_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        )
        """
    )
    op.execute("SELECT add_compression_policy('telemetry_incidents', INTERVAL '7 days')")
    op.execute("SELECT add_retention_policy('telemetry_incidents', INTERVAL '365 days')")


def downgrade() -> None:
    op.drop_table("telemetry_incidents")
    op.execute(
        "SELECT add_retention_policy('telemetry', INTERVAL '30 days', if_not_exists => true)"
    )
