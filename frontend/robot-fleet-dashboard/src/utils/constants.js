/**
 * Shared constants — single source of truth for status colors,
 * risk tones, and configuration values.
 *
 * Previously duplicated across RobotCard, AlertsPanel, and
 * PredictiveMaintenancePanel.
 */

// ── Status metadata ────────────────────────────────────────────────

export const STATUS_META = {
  DEAD: {
    border: "rgba(0, 0, 0, 0.82)",
    pillBg: "rgba(0, 0, 0, 0.92)",
    pillText: "#ffffff",
    dot: "dotBlack",
    alertAccent: "#0f172a",
  },
  OFFLINE: {
    border: "rgba(100, 116, 139, 0.52)",
    pillBg: "rgba(100, 116, 139, 0.16)",
    pillText: "#cbd5e1",
    dot: "dotGray",
    alertAccent: "#94a3b8",
  },
  OVERHEATING: {
    border: "rgba(239, 68, 68, 0.52)",
    pillBg: "rgba(239, 68, 68, 0.14)",
    pillText: "hsl(var(--bad))",
    dot: "dotBad",
    alertAccent: "hsl(var(--bad))",
  },
  "LOW POWER": {
    border: "rgba(245, 158, 11, 0.52)",
    pillBg: "rgba(245, 158, 11, 0.14)",
    pillText: "hsl(var(--warn))",
    dot: "dotWarn",
    alertAccent: "hsl(var(--warn))",
  },
  CHARGING: {
    border: "rgba(56, 189, 248, 0.52)",
    pillBg: "rgba(56, 189, 248, 0.14)",
    pillText: "hsl(var(--info))",
    dot: "dotInfo",
    alertAccent: "hsl(var(--info))",
  },
  ACTIVE: {
    border: "rgba(34, 197, 94, 0.42)",
    pillBg: "rgba(34, 197, 94, 0.12)",
    pillText: "hsl(var(--good))",
    dot: "dotGood",
    alertAccent: "hsl(var(--good))",
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
