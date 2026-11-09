// Loaded first and blocking, from <head> in index.html, so the page paints
// with the right palette. A file rather than an inline script so the
// Content-Security-Policy (nginx.conf) can allow scripts from this site only.
// Paint the right palette on the first frame. Without this the document
// renders dark, then React swaps to light one tick later, which reads as
// a flash on every reload. Mirrors resolveInitialTheme() in
// src/hooks/useTheme.js - keep the two in step.
(function () {
  try {
    var stored = localStorage.getItem("fleetops.theme");
    var theme =
      stored === "light" || stored === "dark"
        ? stored
        : window.matchMedia("(prefers-color-scheme: light)").matches
          ? "light"
          : "dark";
    document.documentElement.dataset.theme = theme;
  } catch {
    document.documentElement.dataset.theme = "dark";
  }
})();
