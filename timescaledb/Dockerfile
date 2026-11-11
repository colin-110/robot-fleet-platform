# TimescaleDB, kept on Postgres 16 so existing data volumes need no major
# upgrade. After moving to a newer TimescaleDB, update the extension in the
# database once (a minor upgrade, done in place):
#   docker compose exec db psql -X -U postgres -d robotfleet \
#     -c "ALTER EXTENSION timescaledb UPDATE;"
FROM timescale/timescaledb:2.31.0-pg16 AS patched

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

# One layer holding the finished filesystem. Replacing gosu above only hides
# the old binary: it would still ship in the base image's layer, and scanners
# (rightly) report what an image contains, not what it runs. Copying the
# result into a fresh image leaves it out, so the base image's settings are
# declared again here (docker image inspect timescale/timescaledb:2.31.0-pg16).
FROM scratch
COPY --from=patched / /
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=en_US.utf8 \
    PG_MAJOR=16 \
    PG_VERSION=16.15 \
    PGDATA=/var/lib/postgresql/data \
    NO_TS_TUNE=true
VOLUME /var/lib/postgresql/data
EXPOSE 5432
STOPSIGNAL SIGINT
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]
