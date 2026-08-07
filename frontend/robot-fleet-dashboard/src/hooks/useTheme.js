import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "fleetops.theme";

/**
 * Resolve the theme to apply on first paint.
 *
 * An explicit choice always wins; without one the OS preference decides, and
 * dark is the fallback because that is what the console was designed against.
 * index.html runs the same resolution inline before React mounts, so the page
 * never flashes the wrong palette — keep the two in step.
 */
export function resolveInitialTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Private-mode Safari throws on localStorage access. Fall through.
  }
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  return "dark";
}

/*
 * One store, not one useState per component.
 *
 * The navbar owns the toggle but the map also needs the value, to pick its
 * basemap. With per-component state those two would drift apart the moment
 * either one changed. An external store keeps every reader on the same value,
 * and the theme itself still lives on `<html data-theme>` so CSS variables
 * cascade without any subtree re-rendering to recolour.
 */
let current = null;
const listeners = new Set();

function applyTheme(theme) {
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = theme;
  }
}

function getSnapshot() {
  if (current === null) {
    current = resolveInitialTheme();
    applyTheme(current);
  }
  return current;
}

// Server/prerender has no DOM to read a preference from; dark matches the
// inline script's own fallback.
function getServerSnapshot() {
  return "dark";
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setTheme(next) {
  if (next !== "light" && next !== "dark") return;
  if (next === current) return;

  current = next;
  applyTheme(next);
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Persistence is a nicety; the session still works without it.
  }
  for (const listener of listeners) listener();
}

/** Current theme plus the controls to change it. */
export default function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggleTheme = useCallback(() => {
    setTheme(current === "dark" ? "light" : "dark");
  }, []);

  return { theme, setTheme, toggleTheme };
}
