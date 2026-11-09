# TimescaleDB, kept on Postgres 16 so existing data volumes need no major
# upgrade. After moving to a newer TimescaleDB, update the extension in the
# database once (a minor upgrade, done in place):
#   docker compose exec db psql -X -U postgres -d robotfleet \
#     -c "ALTER EXTENSION timescaledb UPDATE;"
FROM timescale/timescaledb:2.31.0-pg16

# The image starts as root and its entrypoint drops to postgres itself.
# Patches released since the image was built, and without the bulk-load tool
# this project never uses (it carries most of the image's known
# vulnerabilities, in its bundled Go libraries).
RUN apk upgrade --no-cache && rm -f /usr/local/bin/timescaledb-parallel-copy
