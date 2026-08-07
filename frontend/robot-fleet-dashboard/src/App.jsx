import { useCallback, useEffect, useState } from "react";
import "./App.css";

import useFleetData from "./hooks/useFleetData";
import useRelativeTime from "./hooks/useRelativeTime";
import useTheme from "./hooks/useTheme";
import { matchesQuery } from "./utils/search";

import AnalyticsPanel from "./components/AnalyticsPanel";
import EventLog from "./components/EventLog";
import FleetStatusChart from "./components/FleetStatusChart";
import FleetStats from "./components/FleetStats";
import FleetMap from "./components/FleetMap";
import Navbar from "./components/Navbar";
import RobotCard from "./components/RobotCard";
import Sidebar from "./components/Sidebar";
import LoadingSkeleton from "./components/LoadingSkeleton";
import LoginScreen from "./components/LoginScreen";
import { fetchAuthConfig, getSession, restoreSession } from "./utils/auth";

function DashboardView({ filteredRobots, events, onViewAllEvents }) {
  return (
    <div className="dash">
      <FleetStats robots={filteredRobots} />

      <div className="dash__main">
        <div className="dash__map">
          <FleetMap robots={filteredRobots} />
        </div>
        <div className="dash__aside">
          <FleetStatusChart robots={filteredRobots} />
          <EventLog events={events} onViewAll={onViewAllEvents} />
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

function SectionLabel({ title, count }) {
  return (
    <div className="sectionLabel">
      <h2>{title}</h2>
      <span className="subtle">{count} units</span>
    </div>
  );
}

/**
 * Decides whether a sign-in gate stands in front of the dashboard.
 *
 * The backend is asked rather than assumed, so one build works against both a
 * public demo and a locked-down deployment. Until the answer arrives nothing
 * is rendered: guessing "open" would flash the dashboard before redirecting,
 * and guessing "required" would flash a login screen the demo never needs.
 */
function useAuthGate() {
  const [state, setState] = useState({ status: "checking", loginRequired: false });

  useEffect(() => {
    let cancelled = false;
    restoreSession();

    fetchAuthConfig()
      .then((config) => {
        if (cancelled) return;
        setState({ status: "ready", loginRequired: Boolean(config.login_required) });
      })
      .catch(() => {
        // The backend is unreachable. Fall through to the dashboard, which has
        // its own "Backend is unreachable" banner — a login screen here would
        // blame the operator for an outage.
        if (!cancelled) setState({ status: "ready", loginRequired: false });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

function FleetConsole() {
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
  const { theme, toggleTheme } = useTheme();

  const [activeNav, setActiveNav] = useState("Dashboard");
  const [query, setQuery] = useState("");
  const [navOpen, setNavOpen] = useState(false);

  const closeNav = useCallback(() => setNavOpen(false), []);

  // Escape closes the mobile drawer. Without it the only way out is the scrim,
  // which is not obvious on a phone and impossible from a keyboard.
  useEffect(() => {
    if (!navOpen) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") setNavOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navOpen]);

  const filteredRobots = robots.filter((robot) => matchesQuery(robot, query));

  const showDashboard = activeNav === "Dashboard";
  const showTelemetry = activeNav === "Telemetry";
  const showAnalytics = activeNav === "Fleet Analytics";
  const showEvents = activeNav === "Event Log";
  const showHealth = activeNav === "System Health";
  const showPanelSkeleton = showAnalytics || showTelemetry || showHealth || showEvents;

  return (
    <div className="app-container">
      <Sidebar
        active={activeNav}
        onChange={setActiveNav}
        open={navOpen}
        onClose={closeNav}
      />

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
          onOpenNav={() => setNavOpen(true)}
          theme={theme}
          onToggleTheme={toggleTheme}
          error={error}
        />

        <div className="contentInner">
          {isLoading && showDashboard && (
            <div className="stack">
              <LoadingSkeleton type="stats" />
              <LoadingSkeleton type="cards" />
            </div>
          )}

          {isLoading && showPanelSkeleton && <LoadingSkeleton type="panel" />}

          {!isLoading && showDashboard && (
            <>
              <DashboardView
                filteredRobots={filteredRobots}
                events={events}
                onViewAllEvents={() => setActiveNav("Event Log")}
              />
              <SectionLabel title="Fleet Roster" count={filteredRobots.length} />
              <RobotGrid robots={filteredRobots} />
            </>
          )}

          {!isLoading && showTelemetry && (
            <>
              <div className="dash__map dash__map--tall">
                <FleetMap robots={filteredRobots} />
              </div>
              <SectionLabel title="Fleet Roster" count={filteredRobots.length} />
              <RobotGrid robots={filteredRobots} />
            </>
          )}

          {!isLoading && showAnalytics && <AnalyticsPanel analytics={analytics} />}

          {!isLoading && showEvents && (
            <div className="logPage">
              <EventLog events={events} variant="page" />
            </div>
          )}

          {!isLoading && showHealth && (
            <div className="healthGrid">
              <FleetStatusChart robots={filteredRobots} />
              <EventLog events={events} onViewAll={() => setActiveNav("Event Log")} />
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

function App() {
  const { status, loginRequired } = useAuthGate();
  const [session, setSession] = useState(() => getSession());

  if (status === "checking") return null;
  if (loginRequired && !session) {
    return <LoginScreen onSignedIn={setSession} />;
  }

  return <FleetConsole />;
}

export default App;
