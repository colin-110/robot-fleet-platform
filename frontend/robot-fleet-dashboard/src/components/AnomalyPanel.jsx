function severityMeta(severity) {
  if (severity === "CRITICAL") {
    return { dot: "dotBad", color: "hsl(var(--bad))", label: "CRITICAL" };
  }
  if (severity === "HIGH") {
    return { dot: "dotWarn", color: "hsl(var(--warn))", label: "HIGH" };
  }
  if (severity === "MEDIUM") {
    return { dot: "dotWarn", color: "hsl(var(--warn))", label: "MEDIUM" };
  }
  return { dot: "dot", color: "hsl(var(--brand))", label: severity || "INFO" };
}

function AnomalyPanel({ anomalies }) {
  if (!anomalies || anomalies.length === 0) {
    return null;
  }

  return (
    <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
      <div className="sectionTitle">
        <h2>AI anomaly feed</h2>
        <span className="subtle">{anomalies.length} signals</span>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {anomalies.map((item, index) => {
          const meta = severityMeta(item.severity);
          const score =
            typeof item.anomaly_score === "number"
              ? Math.max(0, Math.min(1, item.anomaly_score))
              : null;
          return (
            <div
              key={`${item.robot_id}-${index}`}
              className="panel"
              style={{
                padding: 12,
                borderColor: "rgba(148, 163, 184, 0.16)"
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span className={`dot ${meta.dot}`} aria-hidden="true" />
                  <div>
                    <div style={{ fontWeight: 900 }}>
                      Robot R{item.robot_id} • {item.anomaly_type}
                    </div>
                    <div className="subtle">Severity: {meta.label}</div>
                  </div>
                </div>

                <span className="pill" style={{ color: meta.color }}>
                  {meta.label}
                </span>
              </div>

              {score !== null && (
                <div style={{ marginTop: 10 }}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "baseline",
                      marginBottom: 6
                    }}
                  >
                    <div className="subtle">Anomaly score</div>
                    <div style={{ fontWeight: 900, color: meta.color }}>
                      {(score * 100).toFixed(0)}%
                    </div>
                  </div>
                  <div
                    style={{
                      height: 10,
                      borderRadius: 999,
                      overflow: "hidden",
                      border: "1px solid rgba(148, 163, 184, 0.16)",
                      background: "rgba(148, 163, 184, 0.12)"
                    }}
                  >
                    <div
                      style={{
                        width: `${score * 100}%`,
                        height: "100%",
                        background: `linear-gradient(90deg, ${meta.color}, rgba(99, 102, 241, 0.55))`,
                        transition: "width 200ms ease"
                      }}
                    />
                  </div>
                </div>
              )}

              <div
                style={{
                  marginTop: 10,
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: 10
                }}
              >
                <div className="panel" style={{ padding: 10 }}>
                  <div className="subtle">Battery</div>
                  <div style={{ fontWeight: 900 }}>{item.battery}%</div>
                </div>
                <div className="panel" style={{ padding: 10 }}>
                  <div className="subtle">Temperature</div>
                  <div style={{ fontWeight: 900 }}>{item.temperature}°C</div>
                </div>
                <div className="panel" style={{ padding: 10 }}>
                  <div className="subtle">Speed</div>
                  <div style={{ fontWeight: 900 }}>{item.speed}</div>
                </div>
              </div>

              {(item.reason || item.recommended_action) && (
                <div style={{ marginTop: 10 }} className="panel">
                  <div style={{ padding: 10, display: "grid", gap: 6 }}>
                    {item.reason && (
                      <div className="subtle">
                        <span style={{ fontWeight: 900, color: "rgba(229,231,235,0.92)" }}>
                          Why:
                        </span>{" "}
                        {item.reason}
                      </div>
                    )}
                    {item.recommended_action && (
                      <div className="subtle">
                        <span style={{ fontWeight: 900, color: "rgba(229,231,235,0.92)" }}>
                          Action:
                        </span>{" "}
                        {item.recommended_action}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default AnomalyPanel;
