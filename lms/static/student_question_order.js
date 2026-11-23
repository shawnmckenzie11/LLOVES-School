/**
 * Push-order labels for student questions (MCK-495).
 *
 * Q1, Q2, Q3 follow the order questions were pushed in the session.
 * The order comes from ``published_at``, then the live item id.
 * No new database column.
 */

/**
 * Sort key for when a question was pushed to students.
 * A missing published time sorts after every dated push.
 *
 * @param {any} item Lifecycle question.
 * @returns {[number, string, number]}
 */
export function questionPushRank(item) {
  const published = String(item?.published_at || "").trim();
  return [published ? 0 : 1, published, Number(item?.id) || 0];
}

/**
 * Compare two questions by push order.
 *
 * @param {any} a
 * @param {any} b
 * @returns {number} Negative when ``a`` was pushed first.
 */
export function compareQuestionPushOrder(a, b) {
  const left = questionPushRank(a);
  const right = questionPushRank(b);
  if (left[0] !== right[0]) return left[0] - right[0];
  if (left[1] < right[1]) return -1;
  if (left[1] > right[1]) return 1;
  return left[2] - right[2];
}

/**
 * True for media, whiteboard, and slides rows, which are not questions.
 *
 * @param {any} item
 * @returns {boolean}
 */
function isSurfaceRow(item) {
  const kind = String(
    item?.kind || item?.item_type || item?.content?.item_type || ""
  ).toLowerCase();
  return kind === "media" || kind === "whiteboard" || kind === "slides";
}

/**
 * Q1, Q2, … for one question from session push order.
 * The number stays with the live item id across open, closed, and docked cards.
 *
 * @param {any} item Question to label.
 * @param {any[]} sessionItems Questions pushed in this session.
 * @returns {string}
 */
export function questionPushLabel(item, sessionItems) {
  const rows = (Array.isArray(sessionItems) ? sessionItems : [])
    .filter((row) => !isSurfaceRow(row))
    .slice()
    .sort(compareQuestionPushOrder);
  const id = Number(item?.id) || 0;
  const index = id ? rows.findIndex((row) => Number(row?.id) === id) : -1;
  const n = index >= 0 ? index + 1 : rows.length + 1;
  return `Q${n}`;
}
