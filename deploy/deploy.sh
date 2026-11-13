#!/bin/sh
# Deploy or update FleetOps on the server made by deploy/fleetops.yaml.
# Run as root on the server (the README shows how, through Session Manager):
#
#   sh /opt/fleetops/deploy/deploy.sh <hostname>
#
# <hostname> is the stack's Hostname output (a-b-c-d.sslip.io) or your own
# domain pointed at its IP. Safe to re-run: it pulls the latest main, keeps the
# secrets it made the first time, and builds and restarts what changed.
set -eu
HOST=${1:?usage: deploy.sh <hostname>}
cd /opt/fleetops
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

[ -f /var/lib/fleetops-ready ] || { echo "The server is still installing Docker; try again in a minute."; exit 1; }

echo "== Code"
git fetch --quiet origin main
git reset --quiet --hard origin/main
git log --oneline -1

echo "== Settings"
# Random secrets on the first run only (make_env.py leaves set ones alone).
python3 backend/make_env.py
set_env() {  # set_env NAME VALUE: replace or append one line of backend/.env
    if grep -q "^$1=" backend/.env; then
        sed -i "s|^$1=.*|$1=$2|" backend/.env
    else
        printf '%s=%s\n' "$1" "$2" >> backend/.env
    fi
}
set_env APP_ENV production
set_env CORS_ORIGINS "https://$HOST"
# Readable by root only: it holds every credential the stack uses.
chmod 600 backend/.env

echo "== Images"
$COMPOSE build --quiet

echo "== Certificate"
# The deploy hook copies each new certificate to where nginx reads it. It is
# expanded inside the certbot container, where certbot sets RENEWED_LINEAGE.
# shellcheck disable=SC2016
HOOK='cp -L "$RENEWED_LINEAGE/fullchain.pem" /web-certs/server.crt && cp -L "$RENEWED_LINEAGE/privkey.pem" /web-certs/server.key'
if $COMPOSE run --rm --entrypoint sh certbot -c "test -s /web-certs/server.crt"; then
    echo "Certificate already in place; the renewal timer keeps it current."
else
    # First time: nothing listens on port 80 yet, so certbot answers the
    # check itself. No email is registered with Let's Encrypt.
    $COMPOSE stop frontend 2>/dev/null || true
    $COMPOSE run --rm -p 80:80 certbot certonly --standalone --non-interactive \
        --agree-tos --register-unsafely-without-email -d "$HOST" --deploy-hook "$HOOK"
fi

echo "== Start"
$COMPOSE up -d --remove-orphans

echo "== Renewal timer"
# Twice a day (deploy/renew-cert.sh); certbot renews only within 30 days of expiry.
cat > /etc/systemd/system/fleetops-renew.service <<'UNIT'
[Unit]
Description=Renew the FleetOps Let's Encrypt certificate
[Service]
Type=oneshot
ExecStart=/bin/sh /opt/fleetops/deploy/renew-cert.sh
UNIT
cat > /etc/systemd/system/fleetops-renew.timer <<'UNIT'
[Unit]
Description=Renew the FleetOps certificate twice a day
[Timer]
OnCalendar=*-*-* 03,15:17:00
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now fleetops-renew.timer >/dev/null

echo "== Done: https://$HOST"
grep -E '^(DEMO_USERNAME|DEMO_PASSWORD)=' backend/.env | sed 's/^/   demo sign-in: /'
