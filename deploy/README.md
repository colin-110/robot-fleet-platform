# Hosting on AWS Lightsail

One 1 GB Lightsail server running the same Docker Compose stack as locally,
with 20 simulated robots, behind HTTPS with a free Let's Encrypt certificate.
$7 a month, which includes the 40 GB disk, the public IPv4 address and 2 TB
of traffic. Deleting the stack removes everything and stops all charges.

| File | What it does |
| :--- | :--- |
| `fleetops.yaml` | CloudFormation: the Lightsail server, a static IP, and a firewall with only 80 and 443 open |
| `remote.sh` | Run on your machine: opens SSH to your address only, signs in with Lightsail's short-lived certificate, runs `deploy.sh`, closes SSH |
| `deploy.sh` | Run on the server: pulls `main`, makes `backend/.env` with random secrets on the first run, pulls the images CI built for that commit, gets the certificate, starts the stack, installs the renewal timer |
| `renew-cert.sh` | Run twice a day by that timer: renews the certificate within 30 days of expiry |
| `../docker-compose.prod.yml` | The production overlay: registry images, 20 robots, sized for 1 GB, dashboard on 80 and 443 with the Let's Encrypt certificate |

The server never builds images (the broker compiles Go, too much for 1 GB):
CI publishes them to the GitHub Container Registry
(`ghcr.io/colin-110/fleetops-*`) after its tests and scans pass on `main`,
tagged with the commit.

## First deployment

```bash
aws cloudformation deploy --region us-east-1 --stack-name fleetops-web \
  --template-file deploy/fleetops.yaml

sh deploy/remote.sh
```

Give the server two or three minutes after the stack is created to install
Docker. The dashboard is then at the stack's `Hostname` output, with the demo
account filled in on the login screen. The admin password is in
`/opt/fleetops/backend/.env` on the server.

## Updating

Push to `main`, wait for CI to publish the images (the Publish Images job),
then run `sh deploy/remote.sh` again: it pulls and restarts what changed,
keeping the data and the secrets.

## Removing it

```bash
aws cloudformation delete-stack --region us-east-1 --stack-name fleetops-web
```

The server's disk goes with it (the data is synthetic).
