function FleetStats({ robots, maintenance }) {
  const totalRobots = robots.length;
  const activeRobots = robots.filter((robot) => robot.status === "ACTIVE").length;
  const lowPowerRobots = robots.filter((robot) => robot.status === "LOW POWER").length;
  const overheatingRobots = robots.filter((robot) => robot.status === "OVERHEATING").length;
  const offlineRobots = robots.filter((robot) => robot.status === "OFFLINE").length;
  const chargingRobots = robots.filter((robot) => robot.status === "CHARGING").length;
  const deadRobots = robots.filter((robot) => robot.status === "DEAD").length;

  const averageBattery =
    robots.length > 0
      ? (
          robots.reduce((sum, robot) => sum + robot.battery, 0) / robots.length
        ).toFixed(1)
      : 0;

  const criticalRiskRobots = maintenance.filter((item) => item.failure_risk >= 85).length;

  const stats = [
    { title: "Total Robots", value: totalRobots, color: "var(--accent-primary)" },
    { title: "Active", value: activeRobots, color: "var(--status-active)" },
    { title: "Charging", value: chargingRobots, color: "var(--status-charging)" },
    { title: "Low Power", value: lowPowerRobots, color: "var(--status-warning)" },
    { title: "Overheating", value: overheatingRobots, color: "var(--status-danger)" },
    { title: "Offline", value: offlineRobots, color: "var(--text-secondary)" },
    { title: "Dead", value: deadRobots, color: "var(--status-dead)" },
    { title: "Average Battery", value: `${averageBattery}%`, color: "var(--status-active)" },
    {
      title: "Critical Risk",
      value: criticalRiskRobots,
      color: criticalRiskRobots > 0 ? "var(--status-danger)" : "var(--status-active)"
    }
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16 }}>
      {stats.map((stat) => (
        <div key={stat.title} className="panel" style={{ padding: "16px 20px" }}>
          <div>
            <div className="stat-label" style={{ fontSize: "0.75rem", marginBottom: 8 }}>{stat.title}</div>
            <div className="stat-value" style={{ color: stat.color, fontSize: "1.5rem" }}>
              {stat.value}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export default FleetStats;
