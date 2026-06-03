function FleetStats({ robots, anomalies }) {

  const totalRobots = robots.length;

  const activeRobots = robots.filter(
    (robot) => robot.status === "ACTIVE"
  ).length;

  const lowPowerRobots = robots.filter(
    (robot) => robot.status === "LOW POWER"
  ).length;

  const overheatingRobots = robots.filter(
    (robot) => robot.status === "OVERHEATING"
  ).length;

  const averageBattery =
    robots.length > 0
      ? (
          robots.reduce(
            (sum, robot) =>
              sum + robot.battery,
            0
          ) / robots.length
        ).toFixed(1)
      : 0;

  const criticalAnomalies = anomalies.filter(
    (anomaly) =>
      anomaly.severity === "CRITICAL"
  ).length;

  const stats = [

    {
      title: "Total Robots",
      value: totalRobots,
      tone: "brand"
    },

    {
      title: "Active Robots",
      value: activeRobots,
      tone: "good"
    },

    {
      title: "Low Power",
      value: lowPowerRobots,
      tone: "warn"
    },

    {
      title: "Overheating",
      value: overheatingRobots,
      tone: "bad"
    },

    {
      title: "Average Battery",
      value: `${averageBattery}%`,
      tone: "good"
    },

    {
      title: "Critical AI Alerts",
      value: criticalAnomalies,
      tone: criticalAnomalies > 0 ? "bad" : "good"
    }
  ];

  return (
    <div className="kpiGrid">
      {stats.map((stat) => {
        const hue =
          stat.tone === "good"
            ? "var(--good)"
            : stat.tone === "warn"
            ? "var(--warn)"
            : stat.tone === "bad"
            ? "var(--bad)"
            : "var(--brand)";

        return (
          <div key={stat.title} className="glass kpi sheen">
            <div className="kpiInner">
              <div className="kpiLabel">{stat.title}</div>
              <div
                className="kpiValue"
                style={{ color: `hsl(${hue})` }}
              >
                {stat.value}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default FleetStats;
