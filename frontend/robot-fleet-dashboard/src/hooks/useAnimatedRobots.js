import { useEffect, useState } from "react";
import { carryForward } from "../utils/predict";

/**
 * Robots as they are right now: each one's last keyframe carried forward by
 * the shared predictor, re-evaluated every ``intervalMs``. This is what makes
 * the map move smoothly although robots send only when they stray from the
 * prediction. It stops at the robot's heartbeat, as the server does.
 */
export default function useAnimatedRobots(robots, intervalMs = 250) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);

  return robots.map((robot) =>
    carryForward(robot, now, { heartbeat_s: robot.bounds?.heartbeat_s }),
  );
}
