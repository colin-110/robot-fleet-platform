/**
 * Fleet search matching.
 *
 * Lives outside App.jsx so that file exports only components — React Fast
 * Refresh cannot preserve state across edits in a module that mixes
 * components with other exports.
 */

// Cards label robots "R1", "R7", so that is what an operator types. Matching
// only the bare numeric id meant the single most obvious search — typing the
// identifier printed on the card — returned nothing at all.
const ROBOT_ID_QUERY = /^r?\s*(\d+)$/i;

/**
 * Does this robot match the operator's search box?
 *
 * @param {object} robot   Robot status payload from /api/v1/robots/status
 * @param {string} rawQuery Raw contents of the search input
 * @returns {boolean}
 */
export function matchesQuery(robot, rawQuery) {
  const query = (rawQuery ?? "").trim();
  if (!query) return true;

  // An id-shaped query matches exactly. Substring matching here is worse than
  // useless: searching "1" would return R1, R10, R11 and R12, so the one robot
  // you asked for arrives buried among three you did not.
  const idMatch = query.match(ROBOT_ID_QUERY);
  if (idMatch) {
    return robot.robot_id === Number(idMatch[1]);
  }

  const haystack = [
    `r${robot.robot_id}`,
    robot.robot_id,
    robot.status,
    robot.mission_type,
    robot.mission_id,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  return haystack.includes(query.toLowerCase());
}
