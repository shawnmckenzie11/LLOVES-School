/**
 * MCK-171 Team challenge views (pure HTML builders, no DOM, no fetch).
 *
 * Projector lanes for the teacher's rank card (Mobbin Sites R4, shape B
 * "Lock-in") with Wonder v3 copy. Students never see the word "race";
 * ``race`` only survives in payload keys.
 *
 * Rules kept here: lanes stay in a fixed team order, no place numbers and no
 * "first" anywhere, no names on lanes, state is always text + icon (never
 * colour alone), and the only motion is one soft check pop when a team's
 * order locks (off under reduced motion, in CSS).
 */

/** Wonder v3 strings (final, Oct 3) used on the teacher/projector side. */
export const RACE_COPY = Object.freeze({
  sentCount: "{k} of {n} teams locked in", // race.sent_count
  agreeCount: "{k} of {m} agree", // race.agree.count
  thinking: "Thinking", // race.lane.thinking (Take turns, before the first placement)
  laneSent: "All agreed ✓", // race.lane.sent
  laneTurnsDone: "Locked in ✓", // race.lane.turns_done (also a teacher lock)
  laneAbsent: "No one here yet", // race.lane.absent
  lockFor: "Lock in for {team}", // race.lock_for
  lockForConfirm: "Lock in {team}'s current order?", // race.lock_for.confirm
  skip: "Skip waiting turn", // race.skip
  skipConfirm: "Skip the waiting turn for {team}?", // race.skip.confirm
  exit: "Exit challenge view", // race.exit
  view: "Team challenge view", // race.view
  close: "Close & reveal", // race.close
  chipTogether: "● Group · rank together · challenge", // race.chip.together
  chipTurns: "● Group · take turns · challenge", // race.chip.turns
  // Reused unchanged from #231 (Take turns progress, screen readers).
  placed: "{k} of {n} placed",
});

/** Team shapes, then the same shapes as outlines (IA §2.9). */
export const TEAM_SHAPES = Object.freeze(["●", "▲", "■", "◆", "★", "⬟"]);
export const TEAM_SHAPES_OUTLINE = Object.freeze(["○", "△", "□", "◇", "☆", "⬠"]);
/** Team colours (each ≥ 4.5:1 on white). */
export const TEAM_COLOURS = Object.freeze([
  "#0f766e",
  "#c2410c",
  "#6d28d9",
  "#1d4ed8",
  "#be185d",
  "#4d7c0f",
]);

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
 * Fill ``{key}`` slots.
 * @param {string} text
 * @param {Record<string, unknown>} values
 * @returns {string}
 */
export function fill(text, values) {
  return String(text).replace(/\{(\w+)\}/g, (all, key) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : all
  );
}

/**
 * Shape + colour for a lane slot (fixed team order).
 * @param {number} slot
 * @returns {{shape: string, colour: string}}
 */
export function teamMark(slot) {
  const n = Math.max(0, Number(slot) || 0);
  const i = n % TEAM_SHAPES.length;
  const outline = Math.floor(n / TEAM_SHAPES.length) % 2 === 1;
  return {
    shape: (outline ? TEAM_SHAPES_OUTLINE : TEAM_SHAPES)[i],
    colour: TEAM_COLOURS[n % TEAM_COLOURS.length],
  };
}

/**
 * The team chip: shape in the team colour (decorative; the name is text).
 * @param {number} slot
 * @returns {string}
 */
export function teamChipHtml(slot) {
  const mark = teamMark(slot);
  return `<span class="race-chip" style="--team:${mark.colour}" aria-hidden="true">${mark.shape}</span>`;
}

/**
 * One lane's status line and state.
 * @param {any} lane Teacher ``race.teams[]`` row.
 * @param {"together"|"turns"} mode
 * @returns {{text: string, state: "locked"|"absent"|"thinking"|"agreeing"|"placing"}}
 */
export function laneStatus(lane, mode) {
  const C = RACE_COPY;
  if (lane?.locked) {
    // A teacher lock is "Locked in ✓", never "All agreed ✓" (v0.2 F3).
    const byTeam = mode === "together" && String(lane.locked_by || "team") === "team";
    return { text: byTeam ? C.laneSent : C.laneTurnsDone, state: "locked" };
  }
  if (lane?.absent) return { text: C.laneAbsent, state: "absent" };
  if (mode === "turns") {
    const total = Math.max(0, Number(lane?.total) || 0);
    const placed = Math.min(total, Math.max(0, Number(lane?.placed) || 0));
    if (!placed) return { text: C.thinking, state: "thinking" };
    return { text: fill(C.placed, { k: placed, n: total }), state: "placing" };
  }
  // Rank together counts from 0 ("0 of 3 agree"), one pip per present member.
  const of = Math.max(0, Number(lane?.agree_of) || 0);
  const k = Math.min(of, Math.max(0, Number(lane?.agree) || 0));
  return { text: fill(C.agreeCount, { k, m: of }), state: "agreeing" };
}

/**
 * Pips for one lane. Rank together: a filled check per agreed member, an
 * empty ring per present member still thinking, a dashed ring per away
 * member (never counted). Take turns: one pip per spot, filled once placed.
 * @param {any} lane
 * @param {"together"|"turns"} mode
 * @returns {{kind: "on"|"off"|"away"}[]}
 */
export function lanePips(lane, mode) {
  /** @type {{kind: "on"|"off"|"away"}[]} */
  const pips = [];
  if (mode === "turns") {
    const total = Math.max(0, Number(lane?.total) || 0);
    const placed = lane?.locked ? total : Math.min(total, Math.max(0, Number(lane?.placed) || 0));
    for (let i = 0; i < total; i += 1) pips.push({ kind: i < placed ? "on" : "off" });
    return pips;
  }
  const present = Math.max(0, Number(lane?.agree_of ?? lane?.present) || 0);
  const members = Math.max(present, Number(lane?.members) || 0);
  const agreed = lane?.locked && String(lane.locked_by || "team") === "team"
    ? present
    : Math.min(present, Math.max(0, Number(lane?.agree) || 0));
  for (let i = 0; i < present; i += 1) pips.push({ kind: i < agreed ? "on" : "off" });
  for (let i = present; i < members; i += 1) pips.push({ kind: "away" });
  return pips;
}

/**
 * @param {{kind: string}[]} pips
 * @returns {string}
 */
function pipsHtml(pips) {
  if (!pips.length) return `<span class="race-pips" aria-hidden="true"></span>`;
  return `<span class="race-pips" aria-hidden="true">${pips
    .map((pip) => `<span class="race-pip is-${pip.kind}">${pip.kind === "on" ? "✓" : ""}</span>`)
    .join("")}</span>`;
}

/**
 * Pull the stem off a lifecycle row's question.
 * @param {any} item ``result.item``
 * @returns {string}
 */
export function raceStem(item) {
  const q = item?.item && typeof item.item === "object" ? item.item : item || {};
  return String(q.text || q.question || q.prompt || q.stem || "").trim();
}

/**
 * Option labels (authored order) off a lifecycle row's rank question.
 * @param {any} item ``result.item``
 * @returns {string[]}
 */
export function raceOptionLabels(item) {
  const q = item?.item && typeof item.item === "object" ? item.item : item || {};
  const rows = Array.isArray(q.rank_options) ? q.rank_options : Array.isArray(q.options) ? q.options : [];
  return rows
    .map((row) => (row && typeof row === "object" ? String(row.label || row.text || "") : String(row || "")).trim())
    .filter(Boolean);
}

/**
 * Projector lanes for a live Team challenge (teacher card, mock v1 §1).
 *
 * @param {any} race Teacher ``race`` block.
 * @param {number} liveItemId
 * @param {{popped?: Set<number>, stem?: string, options?: string[]}} [opts]
 *   ``popped``: team ids whose lock-in pop already played (the caller keeps
 *   it per item), so the 300 ms check pop runs once, when the lane first
 *   locks. ``stem`` / ``options``: the question line for the header.
 * @returns {string}
 */
export function raceLanesHtml(race, liveItemId, opts = {}) {
  const C = RACE_COPY;
  const mode = race?.mode === "turns" ? "turns" : "together";
  const teams = Array.isArray(race?.teams) ? race.teams : [];
  const popped = opts.popped instanceof Set ? opts.popped : null;
  const id = Number(liveItemId) || 0;
  const count = fill(C.sentCount, {
    k: Number(race?.teams_locked) || 0,
    n: Number(race?.teams_total) || teams.length,
  });
  const lanes = teams
    .map((lane, index) => {
      const slot = Number.isFinite(Number(lane.slot)) ? Number(lane.slot) : index;
      const mark = teamMark(slot);
      const status = laneStatus(lane, mode);
      const teamId = Number(lane.team_id) || 0;
      const team = String(lane.team_name || "Team");
      let pop = false;
      if (status.state === "locked" && popped && !popped.has(teamId)) {
        popped.add(teamId);
        pop = true;
      }
      let control = "";
      if (mode === "together" && lane.can_lock) {
        control = `<button type="button" class="secondary race-lane-btn" data-race-lock="${id}" data-team-id="${teamId}" data-team-name="${esc(
          team
        )}">${esc(fill(C.lockFor, { team }))}</button>`;
      } else if (mode === "turns" && lane.can_skip) {
        control = `<button type="button" class="secondary race-lane-btn" data-race-skip="${id}" data-team-id="${teamId}" data-team-name="${esc(
          team
        )}">${esc(C.skip)}</button>`;
      }
      const flag =
        status.state === "locked"
          ? `<span class="race-lane-flag" aria-hidden="true">✓</span>`
          : `<span class="race-lane-flag is-empty" aria-hidden="true"></span>`;
      return `<li class="race-lane is-${status.state}${pop ? " is-popping" : ""}" data-race-team="${teamId}" style="--team:${mark.colour}">
        ${teamChipHtml(slot)}
        <span class="race-lane-name">${esc(team)}</span>
        ${status.state === "absent" ? `<span class="race-pips" aria-hidden="true"></span>` : pipsHtml(lanePips(lane, mode))}
        ${flag}
        <span class="race-lane-status">${esc(status.text)}</span>
        <span class="race-lane-ctl">${control}</span>
      </li>`;
    })
    .join("");
  const stem = String(opts.stem || "").trim();
  const optionLine = Array.isArray(opts.options) ? opts.options.filter(Boolean).join(" · ") : "";
  const chip = mode === "turns" ? C.chipTurns : C.chipTogether;
  return `<section class="race-view" data-race-view="${id}" data-race-mode="${mode}" aria-label="Team challenge">
    <header class="race-view-head">
      <div class="race-view-q">
        ${stem ? `<p class="race-view-stem">${esc(stem)}</p>` : ""}
        <p class="race-view-sub">${optionLine ? `${esc(optionLine)} · ` : ""}${esc(chip)}</p>
      </div>
      <p class="race-view-count" role="status">${esc(count)}</p>
    </header>
    <ol class="race-lanes">${lanes}</ol>
    <footer class="race-view-foot">
      <button type="button" class="secondary race-view-exit" data-race-view-toggle="${id}">${esc(C.exit)}</button>
      <button type="button" class="race-view-close" data-close-live-item="${id}">${esc(C.close)}</button>
    </footer>
  </section>`;
}
