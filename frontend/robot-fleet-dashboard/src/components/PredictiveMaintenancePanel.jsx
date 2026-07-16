import { getRiskMeta, formatRuntime } from "../utils/constants";

function PredictiveMaintenancePanel({ maintenance }) {
  if (!maintenance || maintenance.length === 0) return null;

  return (
    <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
      <div className="sectionTitle">
        <h2>Predictive maintenance</h2>
        <span className="subtle">{maintenance.length} robots scored</span>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {maintenance.map((item) => {
          const meta = getRiskMeta(item.risk_level);

          return (
            <div
              key={item.robot_id}
              className="panel"
              style={{ padding: 12, borderColor: meta.border }}
            >
              <div className="maintenanceRow">
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span className={`dot ${meta.dot}`} aria-hidden="true" />
                  <div>
                    <div style={{ fontWeight: 900 }}>Robot R{item.robot_id}</div>
                    <div className="subtle">Risk level: {item.risk_level}</div>
                  </div>
                </div>

                <div className="pill" style={{ color: meta.color }}>
                  <span className="subtle">Failure Risk</span>
                  <span style={{ opacity: 0.7 }}>|</span>
                  <span style={{ fontWeight: 900 }}>{item.failure_risk}%</span>
                </div>
              </div>

              <div className="maintenanceMetrics">
                <div className="panel metricBox">
                  <div className="subtle">Runtime Remaining</div>
                  <div className="metricValue">{formatRuntime(item.runtime_remaining_minutes)}</div>
                </div>
                <div className="panel metricBox">
                  <div className="subtle">Battery Health</div>
                  <div className="metricValue">{item.battery_health}%</div>
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
