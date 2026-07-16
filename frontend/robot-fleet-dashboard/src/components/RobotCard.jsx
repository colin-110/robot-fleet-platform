import { memo } from "react";
import { getStatusMeta, healthTone, formatRuntime, formatLastSeen } from "../utils/constants";

function formatMission(robot) {
  if (!robot.mission_id || !robot.mission_type) return "Idle";
  const progress =
    typeof robot.mission_progress === "number"
      ? `${robot.mission_progress.toFixed(0)}%`
      : "in progress";
  return `${robot.mission_type} ${progress}`;
}

function RobotCard({ robot }) {
  const meta = getStatusMeta(robot.status);
  const failureRisk =
    typeof robot.failure_risk === "number" ? `${robot.failure_risk}%` : "n/a";
  const healthCards = [
    { label: "Battery Health", value: robot.battery_health },
    { label: "Motor Health", value: robot.motor_health },
    { label: "Sensor Health", value: robot.sensor_health },
    { label: "Network Health", value: robot.network_health },
  ];

  return (
    <div
      className="panel"
      style={{ padding: 24, borderColor: meta.border, height: '100%', display: 'flex', flexDirection: 'column' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div className="stat-label">Robot</div>
          <div className="stat-value">
            R{robot.robot_id}
          </div>
        </div>

        <span
          className="badge"
          style={{
            background: meta.pillBg,
            borderColor: meta.border,
            color: meta.pillText,
          }}
        >
          {robot.status}
        </span>
      </div>

      <div style={{ display: "grid", gap: 16, flex: 1 }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <div className="stat-label">Battery</div>
            <div style={{ fontWeight: 700, color: healthTone(robot.battery) }}>
              {robot.battery}%
            </div>
          </div>
          <div className="progress-bar-container">
            <div
              className="progress-bar-fill"
              style={{
                width: `${Math.max(0, Math.min(100, robot.battery))}%`,
                background: healthTone(robot.battery),
              }}
            />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div className="panel" style={{ padding: 12 }}>
            <div className="stat-label" style={{ fontSize: '0.7rem' }}>Temperature</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text-primary)' }}>{robot.temperature} C</div>
          </div>
          <div className="panel" style={{ padding: 12 }}>
            <div className="stat-label" style={{ fontSize: '0.7rem' }}>Speed</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text-primary)' }}>{robot.speed} m/s</div>
          </div>
          <div className="panel" style={{ padding: 12 }}>
            <div className="stat-label" style={{ fontSize: '0.7rem' }}>Runtime</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text-primary)' }}>{formatRuntime(robot.runtime_remaining_minutes)}</div>
          </div>
          <div className="panel" style={{ padding: 12 }}>
            <div className="stat-label" style={{ fontSize: '0.7rem' }}>Failure Risk</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text-primary)' }}>{failureRisk}</div>
          </div>
        </div>

        <div className="panel" style={{ padding: 16 }}>
          <div className="stat-label">Mission Progress</div>
          <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text-primary)' }}>{formatMission(robot)}</div>
          {robot.mission_id && (
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', marginTop: 4 }}>
              {robot.mission_id}
            </div>
          )}
        </div>

        <div className="panel" style={{ padding: 16 }}>
          <div className="stat-label">Last Seen</div>
          <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text-primary)' }}>{formatLastSeen(robot.last_seen)}</div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 'auto' }}>
          {healthCards.map((item) => (
            <div key={item.label} className="panel" style={{ padding: 12 }}>
              <div className="stat-label" style={{ fontSize: '0.7rem' }}>{item.label}</div>
              <div style={{ fontWeight: 600, marginTop: 4, color: healthTone(item.value) }}>
                {item.value}%
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default memo(RobotCard);
