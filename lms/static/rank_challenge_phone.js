/**
 * MCK-171 Team challenge on the phone (390px), pure HTML builders.
 *
 * IA v0.2 F3 + mock v1 §5, Wonder v3 copy (final). One calm cue line per
 * state, no countdown, no rank or score before the reveal, and the word
 * "race" never reaches the screen (payload keys only).
 */

import { fill, teamMark } from "./rank_challenge_view.js";

/** Wonder v3 strings (final, Oct 3) on the phone. */
export const PHONE_COPY = Object.freeze({
  cue: "Talk it through. Lock in when your whole team agrees.", // race.cue.together
  agree: "I agree", // race.agree
  undo: "Not yet", // race.agree.undo
  count: "{k} of {m} agree", // race.agree.count
  waiting: "Waiting for {names} to agree.", // race.agree.waiting
  reset: "The order changed. Agree again when you're ready.", // race.agree.reset
  agreed: "Your whole team agreed.", // race.agreed
  locked: "Your group's order is in.", // race.locked
  sentCount: "{k} of {n} teams locked in", // race.sent_count
  timesup: "Time's up. We'll score what your group placed.", // race.timesup (0:00 only)
  closed: "Answers are closed. We'll score what your group placed.", // race.closed
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
 * "Eli", "Eli and Gus", "Ava, Eli and Gus".
 * @param {unknown} names
 * @returns {string}
 */
function nameList(names) {
  const list = (Array.isArray(names) ? names : []).map((n) => String(n || "").trim()).filter(Boolean);
  if (list.length < 2) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/**
 * Which phone state a Team challenge card is in.
 * @param {any} group ``item.group_submit`` (with ``race``)
 * @param {string} status Lifecycle status (``active`` / ``closed``).
 * @returns {"ranking"|"waiting"|"reset"|"agreed"|"locked"|"timesup"|"closed"}
 */
export function phoneState(group, status) {
  const race = group?.race || {};
  if (race.locked) {
    return race.mode === "together" && String(race.locked_by || "team") === "team" ? "agreed" : "locked";
  }
  if (status === "closed") return race.closed_unlocked === "timer" ? "timesup" : "closed";
  const agree = race.agree || {};
  if (agree.mine) return "waiting";
  if (agree.reset) return "reset";
  return "ranking";
}

/**
 * The one cue line for a state.
 * @param {any} group
 * @param {string} status
 * @returns {string}
 */
export function phoneCue(group, status) {
  const C = PHONE_COPY;
  const state = phoneState(group, status);
  if (state === "agreed") return C.agreed;
  if (state === "locked") return C.locked;
  if (state === "timesup") return C.timesup;
  if (state === "closed") return C.closed;
  if (state === "reset") return C.reset;
  if (state === "waiting") {
    const names = nameList(group?.race?.agree?.waiting_names);
    return names ? fill(C.waiting, { names }) : C.cue;
  }
  return C.cue;
}

/**
 * Team strip: shape chip, team name, teammates, and the right-hand pill.
 * @param {any} group
 * @param {string} [pill] Pill text (defaults to "{k} of {n} teams locked in").
 * @returns {string}
 */
export function phoneStripHtml(group, pill) {
  const race = group?.race || {};
  const slot = Number(race.slot);
  const mark = teamMark(Number.isFinite(slot) ? slot : 0);
  const members = (Array.isArray(group?.members) ? group.members : [])
    .map((n) => String(n || "").trim())
    .filter(Boolean)
    .join(", ");
  const text =
    pill != null
      ? String(pill)
      : fill(PHONE_COPY.sentCount, { k: Number(race.teams_locked) || 0, n: Number(race.teams_total) || 0 });
  return `<div class="race-phone-strip" style="--team:${mark.colour}">
    <span class="race-phone-chip" aria-hidden="true">${mark.shape}</span>
    <span class="race-phone-team"><span class="race-phone-team-name">${esc(group?.team_name || "")}</span>${
      members ? `<span class="race-phone-members">${esc(members)}</span>` : ""
    }</span>
    <span class="race-phone-pill">${esc(text)}</span>
  </div>`;
}

/**
 * Cue box (one calm line).
 * @param {any} group
 * @param {string} status
 * @returns {string}
 */
export function phoneCueHtml(group, status) {
  const state = phoneState(group, status);
  return `<p class="race-phone-cue is-${state}" aria-live="polite" data-race-phone-state="${state}">${esc(
    phoneCue(group, status)
  )}</p>`;
}

/**
 * Agree pips + "{k} of {m} agree".
 * @param {any} group
 * @returns {string}
 */
export function agreeLineHtml(group) {
  const race = group?.race || {};
  const agree = race.agree || {};
  const of = Math.max(0, Number(agree.of) || 0);
  const k = race.locked && String(race.locked_by || "team") === "team" ? of : Math.min(of, Number(agree.count) || 0);
  const pips = Array.from({ length: of }, (_, i) =>
    `<span class="race-phone-pip${i < k ? " is-on" : ""}">${i < k ? "✓" : ""}</span>`
  ).join("");
  const slot = Number(race.slot);
  const colour = teamMark(Number.isFinite(slot) ? slot : 0).colour;
  return `<p class="race-phone-agree" style="--team:${colour}"><span class="race-phone-pips" aria-hidden="true">${pips}</span><span>${esc(
    fill(PHONE_COPY.count, { k, m: of })
  )}</span></p>`;
}

/**
 * I agree (or Not yet once mine). ``order`` rides along so the server can
 * refuse an agree on an order this phone has not seen yet.
 * @param {any} group
 * @param {boolean} complete Every option is numbered on this phone.
 * @returns {string}
 */
export function agreeButtonHtml(group, complete) {
  const agree = group?.race?.agree || {};
  if (agree.mine) {
    return `<button type="button" class="race-phone-btn is-outline" data-race-agree="0">${esc(PHONE_COPY.undo)}</button>`;
  }
  const ready = Boolean(complete && agree.can_agree !== false);
  return `<button type="button" class="prompt-submit race-phone-btn" data-race-agree="1"${
    ready ? "" : " disabled"
  } aria-disabled="${ready ? "false" : "true"}">${esc(PHONE_COPY.agree)}</button>`;
}

/**
 * Options in the server display order (``race.options``, the MCK-176 hook),
 * keeping the card's own option objects. Ids the server did not list keep
 * their place at the end; an older payload without ``race.options`` keeps
 * the card order.
 * @param {{id: string, label: string}[]} options
 * @param {any} race
 * @returns {{id: string, label: string}[]}
 */
export function raceDisplayOptions(options, race) {
  const list = Array.isArray(options) ? options : [];
  const shown = Array.isArray(race?.options) ? race.options.map((row) => String(row?.id ?? "")) : [];
  if (!shown.length) return list;
  const byId = new Map(list.map((opt) => [String(opt.id), opt]));
  const ordered = shown.map((id) => byId.get(id)).filter(Boolean);
  return ordered.concat(list.filter((opt) => !shown.includes(String(opt.id))));
}

/**
 * Read-only order rows (locked, time's up, closed).
 * @param {{id: string, label: string}[]} options
 * @param {string[]} order
 * @returns {string}
 */
export function readOnlyOrderHtml(options, order) {
  const labels = new Map((Array.isArray(options) ? options : []).map((row) => [String(row.id), row.label]));
  const ids = (Array.isArray(order) ? order : []).map(String);
  if (!ids.length) return "";
  return `<ol class="race-phone-order">${ids
    .map(
      (id, index) =>
        `<li><span class="race-phone-n">${index + 1}</span><span class="race-phone-label">${esc(labels.get(id) || id)}</span></li>`
    )
    .join("")}</ol>`;
}
