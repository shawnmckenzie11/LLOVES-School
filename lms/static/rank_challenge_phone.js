/**
 * MCK-171 Team challenge on the phone (390px), pure HTML builders.
 *
 * MCK-216: no team strip, no I-agree quorum; explicit lock-in + Undo for the
 * last placer when every spot is filled.
 */

import { fill, teamMark } from "./rank_challenge_view.js";

/** Wonder / MCK-216 strings on the phone. */
export const PHONE_COPY = Object.freeze({
  cue: "Talk it through. Lock in when your whole team agrees.", // race.cue.together
  lockIn: "Lock in our order",
  undo: "Undo",
  locked: "Locked in. Waiting for the reveal.",
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
 * Which phone state a Team challenge card is in.
 * @param {any} group ``item.group_submit`` (with ``race``)
 * @param {string} status Lifecycle status (``active`` / ``closed``).
 * @returns {"ranking"|"ready"|"locked"|"timesup"|"closed"}
 */
export function phoneState(group, status) {
  const race = group?.race || {};
  if (race.locked) return "locked";
  if (status === "closed") return race.closed_unlocked === "timer" ? "timesup" : "closed";
  if (race.can_lock) return "ready";
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
  if (state === "locked") return C.locked;
  if (state === "timesup") return C.timesup;
  if (state === "closed") return C.closed;
  return C.cue;
}

/**
 * MCK-216: student phones no longer show the team strip (team name / lock count).
 * @returns {string}
 */
export function phoneStripHtml() {
  return "";
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
 * Lock in our order (enabled when the server says ``race.can_lock``).
 * @param {any} group
 * @returns {string}
 */
export function lockButtonHtml(group) {
  const ready = Boolean(group?.race?.can_lock);
  return `<button type="button" class="prompt-submit race-phone-btn" data-race-lock="1"${
    ready ? "" : " disabled"
  } aria-disabled="${ready ? "false" : "true"}">${esc(PHONE_COPY.lockIn)}</button>`;
}

/**
 * Undo for the teammate who placed last (Rank together only).
 * @param {any} group
 * @returns {string}
 */
export function undoButtonHtml(group) {
  if (!group?.race?.can_undo) return "";
  return `<button type="button" class="race-phone-btn is-outline" data-race-undo="1">${esc(PHONE_COPY.undo)}</button>`;
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

/** Wonder v3 strings (final) for phone results. */
export const PHONE_RESULTS_COPY = Object.freeze({
  right: "{k} of {n} spots right.", // results.right
  rightAll: "All {n} spots right.", // results.right.all
  spot: "Spot {n} · {item}", // results.spot
  answer: "Answer: {item}", // results.spot.right_item
  you: "Your team", // results.you
  podium: "Your team made the podium.", // results.podium
  points: "{pts} points each", // results.points
  pointsOne: "1 point each", // results.points.one
  tie: "{n} teams tied", // results.tie
  fromDraft: "Scored from your group's draft.", // results.from_draft
  pill: "Results", // results.pill
  lateJoin: "You joined after voting closed for this question.",
});

/**
 * @param {number} pts
 * @returns {string}
 */
function phonePoints(pts) {
  const n = Number(pts) || 0;
  return n === 1 ? PHONE_RESULTS_COPY.pointsOne : fill(PHONE_RESULTS_COPY.points, { pts: n });
}

/**
 * Phone results (own team only): ✓ / ring per spot with the right item on a
 * wrong spot, team points, "Your team made the podium." only when on it, and
 * a mini podium (own step outlined). No place number off the podium; a small
 * burst only on podium phones (none under reduced motion, in CSS).
 * @param {any} group ``item.group_submit`` with ``race.results``
 * @returns {string}
 */
export function phoneResultsHtml(group) {
  const C = PHONE_RESULTS_COPY;
  const race = group?.race || {};
  const res = race.results || {};
  const slot = Number(race.slot);
  const colour = teamMark(Number.isFinite(slot) ? slot : 0).colour;
  const n = Number(res.total) || 0;
  const k = Number(res.right) || 0;
  const cue = k === n && n > 0 ? fill(C.rightAll, { n }) : fill(C.right, { k, n });
  const spots = (Array.isArray(res.spots) ? res.spots : [])
    .map((spot) => {
      const answer = !spot.right && spot.answer ? ` <span class="race-phone-answer">· ${esc(fill(C.answer, { item: spot.answer }))}</span>` : "";
      return `<li class="${spot.right ? "is-right" : "is-not"}"><span class="race-phone-label">${esc(
        fill(C.spot, { n: spot.n, item: spot.item || "—" })
      )}${answer}</span><span class="race-phone-mark" aria-label="${
        spot.right ? "right" : "not this one"
      }">${spot.right ? "✓" : ""}</span></li>`;
    })
    .join("");
  const steps = Array.isArray(res.podium) ? res.podium : [];
  const byStep = new Map(steps.map((step) => [Number(step.step), step]));
  const step = (s) => {
    if (!s) return `<div class="race-mini-step is-empty"></div>`;
    const mine = (s.teams || []).some((t) => t.mine);
    const names = (s.teams || []).map((t) => esc(t.name)).join("<br>");
    return `<div class="race-mini-step is-step-${s.step}${mine ? " is-mine" : ""}"><span>${s.step} · ${names}</span></div>`;
  };
  const burst = res.on_podium
    ? `<div class="race-phone-burst" aria-hidden="true">${Array.from({ length: 10 }, (_, i) => `<i style="--i:${i}"></i>`).join("")}</div>`
    : "";
  return `<div class="student-group-card race-phone race-phone-results" data-race-results-card="1" style="--team:${colour}">
    <p class="race-phone-pill">${esc(C.pill)}</p>
    <p class="race-phone-cue is-results">${esc(cue)}</p>
    ${res.from_draft ? `<p class="race-phone-draft">${esc(C.fromDraft)}</p>` : ""}
    <ol class="race-phone-spots">${spots}</ol>
    ${
      res.show_points && res.credited === false
        ? `<p class="student-live-note race-phone-late">${esc(C.lateJoin)}</p>`
        : res.points === null || res.points === undefined
          ? ""
          : `<p class="race-phone-you"><span>${esc(C.you)}</span><strong>${esc(phonePoints(res.points))}</strong></p>`
    }
    ${res.on_podium ? `<p class="race-phone-podium-line">${esc(C.podium)}</p>` : ""}
    ${steps.length ? `<div class="race-mini-podium">${burst}${step(byStep.get(2))}${step(byStep.get(1))}${step(byStep.get(3))}</div>` : ""}
  </div>`;
}
