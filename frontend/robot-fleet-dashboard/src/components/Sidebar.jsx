import { NAV_ITEMS } from "../utils/constants";

const ICONS = {
  Dashboard: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  "Fleet Analytics": (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3v18h18" />
      <path d="m7 14 3-3 3 3 5-6" />
    </svg>
  ),
  Telemetry: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12h4l3 8 4-16 3 8h6" />
    </svg>
  ),
  "System Health": (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 21s-7-4.5-9.5-9A5.5 5.5 0 0 1 12 6a5.5 5.5 0 0 1 9.5 6c-2.5 4.5-9.5 9-9.5 9Z" />
    </svg>
  ),
};

function Sidebar({ active, onChange }) {
  return (
    <aside className="sidebarWrap">
      <div className="sidebar">
        <div className="brand">
          <span className="brandMark" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="8" width="16" height="11" rx="2.5" />
              <path d="M12 8V4M8 3h8M9 13h.01M15 13h.01M9 16h6" />
            </svg>
          </span>
          <div className="brandTitle">
            <strong>FleetOps</strong>
            <span>Robot fleet platform</span>
          </div>
        </div>

        <div className="navGroupLabel">Operations</div>
        <nav className="nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => {
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
                {ICONS[item]}
                <span>{item}</span>
              </div>
            );
          })}
        </nav>

        <div className="navSpacer" />
        <div className="footerHint">
          Real-time telemetry · v1.0<br />
          Redis Streams · WebSocket feed
        </div>
      </div>
    </aside>
  );
}

export default Sidebar;
