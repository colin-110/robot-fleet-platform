#!/bin/sh
# Back up the fleet database: a compressed pg_dump, to S3 or a directory.
#
#   BACKUP_TARGET=s3://my-bucket/fleetops  scripts/backup_db.sh
#   BACKUP_TARGET=/var/backups/fleetops    scripts/backup_db.sh
#
# Nightly from the host's crontab:
#   15 3 * * * cd /opt/fleetops && BACKUP_TARGET=s3://my-bucket/fleetops scripts/backup_db.sh >> /var/log/fleetops-backup.log 2>&1
#
# Expire old dumps with an S3 lifecycle rule rather than here (see
# docs/operations.md). Restore with scripts/restore_db.sh.
#
# pg_dump warns about circular foreign keys in TimescaleDB's own catalog
# (hypertable, chunk, continuous_agg). That only matters for --data-only
# dumps; this is a full one, and the restore has been tested.
set -eu
TARGET=${BACKUP_TARGET:?set BACKUP_TARGET to s3://bucket/prefix or a directory}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.ec2.yml}
PROJECT=${COMPOSE_PROJECT:-fleetops}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="fleetops-$STAMP.dump"

dump() {
  # Custom format: compressed, and restorable table by table.
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'
}

case "$TARGET" in
  s3://*)
    dump | aws s3 cp - "$TARGET/$NAME"
    ;;
  *)
    mkdir -p "$TARGET"
    dump > "$TARGET/$NAME.partial"
    mv "$TARGET/$NAME.partial" "$TARGET/$NAME"
    ;;
esac
echo "$(date -u +%FT%TZ) backed up to $TARGET/$NAME"
