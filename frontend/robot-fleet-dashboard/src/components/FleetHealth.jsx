function FleetHealth({ robots, maintenance }) {
  const maintenanceRisk =
    maintenance.length > 0
      ? maintenance.reduce((sum, item) => sum + item.failure_risk, 0) / maintenance.length
      : 0;

  const lowPowerCount = robots.filter((robot) => robot.status === "LOW POWER").length;
  const overheatingCount = robots.filter((robot) => robot.status === "OVERHEATING").length;
  const offlineCount = robots.filter((robot) => robot.status === "OFFLINE").length;
  const deadCount = robots.filter((robot) => robot.status === "DEAD").length;
  const chargingCount = robots.filter((robot) => robot.status === "CHARGING").length;

  const avgComponentHealth =
    robots.length > 0
      ? robots.reduce(
          (sum, robot) =>
            sum +
            (robot.battery_health +
              robot.motor_health +
              robot.sensor_health +
              robot.network_health) /
              4,
          0
        ) / robots.length
      : 100;

  let score = avgComponentHealth;
  score -= maintenanceRisk * 0.35;
  score -= lowPowerCount * 4;
  score -= overheatingCount * 10;
  score -= offlineCount * 4;
  score -= chargingCount * 2;
  score -= deadCount * 18;
  score = Math.max(0, Math.round(score));

  const color =
    score > 80 ? "hsl(var(--good))" : score > 50 ? "hsl(var(--warn))" : "hsl(var(--bad))";

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
    <div className="panel" style={{ padding: 16 }}>
      <div className="sectionTitle">
        <h2>Fleet health</h2>
        <span className="subtle">Composite status and component health</span>
      </div>

      <div style={{ display: "grid", gap: 14, justifyItems: "center" }}>
        <div style={ringStyle}>
          <div className="fleetRingInner">
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 44, fontWeight: 800, color }}>{score}%</div>
              <div className="subtle">overall</div>
            </div>
          </div>
        </div>

        <div className="subtle" style={{ textAlign: "center" }}>
          Score combines maintenance risk, component health, and live robot status.
        </div>
      </div>
    </div>
  );
}

export default FleetHealth;
