function Navbar({
  title,
  subtitle,
  socketConnected,
  lastFetchText,
  lastWsText,
  query,
  onQueryChange,
  onClearQuery,
  filteredCount,
  totalCount,
  onOpenSidebar,
  onRefresh,
  error
}) {
  const dotClass = error ? "dotBad" : socketConnected ? "dotGood" : "dotWarn";

  const statusText = error
    ? "Backend unreachable"
    : socketConnected
    ? "Live"
    : "Connecting";

  return (
    <div style={{ marginBottom: 18 }}>
      <div className="navHeader">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              className="btn"
              onClick={onOpenSidebar}
              style={{ display: "inline-flex", padding: "8px 10px" }}
            >
              Menu
            </button>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, letterSpacing: -0.4 }}>{title}</h1>
              <div className="subtle">{subtitle}</div>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div className="pill">
            <span className={`dot ${dotClass}`} aria-hidden="true" />
            <span>{statusText}</span>
            <span style={{ opacity: 0.7 }}>|</span>
            <span className="subtle">WS {lastWsText}</span>
          </div>
          <button className="btn" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </div>

      <div className="navFilters">
        <div style={{ position: "relative" }}>
          <input
            className="input"
            placeholder="Search by robot id, mission, status, or risk level"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
          />
          {query && (
            <button
              className="btn"
              onClick={onClearQuery}
              style={{
                position: "absolute",
                right: 8,
                top: "50%",
                transform: "translateY(-50%)",
                padding: "8px 10px",
                borderRadius: 12
              }}
              title="Clear"
            >
              x
            </button>
          )}
        </div>

        <div className="pill" title="Filtered robots">
          <span className="subtle">Robots</span>
          <span style={{ opacity: 0.7 }}>|</span>
          <span style={{ fontWeight: 800 }}>{filteredCount}</span>
          <span className="subtle">/ {totalCount}</span>
        </div>

        <div className="pill" title="Last REST poll">
          <span className="subtle">REST</span>
          <span style={{ opacity: 0.7 }}>|</span>
          <span className="subtle">{lastFetchText}</span>
        </div>
      </div>

      {error && (
        <div style={{ marginTop: 12 }} className="glass">
          <div style={{ padding: 12, display: "flex", gap: 10, alignItems: "center" }}>
            <span className="dot dotBad" aria-hidden="true" />
            <div>
              <div style={{ fontWeight: 700 }}>Cannot reach backend</div>
              <div className="subtle">
                Start the API and verify `VITE_API_BASE_URL`.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default Navbar;
