"""Add stored_watermarks: how far each robot's outbox is committed

Revision ID: a8e2d4c6f1b3
Revises: f3c6a8d2e4b7
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8e2d4c6f1b3"
down_revision: str | None = "f3c6a8d2e4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_watermarks",
        sa.Column("robot_id", sa.Integer(), primary_key=True),
        sa.Column("stream", sa.String(64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stored_watermarks")
