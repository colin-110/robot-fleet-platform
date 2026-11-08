#!/bin/sh
# Give the broker a readable copy of the host's Let's Encrypt certificate.
#
# Let's Encrypt keys are readable by root only; Mosquitto runs as uid 1000.
# Install as a certbot deploy hook so every renewal reaches the broker:
#   sudo ln -s /opt/fleetops/scripts/mqtt_certs.sh /etc/letsencrypt/renewal-hooks/deploy/
# and run it once by hand after the first certificate is issued.
set -eu
DOMAIN=${MQTT_DOMAIN:?set MQTT_DOMAIN to the broker host name}
SRC=/etc/letsencrypt/live/$DOMAIN
DEST=${MQTT_CERT_DIR:-/opt/fleetops/certs}

install -d -m 0750 -o 1000 -g 1000 "$DEST"
install -m 0644 -o 1000 -g 1000 "$SRC/fullchain.pem" "$DEST/fullchain.pem"
install -m 0600 -o 1000 -g 1000 "$SRC/privkey.pem" "$DEST/privkey.pem"

cd /opt/fleetops
docker compose -f docker-compose.ec2.yml -p fleetops restart mqtt
echo "Broker certificate updated for $DOMAIN"
