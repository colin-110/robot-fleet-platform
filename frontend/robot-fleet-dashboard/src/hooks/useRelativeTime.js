import { useEffect, useState } from "react";

/**
 * Custom hook for relative time formatting.
 *
 * Ticks every 10 seconds to keep "Xs ago" labels fresh without
 * causing excessive re-renders.
 */
export default function useRelativeTime() {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 10000);
    return () => clearInterval(timer);
  }, []);

  const formatRelativeTime = (timestampMs) => {
    if (!timestampMs) return "--";
    const seconds = Math.max(0, Math.floor((nowMs - timestampMs) / 1000));
    if (seconds < 5) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ago`;
  };

  return { nowMs, formatRelativeTime };
}
