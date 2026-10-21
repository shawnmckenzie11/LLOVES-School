/**
 * MCK-155 PR C: student renderers for group MC "pick alone, then agree"
 * (option B) and group rank "Take turns".
 *
 * Copy: Wonder v2 (/workspace/mobbin-sites/group-questions-copy-wonder-v2.md)
 * where a key exists. Lines marked NEW are not in Wonder v2 yet; they are
 * listed in the change note for Wonder to review.
 *
 * Pure: no DOM, no fetch, so the node harness can import it.
 */

import { waitingLine } from "./group_instructions.js";

/** Final copy. Help strings are the swap point for Wonder. */
export const GROUP_FLOW_COPY = Object.freeze({
  // MC option B
  stillChoosing: "Still choosing: {names}",
  pickButton: "Send my pick",
  yourPick: "Your pick: {value}", // NEW
  picksLabel: "Your group's picks", // NEW (list label, a11y + caption)
  you: "You",
  whyPlaceholder: "Why this answer?",
  sendReason: "Add your why to send.",
  send: "Send group answer",
  sent: "{name} sent your group's answer.",
  locked: "Your group's answer is locked.",
  mcInstruction: "Everyone in your group picks the same answer to send it.",
  matchReason: "Everyone needs the same pick ({matched} of {total} match)",
  // Rank take turns
  rankInstruction:
    "Take turns moving the cards. Talk it through, then lock in your ranking together.",
  yourTurn: "Your turn. Place one item.", // Wonder v1
  blocked: "You've placed one. Waiting for {names}.",
  placed: "Placed in spot {n}.",
  undo: "Undo",
  done: "Your group's order is in.",
  readyToSend: "Every card is placed. Send when your team agrees.",
  skipped: "{name}'s turn was skipped.",
  skippedYou: "Your turn was skipped.", // gate LOW-2
  waitingFor: "Waiting for {names}.", // gate LOW-2, NEW for the Wonder pass
  spot: "Spot {n}",
  placedBy: "placed by {name}",
  check: "Check",
  sendRanking: "Send our ranking",
  finalOnce: "Final once sent",
});

/**
 * Final ranking help popover. One string so a later edit stays in one place.
 */
export const RANK_CHECK_HELP =
  "Your group gets one check, and anyone can use it at any time. It shows how many cards are in the right spot. Send your ranking whenever you're ready, but you can't change it once it's sent. Get every card right without the check to earn two bonus participation points.";

/**
 * Group multiple-choice help popover. Same sentence as the instruction line.
 */
export const MC_MATCH_HELP =
  "Everyone in your group picks the same answer to send it.";

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

/**
 * Join first names with ", ".
 * @param {unknown[]} names
 * @returns {string}
 */
function nameList(names) {
  // The server already shortens names (MCK-155 gate LOW-3); show as sent,
  // exact repeats once.
  return [
    ...new Set(
      (Array.isArray(names) ? names : [])
        .map((name) => String(name || "").trim().replace(/\s+/g, " "))
        .filter(Boolean)
    ),
  ].join(", ");
}

/**
 * "Still choosing: Ben, Cy" or "".
 * @param {unknown[]} names
 * @returns {string}
 */
export function stillChoosingLine(names) {
  const list = nameList(names);
  return list ? GROUP_FLOW_COPY.stillChoosing.replace("{names}", list) : "";
}

/**
 * Which MC option B step this card is on.
 * @param {any} group ``item.group_submit``
 * @returns {"pick"|"wait"|"agree"|"locked"|""} Empty when not option B.
 */
export function mcFlowStep(group) {
  if (String(group?.flow || "") !== "pick_then_agree") return "";
  if (group.locked || group.submitted) return "locked";
  if (group.pick_step) return String(group.my_pick || "") ? "wait" : "pick";
  return "agree";
}

/**
 * Option buttons (shared by step 1 and step 2).
 * @param {string[]} choices
 * @param {string[]} optionsHtml Trusted authored option HTML, by index.
 * @param {string} selected
 * @returns {string}
 */
function choiceButtons(choices, optionsHtml, selected) {
  return choices
    .map((label, index) => {
      const body = String(optionsHtml[index] || "").trim()
        ? `<span class="live-question-html">${optionsHtml[index]}</span>`
        : esc(label);
      return `<button type="button" class="prompt-choice${
        String(label) === selected ? " is-selected" : ""
      }" data-live-choice="${esc(label)}">${body}</button>`;
    })
    .join("");
}

/**
 * Step 1: pick alone ("Send my pick"), or my pick + who is still choosing.
 * @param {any} group
 * @param {{choices: string[], optionsHtml?: string[], draftChoice?: string, itemId: number}} opts
 * @returns {string}
 */
export function mcPickStepHtml(group, opts) {
  const C = GROUP_FLOW_COPY;
  const mine = String(group?.my_pick || "");
  if (mine) {
    const id = `group-wait-${Number(opts.itemId) || 0}`;
    const reason = waitingLine(Math.max(Number(group?.waiting_count) || 0, (group?.waiting_names || []).length));
    const names = stillChoosingLine(group?.waiting_names);
    return `<div class="student-group-wait" data-mc-step="wait">
      <p class="student-group-my-pick">${esc(C.yourPick.replace("{value}", mine))}</p>
      <p class="student-group-wait-reason" id="${id}" aria-live="polite">${esc(reason)}${
        names ? `<br><span class="student-group-still-writing">${esc(names)}</span>` : ""
      }</p>
    </div>`;
  }
  const selected = String(opts.draftChoice || "");
  return `<div class="student-live-answer-controls" data-live-action="group-pick" data-mc-step="pick">
    ${choiceButtons(opts.choices || [], opts.optionsHtml || [], selected)}
    <button type="button" class="prompt-submit" data-group-pick-send${selected ? "" : " disabled"}>${esc(C.pickButton)}</button>
  </div>`;
}

/**
 * "Ava · 2", "You · 1": each teammate's own pick (own team only).
 * @param {any[]} picks ``group.member_picks``
 * @returns {string}
 */
export function mcMemberPicksHtml(picks) {
  const rows = Array.isArray(picks) ? picks : [];
  if (!rows.length) return "";
  const C = GROUP_FLOW_COPY;
  return `<ul class="student-group-picks" aria-label="${esc(C.picksLabel)}">${rows
    .map((row) => {
      const who = row?.mine ? C.you : String(row?.name || "");
      return `<li${row?.mine ? ' class="is-mine"' : ""}><span class="student-group-pick-name">${esc(who)}</span> · <span class="student-group-pick-value">${esc(row?.value || "")}</span></li>`;
    })
    .join("")}</ul>`;
}

/**
 * Step 2: picks, then the group's choice, the Why, and Send.
 * @param {any} group
 * @param {{choices: string[], optionsHtml?: string[], choice: string, why: string, itemId: number}} opts
 * @returns {string}
 */
export function mcAgreeStepHtml(group, opts) {
  const C = GROUP_FLOW_COPY;
  const ready = Boolean(String(opts.choice || "").trim() && String(opts.why || "").trim());
  const id = `group-send-reason-${Number(opts.itemId) || 0}`;
  return `${mcMemberPicksHtml(group?.member_picks)}
    <div class="student-live-answer-controls" data-live-action="group" data-mc-step="agree">
      ${choiceButtons(opts.choices || [], opts.optionsHtml || [], String(opts.choice || ""))}
      <label class="group-submit-why">Why
        <textarea data-group-why rows="2" maxlength="500" placeholder="${esc(C.whyPlaceholder)}">${esc(opts.why || "")}</textarea>
      </label>
      <button type="button" class="prompt-submit" data-live-submit="group" aria-describedby="${id}"${ready ? "" : " disabled"}>${esc(C.send)}</button>
      <p class="student-group-send-reason" id="${id}" data-group-send-reason${ready ? " hidden" : ""}>${esc(C.sendReason)}</p>
    </div>`;
}

/**
 * Locked: who sent it, the answer and why (read-only). No change button.
 * @param {any} group
 * @returns {string}
 */
export function mcLockedHtml(group) {
  const C = GROUP_FLOW_COPY;
  // Full display name: the server already sends first name only, and
  // "Sam 2" must not read as "Sam" (MCK-183).
  const who = String(group?.last_submitter || "").trim();
  return `<div class="student-group-locked" data-mc-step="locked" aria-live="polite">
    ${who ? `<p class="student-group-sent">${esc(C.sent.replace("{name}", who))}</p>` : ""}
    <p class="student-group-final"><strong>${esc(group?.submitted_choice || "")}</strong>${
      group?.submitted_why ? ` · ${esc(group.submitted_why)}` : ""
    }</p>
    <p class="student-group-locked-note">${esc(C.locked)}</p>
  </div>`;
}

/**
 * The one cue line for a take-turns card.
 * @param {any} turns ``group.turns``
 * @returns {string}
 */
/**
 * Letter for a pick, A for the first choice.
 * @param {string[]} choices
 * @param {unknown} value
 * @returns {string}
 */
export function choiceLetter(choices, value) {
  const list = Array.isArray(choices) ? choices : [];
  const index = list.findIndex((label) => String(label) === String(value ?? ""));
  if (index >= 0 && index < 26) return String.fromCharCode(65 + index);
  const text = String(value ?? "").trim();
  return text ? text.slice(0, 1).toUpperCase() : "";
}

/**
 * The sentence a locked ranking must not keep showing.
 * Matches ``RACE_AGREE_RESET_COPY`` in ``lms/school_db.py``.
 */
export const RANK_ORDER_CHANGED_NOTE =
  "The order changed. Agree again when you're ready.";

/**
 * Help control and its popover. ``onCheck`` draws the small ? on the Check button.
 * @param {string} id
 * @param {string} label Accessible name.
 * @param {string} copy
 * @param {{onCheck?: boolean}} [opts]
 * @returns {string}
 */
function helpControl(id, label, copy, opts = {}) {
  const onCheck = Boolean(opts.onCheck);
  return `<button type="button" class="group-help${onCheck ? " is-on-check" : ""}" data-group-help aria-label="${esc(
    label
  )}" aria-expanded="false" aria-controls="${esc(id)}">?</button>
    <p class="group-help-pop" id="${esc(id)}" role="dialog" hidden>${esc(copy)}</p>`;
}

/**
 * The one cue line for a take-turns card.
 * @param {any} turns ``group.turns``
 * @param {boolean} [pendingSend] Complete order that has not been sent.
 * @returns {string}
 */
export function rankTurnCue(turns, pendingSend = false) {
  const C = GROUP_FLOW_COPY;
  if (!turns) return "";
  if (turns.done) return pendingSend ? C.readyToSend : C.done;
  if (turns.can_place) return C.yourTurn;
  if (Number(turns.placed_spot) > 0 && turns.can_undo) {
    return C.placed.replace("{n}", String(Number(turns.placed_spot)));
  }
  const names = nameList(turns.waiting_names);
  if (!names) return "";
  // Gate LOW-2: "You've placed one" only after this student placed; a
  // skipped student just waits.
  return (turns.placed_last ? C.blocked : C.waitingFor).replace("{names}", names);
}

/**
 * True when ``next`` is an older Take turns card than ``prev`` (a slow
 * POST reply landing after a newer /state). ``rev`` only ever grows.
 * @param {any} prev Current ``group_submit``.
 * @param {any} next Incoming ``group_submit``.
 * @returns {boolean}
 */
export function turnsCardIsStale(prev, next) {
  const a = Number(prev?.turns?.rev);
  const b = Number(next?.turns?.rev);
  return Boolean(prev?.turns && next?.turns) && Number.isFinite(a) && Number.isFinite(b) && b < a;
}

/**
 * Take-turns card: spots, remaining options, one cue line, Undo.
 * @param {any} group ``item.group_submit`` with ``turns``
 * @param {{options: {id: string, label: string}[], itemId: number, note?: string}} opts
 *   ``note`` is a one-off server message (e.g. a 409 conflict line).
 * @returns {string}
 */
export function rankTurnsHtml(group, opts) {
  const C = GROUP_FLOW_COPY;
  const turns = group?.turns || {};
  const spots = Array.isArray(turns.spots) ? turns.spots : [];
  const options = Array.isArray(opts.options) ? opts.options : [];
  const placed = new Set(spots.map((s) => String(s.option_id)));
  const total = Number(turns.total) || options.length;
  const canPlace = Boolean(turns.can_place) && !turns.done;
  const spotRows = [];
  for (let i = 0; i < total; i += 1) {
    const spot = spots[i];
    const n = i + 1;
    if (spot) {
      const who = spot.mine ? C.you : String(spot.by_name || "");
      spotRows.push(
        `<li class="rank-turn-spot is-filled" data-rank-spot="${esc(spot.option_id)}" aria-label="${esc(
          `${C.spot.replace("{n}", String(n))}, ${spot.label || ""}, ${C.placedBy.replace("{name}", who)}`
        )}"><span class="rank-turn-n">${n}</span><span class="rank-turn-label">${esc(spot.label || "")}</span><span class="rank-turn-by">${esc(who)}</span></li>`
      );
    } else {
      const next = i === spots.length && canPlace;
      spotRows.push(
        `<li class="rank-turn-spot${next ? " is-next" : ""}"><span class="rank-turn-n">${n}</span><span class="rank-turn-label is-empty">${esc(
          C.spot.replace("{n}", String(n))
        )}</span></li>`
      );
    }
  }
  const remaining = options.filter((opt) => !placed.has(String(opt.id)));
  const optionRows = turns.done
    ? ""
    : `<div class="rank-turn-options" role="group">${remaining
        .map(
          (opt) =>
            `<button type="button" class="prompt-choice rank-turn-option" data-rank-turn="${esc(opt.id)}"${
              canPlace ? "" : ' disabled aria-disabled="true"'
            }>${esc(opt.label)}</button>`
        )
        .join("")}</div>`;
  const skipped = [
    ...(turns.skipped_me ? [C.skippedYou] : []),
    ...(Array.isArray(turns.skipped_names) ? turns.skipped_names : []).map((name) =>
      C.skipped.replace("{name}", String(name))
    ),
  ]
    .map((line) => `<p class="rank-turn-skipped">${esc(line)}</p>`)
    .join("");
  // MCK-184: Undo is only for the teammate whose pick is the last spot.
  // MCK-310: it sits beside Check, not in the cue line.
  const lastSpot = spots.length ? spots[spots.length - 1] : null;
  const undo = turns.can_undo && lastSpot && lastSpot.mine === true
    ? `<button type="button" class="link-button rank-turn-undo" data-rank-turn-undo>${esc(C.undo)}</button>`
    : "";
  const note = String(opts.note || "").trim();
  const quietNote =
    note === RANK_ORDER_CHANGED_NOTE && (group?.race?.locked || group?.submitted) ? "" : note;
  const check = group?.check && typeof group.check === "object" ? group.check : {};
  const header = Boolean(opts.header);
  const itemId = Number(opts.itemId) || 0;
  const helpId = `rank-help-${itemId}`;
  const checkControl = check.used
    ? `<p class="rank-check-chip" role="status">${esc(check.message || "")}</p>`
    : check.available
      ? `<button type="button" class="secondary rank-check" data-rank-check>${esc(C.check)}</button>`
      : "";
  // The instruction and the check help render on every student ranking
  // card. ``header`` only adds the pinned Send our ranking (a team
  // challenge uses Lock in our order instead).
  const checkWrap = checkControl
    ? `<div class="rank-check-wrap">${checkControl}${helpControl(
        helpId,
        "How Check works",
        RANK_CHECK_HELP,
        { onCheck: true }
      )}</div>`
    : "";
  const checkRow = checkWrap || undo
    ? `<div class="rank-check-row">${checkWrap}${undo}</div>`
    : "";
  const pendingSend = header && Boolean(turns.done) && !group?.submitted;
  const sendPin = header
    ? `<div class="rank-send-pin">
        ${
          group?.submitted
            ? ""
            : `<button type="button" class="prompt-submit" data-live-submit="group"${
                turns.done ? "" : " disabled"
              }>${esc(C.sendRanking)}</button>`
        }
        <p class="rank-send-note">${esc(C.finalOnce)}</p>
      </div>`
    : "";
  const instruction = `<p class="group-flow-instruction">${esc(C.rankInstruction)}</p>`;
  return `<div class="rank-turns" data-rank-turns="${itemId}" data-turn-rev="${Number(turns.rev) || 0}">
    ${instruction}
    ${checkRow}
    <p class="rank-turn-cue" aria-live="polite">${esc(rankTurnCue(turns, pendingSend))}</p>
    <ol class="rank-turn-spots">${spotRows.join("")}</ol>
    ${optionRows}
    ${skipped}
    ${quietNote ? `<p class="rank-turn-note" role="status">${esc(quietNote)}</p>` : ""}
    ${sendPin}
  </div>`;
}

/**
 * Student live take-turns ranking card.
 *
 * The student portal paints this for a published group rank, including a
 * team-challenge race (``opts.race``). The instruction line and the check
 * help always render. A race does not add Send our ranking; the caller
 * passes the lock control as trusted HTML.
 *
 * @param {any} group ``item.group_submit``
 * @param {{options: {id: string, label: string}[], itemId: number, note?: string, lockRow?: string, strip?: string, race?: boolean}} opts
 * @returns {string}
 */
export function liveRankCardHtml(group, opts) {
  const race = Boolean(opts.race);
  const strip = String(opts.strip || "").trim();
  const body = rankTurnsHtml(group, {
    options: opts.options,
    itemId: opts.itemId,
    note: opts.note || "",
    header: !race,
  });
  return `<div class="student-group-card${race ? " race-phone" : ""}" data-live-action="group" data-rank-turns-card="1">
    ${!race && strip ? `<p class="student-group-strip">${esc(strip)}</p>` : ""}
    ${body}
    ${race ? String(opts.lockRow || "") : ""}
  </div>`;
}

/**
 * Group multiple choice: one instruction, send waits until the picks match.
 *
 * Teammate names and letters stay off the card. Students talk to match.
 * The server still decides ``match.ready``.
 *
 * @param {any} group
 * @param {{choices: string[], optionsHtml?: string[], itemId: number}} opts
 * @returns {string}
 */
export function mcMatchHtml(group, opts) {
  const C = GROUP_FLOW_COPY;
  const itemId = Number(opts.itemId) || 0;
  const match = group?.match && typeof group.match === "object" ? group.match : {};
  const matched = Number(match.matched) || 0;
  const total = Number(match.total) || 0;
  const ready = Boolean(match.ready);
  const mine = String(group?.my_pick || "");
  const reason = C.matchReason
    .replace("{matched}", String(matched))
    .replace("{total}", String(total));
  return `<div class="mc-match" data-mc-match="1">
    <p class="group-flow-instruction">${esc(C.mcInstruction)}</p>
    <div class="student-live-answer-controls" data-live-action="group-pick" data-mc-step="match">
      ${choiceButtons(opts.choices || [], opts.optionsHtml || [], mine)}
    </div>
    <button type="button" class="prompt-submit" data-live-submit="group" aria-describedby="group-send-reason-${itemId}"${
      ready ? "" : " disabled"
    }>${esc(C.send)}</button>
    <p class="student-group-send-reason" id="group-send-reason-${itemId}" data-group-send-reason${
      ready ? " hidden" : ""
    }>${esc(reason)}</p>
  </div>`;
}
