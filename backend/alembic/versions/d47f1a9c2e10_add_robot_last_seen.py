"""Add robots.last_seen so the roster can report silent units

Fleet status previously derived the robot list purely from telemetry inside a
15-minute window, so a robot that stopped reporting fell out of the query and
vanished from the dashboard. The roster is now the source of truth for which
robots exist, and last_seen carries how long each has been silent — including
units well outside the telemetry window.

Revision ID: d47f1a9c2e10
Revises: b335be1d5039
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d47f1a9c2e10"
down_revision: str | None = "b335be1d5039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "robots",
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_robots_last_seen", "robots", ["last_seen"])

    # Backfill from existing telemetry so robots that already reported are not
    # shown as never-seen after the upgrade.
    op.execute(
        """
        UPDATE robots r
        SET last_seen = t.max_ts
        FROM (
            SELECT robot_id, MAX(timestamp) AS max_ts
            FROM telemetry
            GROUP BY robot_id
        ) t
        WHERE t.robot_id = r.id
        """
    )

    # Register any robot that has telemetry but no roster row. Without this the
    # roster starts empty on an existing database and the dashboard would show
    # nothing until each robot next reported.
    op.execute(
        """
        INSERT INTO robots (id, is_active, registered_at, last_seen)
        SELECT t.robot_id, TRUE, MIN(t.timestamp), MAX(t.timestamp)
        FROM telemetry t
        WHERE NOT EXISTS (SELECT 1 FROM robots r WHERE r.id = t.robot_id)
        GROUP BY t.robot_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_robots_last_seen", table_name="robots")
    op.drop_column("robots", "last_seen")
