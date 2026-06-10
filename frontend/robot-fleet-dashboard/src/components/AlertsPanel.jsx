function alertMeta(status) {
  if (status === "DEAD") {
    return {
      dot: "dotBlack",
      border: "rgba(0, 0, 0, 0.5)",
      accent: "#0f172a"
    };
  }

  if (status === "OFFLINE") {
    return {
      dot: "dotGray",
      border: "rgba(100, 116, 139, 0.35)",
      accent: "#94a3b8"
    };
  }

  if (status === "OVERHEATING") {
    return {
      dot: "dotBad",
      border: "rgba(239, 68, 68, 0.25)",
      accent: "hsl(var(--bad))"
    };
  }

  return {
    dot: "dotWarn",
    border: "rgba(245, 158, 11, 0.25)",
    accent: "hsl(var(--warn))"
  };
}

function AlertsPanel({ robots }) {
  const alerts = robots.filter((robot) =>
    ["LOW POWER", "OVERHEATING", "OFFLINE", "DEAD"].includes(robot.status)
  );

  if (alerts.length === 0) {
    return null;
  }

  return (
    <div className="glassStrong sheen" style={{ padding: 16, marginBottom: 16 }}>
      <div className="sectionTitle">
        <h2>Active alerts</h2>
        <span className="subtle">{alerts.length} affected</span>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {alerts.map((robot) => {
          const meta = alertMeta(robot.status);

          return (
            <div
              key={robot.robot_id}
              className="glass"
              style={{
                padding: 12,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                borderColor: meta.border
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className={`dot ${meta.dot}`} aria-hidden="true" />
                <div>
                  <div style={{ fontWeight: 800 }}>Robot R{robot.robot_id}</div>
                  <div className="subtle">{robot.status}</div>
                </div>
              </div>

              <div className="pill" style={{ borderColor: "rgba(148, 163, 184, 0.18)" }}>
                <span className="subtle">Battery</span>
                <span style={{ opacity: 0.7 }}>|</span>
                <span style={{ color: meta.accent, fontWeight: 800 }}>
                  {robot.battery}%
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default AlertsPanel;
