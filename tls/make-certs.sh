#!/bin/sh
# Certificates for the local stack, made on first start by the compose
# service `certs` (never into the repository):
#
#   ca.key                   a local certificate authority's key, valid 5
#                            years, in the `tls_ca` volume, which only this
#                            one-off service mounts
#   ca.crt                   its certificate, for clients to trust
#   server.crt / server.key  the broker's and the dashboard's certificate,
#                            signed by that CA, valid 1 year, for the names
#                            robots and browsers use: localhost, 127.0.0.1,
#                            and the compose service names mqtt and frontend
#
# Existing files are kept, so every client that trusts ca.crt keeps working;
# the server certificate alone is renewed when it has under 30 days left.
# Extra names (a LAN hostname or address robots connect to) can be added with
# TLS_EXTRA_SANS, for example "DNS:fleet.local,IP:192.168.1.8".
#
# To trust the CA on this machine, copy it out of the volume:
#   docker compose cp mqtt:/mosquitto/certs/ca.crt ./fleetops-ca.crt
set -eu

DIR=${CERT_DIR:-/certs}
CA=${CA_DIR:-/ca}
mkdir -p "$DIR" "$CA"
cd "$DIR"
SANS="DNS:localhost,IP:127.0.0.1,DNS:mqtt,DNS:frontend${TLS_EXTRA_SANS:+,$TLS_EXTRA_SANS}"

if [ ! -s "$CA/ca.key" ] || [ ! -s "$CA/ca.crt" ]; then
    echo "Creating a local certificate authority"
    openssl ecparam -name prime256v1 -genkey -noout -out "$CA/ca.key"
    openssl req -x509 -new -key "$CA/ca.key" -sha256 -days 1825 \
        -subj "/CN=FleetOps local CA" -out "$CA/ca.crt" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign"
    rm -f server.crt
fi
cp "$CA/ca.crt" ca.crt

# 30 days = 2592000 seconds.
if [ -s server.crt ] && openssl x509 -checkend 2592000 -noout -in server.crt >/dev/null; then
    echo "Server certificate still valid; keeping it"
else
    echo "Issuing the server certificate for $SANS"
    openssl ecparam -name prime256v1 -genkey -noout -out server.key
    openssl req -new -key server.key -subj "/CN=fleetops" -out server.csr
    printf 'subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' \
        "$SANS" > server.ext
    openssl x509 -req -in server.csr -CA "$CA/ca.crt" -CAkey "$CA/ca.key" -CAcreateserial \
        -CAserial "$CA/ca.srl" \
        -days 365 -sha256 -extfile server.ext -out server.crt
    rm -f server.csr server.ext
fi

# The broker (uid 999 in its image) reads the server key; nginx reads it as root.
chmod 600 "$CA/ca.key"
chmod 644 ca.crt server.crt
chown 999:999 server.key
chmod 600 server.key
openssl verify -CAfile ca.crt server.crt
