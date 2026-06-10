function statusMeta(status) {
  if (status === "DEAD") {
    return {
      border: "rgba(0, 0, 0, 0.78)",
      pillBg: "rgba(0, 0, 0, 0.92)",
      pillText: "#ffffff",
      dot: "dotBlack"
    };
  }

  if (status === "OFFLINE") {
    return {
      border: "rgba(100, 116, 139, 0.45)",
      pillBg: "rgba(100, 116, 139, 0.16)",
      pillText: "#cbd5e1",
      dot: "dotGray"
    };
  }

  if (status === "OVERHEATING") {
    return {
      border: "rgba(239, 68, 68, 0.45)",
      pillBg: "rgba(239, 68, 68, 0.12)",
      pillText: "hsl(var(--bad))",
      dot: "dotBad"
    };
  }

  if (status === "LOW POWER") {
    return {
      border: "rgba(245, 158, 11, 0.45)",
      pillBg: "rgba(245, 158, 11, 0.12)",
      pillText: "hsl(var(--warn))",
      dot: "dotWarn"
    };
  }

  return {
    border: "rgba(34, 197, 94, 0.35)",
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

  const hours = minutes / 60;
  return `${hours.toFixed(1)} h`;
}

function formatLastSeen(lastSeen) {
  if (!lastSeen) {
    return "n/a";
  }

  const seenAt = new Date(lastSeen);

  if (Number.isNaN(seenAt.getTime())) {
    return "n/a";
  }

  const seconds = Math.max(
    0,
    Math.floor((Date.now() - seenAt.getTime()) / 1000)
  );

  if (seconds < 60) {
    return `${seconds}s ago`;
  }

  const minutes = Math.floor(seconds / 60);

  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  const hours = Math.floor(minutes / 60);

  return `${hours}h ago`;
}

function RobotCard({ robot }) {
  const meta = statusMeta(robot.status);

  const batteryColor =
    robot.battery < 25 ? "hsl(var(--warn))" : "hsl(var(--good))";

  const failureRisk =
    typeof robot.failure_risk === "number" ? robot.failure_risk : null;

  return (
    <div
      className="glassStrong sheen"
      style={{
        padding: 16,
        borderColor: meta.border
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          marginBottom: 12
        }}
      >
        <div>
          <div style={{ fontSize: 12, color: "rgba(148, 163, 184, 0.92)" }}>
            Robot
          </div>
          <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: -0.3 }}>
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

      <div style={{ display: "grid", gap: 10 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline"
          }}
        >
          <div className="subtle">Battery</div>
          <div style={{ fontWeight: 800, color: batteryColor }}>
            {robot.battery}%
          </div>
        </div>

        <div
          style={{
            height: 10,
            borderRadius: 999,
            background: "rgba(148, 163, 184, 0.14)",
            overflow: "hidden",
            border: "1px solid rgba(148, 163, 184, 0.16)"
          }}
        >
          <div
            style={{
              width: `${Math.max(0, Math.min(100, robot.battery))}%`,
              height: "100%",
              background: `linear-gradient(90deg, ${batteryColor}, rgba(99, 102, 241, 0.55))`,
              transition: "width 240ms ease"
            }}
          />
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap: 10
          }}
        >
          <div className="glass" style={{ padding: 10 }}>
            <div className="subtle">Temperature</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>
              {robot.temperature} C
            </div>
          </div>
          <div className="glass" style={{ padding: 10 }}>
            <div className="subtle">Speed</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>
              {robot.speed}
            </div>
          </div>
          <div className="glass" style={{ padding: 10 }}>
            <div className="subtle">Runtime Remaining</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>
              {formatRuntime(robot.runtime_remaining_minutes)}
            </div>
          </div>
          <div className="glass" style={{ padding: 10 }}>
            <div className="subtle">Failure Risk</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>
              {failureRisk === null ? "n/a" : `${failureRisk}%`}
            </div>
          </div>
        </div>

        <div className="glass" style={{ padding: 10 }}>
          <div className="subtle">Last Seen</div>
          <div style={{ fontSize: 15, fontWeight: 800 }}>
            {formatLastSeen(robot.last_seen)}
          </div>
        </div>
      </div>
    </div>
  );
}

export default RobotCard;
