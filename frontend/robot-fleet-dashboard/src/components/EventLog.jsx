import { useMemo, useState } from "react";

import { EVENT_PANEL_LIMIT } from "../utils/constants";

/** Colour-code a row by what the event says, not by which socket delivered it. */
function accentFor(event, message) {
  if (/restricted|zone/i.test(message)) return "var(--c-warn)";
  if (/completed/i.test(message)) return "var(--c-good)";
  if (event.type && event.type.startsWith("COMMAND")) return "var(--c-info)";
  return "var(--accent)";
}

function describe(event) {
  if (event.type === "COMMAND") return `Command: ${event.action}`;
  return event.message || "";
}

function formatTime(timestamp) {
  const at = new Date(timestamp);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function EventRow({ event }) {
  const message = describe(event);
  return (
    <li className="eventRow">
      <span className="eventRow__accent" style={{ background: accentFor(event, message) }} />
      <div className="eventRow__body">
        <div className="eventRow__msg">{message}</div>
        <div className="eventRow__meta">
          <span>Robot {event.robot_id}</span>
          <span aria-hidden="true">·</span>
          <span>{formatTime(event.timestamp)}</span>
        </div>
      </div>
    </li>
  );
}

/**
 * Live event feed.
 *
 * Two shapes, one component:
 *
 * - `variant="panel"` — the dashboard sidebar. Shows only the newest handful
 *   inside a fixed-height scroller. It previously rendered the whole buffer in
 *   a track that grew with its content, so a busy fleet stretched the right
 *   column far past the map next to it and dragged the page down with it.
 * - `variant="page"` — the Event Log tab. Same feed, full buffer, with a
 *   filter box, still scrolling inside its own box rather than the document.
 *
 * Either way the surrounding layout owns the height and the list scrolls
 * within it, so incoming events can never change the page's dimensions.
 */
export default function EventLog({ events, variant = "panel", onViewAll }) {
  const [filter, setFilter] = useState("");
  const isPage = variant === "page";
  const all = useMemo(() => events || [], [events]);

  const visible = useMemo(() => {
    if (!isPage) return all.slice(0, EVENT_PANEL_LIMIT);
    const needle = filter.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((event) => {
      const haystack = `${describe(event)} robot ${event.robot_id}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [all, filter, isPage]);

  const hidden = all.length - visible.length;

  return (
    <section className={`glassStrong eventLog ${isPage ? "eventLog--page" : ""}`}>
      <div className="panelHead">
        <h2>{isPage ? "Event Log" : "Live Events"}</h2>
        <div className="eventLog__headTools">
          {all.length > 0 && <span className="pill eventLog__count">{all.length}</span>}
          {!isPage && hidden > 0 && onViewAll && (
            <button type="button" className="linkBtn" onClick={onViewAll}>
              View all
            </button>
          )}
        </div>
      </div>

      {isPage && (
        <div className="eventLog__filter">
          <input
            className="input"
            placeholder="Filter events by text or robot id"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter events"
          />
        </div>
      )}

      <div className="eventLog__scroll">
        {visible.length > 0 ? (
          <ul className="eventList">
            {visible.map((event, index) => (
              <EventRow key={`${event.robot_id}-${event.timestamp}-${index}`} event={event} />
            ))}
          </ul>
        ) : (
          <div className="eventLog__empty">
            {all.length === 0 ? "No recent events" : "No events match that filter"}
          </div>
        )}
      </div>
    </section>
  );
}
