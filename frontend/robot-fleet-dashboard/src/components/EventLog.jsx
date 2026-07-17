export default function EventLog({ events }) {
  return (
    <div className="glassStrong" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="drag-handle" style={{ padding: "16px 20px", cursor: "grab", borderBottom: "1px solid var(--stroke)" }}>
        <h2 style={{ margin: 0, fontSize: "16px", color: "rgba(226, 232, 240, 0.96)" }}>Live Event Log</h2>
      </div>
      
      <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
        {events && events.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {events.map((evt, idx) => {
              const time = new Date(evt.timestamp).toLocaleTimeString();
              return (
                <div key={idx} style={{ padding: "12px", border: "1px solid var(--stroke)", borderRadius: "8px", background: "rgba(2, 6, 23, 0.2)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                    <span style={{ fontSize: "12px", color: "var(--muted)" }}>{time}</span>
                    <span className="badge" style={{ background: "rgba(14, 165, 233, 0.1)", color: "var(--info)", fontSize: "10px", padding: "2px 6px" }}>
                      Robot {evt.robot_id}
                    </span>
                  </div>
                  <div style={{ fontSize: "14px", color: "var(--text)", fontWeight: "500" }}>
                    {evt.type === "COMMAND" ? `Command Sent: ${evt.action}` : evt.message}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--muted)", fontSize: "14px" }}>
            No recent events
          </div>
        )}
      </div>
    </div>
  );
}
