import { useState } from "react";
import axios from "axios";
import { getTicket } from "../utils/ticket";
import { authHeaders } from "../utils/auth";
import { mergeAlarm } from "../utils/alarms";

const SEVERITY_COLOR = { critical: "var(--c-bad)", warning: "var(--c-warn)" };

function formatTime(iso) {
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? ""
    : at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * Open alarms, most urgent first. Acknowledging says someone is on it; the
 * alarm stays listed until its condition clears by itself.
 */
export default function AlarmsPanel({ alarms, onChange }) {
  const [busy, setBusy] = useState(null);

  const acknowledge = async (alarm) => {
    const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
    setBusy(alarm.id);
    try {
      const ticket = await getTicket();
      const response = await axios.post(
        `${API_BASE}/api/v1/alarms/${alarm.id}/ack`,
        null,
        { headers: { "X-Console-Ticket": ticket, ...authHeaders() } },
      );
      onChange((current) => mergeAlarm(current, response.data));
    } catch (err) {
      console.error("Failed to acknowledge alarm", err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="glassStrong alarmsPanel">
      <div className="panelHead">
        <h2>Alarms</h2>
        <span className="subtle">{alarms.length ? `${alarms.length} open` : "all clear"}</span>
      </div>
      {alarms.length === 0 ? (
        <p className="subtle alarmsPanel__empty">No open alarms.</p>
      ) : (
        <ul className="alarmsPanel__list">
          {alarms.map((alarm) => (
            <li key={alarm.id} className="alarmRow" data-state={alarm.state}>
              <span
                className="alarmRow__accent"
                style={{ background: SEVERITY_COLOR[alarm.severity] || "var(--accent)" }}
              />
              <div className="alarmRow__body">
                <div className="alarmRow__msg">
                  R{alarm.robot_id} · {alarm.message}
                </div>
                <div className="alarmRow__meta">
                  <span>{alarm.kind}</span>
                  <span aria-hidden="true">·</span>
                  <span>{formatTime(alarm.raised_at)}</span>
                  {alarm.state === "ACKNOWLEDGED" && (
                    <span>· acknowledged by {alarm.acknowledged_by}</span>
                  )}
                </div>
              </div>
              {alarm.state === "ACTIVE" && (
                <button
                  className="btn"
                  disabled={busy === alarm.id}
                  onClick={() => acknowledge(alarm)}
                >
                  {busy === alarm.id ? "…" : "Acknowledge"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
