import { getStatusMeta, ALERT_STATUSES } from "../utils/constants";

function AlertsPanel({ robots }) {
  const alerts = robots.filter((robot) => ALERT_STATUSES.includes(robot.status));

  if (alerts.length === 0) return null;

  return (
    <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
      <div className="sectionTitle">
        <h2>Active alerts</h2>
        <span className="subtle">{alerts.length} affected</span>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {alerts.map((robot) => {
          const meta = getStatusMeta(robot.status);

          return (
            <div
              key={robot.robot_id}
              className="panel"
              style={{
                padding: 12,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                borderColor: meta.border,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className={`dot ${meta.dot}`} aria-hidden="true" />
                <div>
                  <div style={{ fontWeight: 800 }}>Robot R{robot.robot_id}</div>
                  <div className="subtle">{robot.status}</div>
                </div>
              </div>

              <div className="pill" style={{ borderColor: "rgba(148, 163, 184, 0.18)" }}>
                <span className="subtle">Mission</span>
                <span style={{ opacity: 0.7 }}>|</span>
                <span style={{ color: meta.alertAccent, fontWeight: 800 }}>
                  {robot.mission_type || "Idle"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default AlertsPanel;
