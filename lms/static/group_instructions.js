/**
 * MCK-155 S4 + S5: one calm instruction line per group question type, and
 * the open-ended "who are we waiting for" copy.
 *
 * Copy: Wonder v1 + v2 (/workspace/mobbin-sites/group-questions-copy-wonder-v2.md).
 * PR C: group MC runs option B ("pick alone, then agree"), and rank can
 * "Take turns". The line follows the card's step and the rank Group mode.
 *
 * Pure: no DOM, so the node harness can import it.
 */

/** Final copy. */
export const GROUP_INSTRUCTION_COPY = Object.freeze({
  open: "Everyone writes their own answer first. Then your group sends one answer together.",
  rank: "Rank these together. Anyone can move them.",
  rank_turns: "Take turns. Each person places one item, then passes.",
  mc_pick: "Pick your own answer first.",
  mc_agree: "Now agree on one answer. Whoever sends it writes why.",
  waitingMany: "Waiting for {n} more teammates.",
  waitingOne: "Waiting for 1 more teammate.",
  stillWriting: "Still writing: {names}",
  ready: "Everyone's in. Send your group's answer.",
  send: "Send group answer",
});

/**
 * Which instruction a group card gets.
 * @param {{response_mode?: string, group_submit?: any}} item Live item.
 * @param {{type?: string, kind?: string}} [content] Item content.
 * @returns {"open"|"rank"|"rank_turns"|"mc_pick"|"mc_agree"|""} Empty for
 *   individual items.
 */
export function groupInstructionKey(item, content = {}) {
  const mode = String(item?.response_mode || "");
  const type = String(content?.type || content?.kind || "").toLowerCase();
  const group = item?.group_submit || {};
  if (mode === "group_consensus") return "open";
  if (mode !== "group_submit") return "";
  if (type === "rank") return String(group.rank_mode || "") === "turns" ? "rank_turns" : "rank";
  return group.pick_step === false ? "mc_agree" : "mc_pick";
}

/**
 * The one instruction line, or "" when the card is not an open group card.
 * @param {any} item
 * @param {any} [content]
 * @returns {string}
 */
export function groupInstructionHtml(item, content = {}) {
  if (String(item?.status || "active") !== "active") return "";
  const key = groupInstructionKey(item, content);
  if (!key) return "";
  return `<p class="student-group-instruction" data-group-instruction="${key}">${esc(
    GROUP_INSTRUCTION_COPY[key]
  )}</p>`;
}

/**
 * "Waiting for 2 more teammates." / "Waiting for 1 more teammate."
 * @param {number} n
 * @returns {string} Empty when nobody is left.
 */
export function waitingLine(n) {
  const count = Math.max(0, Number(n) || 0);
  if (!count) return "";
  return count === 1
    ? GROUP_INSTRUCTION_COPY.waitingOne
    : GROUP_INSTRUCTION_COPY.waitingMany.replace("{n}", String(count));
}

/**
 * "Still writing: Ben, Cy" (first names only, joined by ", ").
 * @param {unknown[]} names
 * @returns {string} Empty when the list is empty.
 */
export function stillWritingLine(names) {
  const list = (Array.isArray(names) ? names : [])
    .map((name) => String(name || "").trim().split(/\s+/)[0])
    .filter(Boolean);
  if (!list.length) return "";
  return GROUP_INSTRUCTION_COPY.stillWriting.replace("{names}", list.join(", "));
}

/**
 * S5 wait block shown after this student has answered: a disabled
 * "Send group answer" with its reason and who is still writing.
 * @param {{waiting_count?: number, waiting_names?: string[]}} group
 * @param {number} itemId
 * @returns {string}
 */
export function consensusWaitHtml(group, itemId) {
  const count = Math.max(
    Number(group?.waiting_count) || 0,
    Array.isArray(group?.waiting_names) ? group.waiting_names.length : 0
  );
  const reason = waitingLine(count);
  const names = stillWritingLine(group?.waiting_names);
  const id = `group-wait-${Number(itemId) || 0}`;
  return `<div class="student-group-wait">
    <button type="button" class="prompt-submit" disabled aria-disabled="true" aria-describedby="${id}">${esc(
      GROUP_INSTRUCTION_COPY.send
    )}</button>
    <p class="student-group-wait-reason" id="${id}" aria-live="polite">${esc(reason)}${
      names ? `<br><span class="student-group-still-writing">${esc(names)}</span>` : ""
    }</p>
  </div>`;
}

/**
 * Minimal HTML escape.
 * @param {unknown} value
 * @returns {string}
 */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
