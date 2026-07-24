function FleetStats({ robots }) {
  const count = (s) => robots.filter((r) => r.status === s).length;

  const averageBattery =
    robots.length > 0
      ? (robots.reduce((sum, r) => sum + r.battery, 0) / robots.length).toFixed(1)
      : "0.0";

  const stats = [
    { title: "Total Robots", value: robots.length, color: "#4f8cff", hint: "in fleet" },
    { title: "Active", value: count("ACTIVE"), color: "#3fb950", hint: "on mission" },
    { title: "Charging", value: count("CHARGING"), color: "#58a6ff", hint: "at base" },
    { title: "Low Power", value: count("LOW POWER"), color: "#d29922", hint: "below 30%" },
    { title: "Overheating", value: count("OVERHEATING"), color: "#f85149", hint: "thermal" },
    { title: "Offline", value: count("OFFLINE"), color: "#8b94a7", hint: "no signal" },
    { title: "Dead", value: count("DEAD"), color: "#6e7681", hint: "needs service" },
    { title: "Avg Battery", value: `${averageBattery}%`, color: "#3fb950", hint: "fleet mean" },
  ];

  return (
    <div className="statGrid">
      {stats.map((s) => (
        <div key={s.title} className="glass statCard">
          <div className="statCard__top">
            <span className="statCard__dot" style={{ background: s.color }} />
            <span className="statCard__label">{s.title}</span>
          </div>
          <div className="statCard__value" style={{ color: s.color }}>{s.value}</div>
          <div className="statCard__hint">{s.hint}</div>
        </div>
      ))}
    </div>
  );
}

export default FleetStats;
