import { useEffect, useEffectEvent, useState, useCallback, useRef } from "react";
import axios from "axios";
import { useWebSocket } from "./useWebSocket";

/**
 * Custom hook that encapsulates ALL fleet data fetching logic:
 *  - REST polling (robots, analytics)
 *  - WebSocket connection management
 *  - Error state tracking
 *  - Connection timestamps
 *
 * Extracts ~200 lines of logic out of App.jsx.
 */
export default function useFleetData() {
  const [robots, setRobots] = useState([]);

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
  const [events, setEvents] = useState([]);

  const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
  const WS_BASE = import.meta.env.VITE_API_BASE_URL
    ? import.meta.env.VITE_API_BASE_URL.replace("http", "ws")
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;

  // Use /api/v1 prefix for new backend, fall back to root for backward compat
  const API_PREFIX = `${API_BASE}/api/v1`;

  const fetchRobots = async () => {
    // Ask for the whole fleet — the default page size used to clip it to 50.
    const response = await axios.get(`${API_PREFIX}/robots/status`, {
      params: { limit: 1000 },
    });
    // Replace (not merge) with the authoritative snapshot. This prunes robots
    // that have been retired or have gone stale, so counts can't drift upward.
    setRobots(Array.isArray(response.data) ? response.data : []);
  };

  const fetchAnalytics = async () => {
    const response = await axios.get(`${API_PREFIX}/analytics/fleet`);
    setAnalytics(response.data || {});
  };

  const refreshAll = useCallback(async () => {
    // Robots gate the first paint, so fetch them first and reveal the UI as
    // soon as they land. Analytics is heavier and must NOT block rendering —
    // fire it independently.
    try {
      await fetchRobots();
      setLastFetchAt(Date.now());
      setError("");
    } catch (requestError) {
      console.error(requestError);
      setError("Backend is unreachable.");
    }
    fetchAnalytics().catch(console.error);
  }, [API_PREFIX]);

  const refreshAllEvent = useEffectEvent(async () => {
    await refreshAll();
  });

  // ── WebSocket + Polling ──────────────────────────────────────────

  const pendingUpdates = useRef({ robots: {}, events: [] });
  const updateTimer = useRef(null);

  // Plain handler (not useEffectEvent): useWebSocket keeps it in a ref, so the
  // latest closure is always used and identity churn is harmless.
  const handleMessage = (lastMessage) => {
    if (lastMessage) {
      setLastWsAt(Date.now());
      
      try {
        if (lastMessage.type) {
          if (lastMessage.type.startsWith("COMMAND") || lastMessage.type === "EVENT") {
            pendingUpdates.current.events.unshift(lastMessage);
          }
        } else if (lastMessage.robot_id && lastMessage.battery !== undefined) {
          // Buffer the live telemetry update
          pendingUpdates.current.robots[lastMessage.robot_id] = lastMessage;
        }
      } catch {
        // ignore parse errors
      }

      if (!updateTimer.current) {
        updateTimer.current = setTimeout(() => {
          const updates = pendingUpdates.current;
          pendingUpdates.current = { robots: {}, events: [] };
          updateTimer.current = null;
          
          const robotIds = Object.keys(updates.robots);
          if (robotIds.length > 0) {
            setRobots(prevRobots => {
              const newRobots = [...prevRobots];
              for (const id of robotIds) {
                const msg = updates.robots[id];
                const index = newRobots.findIndex(r => String(r.robot_id) === String(msg.robot_id));
                if (index >= 0) {
                  newRobots[index] = { ...newRobots[index], ...msg };
                } else {
                  newRobots.push(msg);
                }
              }
              return newRobots;
            });
          }
          
          if (updates.events.length > 0) {
            setEvents(prev => [...updates.events, ...prev].slice(0, 50));
          }
        }, 100); // Throttle state updates to 10Hz
      }
    }
  };

  const { isConnected: socketConnected } = useWebSocket(`${WS_BASE}/ws`, handleMessage);



  useEffect(() => {
    setTimeout(() => refreshAllEvent(), 0);
    // Always re-sync the authoritative snapshot, even while the WebSocket is
    // connected. The WS only ever *adds/updates* robots, so without this poll
    // retired/dead robots would linger forever and inflate the fleet counts.
    // The REST snapshot replaces the list, pruning anything no longer present.
    const pollTimer = setInterval(() => {
      refreshAllEvent();
    }, 5000);

    return () => {
      clearInterval(pollTimer);
    };
  }, []);

  return {
    robots,

    analytics,
    isLoading: lastFetchAt === 0 && robots.length === 0 && !error,
    error,
    socketConnected,
    lastFetchAt,
    lastWsAt,
    events,
    refreshAll,
  };
}
