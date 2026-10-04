/**
 * MCK-185: class-wide "put every item in the right order" line.
 *
 * One calm line, a soft pill with each team's colour dot, at the very bottom
 * of the question card (phones, and the teacher view under the lanes or
 * stack). No emoji, exclamation mark, confetti or sound. Team names only:
 * never which spots or what order. The server decides WHEN it shows
 * (``rank_challenge.FULL_ORDER_WHEN``); this only words it.
 */

import { fill, teamMark } from "./rank_challenge_view.js";

/** Wonder copy (final, Oct 4). */
export const FULL_ORDER_COPY = Object.freeze({
  one: "{team} put every item in the right order.", // results.full_order.one
  many: "{teams} put every item in the right order.", // results.full_order.many
  you: "Your team put every item in the right order.", // results.full_order.you
  youMany: "Your team and {teams} put every item in the right order.", // results.full_order.you_many
  more: "{k} more teams", // results.full_order.more (4 or more teams)
});

/**
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

/**
 * "Maple"; "Maple and Aspen"; "Maple, Aspen and Birch" (no Oxford comma);
 * 4 or more: "Maple, Aspen and {k} more teams".
 * @param {string[]} names Team names exactly as shown in class.
 * @returns {string}
 */
export function joinTeamNames(names) {
  const list = (Array.isArray(names) ? names : []).map((name) => String(name ?? "")).filter(Boolean);
  if (list.length <= 1) return list[0] || "";
  if (list.length === 2) return `${list[0]} and ${list[1]}`;
  if (list.length === 3) return `${list[0]}, ${list[1]} and ${list[2]}`;
  return `${list[0]}, ${list[1]} and ${fill(FULL_ORDER_COPY.more, { k: list.length - 2 })}`;
}

/**
 * The sentence for a ``full_order`` payload.
 * @param {{you?: boolean, teams?: Array<{name: string}>} | null | undefined} notice
 * @returns {string} "" when no team got it all.
 */
export function fullOrderText(notice) {
  const others = (Array.isArray(notice?.teams) ? notice.teams : []).map((team) => String(team?.name ?? "")).filter(Boolean);
  if (notice?.you) {
    return others.length ? fill(FULL_ORDER_COPY.youMany, { teams: joinTeamNames(others) }) : FULL_ORDER_COPY.you;
  }
  if (!others.length) return "";
  return others.length === 1
    ? fill(FULL_ORDER_COPY.one, { team: others[0] })
    : fill(FULL_ORDER_COPY.many, { teams: joinTeamNames(others) });
}

/**
 * The pill. Dots: the viewer's team first (``you``), then the others, at
 * most four (the sentence carries the rest).
 * @param {{you?: boolean, slot?: number, teams?: Array<{name: string, slot?: number}>} | null | undefined} notice
 * @returns {string} "" when no team got it all.
 */
export function fullOrderHtml(notice) {
  const text = fullOrderText(notice);
  if (!text) return "";
  const slots = [];
  if (notice?.you) slots.push(Number(notice.slot) || 0);
  for (const team of Array.isArray(notice?.teams) ? notice.teams : []) slots.push(Number(team?.slot) || 0);
  const dots = slots
    .slice(0, 4)
    .map((slot) => `<span class="full-order-dot" style="--team:${teamMark(slot).colour}" aria-hidden="true"></span>`)
    .join("");
  return `<p class="full-order-line" data-full-order="1" role="status"><span class="full-order-dots">${dots}</span><span class="full-order-text">${esc(text)}</span></p>`;
}
