"""Add the users table for JWT authentication

Only populated when AUTH_MODE=required. The hosted demo runs in open mode with
no accounts at all, so this table is empty there — see app/security.py for why
authentication is a mode rather than a hard requirement.

The table is created unconditionally rather than behind a flag: a schema that
differs by runtime configuration is a schema you cannot reason about, and an
empty table costs nothing.

Revision ID: e58c2b7f10a4
Revises: d47f1a9c2e10
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e58c2b7f10a4"
down_revision: str | None = "d47f1a9c2e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        # bcrypt output is 60 bytes; the headroom is for a future algorithm
        # prefix so a rehash does not need a migration.
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    # Unique: the username is the login identifier, so a duplicate would make
    # authentication ambiguous rather than merely untidy.
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
