"""Add robots.key_hash for per-robot device keys

Revision ID: a7c3e9d2b4f1
Revises: f1a9c3e7b2d4
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e9d2b4f1"
down_revision: str | None = "f1a9c3e7b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("robots", sa.Column("key_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("robots", "key_hash")
