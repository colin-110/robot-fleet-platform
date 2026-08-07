/**
 * Shared constants — single source of truth for status colors
 * and configuration values.
 *
 * Colors are CSS custom properties rather than literals so the light and dark
 * palettes stay in one place (src/styles/global.css). Anything that ends up in
 * an inline `style` or an SVG presentation attribute can hold a `var(...)`
 * string, which is why these read as tokens and not hexes.
 */

// ── Status metadata ────────────────────────────────────────────────

export const STATUS_META = {
  DEAD: {
    border: "var(--st-dead-line)",
    pillBg: "var(--st-dead-bg)",
    pillText: "var(--st-dead-fg)",
    dot: "dotGray",
    alertAccent: "var(--c-dead)",
  },
  OFFLINE: {
    border: "var(--st-idle-line)",
    pillBg: "var(--st-idle-bg)",
    pillText: "var(--st-idle-fg)",
    dot: "dotGray",
    alertAccent: "var(--c-idle)",
  },
  STOPPED: {
    border: "var(--st-idle-line)",
    pillBg: "var(--st-idle-bg)",
    pillText: "var(--st-idle-fg)",
    dot: "dotGray",
    alertAccent: "var(--c-idle)",
  },
  OVERHEATING: {
    border: "var(--st-hot-line)",
    pillBg: "var(--st-hot-bg)",
    pillText: "var(--st-hot-fg)",
    dot: "dotBad",
    alertAccent: "var(--c-bad)",
  },
  "LOW POWER": {
    border: "var(--st-low-line)",
    pillBg: "var(--st-low-bg)",
    pillText: "var(--st-low-fg)",
    dot: "dotWarn",
    alertAccent: "var(--c-warn)",
  },
  CHARGING: {
    border: "var(--st-charging-line)",
    pillBg: "var(--st-charging-bg)",
    pillText: "var(--st-charging-fg)",
    dot: "dotInfo",
    alertAccent: "var(--c-info)",
  },
  ACTIVE: {
    border: "var(--st-active-line)",
    pillBg: "var(--st-active-bg)",
    pillText: "var(--st-active-fg)",
    dot: "dotGood",
    alertAccent: "var(--c-good)",
  },
};

export function getStatusMeta(status) {
  return STATUS_META[status] || STATUS_META.ACTIVE;
}

// ── Fleet status breakdown ─────────────────────────────────────────

/**
 * Every status the fleet can report, with the label and colour used wherever
 * it is counted.
 *
 * This list has to stay exhaustive. The KPI row used to hardcode six of these
 * and omit STOPPED, so an emergency-stopped robot counted toward the total and
 * toward nothing else — the breakdown quietly failed to add up to the fleet
 * size. `FLEET_STATUSES` is now the one list, and anything not in it is rolled
 * into an explicit "Other" bucket rather than disappearing.
 */
export const FLEET_STATUSES = [
  { key: "ACTIVE", label: "Active", hint: "on mission", color: "var(--c-good)" },
  { key: "CHARGING", label: "Charging", hint: "at base", color: "var(--c-info)" },
  { key: "LOW POWER", label: "Low Power", hint: "below 30%", color: "var(--c-warn)" },
  { key: "OVERHEATING", label: "Overheating", hint: "thermal", color: "var(--c-bad)" },
  { key: "STOPPED", label: "Stopped", hint: "halted by operator", color: "var(--c-idle)" },
  { key: "OFFLINE", label: "Offline", hint: "no signal", color: "var(--c-idle)" },
  { key: "DEAD", label: "Dead", hint: "needs service", color: "var(--c-dead)" },
];

/** Colour for a status in charts and legends, with a neutral fallback. */
export function statusColor(status) {
  const entry = FLEET_STATUSES.find((s) => s.key === status);
  return entry ? entry.color : "var(--c-idle)";
}

/**
 * Count robots by status.
 *
 * Returns the per-status counts plus `other`, which absorbs any status the
 * backend or simulator introduces that this build does not know about. Callers
 * can therefore always show a breakdown that sums to `robots.length`.
 */
export function statusBreakdown(robots) {
  const counts = Object.fromEntries(FLEET_STATUSES.map((s) => [s.key, 0]));
  let other = 0;

  for (const robot of robots) {
    if (robot.status in counts) counts[robot.status] += 1;
    else other += 1;
  }

  return { counts, other, total: robots.length };
}

// ── Utility functions ──────────────────────────────────────────────

export function healthTone(value) {
  if (value <= 60) return "hsl(var(--bad))";
  if (value <= 80) return "hsl(var(--warn))";
  return "hsl(var(--good))";
}

export function formatRuntime(minutes) {
  if (minutes === null || minutes === undefined) return "n/a";
  if (minutes < 60) return `${minutes.toFixed(1)} min`;
  return `${(minutes / 60).toFixed(1)} h`;
}

export function formatLastSeen(lastSeen) {
  if (!lastSeen) return "n/a";
  const seenAt = new Date(lastSeen);
  if (Number.isNaN(seenAt.getTime())) return "n/a";
  const seconds = Math.max(0, Math.floor((Date.now() - seenAt.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

// ── Alert statuses ─────────────────────────────────────────────────

export const ALERT_STATUSES = ["LOW POWER", "OVERHEATING", "OFFLINE", "CHARGING", "DEAD"];

// ── Event log sizing ───────────────────────────────────────────────

/**
 * How many events the client keeps, and how many the dashboard's side panel
 * shows before deferring to the Event Log tab.
 *
 * The panel used to render the entire buffer inside a track that grew with its
 * content, so a busy fleet stretched the dashboard column past the map beside
 * it. The panel is now capped and scrolls internally; the full history lives on
 * its own page.
 */
export const EVENT_BUFFER_LIMIT = 200;
export const EVENT_PANEL_LIMIT = 12;

// ── Navigation items ───────────────────────────────────────────────

export const NAV_ITEMS = [
  "Dashboard",
  "Fleet Analytics",
  "Telemetry",
  "Event Log",
  "System Health",
];
