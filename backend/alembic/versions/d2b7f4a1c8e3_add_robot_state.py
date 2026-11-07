"""Add robot_state: the latest reading per robot

Fleet status used to be recomputed from the last 15 minutes of telemetry on
every request. This table holds one row per robot, updated on write.

Revision ID: d2b7f4a1c8e3
Revises: c4d8e1f6a9b2
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2b7f4a1c8e3"
down_revision: str | None = "c4d8e1f6a9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

READING_COLUMNS = (
    "battery, temperature, speed, status, mission_id, mission_type, mission_progress, "
    "mission_start_time, battery_health, motor_health, sensor_health, network_health, x, y"
)


def upgrade() -> None:
    op.create_table(
        "robot_state",
        sa.Column("robot_id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("battery", sa.Float(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("mission_id", sa.String(length=64), nullable=True),
        sa.Column("mission_type", sa.String(length=32), nullable=True),
        sa.Column("mission_progress", sa.Float(), nullable=True),
        sa.Column("mission_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("battery_health", sa.Float(), nullable=True),
        sa.Column("motor_health", sa.Float(), nullable=True),
        sa.Column("sensor_health", sa.Float(), nullable=True),
        sa.Column("network_health", sa.Float(), nullable=True),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("battery_drain_per_min", sa.Float(), nullable=True),
    )
    # Seed from the newest stored reading of each robot.
    op.execute(
        f"""
        INSERT INTO robot_state (robot_id, timestamp, {READING_COLUMNS})
        SELECT DISTINCT ON (robot_id) robot_id, timestamp, {READING_COLUMNS}
        FROM telemetry
        ORDER BY robot_id, timestamp DESC
        """
    )


def downgrade() -> None:
    op.drop_table("robot_state")
