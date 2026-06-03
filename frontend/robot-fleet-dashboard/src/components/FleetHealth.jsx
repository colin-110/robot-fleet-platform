function FleetHealth({ robots, anomalies }) {

  let score = 100;

  score -= anomalies.length * 5;

  robots.forEach((robot) => {

    if (robot.status === "LOW POWER") {
      score -= 5;
    }

    if (robot.status === "OVERHEATING") {
      score -= 10;
    }
  });

  score = Math.max(0, score);

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
          {anomalies.length} anomaly{anomalies.length === 1 ? "" : "ies"}
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
          Score degrades with critical anomalies, overheating, and low power.
        </div>
      </div>
    </div>
  );
}

export default FleetHealth;
