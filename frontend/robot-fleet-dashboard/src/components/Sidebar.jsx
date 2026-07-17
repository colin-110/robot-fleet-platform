import { NAV_ITEMS } from "../utils/constants";

function Sidebar({ active, onChange }) {
  return (
    <aside className="sidebarWrap">
      <div className="sidebar">
        <div className="brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="brandMark" aria-hidden="true" />
            <div className="brandTitle">
              <strong>FleetOps AI</strong>
              <span>Robot fleet platform</span>
            </div>
          </div>
        </div>

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
                <span>{item}</span>
                {isActive && <span className="subtle">Live</span>}
              </div>
            );
          })}
        </nav>

          Mission dispatch and telemetry ingestion.
      </div>
    </aside>
  );
}

export default Sidebar;
