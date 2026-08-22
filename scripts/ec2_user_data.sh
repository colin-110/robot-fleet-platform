#!/bin/bash
# EC2 user-data — runs once, as root, on first boot of the app host.
#
# Brings a bare Ubuntu 22.04 instance to the state the CD pipeline assumes:
# Docker with the Compose plugin, and the repository checked out at
# /opt/fleetops. The `deploy-aws` job in .github/workflows/ci.yml does a
# `cd /opt/fleetops && git reset --hard && docker compose pull && up -d`, and
# every one of those preconditions used to be a manual step nobody recorded.
#
# Output lands in /var/log/cloud-init-output.log on the instance.
set -euxo pipefail

REPO_URL="https://github.com/colin-110/robot-fleet-platform.git"
APP_DIR="/opt/fleetops"

# Unattended-upgrades holds the apt lock on a fresh boot; wait rather than die.
export DEBIAN_FRONTEND=noninteractive
APT="apt-get -o DPkg::Lock::Timeout=300 -y"

$APT update
$APT install git curl ca-certificates

# 1 GB of swap. The box has 1 GB of RAM and runs backend, worker, simulator,
# nginx and Prometheus; the OOM killer taking out the backend mid-migration is
# a worse outage than the paging. Kept to 1 GB because the 7.6 GB disk is also
# holding container images, Prometheus data and logs.
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Docker's own installer, not Ubuntu's docker.io: jammy has no
# docker-compose-plugin package, and the deploy is written against
# `docker compose` (v2), not the retired `docker-compose` script.
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
usermod -aG docker ubuntu || true

# The pipeline's `git fetch && git reset --hard` needs a checkout to act on.
if [ ! -d "$APP_DIR/.git" ]; then
    git clone "$REPO_URL" "$APP_DIR"
fi

cat <<'NOTE'
================================================================
Host bootstrapped. Two steps remain and neither can be automated
from here, because both involve secrets that are not in the repo:

  1. Write /opt/fleetops/backend/.env (see backend/.env.example).
     It carries the RDS and ElastiCache URLs, TELEMETRY_API_KEY,
     APP_ENV=production, CORS_ORIGINS and TRUSTED_PROXY_COUNT=2.
     Compose cannot interpolate from env_file, so the stack starts
     without these rather than failing on them.

  2. If the GHCR packages are private, authenticate the host once:
       docker login ghcr.io -u <user> -p <read:packages PAT>
     Otherwise `docker compose pull` fails with 'denied'. Making the
     three packages public is the other, simpler option.

Then the deploy-aws job can roll this host over on every push.
================================================================
NOTE
