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

/**
 * Apply presence changes from the broker. A robot whose connection dropped is
 * shown OFFLINE at once; coming back online needs no action here, because the
 * robot's next reading carries its real status.
 */
export function applyPresence(robots, presence) {
  const offline = new Set(
    Object.entries(presence)
      .filter(([, online]) => online === false)
      .map(([id]) => String(id)),
  );
  if (offline.size === 0) return robots;
  return robots.map((robot) =>
    offline.has(String(robot.robot_id)) ? { ...robot, status: "OFFLINE" } : robot,
  );
}
