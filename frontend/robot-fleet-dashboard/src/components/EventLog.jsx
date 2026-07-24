export default function EventLog({ events }) {
  return (
    <div className="glassStrong" style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="panelHead">
        <h2>Live Event Log</h2>
        {events && events.length > 0 && (
          <span className="badge" style={{ background: "var(--accent-weak)", color: "#9dbdff", borderColor: "rgba(79,140,255,0.25)" }}>
            {events.length}
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 12, minHeight: 0 }}>
        {events && events.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {events.map((evt, idx) => {
              const time = new Date(evt.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
              const msg = evt.type === "COMMAND" ? `Command: ${evt.action}` : (evt.message || "");
              const accent = /restricted|zone/i.test(msg) ? "#d29922"
                : /completed/i.test(msg) ? "#3fb950"
                : evt.type && evt.type.startsWith("COMMAND") ? "#58a6ff"
                : "var(--accent)";
              return (
                <div
                  key={`${evt.robot_id}-${evt.timestamp}-${idx}`}
                  style={{
                    display: "flex",
                    gap: 11,
                    padding: "10px 12px",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--bg-2)",
                    border: "1px solid var(--line)",
                  }}
                >
                  <span style={{ width: 3, borderRadius: 3, background: accent, flexShrink: 0 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 13, color: "var(--text)", fontWeight: 500, lineHeight: 1.35 }}>
                      {msg}
                    </div>
                    <div style={{ display: "flex", gap: 8, marginTop: 3, fontSize: 11, color: "var(--muted)" }}>
                      <span>Robot {evt.robot_id}</span>
                      <span>·</span>
                      <span>{time}</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--muted)", fontSize: 13 }}>
            No recent events
          </div>
        )}
      </div>
    </div>
  );
}
