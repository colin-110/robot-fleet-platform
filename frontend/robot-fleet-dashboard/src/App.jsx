import { useState } from "react";
import "./App.css";

import useFleetData from "./hooks/useFleetData";
import useRelativeTime from "./hooks/useRelativeTime";

import AnalyticsPanel from "./components/AnalyticsPanel";
import EventLog from "./components/EventLog";
import FleetStatusChart from "./components/FleetStatusChart";
import FleetStats from "./components/FleetStats";
import FleetMap from "./components/FleetMap";
import Navbar from "./components/Navbar";
import RobotCard from "./components/RobotCard";
import Sidebar from "./components/Sidebar";
import LoadingSkeleton from "./components/LoadingSkeleton";

function DashboardView({ filteredRobots, events }) {
  return (
    <div className="dash">
      <FleetStats robots={filteredRobots} />

      <div className="dash__main">
        <div className="dash__map">
          <FleetMap robots={filteredRobots} />
        </div>
        <div className="dash__aside">
          <FleetStatusChart robots={filteredRobots} />
          <EventLog events={events} />
        </div>
      </div>
    </div>
  );
}

function RobotGrid({ robots }) {
  return (
    <div className="robotGridPro">
      {robots.map((robot) => (
        <RobotCard key={robot.robot_id} robot={robot} />
      ))}
    </div>
  );
}

function App() {
  const {
    robots,
    analytics,
    error,
    socketConnected,
    lastFetchAt,
    lastWsAt,
    events,
    refreshAll,
    isLoading,
  } = useFleetData();

  const { formatRelativeTime } = useRelativeTime();

  const [activeNav, setActiveNav] = useState("Dashboard");
  const [query, setQuery] = useState("");

  const filteredRobots = robots.filter((robot) => {
    if (!query) return true;
    const haystack =
      `${robot.robot_id} ${robot.status} ` +
      `${robot.mission_type ?? ""} ${robot.mission_id ?? ""}`;
    return haystack.toLowerCase().includes(query.toLowerCase());
  });

  const showDashboard = activeNav === "Dashboard";
  const showTelemetry = activeNav === "Telemetry";
  const showAnalytics = activeNav === "Fleet Analytics";
  const showHealth = activeNav === "System Health";

  return (
    <div className="app-container">
      <Sidebar active={activeNav} onChange={(next) => setActiveNav(next)} />

      <div className="main-content">
        <Navbar
          title="FleetOps"
          subtitle="Mission dispatch & fleet telemetry"
          socketConnected={socketConnected}
          lastFetchText={formatRelativeTime(lastFetchAt)}
          lastWsText={formatRelativeTime(lastWsAt)}
          query={query}
          onQueryChange={setQuery}
          onClearQuery={() => setQuery("")}
          filteredCount={filteredRobots.length}
          totalCount={robots.length}
          onRefresh={refreshAll}
          error={error}
        />

        <div className="contentInner">
        {isLoading && showDashboard && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <LoadingSkeleton type="stats" />
            <LoadingSkeleton type="cards" />
          </div>
        )}

        {isLoading && (showAnalytics || showTelemetry || showHealth) && (
          <LoadingSkeleton type="panel" />
        )}

        {!isLoading && showDashboard && (
          <>
            <DashboardView filteredRobots={filteredRobots} events={events} />
            <div className="sectionLabel">
              <h2>Fleet Roster</h2>
              <span className="subtle">{filteredRobots.length} units</span>
            </div>
            <RobotGrid robots={filteredRobots} />
          </>
        )}

        {!isLoading && showTelemetry && (
          <>
            <div className="dash__map dash__map--tall">
              <FleetMap robots={filteredRobots} />
            </div>
            <div className="sectionLabel">
              <h2>Fleet Roster</h2>
              <span className="subtle">{filteredRobots.length} units</span>
            </div>
            <RobotGrid robots={filteredRobots} />
          </>
        )}

        {!isLoading && showAnalytics && <AnalyticsPanel analytics={analytics} />}

        {!isLoading && showHealth && (
          <div className="healthGrid">
            <FleetStatusChart robots={filteredRobots} />
            <EventLog events={events} />
          </div>
        )}

        {!isLoading && robots.length === 0 && !error && (
          <div className="glass emptyState">
            <div className="section-title">
              <h2>Waiting for telemetry</h2>
            </div>
            <div className="subtle">No robots yet. Connect the simulator and backend.</div>
          </div>
        )}

        {!isLoading && robots.length > 0 && filteredRobots.length === 0 && (
          <div className="glass emptyState">
            <div className="section-title">
              <h2>No results</h2>
              <button className="btn" onClick={() => setQuery("")}>Clear search</button>
            </div>
            <div className="subtle">Try a robot id, mission type, or status.</div>
          </div>
        )}
        </div>
      </div>
    </div>
  );
}

export default App;
