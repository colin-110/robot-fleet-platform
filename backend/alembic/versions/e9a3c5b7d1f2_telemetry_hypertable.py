"""Convert telemetry to a TimescaleDB hypertable

- (robot_id, timestamp) becomes the primary key and the id column goes away:
  it was never the reading's identity, and a hypertable's unique keys must
  include the time column.
- Indexes nothing queries are dropped (status), or are covered by the new key
  (robot_id) or by the hypertable's own time index.
- Chunks are one day. Chunks older than a day are compressed, segmented by
  robot, and chunks older than RETENTION are dropped whole. This replaces the
  prune loops that deleted old rows in batches.

Requires the timescaledb extension (the timescale/timescaledb image).
To change retention later:
    SELECT remove_retention_policy('telemetry');
    SELECT add_retention_policy('telemetry', INTERVAL '<n> days');

Revision ID: e9a3c5b7d1f2
Revises: d2b7f4a1c8e3
Create Date: 2026-11-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9a3c5b7d1f2"
down_revision: str | None = "d2b7f4a1c8e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETENTION = "30 days"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.execute("ALTER TABLE telemetry DROP CONSTRAINT telemetry_pkey")
    op.execute("ALTER TABLE telemetry DROP COLUMN id")
    op.execute("DROP INDEX IF EXISTS ix_telemetry_robot_timestamp")
    op.execute("DROP INDEX IF EXISTS ix_telemetry_robot_id")
    op.execute("DROP INDEX IF EXISTS ix_telemetry_status")
    op.execute("DROP INDEX IF EXISTS ix_telemetry_timestamp")
    op.execute("ALTER TABLE telemetry ADD PRIMARY KEY (robot_id, timestamp)")

    op.execute(
        """
        SELECT create_hypertable(
            'telemetry', by_range('timestamp', INTERVAL '1 day'), migrate_data => true
        )
        """
    )
    op.execute(
        """
        ALTER TABLE telemetry SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'robot_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        )
        """
    )
    op.execute("SELECT add_compression_policy('telemetry', INTERVAL '1 day')")
    op.execute(f"SELECT add_retention_policy('telemetry', INTERVAL '{RETENTION}')")


def downgrade() -> None:
    # A hypertable cannot be turned back into a plain table in place, so copy
    # the rows into a fresh one.
    op.execute("SELECT remove_retention_policy('telemetry', if_exists => true)")
    op.execute("SELECT remove_compression_policy('telemetry', if_exists => true)")
    op.execute("CREATE TABLE telemetry_plain (LIKE telemetry INCLUDING DEFAULTS)")
    op.execute("INSERT INTO telemetry_plain SELECT * FROM telemetry ORDER BY timestamp")
    op.execute("DROP TABLE telemetry")
    op.execute("ALTER TABLE telemetry_plain RENAME TO telemetry")
    op.execute("ALTER TABLE telemetry ADD COLUMN id SERIAL PRIMARY KEY")
    # The indexes the table had before this revision.
    op.create_index(
        "ix_telemetry_robot_timestamp", "telemetry", ["robot_id", "timestamp"], unique=True
    )
    op.create_index("ix_telemetry_robot_id", "telemetry", ["robot_id"])
    op.create_index("ix_telemetry_status", "telemetry", ["status"])
    op.create_index("ix_telemetry_timestamp", "telemetry", ["timestamp"])
