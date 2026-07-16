import { useEffect, useEffectEvent, useState, useCallback, useRef } from "react";
import axios from "axios";
import { useWebSocket } from "./useWebSocket";

/**
 * Custom hook that encapsulates ALL fleet data fetching logic:
 *  - REST polling (robots, maintenance, analytics)
 *  - WebSocket connection management
 *  - Error state tracking
 *  - Connection timestamps
 *
 * Extracts ~200 lines of logic out of App.jsx.
 */
export default function useFleetData() {
  const [robots, setRobots] = useState([]);
  const [maintenance, setMaintenance] = useState([]);
  const [analytics, setAnalytics] = useState({
    fleet_health_trend: [],
    battery_distribution: [],
    temperature_distribution: [],
    mission_completion_count: [],
    robot_status_breakdown: [],
  });
  const [error, setError] = useState("");
  const [lastFetchAt, setLastFetchAt] = useState(0);
  const [lastWsAt, setLastWsAt] = useState(0);

  const API_BASE =
    import.meta.env.VITE_API_BASE_URL ||
    `http://${window.location.hostname}:8000`;

  const WS_BASE = API_BASE.replace("http", "ws");

  // Use /api/v1 prefix for new backend, fall back to root for backward compat
  const API_PREFIX = `${API_BASE}/api/v1`;

  const fetchRobots = async () => {
    const response = await axios.get(`${API_PREFIX}/robots/status`);
    setRobots(Array.isArray(response.data) ? response.data : []);
  };

  const fetchMaintenance = async () => {
    const response = await axios.get(`${API_PREFIX}/robots/predictive-maintenance`);
    setMaintenance(Array.isArray(response.data) ? response.data : []);
  };

  const fetchAnalytics = async () => {
    const response = await axios.get(`${API_PREFIX}/analytics/fleet`);
    setAnalytics(response.data || {});
  };

  const refreshAll = useCallback(async () => {
    try {
      await Promise.all([fetchRobots(), fetchMaintenance(), fetchAnalytics()]);
      setLastFetchAt(Date.now());
      setError("");
    } catch (requestError) {
      console.error(requestError);
      setError("Backend is unreachable.");
    }
  }, [API_PREFIX]);

  const refreshAllEvent = useEffectEvent(async () => {
    await refreshAll();
  });

  // ── WebSocket + Polling ──────────────────────────────────────────

  const { isConnected: socketConnected, lastMessage } = useWebSocket(`${WS_BASE}/ws`);

  useEffect(() => {
    if (lastMessage) {
      setLastWsAt(Date.now());
      refreshAllEvent();
    }
    // refreshAllEvent is a useEffectEvent and should not be in the dependency array
  }, [lastMessage]);

  useEffect(() => {
    setTimeout(() => refreshAllEvent(), 0);
    const pollTimer = setInterval(() => refreshAllEvent(), 5000);

    return () => {
      clearInterval(pollTimer);
    };
  }, []);

  return {
    robots,
    maintenance,
    analytics,
    error,
    socketConnected,
    lastFetchAt,
    lastWsAt,
    refreshAll,
  };
}
