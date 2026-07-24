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
  onRefresh,
  error,
}) {
  const dotClass = error ? "dotBad" : socketConnected ? "dotGood" : "dotWarn";
  const statusText = error ? "Offline" : socketConnected ? "Live" : "Connecting";

  return (
    <header className="topbar">
      <div className="topbarInner">
        <div className="topbarTitle">
          <h1>{title}</h1>
          <div className="subtle">{subtitle}</div>
        </div>

        <div className="topbarSearch">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            className="input"
            placeholder="Search by robot id, mission, or status"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
          />
          {query && (
            <button className="searchClear" onClick={onClearQuery} title="Clear" aria-label="Clear search">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        <div className="topbarRight">
          <span className="statusChip">
            <span className={`dot ${dotClass}`} aria-hidden="true" />
            <b>{statusText}</b>
            <span className="sep">·</span>
            <span>{filteredCount}/{totalCount}</span>
          </span>
          <span className="statusChip" title="Realtime feed / last poll">
            <span>WS {lastWsText}</span>
            <span className="sep">·</span>
            <span>REST {lastFetchText}</span>
          </span>
          <button className="btn btnPrimary" onClick={onRefresh}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
            </svg>
            Refresh
          </button>
        </div>
      </div>
    </header>
  );
}

export default Navbar;
