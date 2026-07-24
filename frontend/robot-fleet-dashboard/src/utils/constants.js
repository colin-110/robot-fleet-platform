/**
 * Shared constants — single source of truth for status colors
 * and configuration values.
 */

// ── Status metadata ────────────────────────────────────────────────

export const STATUS_META = {
  DEAD: {
    border: "rgba(110, 118, 129, 0.35)",
    pillBg: "rgba(110, 118, 129, 0.14)",
    pillText: "#8b94a3",
    dot: "dotGray",
    alertAccent: "#6e7681",
  },
  OFFLINE: {
    border: "rgba(139, 148, 163, 0.35)",
    pillBg: "rgba(139, 148, 163, 0.14)",
    pillText: "#aab2c0",
    dot: "dotGray",
    alertAccent: "#8b94a3",
  },
  STOPPED: {
    border: "rgba(139, 148, 163, 0.35)",
    pillBg: "rgba(139, 148, 163, 0.14)",
    pillText: "#aab2c0",
    dot: "dotGray",
    alertAccent: "#8b94a3",
  },
  OVERHEATING: {
    border: "rgba(248, 81, 73, 0.4)",
    pillBg: "rgba(248, 81, 73, 0.13)",
    pillText: "#ff7b72",
    dot: "dotBad",
    alertAccent: "#f85149",
  },
  "LOW POWER": {
    border: "rgba(210, 153, 34, 0.4)",
    pillBg: "rgba(210, 153, 34, 0.13)",
    pillText: "#e3b341",
    dot: "dotWarn",
    alertAccent: "#d29922",
  },
  CHARGING: {
    border: "rgba(88, 166, 255, 0.4)",
    pillBg: "rgba(88, 166, 255, 0.13)",
    pillText: "#79b8ff",
    dot: "dotInfo",
    alertAccent: "#58a6ff",
  },
  ACTIVE: {
    border: "rgba(63, 185, 80, 0.4)",
    pillBg: "rgba(63, 185, 80, 0.13)",
    pillText: "#56d364",
    dot: "dotGood",
    alertAccent: "#3fb950",
  },
};

export function getStatusMeta(status) {
  return STATUS_META[status] || STATUS_META.ACTIVE;
}


// ── Pie chart colors ───────────────────────────────────────────────

export const PIE_COLORS = {
  ACTIVE: "#22c55e",
  "LOW POWER": "#f59e0b",
  OVERHEATING: "#ef4444",
  OFFLINE: "#94a3b8",
  CHARGING: "#38bdf8",
  DEAD: "#020617",
};

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

// ── Navigation items ───────────────────────────────────────────────

export const NAV_ITEMS = [
  "Dashboard",
  "Fleet Analytics",
  "Telemetry",
  "System Health",
];
