import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";

import { FLEET_STATUSES, statusColor } from "../utils/constants";

const LABELS = Object.fromEntries(FLEET_STATUSES.map((s) => [s.key, s.label]));
const ORDER = FLEET_STATUSES.map((s) => s.key);

const label = (status) => LABELS[status] ?? status;

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    return (
      <div className="glass" style={{ padding: "8px 12px", border: "1px solid var(--stroke)" }}>
        <div style={{ color: "var(--text)", fontSize: "14px", fontWeight: "500" }}>
          {label(data.name)}
        </div>
        <div style={{ color: "var(--muted)", fontSize: "13px" }}>Count: {data.value}</div>
      </div>
    );
  }
  return null;
};

/**
 * The fleet by status: a donut, the leading status in its middle, and a legend
 * with each status's count and share, so it reads without hovering. Only
 * statuses some robot is in are drawn; with a single one the ring is whole
 * (the gap between slices would otherwise cut a notch in it).
 */
export default function FleetStatusChart({ robots }) {
  const counts = robots.reduce((acc, robot) => {
    const status = robot.status || "UNKNOWN";
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});

  // Known statuses in their usual order, then anything new.
  const data = Object.keys(counts)
    .sort((a, b) => {
      const ia = ORDER.indexOf(a) === -1 ? ORDER.length : ORDER.indexOf(a);
      const ib = ORDER.indexOf(b) === -1 ? ORDER.length : ORDER.indexOf(b);
      return ia - ib || a.localeCompare(b);
    })
    .map((status) => ({ name: status, value: counts[status] }));
  const top = data.reduce((best, d) => (best && best.value >= d.value ? best : d), null);
  const share = (value) => Math.round((100 * value) / robots.length);

  return (
    <div className="glassStrong" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="panelHead">
        <h2>Status Distribution</h2>
        <span className="subtle">{robots.length} units</span>
      </div>

      {data.length > 0 ? (
        <>
          <div style={{ flex: 1, position: "relative", minHeight: 0, padding: "8px 0" }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  cx="50%"
                  cy="50%"
                  innerRadius="60%"
                  outerRadius="80%"
                  paddingAngle={data.length > 1 ? 3 : 0}
                  dataKey="value"
                  stroke="none"
                  isAnimationActive={false}
                >
                  {data.map((entry) => (
                    <Cell key={entry.name} fill={statusColor(entry.name)} />
                  ))}
                </Pie>
                <Tooltip content={<CustomTooltip />} />
              </PieChart>
            </ResponsiveContainer>
            <div
              data-testid="status-center"
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                pointerEvents: "none",
              }}
            >
              <span style={{ fontSize: "22px", fontWeight: 600, color: "var(--text)" }}>
                {share(top.value)}%
              </span>
              <span style={{ fontSize: "12px", color: "var(--muted)" }}>{label(top.name)}</span>
            </div>
          </div>
          <ul
            aria-label="Status counts"
            style={{
              listStyle: "none",
              margin: 0,
              padding: "0 12px 10px",
              display: "flex",
              flexWrap: "wrap",
              gap: "6px 14px",
              justifyContent: "center",
              fontSize: "12px",
              color: "var(--muted)",
            }}
          >
            {data.map((d) => (
              <li key={d.name} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: statusColor(d.name),
                  }}
                />
                <span style={{ color: "var(--text)" }}>{label(d.name)}</span>
                <span>
                  {d.value} ({share(d.value)}%)
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <div
          style={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--muted)",
            fontSize: "14px",
          }}
        >
          No data available
        </div>
      )}
    </div>
  );
}
