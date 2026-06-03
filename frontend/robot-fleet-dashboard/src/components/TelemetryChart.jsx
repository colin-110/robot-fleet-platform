import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip
} from "recharts";

const tooltipStyle = {
  background: "var(--chart-tooltip)",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderRadius: 12
};

function TooltipContent({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  const battery = payload.find((p) => p.dataKey === "battery")?.value;
  const temperature = payload.find((p) => p.dataKey === "temperature")?.value;

  return (
    <div style={{ padding: 10, minWidth: 160 }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{label}</div>
      <div className="subtle" style={{ display: "grid", gap: 4 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Battery</span>
          <span style={{ color: "hsl(var(--chart-1))", fontWeight: 700 }}>
            {battery}%
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Temp</span>
          <span style={{ color: "hsl(var(--chart-2))", fontWeight: 700 }}>
            {temperature}°C
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
    temperature: robot.temperature
  }));

  return (
    <div
      className="glassStrong sheen"
      style={{ padding: 16 }}
    >

      <div className="sectionTitle">
        <h2>Fleet telemetry</h2>
        <span className="subtle">
          {robots.length} robot{robots.length === 1 ? "" : "s"}
        </span>
      </div>

      <ResponsiveContainer
        width="100%"
        height={300}
      >

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
            cursor={{ stroke: "rgba(99, 102, 241, 0.25)", strokeWidth: 1 }}
            content={<TooltipContent />}
            wrapperStyle={tooltipStyle}
          />

          <Line
            type="monotone"
            dataKey="battery"
            stroke="hsl(var(--chart-1))"
            strokeWidth={3}
            dot={false}
          />

          <Line
            type="monotone"
            dataKey="temperature"
            stroke="hsl(var(--chart-2))"
            strokeWidth={3}
            dot={false}
          />

        </LineChart>

      </ResponsiveContainer>

    </div>
  );
}

export default TelemetryChart;
