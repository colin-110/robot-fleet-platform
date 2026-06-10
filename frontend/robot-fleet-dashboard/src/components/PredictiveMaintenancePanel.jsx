function riskTone(level) {
  if (level === "CRITICAL") {
    return {
      dot: "dotBad",
      color: "hsl(var(--bad))",
      border: "rgba(239, 68, 68, 0.25)"
    };
  }

  if (level === "HIGH") {
    return {
      dot: "dotBad",
      color: "hsl(var(--bad))",
      border: "rgba(239, 68, 68, 0.18)"
    };
  }

  if (level === "MEDIUM") {
    return {
      dot: "dotWarn",
      color: "hsl(var(--warn))",
      border: "rgba(245, 158, 11, 0.18)"
    };
  }

  return {
    dot: "dotGood",
    color: "hsl(var(--good))",
    border: "rgba(34, 197, 94, 0.18)"
  };
}

function formatRuntime(minutes) {
  if (minutes === null || minutes === undefined) {
    return "n/a";
  }

  if (minutes < 60) {
    return `${minutes.toFixed(1)} min`;
  }

  return `${(minutes / 60).toFixed(1)} h`;
}

function PredictiveMaintenancePanel({ maintenance }) {
  if (!maintenance || maintenance.length === 0) {
    return null;
  }

  return (
    <div className="glassStrong sheen" style={{ padding: 16, marginBottom: 16 }}>
      <div className="sectionTitle">
        <h2>Predictive maintenance</h2>
        <span className="subtle">{maintenance.length} robots scored</span>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {maintenance.map((item) => {
          const meta = riskTone(item.risk_level);

          return (
            <div
              key={item.robot_id}
              className="glass"
              style={{
                padding: 12,
                borderColor: meta.border
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: 12
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span className={`dot ${meta.dot}`} aria-hidden="true" />
                  <div>
                    <div style={{ fontWeight: 900 }}>Robot R{item.robot_id}</div>
                    <div className="subtle">
                      Risk level: {item.risk_level}
                    </div>
                  </div>
                </div>

                <div className="pill" style={{ color: meta.color }}>
                  <span className="subtle">Risk</span>
                  <span style={{ opacity: 0.7 }}>|</span>
                  <span style={{ fontWeight: 900 }}>{item.failure_risk}%</span>
                </div>
              </div>

              <div
                style={{
                  marginTop: 10,
                  display: "grid",
                  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  gap: 10
                }}
              >
                <div className="glass" style={{ padding: 10 }}>
                  <div className="subtle">Runtime Remaining</div>
                  <div style={{ fontWeight: 900 }}>
                    {formatRuntime(item.runtime_remaining_minutes)}
                  </div>
                </div>
                <div className="glass" style={{ padding: 10 }}>
                  <div className="subtle">Health Score</div>
                  <div style={{ fontWeight: 900 }}>{item.health_score}%</div>
                </div>
              </div>

              <div style={{ marginTop: 10 }}>
                <div className="subtle" style={{ marginBottom: 8 }}>
                  Reasons
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {(item.reasons || []).map((reason) => (
                    <span key={reason} className="pill">
                      {reason}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default PredictiveMaintenancePanel;
