import { FLEET_STATUSES, statusBreakdown } from "../utils/constants";

/**
 * The KPI row: fleet size, then a breakdown that always adds up to it.
 *
 * Empty buckets are still rendered — an operator reading "Overheating 0" is
 * being told something, and cards that appear and vanish as the fleet changes
 * make the row jump. The one conditional card is "Other", which only appears
 * when a robot reports a status this build has no bucket for; that is the case
 * that used to make the numbers silently disagree with the total.
 */
function FleetStats({ robots }) {
  const { counts, other, total } = statusBreakdown(robots);

  const averageBattery =
    total > 0 ? (robots.reduce((sum, r) => sum + r.battery, 0) / total).toFixed(1) : "0.0";

  const stats = [
    { title: "Total Robots", value: total, color: "var(--accent)", hint: "in fleet" },
    ...FLEET_STATUSES.map((s) => ({
      title: s.label,
      value: counts[s.key],
      color: s.color,
      hint: s.hint,
    })),
    ...(other > 0
      ? [{ title: "Other", value: other, color: "var(--c-idle)", hint: "unrecognised status" }]
      : []),
    { title: "Avg Battery", value: `${averageBattery}%`, color: "var(--c-good)", hint: "fleet mean" },
  ];

  return (
    <div className="statGrid">
      {stats.map((s) => (
        <div key={s.title} className="glass statCard">
          <div className="statCard__top">
            <span className="statCard__dot" style={{ background: s.color }} />
            <span className="statCard__label">{s.title}</span>
          </div>
          <div className="statCard__value" style={{ color: s.color }}>{s.value}</div>
          <div className="statCard__hint">{s.hint}</div>
        </div>
      ))}
    </div>
  );
}

export default FleetStats;
