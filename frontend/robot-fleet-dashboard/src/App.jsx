import { useEffect, useState } from "react";
import axios from "axios";
import { AnimatePresence, motion } from "framer-motion";

import RobotCard from "./components/RobotCard";
import AlertsPanel from "./components/AlertsPanel";
import TelemetryChart from "./components/TelemetryChart";
import FleetStats from "./components/FleetStats";
import Sidebar from "./components/Sidebar";
import Navbar from "./components/Navbar";
import FleetHealth from "./components/FleetHealth";
import PredictiveMaintenancePanel from "./components/PredictiveMaintenancePanel";

function App() {
  const [robots, setRobots] = useState([]);
  const [maintenance, setMaintenance] = useState([]);
  const [error, setError] = useState("");
  const [socketConnected, setSocketConnected] = useState(false);
  const [lastFetchAt, setLastFetchAt] = useState(0);
  const [lastWsAt, setLastWsAt] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeNav, setActiveNav] = useState("Dashboard");
  const [query, setQuery] = useState("");

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL ||
    `http://${window.location.hostname}:8000`;

  const WS_BASE = API_BASE.replace("http", "ws");

  const formatRelativeTime = (timestampMs) => {
    if (!timestampMs) return "--";
    const seconds = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000));
    if (seconds < 5) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ago`;
  };

  const fetchRobots = async () => {
    try {
      const response = await axios.get(`${API_BASE}/robots/status`);

      setRobots(Array.isArray(response.data) ? response.data : []);
      setLastFetchAt(Date.now());
      setError("");
    } catch (requestError) {
      console.error(requestError);
      setError("Backend is unreachable.");
    }
  };

  const fetchMaintenance = async () => {
    try {
      const response = await axios.get(
        `${API_BASE}/robots/predictive-maintenance`
      );

      setMaintenance(Array.isArray(response.data) ? response.data : []);
    } catch (requestError) {
      console.error(requestError);
    }
  };

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
        await fetchRobots();
        await fetchMaintenance();
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

    fetchRobots();
    fetchMaintenance();
    connectWebSocket();

    pollTimer = setInterval(() => {
      fetchRobots();
      fetchMaintenance();
    }, 3000);

    return () => {
      unmounted = true;
      clearInterval(pollTimer);
      clearInterval(keepAliveTimer);
      clearTimeout(reconnectTimer);

      if (socket) {
        socket.close();
      }
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
    const haystack = `${robot.robot_id} ${robot.status} ${robot.risk_level ?? ""} ${robot.failure_risk ?? ""}`.toLowerCase();
    return haystack.includes(query.toLowerCase());
  });

  const filteredMaintenance = maintenance
    .filter((item) => {
      if (!query) return true;
      const haystack = `${item.robot_id} ${item.risk_level} ${(item.reasons || []).join(" ")}`.toLowerCase();
      return haystack.includes(query.toLowerCase());
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
          subtitle="Predictive fleet monitoring | real-time telemetry"
          socketConnected={socketConnected}
          lastFetchText={formatRelativeTime(lastFetchAt)}
          lastWsText={formatRelativeTime(lastWsAt)}
          query={query}
          onQueryChange={setQuery}
          onClearQuery={() => setQuery("")}
          filteredCount={filteredRobots.length}
          totalCount={robotsWithInsights.length}
          onOpenSidebar={() => setSidebarOpen(true)}
          onRefresh={() => {
            fetchRobots();
            fetchMaintenance();
          }}
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

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(12, 1fr)",
                gap: "14px",
                marginBottom: "16px"
              }}
            >
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

        {(showTelemetry || showAnalytics) && (
          <motion.div
            key="telemetry"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <TelemetryChart robots={filteredRobots} />
          </motion.div>
        )}

        {showHealth && (
          <motion.div
            key="health"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <div style={{ maxWidth: 520 }}>
              <FleetHealth robots={filteredRobots} maintenance={filteredMaintenance} />
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
              <span className="subtle">Connect simulator + backend</span>
            </div>
            <div className="subtle">
              No robots yet. When data arrives, cards will animate in.
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
              Try searching by robot id, status, or risk level.
            </div>
          </div>
        )}

        {(showDashboard || showTelemetry || showAnalytics) && (
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
