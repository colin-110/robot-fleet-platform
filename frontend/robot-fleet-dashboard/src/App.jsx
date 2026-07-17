import { useState } from "react";
import { ResponsiveGridLayout, useContainerWidth } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
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
  } = useFleetData();

  const { formatRelativeTime } = useRelativeTime();
  const { width: gridWidth, containerRef: gridRef } = useContainerWidth();

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
      <Sidebar
        active={activeNav}
        onChange={(next) => setActiveNav(next)}
      />

      <div className="main-content">
        <Navbar
          title="FleetOps"
          subtitle="Mission dispatch and fleet telemetry"
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

        {showDashboard && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            <FleetStats robots={filteredRobots} />
            <div ref={gridRef}>
              {gridWidth > 0 && (
                <ResponsiveGridLayout
                  className="layout"
                  layouts={{
                    lg: [
                      { i: "telemetry", x: 0, y: 0, w: 8, h: 6 },
                      { i: "status", x: 8, y: 0, w: 4, h: 3 },
                      { i: "events", x: 8, y: 3, w: 4, h: 3 }
                    ],
                    md: [
                      { i: "telemetry", x: 0, y: 0, w: 10, h: 6 },
                      { i: "status", x: 0, y: 6, w: 5, h: 3 },
                      { i: "events", x: 5, y: 6, w: 5, h: 3 }
                    ]
                  }}
                  breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
                  cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
                  rowHeight={100}
                  width={gridWidth}
                  draggableHandle=".drag-handle"
                  isDraggable={true}
                  isResizable={true}
                >
                  <div key="telemetry">
                    <FleetMap robots={filteredRobots} />
                  </div>
                  <div key="status">
                    <FleetStatusChart robots={filteredRobots} />
                  </div>
                  <div key="events">
                    <EventLog events={events} />
                  </div>
                </ResponsiveGridLayout>
              )}
            </div>
          </div>
        )}

        {showTelemetry && (
          <div style={{ height: "600px" }}>
            <FleetMap robots={filteredRobots} />
          </div>
        )}

        {showAnalytics && (
          <div>
            <AnalyticsPanel analytics={analytics} />
          </div>
        )}

        {showHealth && (
          <div style={{ display: "grid", gap: 24, height: 400 }}>
            <FleetStatusChart robots={filteredRobots} />
          </div>
        )}

        {robots.length === 0 && !error && (
          <div className="panel" style={{ padding: 24, marginBottom: 16 }}>
            <div className="section-title">
              <h2>Waiting for telemetry</h2>
            </div>
            <div className="subtle">
              No robots yet. Connect simulator and backend.
            </div>
          </div>
        )}

        {robots.length > 0 && filteredRobots.length === 0 && (
          <div className="panel" style={{ padding: 24, marginBottom: 16 }}>
            <div className="section-title">
              <h2>No results</h2>
              <button className="btn" onClick={() => setQuery("")}>
                Clear search
              </button>
            </div>
            <div className="subtle">
              Try robot id, mission type, or status.
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
