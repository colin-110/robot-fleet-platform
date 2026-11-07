/**
 * When a robot state was measured: the live reading's timestamp, or the
 * snapshot's last_seen. 0 when unknown, so anything with a time beats it.
 */
export function readingTime(item) {
  const value = new Date(item?.timestamp ?? item?.last_seen ?? 0).getTime();
  return Number.isNaN(value) ? 0 : value;
}

/**
 * Whether an incoming reading should replace the current state.
 *
 * Robots resend readings the server has not confirmed (after a failed upload
 * or a server restart), so an older reading can arrive after a newer one. It
 * must not move the robot back on the map.
 */
export function isNewerReading(incoming, current) {
  return !current || readingTime(incoming) >= readingTime(current);
}
