const MenuIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
);

const SearchIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);

const RefreshIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" />
  </svg>
);

const SunIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);

const MoonIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </svg>
);

/**
 * Top bar: identity, search, and the controls that act on the whole console.
 *
 * The connection state is deliberately one chip. It used to be three separate
 * readouts — a Live/count pill plus "WS <age>" and "REST <age>" — which put
 * four numbers in the corner of every screen to answer one question: is this
 * data current? The detail is still there, in the chip's tooltip, for the times
 * that question turns into "why not?".
 */
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
  onOpenNav,
  theme,
  onToggleTheme,
  error,
}) {
  const dotClass = error ? "dotBad" : socketConnected ? "dotGood" : "dotWarn";
  const statusText = error ? "Offline" : socketConnected ? "Live" : "Connecting";
  const feedDetail = error
    ? "Backend is unreachable"
    : `Live feed ${lastWsText} · snapshot ${lastFetchText}`;
  const isFiltered = Boolean(query) && filteredCount !== totalCount;

  return (
    <header className="topbar">
      <div className="topbarInner">
        <button
          type="button"
          className="iconBtn topbarMenu"
          onClick={onOpenNav}
          aria-label="Open navigation"
        >
          <MenuIcon />
        </button>

        <div className="topbarTitle">
          <h1>{title}</h1>
          <div className="subtle">{subtitle}</div>
        </div>

        <div className="topbarSearch">
          <SearchIcon />
          <input
            className="input"
            placeholder="Search robots"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            aria-label="Search robots by id, mission, or status"
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
          {isFiltered && (
            <span className="pill topbarCount">
              {filteredCount} of {totalCount}
            </span>
          )}

          <span className="statusChip" title={feedDetail}>
            <span className={`dot ${dotClass}`} aria-hidden="true" />
            <b>{statusText}</b>
          </span>

          <button
            type="button"
            className="iconBtn"
            onClick={onToggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Light theme" : "Dark theme"}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>

          <button type="button" className="iconBtn" onClick={onRefresh} aria-label="Refresh" title="Refresh">
            <RefreshIcon />
          </button>
        </div>
      </div>
    </header>
  );
}

export default Navbar;
