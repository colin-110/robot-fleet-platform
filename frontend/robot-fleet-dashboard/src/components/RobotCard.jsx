import { memo, useState } from "react";
import axios from "axios";
import { getStatusMeta, healthTone, formatRuntime, formatLastSeen } from "../utils/constants";

const API_KEY = import.meta.env.VITE_WS_API_KEY || "";

function formatMission(robot) {
  if (!robot.mission_id || !robot.mission_type) return "Idle";
  const progress =
    typeof robot.mission_progress === "number"
      ? `${robot.mission_progress.toFixed(0)}%`
      : "in progress";
  return `${robot.mission_type} · ${progress}`;
}

function HealthCell({ label, value }) {
  const pct = typeof value === "number" ? Math.max(0, Math.min(100, value)) : 0;
  const color = healthTone(value);
  return (
    <div className="healthCell">
      <div className="k">{label}</div>
      <div className="v" style={{ color }}>{typeof value === "number" ? `${value.toFixed(0)}` : "—"}</div>
      <div className="miniBar"><span style={{ width: `${pct}%`, background: color }} /></div>
    </div>
  );
}

function RobotCard({ robot }) {
  const meta = getStatusMeta(robot.status);
  const [loadingAction, setLoadingAction] = useState("");

  const handleCommand = async (action) => {
    const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
    setLoadingAction(action);
    try {
      await axios.post(
        `${API_BASE}/api/v1/commands/${robot.robot_id}`,
        { command_type: action },
        { headers: { "X-API-Key": API_KEY } },
      );
    } catch (err) {
      console.error("Failed to send command", err);
    } finally {
      setLoadingAction("");
    }
  };

  const batteryPct = Math.max(0, Math.min(100, robot.battery));

  return (
    <div className="glass robotCard">
      <div className="robotCard__head">
        <div>
          <div className="robotCard__idLabel">Unit</div>
          <div className="robotCard__id">R{robot.robot_id}</div>
        </div>
        <span
          className="badge"
          style={{ background: meta.pillBg, borderColor: meta.border, color: meta.pillText }}
        >
          {robot.status}
        </span>
      </div>

      {/* Battery */}
      <div>
        <div className="rowLabel">
          <span className="k">Battery</span>
          <span className="v" style={{ color: healthTone(robot.battery) }}>{robot.battery}%</span>
        </div>
        <div className="progress-bar-container">
          <div className="progress-bar-fill" style={{ width: `${batteryPct}%`, background: healthTone(robot.battery) }} />
        </div>
      </div>

      {/* Live metrics */}
      <div className="metricRow">
        <div className="metricTile"><div className="k">Temp</div><div className="v">{robot.temperature}°C</div></div>
        <div className="metricTile"><div className="k">Speed</div><div className="v">{robot.speed} m/s</div></div>
        <div className="metricTile"><div className="k">Runtime</div><div className="v">{formatRuntime(robot.runtime_remaining_minutes)}</div></div>
      </div>

      {/* Mission + last seen */}
      <div className="metricRow" style={{ gridTemplateColumns: "1.6fr 1fr" }}>
        <div className="metricTile wide">
          <div className="k">Mission</div>
          <div className="v">{formatMission(robot)}</div>
          {robot.mission_id && <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 2 }}>{robot.mission_id}</div>}
        </div>
        <div className="metricTile wide" style={{ textAlign: "right" }}>
          <div className="k">Last seen</div>
          <div className="v">{formatLastSeen(robot.last_seen)}</div>
        </div>
      </div>

      {/* Component health */}
      <div className="healthRow">
        <HealthCell label="Battery" value={robot.battery_health} />
        <HealthCell label="Motor" value={robot.motor_health} />
        <HealthCell label="Sensor" value={robot.sensor_health} />
        <HealthCell label="Network" value={robot.network_health} />
      </div>

      {/* Commands */}
      <div className="robotActions">
        <button className="btn" disabled={loadingAction === "RETURN_TO_BASE"} onClick={() => handleCommand("RETURN_TO_BASE")}>
          {loadingAction === "RETURN_TO_BASE" ? "Sending…" : "Return to Base"}
        </button>
        {robot.status === "STOPPED" ? (
          <button className="btn" style={{ borderColor: "rgba(63,185,80,0.45)", color: "#3fb950" }} disabled={loadingAction === "RESUME"} onClick={() => handleCommand("RESUME")}>
            {loadingAction === "RESUME" ? "Sending…" : "Resume"}
          </button>
        ) : (
          <button className="btn" style={{ borderColor: "rgba(248,81,73,0.45)", color: "#f85149" }} disabled={loadingAction === "EMERGENCY_STOP"} onClick={() => handleCommand("EMERGENCY_STOP")}>
            {loadingAction === "EMERGENCY_STOP" ? "Sending…" : "Emergency Stop"}
          </button>
        )}
      </div>
    </div>
  );
}

export default memo(RobotCard);
