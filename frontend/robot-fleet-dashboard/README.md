# FleetOps dashboard

The operator console: a React 19 + Vite single-page app that shows the live fleet and dispatches commands to robots.

## What it does

- **Fleet roster and map.** Robot cards and a Leaflet map (`react-leaflet`), updated from the WebSocket feed and reconciled against `GET /api/v1/robots/status` every 5 seconds.
- **Analytics.** Recharts panels for the fleet health trend and mission completions (`GET /api/v1/analytics/fleet`).
- **Event log.** Live events from the socket, backfilled from `GET /api/v1/events` and de-duplicated where the two overlap.
- **Commands.** Return to base, emergency stop, and resume. Each click sends one idempotency key, and the button stays disabled while the request is in flight.

State is plain React hooks (`src/hooks/useFleetData.js`). WebSocket frames are buffered and applied in 100 ms batches, so a burst of telemetry doesn't trigger a re-render for every frame.

## Authentication

The bundle contains no API key. At runtime the dashboard asks for a short-lived console ticket (`POST /api/v1/auth/ticket`) and uses it for the socket and for commands. When the backend runs with `AUTH_MODE=required`, the user also signs in, and the bearer token is sent alongside the ticket.

## Local development

Node 22 (the same version as CI and the Dockerfile).

```bash
cd frontend/robot-fleet-dashboard
npm ci
npm run dev        # http://localhost:5173
npm run lint
npm run test:coverage
npm run build
```

`VITE_API_BASE_URL` sets the backend origin; see `.env.example`. Leave it empty to use same-origin URLs, which is what the local Docker stack's nginx proxy expects.

## Deployment

Vercel builds this directory from source. The `Dockerfile` and `nginx.conf` are used only by the local `docker compose` stack.
