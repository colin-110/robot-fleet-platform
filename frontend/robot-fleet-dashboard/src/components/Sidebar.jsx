function Sidebar({
  open,
  active,
  onChange,
  onToggle
}) {

  const items = [
    "Dashboard",
    "Fleet Analytics",
    "AI Alerts",
    "Telemetry",
    "System Health"
  ];

  return (
    <aside className={`sidebarWrap ${open ? "sidebarWrapOpen" : ""}`}>
      <div className="sidebar glassStrong sheen">
        <div className="brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="brandMark" aria-hidden="true" />
            <div className="brandTitle">
              <strong>FleetOps AI</strong>
              <span>Robot fleet platform</span>
            </div>
          </div>

          <button className="btn" onClick={onToggle} style={{ padding: "8px 10px" }}>
            ☰
          </button>
        </div>

        <nav className="nav" aria-label="Primary">
          {items.map((item) => {
            const isActive = active === item;
            return (
              <div
                key={item}
                className={`navItem ${isActive ? "navItemActive" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => onChange?.(item)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onChange?.(item);
                  }
                }}
              >
                <span>{item}</span>
                {isActive && <span className="subtle">●</span>}
              </div>
            );
          })}
        </nav>

        <div className="footerHint">
          Real-time telemetry · WebSocket updates · Anomaly signals
        </div>
      </div>
    </aside>
  );
}

export default Sidebar;
