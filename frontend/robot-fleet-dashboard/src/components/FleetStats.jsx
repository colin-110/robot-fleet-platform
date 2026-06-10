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
    { title: "Total Robots", value: totalRobots, color: "hsl(var(--brand))" },
    { title: "Active", value: activeRobots, color: "hsl(var(--good))" },
    { title: "Charging", value: chargingRobots, color: "hsl(var(--info))" },
    { title: "Low Power", value: lowPowerRobots, color: "hsl(var(--warn))" },
    { title: "Overheating", value: overheatingRobots, color: "hsl(var(--bad))" },
    { title: "Offline", value: offlineRobots, color: "#94a3b8" },
    { title: "Dead", value: deadRobots, color: "#020617" },
    { title: "Average Battery", value: `${averageBattery}%`, color: "hsl(var(--good))" },
    {
      title: "Critical Risk",
      value: criticalRiskRobots,
      color: criticalRiskRobots > 0 ? "hsl(var(--bad))" : "hsl(var(--good))"
    }
  ];

  return (
    <div className="kpiGrid">
      {stats.map((stat) => (
        <div key={stat.title} className="glass kpi sheen">
          <div className="kpiInner">
            <div className="kpiLabel">{stat.title}</div>
            <div className="kpiValue" style={{ color: stat.color }}>
              {stat.value}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export default FleetStats;
