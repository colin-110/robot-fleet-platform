# TimescaleDB, kept on Postgres 16 so existing data volumes need no major
# upgrade. After moving to a newer TimescaleDB, update the extension in the
# database once (a minor upgrade, done in place):
#   docker compose exec db psql -X -U postgres -d robotfleet \
#     -c "ALTER EXTENSION timescaledb UPDATE;"
FROM timescale/timescaledb:2.31.0-pg16

# Every known vulnerability left in the image is in three Go tools built with
# an old Go release, so they go:
# - gosu, which the entrypoint uses once to drop from root to postgres, is
#   replaced by su-exec (the same job, a few lines of C, from Alpine).
# - timescaledb-tune only picks memory settings on first start; the compose
#   file sets them itself (shared_buffers, max_connections).
# - timescaledb-parallel-copy is a bulk-load tool this project never uses.
# Plus any Alpine patches released since the image was built.
ENV NO_TS_TUNE=true
RUN apk upgrade --no-cache \
    && apk add --no-cache su-exec \
    && ln -sf /sbin/su-exec /usr/local/bin/gosu \
    && rm -f /usr/local/bin/timescaledb-tune /usr/local/bin/timescaledb-parallel-copy
