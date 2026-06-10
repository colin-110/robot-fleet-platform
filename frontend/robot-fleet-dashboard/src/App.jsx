import { useEffect, useEffectEvent, useState } from "react";
import axios from "axios";
import { AnimatePresence, motion } from "framer-motion";

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
  const [robots, setRobots] = useState([]);
  const [maintenance, setMaintenance] = useState([]);
  const [analytics, setAnalytics] = useState({
    fleet_health_trend: [],
    battery_distribution: [],
    temperature_distribution: [],
    mission_completion_count: [],
    robot_status_breakdown: []
  });
  const [error, setError] = useState("");
  const [socketConnected, setSocketConnected] = useState(false);
  const [lastFetchAt, setLastFetchAt] = useState(0);
  const [lastWsAt, setLastWsAt] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeNav, setActiveNav] = useState("Dashboard");
  const [query, setQuery] = useState("");

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL ||
    `http://${window.location.hostname}:8000`;

  const WS_BASE = API_BASE.replace("http", "ws");

  const formatRelativeTime = (timestampMs) => {
    if (!timestampMs) return "--";
    const seconds = Math.max(0, Math.floor((nowMs - timestampMs) / 1000));
    if (seconds < 5) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ago`;
  };

  const fetchRobots = async () => {
    const response = await axios.get(`${API_BASE}/robots/status`);
    setRobots(Array.isArray(response.data) ? response.data : []);
  };

  const fetchMaintenance = async () => {
    const response = await axios.get(`${API_BASE}/robots/predictive-maintenance`);
    setMaintenance(Array.isArray(response.data) ? response.data : []);
  };

  const fetchAnalytics = async () => {
    const response = await axios.get(`${API_BASE}/analytics/fleet`);
    setAnalytics(response.data || {});
  };

  const refreshAll = async () => {
    try {
      await Promise.all([fetchRobots(), fetchMaintenance(), fetchAnalytics()]);
      setLastFetchAt(Date.now());
      setError("");
    } catch (requestError) {
      console.error(requestError);
      setError("Backend is unreachable.");
    }
  };

  const refreshAllEvent = useEffectEvent(async () => {
    await refreshAll();
  });

  useEffect(() => {
    let socket;
    let reconnectTimer;
    let pollTimer;
    let keepAliveTimer;
    let unmounted = false;

    const connectWebSocket = () => {
      if (unmounted) {
        return;
      }

      socket = new WebSocket(`${WS_BASE}/ws`);

      socket.onopen = () => {
        setSocketConnected(true);
        setLastWsAt(Date.now());
        setError("");

        keepAliveTimer = setInterval(() => {
          if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 10000);
      };

      socket.onmessage = async () => {
        setLastWsAt(Date.now());
        await refreshAllEvent();
      };

      socket.onerror = (websocketError) => {
        console.error("WebSocket error:", websocketError);
        setSocketConnected(false);
      };

      socket.onclose = () => {
        if (unmounted) {
          return;
        }

        setSocketConnected(false);
        clearInterval(keepAliveTimer);
        reconnectTimer = setTimeout(connectWebSocket, 2000);
      };
    };

    setTimeout(() => {
      refreshAllEvent();
    }, 0);
    connectWebSocket();

    pollTimer = setInterval(() => {
      refreshAllEvent();
    }, 5000);

    return () => {
      unmounted = true;
      clearInterval(pollTimer);
      clearInterval(keepAliveTimer);
      clearTimeout(reconnectTimer);

      if (socket) {
        socket.close();
      }
    };
  }, [WS_BASE]);

  useEffect(() => {
    const timer = setInterval(() => {
      setNowMs(Date.now());
    }, 10000);

    return () => {
      clearInterval(timer);
    };
  }, []);

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
        null
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
      if (right.failure_risk !== left.failure_risk) {
        return right.failure_risk - left.failure_risk;
      }
      return left.robot_id - right.robot_id;
    });

  const showDashboard = activeNav === "Dashboard";
  const showTelemetry = activeNav === "Telemetry";
  const showAlerts = activeNav === "AI Alerts";
  const showAnalytics = activeNav === "Fleet Analytics";
  const showHealth = activeNav === "System Health";

  return (
    <div className="shell">
      <AnimatePresence>
        {sidebarOpen && (
          <motion.div
            className="overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>

      <Sidebar
        open={sidebarOpen}
        active={activeNav}
        onChange={(next) => {
          setActiveNav(next);
          setSidebarOpen(false);
        }}
        onToggle={() => setSidebarOpen((prev) => !prev)}
      />

      <div className="content">
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
          onOpenSidebar={() => setSidebarOpen(true)}
          onRefresh={refreshAll}
          error={error}
        />

        {showDashboard && (
          <motion.div
            key="dashboard"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <FleetStats robots={filteredRobots} maintenance={filteredMaintenance} />

            <div className="twoColumnGrid">
              <div className="colSpan7">
                <TelemetryChart robots={filteredRobots} />
              </div>
              <div className="colSpan5">
                <FleetHealth robots={filteredRobots} maintenance={filteredMaintenance} />
              </div>
            </div>

            <AlertsPanel robots={filteredRobots} />
            <PredictiveMaintenancePanel maintenance={filteredMaintenance} />
          </motion.div>
        )}

        {showTelemetry && (
          <motion.div
            key="telemetry"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <TelemetryChart robots={filteredRobots} />
          </motion.div>
        )}

        {showAnalytics && (
          <motion.div
            key="analytics"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <AnalyticsPanel analytics={analytics} />
          </motion.div>
        )}

        {showHealth && (
          <motion.div
            key="health"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <div style={{ display: "grid", gap: 16 }}>
              <FleetHealth robots={filteredRobots} maintenance={filteredMaintenance} />
              <PredictiveMaintenancePanel maintenance={filteredMaintenance} />
            </div>
          </motion.div>
        )}

        {showAlerts && (
          <motion.div
            key="alerts"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <AlertsPanel robots={filteredRobots} />
            <PredictiveMaintenancePanel maintenance={filteredMaintenance} />
          </motion.div>
        )}

        {robotsWithInsights.length === 0 && !error && (
          <div className="glass" style={{ padding: 16, marginBottom: 16 }}>
            <div className="sectionTitle">
              <h2>Waiting for telemetry</h2>
              <span className="subtle">Connect simulator and backend</span>
            </div>
            <div className="subtle">
              No robots yet. Fleet cards will render once telemetry arrives.
            </div>
          </div>
        )}

        {robotsWithInsights.length > 0 && filteredRobots.length === 0 && (
          <div className="glass" style={{ padding: 16, marginBottom: 16 }}>
            <div className="sectionTitle">
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
          <div className="robotGrid">
            <AnimatePresence initial={false}>
              {filteredRobots.map((robot) => (
                <motion.div
                  key={robot.robot_id}
                  className="robotCol"
                  initial={{ opacity: 0, y: 10, scale: 0.98 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 10, scale: 0.98 }}
                  transition={{ duration: 0.18, ease: "easeOut" }}
                >
                  <RobotCard robot={robot} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
