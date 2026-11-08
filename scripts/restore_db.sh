#!/bin/sh
# Restore a dump made by scripts/backup_db.sh into the stack's database.
#
#   scripts/restore_db.sh s3://my-bucket/fleetops/fleetops-20261108T031500Z.dump
#   scripts/restore_db.sh /var/backups/fleetops/fleetops-20261108T031500Z.dump
#
# Replaces everything in the database. Stop the API first so nothing writes
# meanwhile:  docker compose -f docker-compose.ec2.yml -p fleetops stop backend
# TimescaleDB needs its pre- and post-restore calls around pg_restore.
set -eu
SOURCE=${1:?usage: restore_db.sh <s3://... or path to a .dump>}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.ec2.yml}
PROJECT=${COMPOSE_PROJECT:-fleetops}

fetch() {
  case "$SOURCE" in
    s3://*) aws s3 cp "$SOURCE" - ;;
    *) cat "$SOURCE" ;;
  esac
}

in_db() {
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T db sh -c "$1"
}

in_db 'psql -U "$POSTGRES_USER" -d postgres -qc "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""'
in_db 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb" -c "SELECT timescaledb_pre_restore()"' >/dev/null
fetch | in_db 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner'
in_db 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qc "SELECT timescaledb_post_restore()"' >/dev/null
echo "Restored $SOURCE"
