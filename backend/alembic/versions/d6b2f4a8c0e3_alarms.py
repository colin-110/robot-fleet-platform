"""Add alarms

Revision ID: d6b2f4a8c0e3
Revises: c5a1e3f7b9d2
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6b2f4a8c0e3"
down_revision: str | None = "c5a1e3f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alarms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("robot_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("message", sa.String(255), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(64), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_alarms_open",
        "alarms",
        ["robot_id", "kind"],
        unique=True,
        postgresql_where=sa.text("state <> 'CLEARED'"),
    )
    op.create_index("ix_alarms_raised", "alarms", [sa.text("raised_at DESC")])


def downgrade() -> None:
    op.drop_table("alarms")
