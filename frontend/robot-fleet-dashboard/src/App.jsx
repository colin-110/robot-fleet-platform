import { useState } from "react";
import "./App.css";

import useFleetData from "./hooks/useFleetData";
import useRelativeTime from "./hooks/useRelativeTime";

import AlertsPanel from "./components/AlertsPanel";
import AnalyticsPanel from "./components/AnalyticsPanel";
import FleetHealth from "./components/FleetHealth";
import FleetStats from "./components/FleetStats";
import Navbar from "./components/Navbar";
import PredictiveMaintenancePanel from "./components/PredictiveMaintenancePanel";
import RobotCard from "./components/RobotCard";
import Sidebar from "./components/Sidebar";
import TelemetryChart from "./components/TelemetryChart";

function App() {
  const {
    robots,
    maintenance,
    analytics,
    error,
    socketConnected,
    lastFetchAt,
    lastWsAt,
    refreshAll,
  } = useFleetData();

  const { formatRelativeTime } = useRelativeTime();

  const [activeNav, setActiveNav] = useState("Dashboard");
  const [query, setQuery] = useState("");

  const maintenanceByRobotId = new Map(
    maintenance.map((item) => [item.robot_id, item])
  );

  const robotsWithInsights = robots.map((robot) => {
    const insight = maintenanceByRobotId.get(robot.robot_id);
    return {
      ...robot,
      failure_risk: insight?.failure_risk ?? null,
      risk_level: insight?.risk_level ?? null,
      reasons: insight?.reasons ?? [],
      runtime_remaining_minutes:
        robot.runtime_remaining_minutes ??
        insight?.runtime_remaining_minutes ??
        null,
    };
  });

  const filteredRobots = robotsWithInsights.filter((robot) => {
    if (!query) return true;
    const haystack =
      `${robot.robot_id} ${robot.status} ${robot.risk_level ?? ""} ` +
      `${robot.failure_risk ?? ""} ${robot.mission_type ?? ""} ${robot.mission_id ?? ""}`;
    return haystack.toLowerCase().includes(query.toLowerCase());
  });

  const filteredMaintenance = maintenance
    .filter((item) => {
      if (!query) return true;
      const haystack = `${item.robot_id} ${item.risk_level} ${(item.reasons || []).join(" ")}`;
      return haystack.toLowerCase().includes(query.toLowerCase());
    })
    .sort((left, right) => {
      if (right.failure_risk !== left.failure_risk)
        return right.failure_risk - left.failure_risk;
      return left.robot_id - right.robot_id;
    });

  const showDashboard = activeNav === "Dashboard";
  const showTelemetry = activeNav === "Telemetry";
  const showAlerts = activeNav === "AI Alerts";
  const showAnalytics = activeNav === "Fleet Analytics";
  const showHealth = activeNav === "System Health";

  return (
    <div className="app-container">
      <Sidebar
        active={activeNav}
        onChange={(next) => setActiveNav(next)}
      />

      <div className="main-content">
        <Navbar
          title="FleetOps AI"
          subtitle="Mission dispatch, fleet telemetry, and predictive maintenance"
          socketConnected={socketConnected}
          lastFetchText={formatRelativeTime(lastFetchAt)}
          lastWsText={formatRelativeTime(lastWsAt)}
          query={query}
          onQueryChange={setQuery}
          onClearQuery={() => setQuery("")}
          filteredCount={filteredRobots.length}
          totalCount={robotsWithInsights.length}
          onRefresh={refreshAll}
          error={error}
        />

        {showDashboard && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            <FleetStats robots={filteredRobots} maintenance={filteredMaintenance} />
            <div className="dashboard-grid">
              <div className="col-span-8">
                <TelemetryChart robots={filteredRobots} />
              </div>
              <div className="col-span-4">
                <FleetHealth robots={filteredRobots} maintenance={filteredMaintenance} />
              </div>
            </div>
            <AlertsPanel robots={filteredRobots} />
            <PredictiveMaintenancePanel maintenance={filteredMaintenance} />
          </div>
        )}

        {showTelemetry && (
          <div>
            <TelemetryChart robots={filteredRobots} />
          </div>
        )}

        {showAnalytics && (
          <div>
            <AnalyticsPanel analytics={analytics} />
          </div>
        )}

        {showHealth && (
          <div style={{ display: "grid", gap: 24 }}>
            <FleetHealth robots={filteredRobots} maintenance={filteredMaintenance} />
            <PredictiveMaintenancePanel maintenance={filteredMaintenance} />
          </div>
        )}

        {showAlerts && (
          <div>
            <AlertsPanel robots={filteredRobots} />
            <PredictiveMaintenancePanel maintenance={filteredMaintenance} />
          </div>
        )}

        {robotsWithInsights.length === 0 && !error && (
          <div className="panel" style={{ padding: 24, marginBottom: 16 }}>
            <div className="section-title">
              <h2>Waiting for telemetry</h2>
            </div>
            <div className="subtle">
              No robots yet. Connect simulator and backend.
            </div>
          </div>
        )}

        {robotsWithInsights.length > 0 && filteredRobots.length === 0 && (
          <div className="panel" style={{ padding: 24, marginBottom: 16 }}>
            <div className="section-title">
              <h2>No results</h2>
              <button className="btn" onClick={() => setQuery("")}>
                Clear search
              </button>
            </div>
            <div className="subtle">
              Try robot id, mission type, status, or risk level.
            </div>
          </div>
        )}

        {(showDashboard || showTelemetry) && (
          <div className="dashboard-grid">
            {filteredRobots.map((robot) => (
              <div key={robot.robot_id} className="col-span-4">
                <RobotCard robot={robot} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
