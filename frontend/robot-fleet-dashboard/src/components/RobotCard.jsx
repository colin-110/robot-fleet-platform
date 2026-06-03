function RobotCard({ robot }) {
  const statusTone =
    robot.status === "OVERHEATING"
      ? "bad"
      : robot.status === "LOW POWER"
      ? "warn"
      : "good";

  const border =
    statusTone === "bad"
      ? "rgba(239, 68, 68, 0.45)"
      : statusTone === "warn"
      ? "rgba(245, 158, 11, 0.45)"
      : "rgba(34, 197, 94, 0.35)";

  const pillBg =
    statusTone === "bad"
      ? "rgba(239, 68, 68, 0.12)"
      : statusTone === "warn"
      ? "rgba(245, 158, 11, 0.12)"
      : "rgba(34, 197, 94, 0.12)";

  const pillText =
    statusTone === "bad"
      ? "hsl(var(--bad))"
      : statusTone === "warn"
      ? "hsl(var(--warn))"
      : "hsl(var(--good))";

  const batteryColor =
    robot.battery < 25
      ? "hsl(var(--warn))"
      : "hsl(var(--good))";

  const statusDot =
    statusTone === "bad"
      ? "dotBad"
      : statusTone === "warn"
      ? "dotWarn"
      : "dotGood";

  return (
    <div
      className="glassStrong sheen"
      style={{
        padding: 16,
        borderColor: border
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
            background: pillBg,
            borderColor: "rgba(148, 163, 184, 0.18)",
            color: pillText,
            fontWeight: 800
          }}
        >
          <span className={`dot ${statusDot}`} aria-hidden="true" />
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
            gridTemplateColumns: "1fr 1fr",
            gap: 10
          }}
        >
          <div className="glass" style={{ padding: 10 }}>
            <div className="subtle">Temperature</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>
              {robot.temperature}°C
            </div>
          </div>
          <div className="glass" style={{ padding: 10 }}>
            <div className="subtle">Speed</div>
            <div style={{ fontSize: 16, fontWeight: 800 }}>
              {robot.speed}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default RobotCard;
