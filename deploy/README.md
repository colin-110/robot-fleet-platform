# Hosting on AWS

One ARM EC2 server running the same Docker Compose stack as locally, behind
HTTPS with a free Let's Encrypt certificate. About $17.50 a month on a
t4g.small in us-east-1 (server, 20 GB disk, public IPv4), plus nothing for
traffic at demo scale. Deleting the stack removes everything and stops all
charges.

| File | What it does |
| :--- | :--- |
| `fleetops.yaml` | CloudFormation: the server (IMDSv2 only, encrypted disk), a fixed IP, a security group with only 80 and 443 open, and a role for Session Manager (no SSH) |
| `deploy.sh` | Run on the server: pulls `main`, makes `backend/.env` with random secrets on the first run, gets the certificate, builds and starts the stack, installs the renewal timer |
| `renew-cert.sh` | Run twice a day by that timer: renews the certificate within 30 days of expiry |
| `../docker-compose.prod.yml` | The production overlay: dashboard on 80 and 443, the Let's Encrypt certificate, `APP_ENV=production` |

## First deployment

```bash
aws cloudformation deploy --region us-east-1 --stack-name fleetops \
  --template-file deploy/fleetops.yaml --capabilities CAPABILITY_IAM

aws cloudformation describe-stacks --region us-east-1 --stack-name fleetops \
  --query 'Stacks[0].Outputs' --output table   # InstanceId, PublicIp, Hostname
```

Give the server two or three minutes to install Docker, then run the deploy
script on it through Session Manager (the first run builds every image, about
ten minutes):

```bash
aws ssm send-command --region us-east-1 --document-name AWS-RunShellScript \
  --instance-ids <InstanceId> --timeout-seconds 3600 \
  --parameters 'commands=["sh /opt/fleetops/deploy/deploy.sh <Hostname>"]'
```

The dashboard is then at `https://<Hostname>`, with the demo account filled in
on the login screen. The admin password is in `/opt/fleetops/backend/.env` on
the server (`aws ssm start-session --target <InstanceId>` for a shell).

## Updating

Push to `main`, then run the same `send-command`: the script pulls, rebuilds
what changed and restarts it, keeping the data and the secrets.

## Removing it

```bash
aws cloudformation delete-stack --region us-east-1 --stack-name fleetops
```

The server's disk goes with it (the data is synthetic).
