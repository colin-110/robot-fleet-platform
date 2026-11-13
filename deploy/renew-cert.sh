#!/bin/sh
# Renew the dashboard's Let's Encrypt certificate if it is within 30 days of
# expiry, then have nginx load it. Run twice a day by the fleetops-renew timer
# that deploy/deploy.sh installs. certbot answers Let's Encrypt's check through
# nginx, which serves the webroot on port 80.
set -eu
cd /opt/fleetops
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
# Expanded inside the certbot container, where certbot sets RENEWED_LINEAGE.
# shellcheck disable=SC2016
HOOK='cp -L "$RENEWED_LINEAGE/fullchain.pem" /web-certs/server.crt && cp -L "$RENEWED_LINEAGE/privkey.pem" /web-certs/server.key'
$COMPOSE run --rm certbot renew --webroot -w /var/www/certbot --deploy-hook "$HOOK"
$COMPOSE exec -T frontend nginx -s reload
