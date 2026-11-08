// Alarm list helpers. The list holds open alarms (ACTIVE or ACKNOWLEDGED),
// newest first; a CLEARED one leaves it.

const SEVERITY_RANK = { critical: 0, warning: 1 };

export function mergeAlarm(alarms, alarm) {
  const rest = alarms.filter((a) => a.id !== alarm.id);
  if (alarm.state === "CLEARED") return rest;
  return sortAlarms([alarm, ...rest]);
}

export function sortAlarms(alarms) {
  return [...alarms].sort(
    (a, b) =>
      (a.state === "ACTIVE" ? 0 : 1) - (b.state === "ACTIVE" ? 0 : 1) ||
      (SEVERITY_RANK[a.severity] ?? 2) - (SEVERITY_RANK[b.severity] ?? 2) ||
      String(b.raised_at).localeCompare(String(a.raised_at)),
  );
}
