import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

const tooltipStyle = {
  background: "var(--chart-tooltip)",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderRadius: 12
};

function TooltipContent({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  const battery = payload.find((point) => point.dataKey === "battery")?.value;
  const temperature = payload.find((point) => point.dataKey === "temperature")?.value;
  const speed = payload.find((point) => point.dataKey === "speed")?.value;

  return (
    <div style={{ padding: 10, minWidth: 180 }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{label}</div>
      <div className="subtle" style={{ display: "grid", gap: 4 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Battery</span>
          <span style={{ color: "var(--status-active)", fontWeight: 700 }}>
            {battery}%
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Temperature</span>
          <span style={{ color: "var(--status-warning)", fontWeight: 700 }}>
            {temperature}C
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Speed</span>
          <span style={{ color: "var(--status-charging)", fontWeight: 700 }}>
            {speed} m/s
          </span>
        </div>
      </div>
    </div>
  );
}

function TelemetryChart({ robots }) {
  const chartData = robots.map((robot) => ({
    name: `R${robot.robot_id}`,
    battery: robot.battery,
    temperature: robot.temperature,
    speed: robot.speed
  }));

  return (
    <div className="glass" style={{ padding: 16, height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="sectionTitle drag-handle" style={{ cursor: "grab", flexShrink: 0 }}>
        <h2>Fleet telemetry</h2>
        <span className="subtle">
          {robots.length} robot{robots.length === 1 ? "" : "s"}
        </span>
      </div>

      <div style={{ flexGrow: 1, minHeight: 0, marginTop: 8 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData}>
            <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 6" />
            <XAxis
              dataKey="name"
              tick={{ fill: "var(--chart-axis)", fontSize: 12 }}
              axisLine={{ stroke: "rgba(148, 163, 184, 0.22)" }}
              tickLine={{ stroke: "rgba(148, 163, 184, 0.22)" }}
            />
            <YAxis
              tick={{ fill: "var(--chart-axis)", fontSize: 12 }}
              axisLine={{ stroke: "rgba(148, 163, 184, 0.22)" }}
              tickLine={{ stroke: "rgba(148, 163, 184, 0.22)" }}
            />
            <Tooltip
              cursor={{ stroke: "rgba(33, 211, 146, 0.25)", strokeWidth: 1 }}
              content={<TooltipContent />}
              wrapperStyle={tooltipStyle}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="battery"
              stroke="var(--status-active)"
              strokeWidth={3}
              dot={{ r: 3 }}
            />
            <Line
              type="monotone"
              dataKey="temperature"
              stroke="var(--status-warning)"
              strokeWidth={3}
              dot={{ r: 3 }}
            />
            <Line
              type="monotone"
              dataKey="speed"
              stroke="var(--status-charging)"
              strokeWidth={3}
              dot={{ r: 3 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default TelemetryChart;
