"""Add robot_configs: desired and reported robot settings

Revision ID: b4f7c9e1a2d5
Revises: a8e2d4c6f1b3
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4f7c9e1a2d5"
down_revision: str | None = "a8e2d4c6f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "robot_configs",
        sa.Column("robot_id", sa.Integer(), primary_key=True),
        sa.Column("desired", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported", sa.JSON(), nullable=True),
        sa.Column("reported_rejected", sa.JSON(), nullable=True),
        sa.Column("reported_version", sa.Integer(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("robot_configs")
