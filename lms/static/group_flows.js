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

/** Final copy. */
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
  // Rank take turns
  yourTurn: "Your turn. Place one item.", // Wonder v1
  blocked: "You've placed one. Waiting for {names}.",
  placed: "Placed in spot {n}.",
  undo: "Undo",
  done: "Your group's order is in.",
  skipped: "{name}'s turn was skipped.",
  spot: "Spot {n}",
  placedBy: "placed by {name}",
});

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
  const who = String(group?.last_submitter || "").trim().split(/\s+/)[0] || "";
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
export function rankTurnCue(turns) {
  const C = GROUP_FLOW_COPY;
  if (!turns) return "";
  if (turns.done) return C.done;
  if (turns.can_place) return C.yourTurn;
  if (Number(turns.placed_spot) > 0 && turns.can_undo) {
    return C.placed.replace("{n}", String(Number(turns.placed_spot)));
  }
  const names = nameList(turns.waiting_names);
  return names ? C.blocked.replace("{names}", names) : "";
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
        `<li class="rank-turn-spot is-filled" aria-label="${esc(
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
  const skipped = (Array.isArray(turns.skipped_names) ? turns.skipped_names : [])
    .map((name) => `<p class="rank-turn-skipped">${esc(C.skipped.replace("{name}", String(name)))}</p>`)
    .join("");
  const undo = turns.can_undo
    ? ` <button type="button" class="link-button rank-turn-undo" data-rank-turn-undo>${esc(C.undo)}</button>`
    : "";
  const note = String(opts.note || "").trim();
  return `<div class="rank-turns" data-rank-turns="${Number(opts.itemId) || 0}" data-turn-rev="${Number(turns.rev) || 0}">
    <ol class="rank-turn-spots">${spotRows.join("")}</ol>
    ${optionRows}
    ${skipped}
    ${note ? `<p class="rank-turn-note" role="status">${esc(note)}</p>` : ""}
    <p class="rank-turn-cue" aria-live="polite">${esc(rankTurnCue(turns))}${undo}</p>
  </div>`;
}
