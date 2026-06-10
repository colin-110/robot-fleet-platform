function FleetHealth({ robots, maintenance }) {
  const maintenanceRisk =
    maintenance.length > 0
      ? maintenance.reduce((sum, item) => sum + item.failure_risk, 0) /
        maintenance.length
      : 0;

  const lowPowerCount = robots.filter(
    (robot) => robot.status === "LOW POWER"
  ).length;

  const overheatingCount = robots.filter(
    (robot) => robot.status === "OVERHEATING"
  ).length;

  const offlineCount = robots.filter((robot) => robot.status === "OFFLINE").length;
  const deadCount = robots.filter((robot) => robot.status === "DEAD").length;
  const criticalRiskCount = maintenance.filter(
    (item) => item.failure_risk >= 85
  ).length;

  let score = 100;
  score -= maintenanceRisk * 0.5;
  score -= lowPowerCount * 5;
  score -= overheatingCount * 10;
  score -= offlineCount * 4;
  score -= deadCount * 18;
  score -= criticalRiskCount * 4;
  score = Math.max(0, Math.round(score));

  const color =
    score > 80
      ? "hsl(var(--good))"
      : score > 50
      ? "hsl(var(--warn))"
      : "hsl(var(--bad))";

  const ringStyle = {
    width: 180,
    height: 180,
    borderRadius: 999,
    background: `conic-gradient(${color} ${score * 3.6}deg, rgba(148, 163, 184, 0.12) 0deg)`,
    display: "grid",
    placeItems: "center",
    border: "1px solid rgba(148, 163, 184, 0.18)"
  };

  return (
    <div className="glassStrong sheen" style={{ padding: 16 }}>
      <div className="sectionTitle">
        <h2>Fleet health</h2>
        <span className="subtle">
          {criticalRiskCount} high-risk robot{criticalRiskCount === 1 ? "" : "s"}
        </span>
      </div>

      <div style={{ display: "grid", gap: 14, justifyItems: "center" }}>
        <div style={ringStyle}>
          <div
            style={{
              width: 148,
              height: 148,
              borderRadius: 999,
              background: "rgba(3, 7, 18, 0.55)",
              border: "1px solid rgba(148, 163, 184, 0.14)",
              display: "grid",
              placeItems: "center",
              boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.04)"
            }}
          >
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 44, fontWeight: 800, color }}>{score}%</div>
              <div className="subtle">overall</div>
            </div>
          </div>
        </div>

        <div className="subtle" style={{ textAlign: "center" }}>
          Score degrades with predictive risk, overheating, low power, and offline robots.
        </div>
      </div>
    </div>
  );
}

export default FleetHealth;
