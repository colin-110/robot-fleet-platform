# Security

## Reporting a vulnerability

Please report it privately, through this repository's **Security** tab ("Report a vulnerability"), not in a public issue. Include what you found, how to reproduce it, and what an attacker could do with it. You will get a reply within a week, and a fix or a decision within 30 days. Only the `main` branch is supported.

FleetOps is a portfolio project that runs on synthetic data, but it is built as if the data were real, and reports are welcome.

## What is protected, and from whom

| Asset | Threat | Controls |
| :--- | :--- | :--- |
| Robot commands | Anyone sending a robot an order | Sign-in with roles (only `operator` and `admin` dispatch), compare-and-set state transitions, rate limit on dispatch |
| Fleet data (positions, history, alarms) | Anyone reading it | Sign-in required for every read when `AUTH_MODE=required` (the default in `.env.example`); TLS to the dashboard |
| Robot identity | A robot, or an attacker, acting as another robot | A key per robot, stored only as a hash; the broker allows each robot only its own topics and ties its client id to its login; the master key is refused on robot endpoints |
| Operator accounts | Password guessing | bcrypt, 10 sign-in attempts a minute per client, one message for unknown users and wrong passwords |
| Credentials | Leaked or default secrets | No default secrets: the API refuses to start with the example's placeholders; `backend/make_env.py` makes random ones; the browser never holds the master key (short-lived tickets); secret scanning over the whole git history in CI |
| Traffic | Eavesdropping, tampering | TLS for robots (MQTT on 8883) and browsers (HTTPS on 443); every published port listens on localhost unless `BIND_ADDRESS` says otherwise |
| The dashboard | Script injection, clickjacking | Content-Security-Policy with no inline scripts and no eval, `frame-ancestors 'none'`, nosniff, referrer and permissions policies |
| The software itself | Vulnerable or tampered dependencies | Hash-checked Python lockfiles; current, patched images; non-root containers; CI fails on known vulnerabilities (pip-audit, npm audit, Trivy, govulncheck) and pins its actions to commits |

Details: [docs/operations.md](docs/operations.md#security-posture).

## Known limitations

- **The demo account is public.** `.env.example` sets one (`demo` / `fleetops-demo`) and the login screen shows it, so anyone who can reach the dashboard can watch the fleet and send commands. It can never be an admin. Remove `DEMO_USERNAME` and `DEMO_PASSWORD` for a real fleet.
- **No backups and no replica.** A lost database disk loses all history. Fine for synthetic data; see [Backups](docs/operations.md#backups) for what real data needs.
- **A local certificate authority.** Browsers warn until it is trusted. A public deployment needs certificates from a trusted authority.
- **Inside the Docker network nothing is encrypted**: the API's connections to the broker and the database are plain.
- **One master key** authenticates the API to the broker and signs console tickets. Rotating it revokes every ticket and token at once.
- **The broker has no per-client rate limit.** A robot with a valid key could flood it.
- **The broker's auth plugin** (go-auth 3.0.0) includes a JWT library with one known vulnerability and no fixed release. Only the plugin's JWT backend reaches it, and this broker uses its HTTP backend alone (`mosquitto/go-updates.txt`).
- **Robot outboxes** can lose their last few readings if the robot loses power (`synchronous=NORMAL`), and drop their oldest items when full (100,000 by default).
