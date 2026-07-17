import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";

const COLORS = {
  ACTIVE: "hsl(142, 72%, 45%)", // good
  CHARGING: "hsl(198, 93%, 60%)", // info
  "LOW POWER": "hsl(38, 92%, 50%)", // warn
  OFFLINE: "#94a3b8", // gray
  ERROR: "hsl(0, 84%, 60%)" // bad
};

const CustomTooltip = ({ active, payload }) => {
  if (active && payload && payload.length) {
    const data = payload[0].payload;
    return (
      <div className="glass" style={{ padding: "8px 12px", border: "1px solid var(--stroke)" }}>
        <div style={{ color: "var(--text)", fontSize: "14px", fontWeight: "500" }}>{data.name}</div>
        <div style={{ color: "var(--muted)", fontSize: "13px" }}>Count: {data.value}</div>
      </div>
    );
  }
  return null;
};

export default function FleetStatusChart({ robots }) {
  // Calculate status counts
  const statusCounts = robots.reduce((acc, robot) => {
    const status = robot.status || "UNKNOWN";
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});

  // Format data for Recharts
  const data = Object.keys(statusCounts).map((status) => ({
    name: status,
    value: statusCounts[status],
  }));

  return (
    <div className="glassStrong" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="drag-handle" style={{ padding: "16px 20px", cursor: "grab", borderBottom: "1px solid var(--stroke)" }}>
        <h2 style={{ margin: 0, fontSize: "16px", color: "rgba(226, 232, 240, 0.96)" }}>Status Distribution</h2>
      </div>
      
      <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius="60%"
                outerRadius="80%"
                paddingAngle={5}
                dataKey="value"
                stroke="none"
              >
                {data.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[entry.name] || "#94a3b8"} />
                ))}
              </Pie>
              <Tooltip content={<CustomTooltip />} />
            </PieChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--muted)", fontSize: "14px" }}>
            No data available
          </div>
        )}
      </div>
    </div>
  );
}
