/**
 * One points figure for a bank question.
 *
 * A stored live award (``live_points``) wins. Otherwise the question's own
 * points are used, so the row and the editor show the same number. Missing
 * both means 1, which is what Add New starts from.
 */

/**
 * @param {Record<string, unknown>|null|undefined} question
 * @returns {string}
 */
export function questionPointsValue(question) {
  const live = question?.live_points;
  if (live != null && live !== "") return String(live);
  const own = question?.points ?? question?.points_possible;
  if (own != null && own !== "") return String(own);
  return "1";
}
