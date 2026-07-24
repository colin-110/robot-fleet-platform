import {
  Bar,
  BarChart,
  Cell,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { PIE_COLORS } from "../utils/constants";

const chartCardStyle = {
  padding: 16,
  minHeight: 320
};

function AnalyticsPanel({ analytics }) {
  const trend = analytics?.fleet_health_trend || [];
  const batteryDistribution = analytics?.battery_distribution || [];
  const temperatureDistribution = analytics?.temperature_distribution || [];
  const missionCompletion = analytics?.mission_completion_count || [];
  const statusBreakdown = analytics?.robot_status_breakdown || [];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="twoColumnGrid">
        <div className="glass colSpan7" style={chartCardStyle}>
          <div className="sectionTitle">
            <h2>Fleet Health Trend</h2>
            <span className="subtle">Recent telemetry windows</span>
          </div>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={trend}>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 6" />
              <XAxis
                dataKey="timestamp"
                tickFormatter={(value) => new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                tick={{ fill: "var(--chart-axis)", fontSize: 12 }}
              />
              <YAxis tick={{ fill: "var(--chart-axis)", fontSize: 12 }} domain={[0, 100]} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="health_score"
                stroke="hsl(var(--chart-1))"
                strokeWidth={3}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="glass colSpan5" style={chartCardStyle}>
          <div className="sectionTitle">
            <h2>Robot Status Breakdown</h2>
            <span className="subtle">Current fleet state</span>
          </div>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie
                data={statusBreakdown}
                dataKey="count"
                nameKey="status"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={3}
              >
                {statusBreakdown.map((entry) => (
                  <Cell
                    key={entry.status}
                    fill={PIE_COLORS[entry.status] || "#64748b"}
                  />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="analyticsGrid">
        <div className="glass" style={chartCardStyle}>
          <div className="sectionTitle">
            <h2>Battery Distribution</h2>
            <span className="subtle">Latest robot snapshot</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={batteryDistribution}>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 6" />
              <XAxis dataKey="range" tick={{ fill: "var(--chart-axis)", fontSize: 12 }} />
              <YAxis tick={{ fill: "var(--chart-axis)", fontSize: 12 }} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="hsl(var(--chart-1))" radius={[10, 10, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="glass" style={chartCardStyle}>
          <div className="sectionTitle">
            <h2>Temperature Distribution</h2>
            <span className="subtle">Latest robot snapshot</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={temperatureDistribution}>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 6" />
              <XAxis dataKey="range" tick={{ fill: "var(--chart-axis)", fontSize: 12 }} />
              <YAxis tick={{ fill: "var(--chart-axis)", fontSize: 12 }} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="hsl(var(--chart-2))" radius={[10, 10, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="glass" style={chartCardStyle}>
          <div className="sectionTitle">
            <h2>Mission Completion Count</h2>
            <span className="subtle">Completed missions</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={missionCompletion}>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 6" />
              <XAxis dataKey="mission_type" tick={{ fill: "var(--chart-axis)", fontSize: 12 }} />
              <YAxis tick={{ fill: "var(--chart-axis)", fontSize: 12 }} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" fill="hsl(var(--chart-3))" radius={[10, 10, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

export default AnalyticsPanel;
