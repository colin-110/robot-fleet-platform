function AlertsPanel({ robots }) {
  const alerts = robots.filter(
    (robot) => robot.status === "LOW POWER" || robot.status === "OVERHEATING"
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
          const isHot = robot.status === "OVERHEATING";
          const tone = isHot ? "dotBad" : "dotWarn";
          const accent = isHot ? "hsl(var(--bad))" : "hsl(var(--warn))";

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
                borderColor: isHot
                  ? "rgba(239, 68, 68, 0.25)"
                  : "rgba(245, 158, 11, 0.25)"
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className={`dot ${tone}`} aria-hidden="true" />
                <div>
                  <div style={{ fontWeight: 800 }}>Robot R{robot.robot_id}</div>
                  <div className="subtle">{robot.status}</div>
                </div>
              </div>

              <div className="pill" style={{ borderColor: "rgba(148, 163, 184, 0.18)" }}>
                <span className="subtle">Battery</span>
                <span style={{ opacity: 0.7 }}>•</span>
                <span style={{ color: accent, fontWeight: 800 }}>
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
