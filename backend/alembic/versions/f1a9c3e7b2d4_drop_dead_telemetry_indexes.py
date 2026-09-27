"""Drop redundant/unused telemetry indexes

A live check against the production database found ix_telemetry_id was a
pure duplicate of the primary key's own index (both on `id`, ~13 MB doing
identical work), and ix_telemetry_robot_id_desc had zero scans since
creation (~26 MB, fully superseded by ix_telemetry_robot_timestamp). With
continuous ingestion (no viewer-gating) these two alone would have pushed
index size to roughly 1 GB within a day at 24-robot volume, which does not
fit a free-tier database's cap. Together they free ~39 MB immediately and
meaningfully lower future growth per row.

Revision ID: f1a9c3e7b2d4
Revises: e58c2b7f10a4
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a9c3e7b2d4"
down_revision: str | None = "e58c2b7f10a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_telemetry_id", table_name="telemetry")
    op.drop_index("ix_telemetry_robot_id_desc", table_name="telemetry")


def downgrade() -> None:
    op.create_index("ix_telemetry_id", "telemetry", ["id"])
    op.create_index(
        "ix_telemetry_robot_id_desc", "telemetry", ["robot_id", "timestamp"]
    )
