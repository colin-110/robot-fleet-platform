#!/bin/sh
# Deploy (or update) FleetOps on its Lightsail server from your machine:
#
#   sh deploy/remote.sh            # stack fleetops-web in us-east-1
#
# SSH stays closed on the server except while this runs: it opens port 22 to
# this machine's public address only, signs in with Lightsail's short-lived
# access certificate (no SSH key is kept anywhere), checks the server's host
# key against the one Lightsail reports, runs deploy/deploy.sh, and closes the
# port again however it ends. Needs the AWS CLI, signed in.
set -eu
STACK=${STACK:-fleetops-web}
REGION=${REGION:-us-east-1}

output() {
    aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
NAME=$(output InstanceName)
HOST=$(output Hostname)
MYIP=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
TMP=$(mktemp -d)

close() {
    aws lightsail close-instance-public-ports --region "$REGION" --instance-name "$NAME" \
        --port-info "fromPort=22,toPort=22,protocol=tcp,cidrs=$MYIP/32" >/dev/null || true
    rm -rf "$TMP"
}
trap close EXIT INT TERM

echo "Opening SSH on $NAME to $MYIP only"
aws lightsail open-instance-public-ports --region "$REGION" --instance-name "$NAME" \
    --port-info "fromPort=22,toPort=22,protocol=tcp,cidrs=$MYIP/32" >/dev/null

aws lightsail get-instance-access-details --region "$REGION" --instance-name "$NAME" \
    --protocol ssh --output json > "$TMP/access.json"
python3 - "$TMP" <<'PY'
import json, os, sys
tmp = sys.argv[1]
d = json.load(open(os.path.join(tmp, "access.json")))["accessDetails"]
def write(name, text, mode=0o600):
    path = os.path.join(tmp, name)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
write("key", d["privateKey"])
write("key-cert.pub", d["certKey"])
write("known_hosts", "".join(f"{d['ipAddress']} {k['algorithm']} {k['publicKey']}\n" for k in d.get("hostKeys", [])))
write("target", f"{d['username']}@{d['ipAddress']}")
PY

ssh -i "$TMP/key" -o CertificateFile="$TMP/key-cert.pub" \
    -o UserKnownHostsFile="$TMP/known_hosts" -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=20 -o ServerAliveInterval=30 \
    "$(cat "$TMP/target")" "sudo sh /opt/fleetops/deploy/deploy.sh $HOST"
