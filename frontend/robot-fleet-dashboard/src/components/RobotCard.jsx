function statusMeta(status) {
  if (status === "DEAD") {
    return {
      border: "rgba(0, 0, 0, 0.82)",
      pillBg: "rgba(0, 0, 0, 0.92)",
      pillText: "#ffffff",
      dot: "dotBlack"
    };
  }

  if (status === "OFFLINE") {
    return {
      border: "rgba(100, 116, 139, 0.52)",
      pillBg: "rgba(100, 116, 139, 0.16)",
      pillText: "#cbd5e1",
      dot: "dotGray"
    };
  }

  if (status === "OVERHEATING") {
    return {
      border: "rgba(239, 68, 68, 0.52)",
      pillBg: "rgba(239, 68, 68, 0.14)",
      pillText: "hsl(var(--bad))",
      dot: "dotBad"
    };
  }

  if (status === "LOW POWER") {
    return {
      border: "rgba(245, 158, 11, 0.52)",
      pillBg: "rgba(245, 158, 11, 0.14)",
      pillText: "hsl(var(--warn))",
      dot: "dotWarn"
    };
  }

  if (status === "CHARGING") {
    return {
      border: "rgba(56, 189, 248, 0.52)",
      pillBg: "rgba(56, 189, 248, 0.14)",
      pillText: "hsl(var(--info))",
      dot: "dotInfo"
    };
  }

  return {
    border: "rgba(34, 197, 94, 0.42)",
    pillBg: "rgba(34, 197, 94, 0.12)",
    pillText: "hsl(var(--good))",
    dot: "dotGood"
  };
}

function formatRuntime(minutes) {
  if (minutes === null || minutes === undefined) {
    return "n/a";
  }

  if (minutes < 60) {
    return `${minutes.toFixed(1)} min`;
  }

  return `${(minutes / 60).toFixed(1)} h`;
}

function formatLastSeen(lastSeen) {
  if (!lastSeen) {
    return "n/a";
  }

  const seenAt = new Date(lastSeen);
  if (Number.isNaN(seenAt.getTime())) {
    return "n/a";
  }

  const seconds = Math.max(0, Math.floor((Date.now() - seenAt.getTime()) / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  return `${Math.floor(minutes / 60)}h ago`;
}

function healthTone(value) {
  if (value <= 60) return "hsl(var(--bad))";
  if (value <= 80) return "hsl(var(--warn))";
  return "hsl(var(--good))";
}

function formatMission(robot) {
  if (!robot.mission_id || !robot.mission_type) {
    return "Idle";
  }

  const progress =
    typeof robot.mission_progress === "number"
      ? `${robot.mission_progress.toFixed(0)}%`
      : "in progress";

  return `${robot.mission_type} ${progress}`;
}

function RobotCard({ robot }) {
  const meta = statusMeta(robot.status);
  const failureRisk =
    typeof robot.failure_risk === "number" ? `${robot.failure_risk}%` : "n/a";
  const healthCards = [
    { label: "Battery Health", value: robot.battery_health },
    { label: "Motor Health", value: robot.motor_health },
    { label: "Sensor Health", value: robot.sensor_health },
    { label: "Network Health", value: robot.network_health }
  ];

  return (
    <div
      className="glassStrong sheen"
      style={{
        padding: 16,
        borderColor: meta.border
      }}
    >
      <div className="robotHeader">
        <div>
          <div className="subtle">Robot</div>
          <div style={{ fontSize: 20, fontWeight: 900, letterSpacing: -0.4 }}>
            R{robot.robot_id}
          </div>
        </div>

        <span
          className="pill"
          style={{
            background: meta.pillBg,
            borderColor: "rgba(148, 163, 184, 0.18)",
            color: meta.pillText,
            fontWeight: 800
          }}
        >
          <span className={`dot ${meta.dot}`} aria-hidden="true" />
          {robot.status}
        </span>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <div className="subtle">Battery</div>
            <div style={{ fontWeight: 900, color: healthTone(robot.battery) }}>
              {robot.battery}%
            </div>
          </div>
          <div className="progressTrack">
            <div
              className="progressFill"
              style={{
                width: `${Math.max(0, Math.min(100, robot.battery))}%`,
                background: `linear-gradient(90deg, ${healthTone(robot.battery)}, rgba(33, 211, 146, 0.35))`
              }}
            />
          </div>
        </div>

        <div className="robotMetricGrid">
          <div className="glass metricBox">
            <div className="subtle">Temperature</div>
            <div className="metricValue">{robot.temperature} C</div>
          </div>
          <div className="glass metricBox">
            <div className="subtle">Speed</div>
            <div className="metricValue">{robot.speed} m/s</div>
          </div>
          <div className="glass metricBox">
            <div className="subtle">Runtime Remaining</div>
            <div className="metricValue">{formatRuntime(robot.runtime_remaining_minutes)}</div>
          </div>
          <div className="glass metricBox">
            <div className="subtle">Failure Risk</div>
            <div className="metricValue">{failureRisk}</div>
          </div>
        </div>

        <div className="glass metricBox">
          <div className="subtle">Mission Progress</div>
          <div className="metricValue">{formatMission(robot)}</div>
          {robot.mission_id && (
            <div className="subtle" style={{ marginTop: 4 }}>
              {robot.mission_id}
            </div>
          )}
        </div>

        <div className="glass metricBox">
          <div className="subtle">Last Seen</div>
          <div className="metricValue">{formatLastSeen(robot.last_seen)}</div>
        </div>

        <div className="componentGrid">
          {healthCards.map((item) => (
            <div key={item.label} className="glass componentCard">
              <div className="subtle">{item.label}</div>
              <div className="metricValue" style={{ color: healthTone(item.value) }}>
                {item.value}%
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default RobotCard;
