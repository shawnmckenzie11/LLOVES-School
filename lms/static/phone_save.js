/**
 * Calm notes for a shared draft that did not save, and the 409 resync skip.
 *
 * The Take turns 400 sentence has no names. Any other server line might,
 * so the card shows one fixed sentence instead.
 */

/** Shown when a draft POST is not the known Take turns 400. */
export const DRAFT_SAVE_NOTE = "Couldn't save that. Try again.";

/** Server text when a rank-together draft hits a Take turns item. */
export const TURNS_DRAFT_SERVER_TEXT =
  "This question takes turns. Place one item at a time.";

/**
 * Note to paint when a draft POST is not OK.
 * @param {number} status HTTP status, or 0 when the request never returned.
 * @param {string} serverText ``error`` from the JSON body. Ignored unless it
 *   is exactly the Take turns 400 sentence.
 * @returns {string}
 */
export function draftFailureNote(status, serverText) {
  const text = String(serverText || "");
  if (Number(status) === 400 && text === TURNS_DRAFT_SERVER_TEXT) return text;
  return DRAFT_SAVE_NOTE;
}

/**
 * A 409 that already carries the fresh group card does not need another /state.
 * @param {number} status
 * @param {{group_submit?: unknown}|null|undefined} data
 * @returns {boolean}
 */
export function groupFlowReplySkipsResync(status, data) {
  return Number(status) === 409 && Boolean(data && data.group_submit);
}
