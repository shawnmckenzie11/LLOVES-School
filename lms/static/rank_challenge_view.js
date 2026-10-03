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
  // race.options is the server display order (MCK-176 hook); the item's
  // authored options are only a fallback for an older payload.
  const shown = Array.isArray(race?.options) && race.options.length
    ? race.options.map((row) => String(row?.label || "").trim())
    : Array.isArray(opts.options) ? opts.options : [];
  const optionLine = shown.filter(Boolean).join(" · ");
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

// ---------------------------------------------------------------------------
// Results moment (R6, IA v0.2 F4): rows, then points, then the podium.
// Teacher-paced by Next; 2 points per right spot, nothing else.
// ---------------------------------------------------------------------------

/** Wonder v3 strings (final, Oct 3) for the results moment. */
export const RESULTS_COPY = Object.freeze({
  revealTitle: "The right order", // results.reveal.title
  pointsTitle: "Points", // results.points.title
  podiumTitle: "Top teams", // results.podium.title
  spot: "Spot {n} · {item}", // results.spot
  right: "{k} of {n} spots right.", // results.right
  rightAll: "All {n} spots right.", // results.right.all
  points: "{pts} points each", // results.points
  pointsOne: "1 point each", // results.points.one
  spotChip: "Spot {k} of {n}", // results.spot.chip
  fromDraft: "Scored from your group's draft.", // results.from_draft
  tie: "{n} teams tied", // results.tie
  others: "Also on the board", // results.others
  next: "Next", // race.next
  done: "Back to question", // results.done
  absent: "No one here yet", // race.lane.absent
});

/**
 * "{pts} points each" / "1 point each".
 * @param {number} pts
 * @returns {string}
 */
export function pointsText(pts) {
  const n = Number(pts) || 0;
  return n === 1 ? RESULTS_COPY.pointsOne : fill(RESULTS_COPY.points, { pts: n });
}

/**
 * "{k} of {n} spots right." / "All {n} spots right.".
 * @param {number} k
 * @param {number} n
 * @returns {string}
 */
export function rightText(k, n) {
  return Number(k) === Number(n) && Number(n) > 0
    ? fill(RESULTS_COPY.rightAll, { n })
    : fill(RESULTS_COPY.right, { k: Number(k) || 0, n: Number(n) || 0 });
}

/**
 * Steps of the reveal: 0..n rows (n = spots), n+1 points, n+2 podium.
 * @param {any} results Teacher ``race.results``.
 * @returns {number} The last step index.
 */
export function lastResultsStep(results) {
  return (Number(results?.total) || 0) + 2;
}

/**
 * Header shared by the three results frames.
 * @param {string} title
 * @param {string} sub
 * @param {string} chip
 * @returns {string}
 */
function resultsHead(title, sub, chip) {
  return `<header class="race-view-head">
      <div class="race-view-q">
        <p class="race-view-stem">${esc(title)}</p>
        ${sub ? `<p class="race-view-sub">${esc(sub)}</p>` : ""}
      </div>
      ${chip ? `<p class="race-view-count">${esc(chip)}</p>` : ""}
    </header>`;
}

/**
 * Rows frame: the answer, one spot per Next. ✓ in the team colour or a
 * neutral grey ring; no red ✗, no names. "—" for a team that placed nothing.
 * @param {any} results
 * @param {number} shown Rows revealed so far (0..n).
 * @param {string} stem
 * @returns {string}
 */
function rowsFrame(results, shown, stem) {
  const teams = Array.isArray(results?.teams) ? results.teams : [];
  const spots = Array.isArray(results?.spots) ? results.spots : [];
  const head = teams
    .map((team, i) => {
      const slot = Number.isFinite(Number(team.slot)) ? Number(team.slot) : i;
      const mark = teamMark(slot);
      return `<th scope="col" style="--team:${mark.colour}"><span class="race-chip" aria-hidden="true">${mark.shape}</span><span class="race-col-name">${esc(
        team.team_name
      )}</span></th>`;
    })
    .join("");
  const rows = spots
    .map((spot, index) => {
      const open = index < shown;
      const cells = teams
        .map((team, i) => {
          const slot = Number.isFinite(Number(team.slot)) ? Number(team.slot) : i;
          const colour = teamMark(slot).colour;
          if (!open) return `<td></td>`;
          if (!team.scored) return `<td><span class="race-mark is-none" aria-label="no answer">—</span></td>`;
          const right = Boolean(spot.teams?.[String(team.team_id)]);
          return right
            ? `<td><span class="race-mark is-right" style="--team:${colour}" aria-label="right">✓</span></td>`
            : `<td><span class="race-mark is-not" aria-label="not this one"></span></td>`;
        })
        .join("");
      const label = open
        ? fill(RESULTS_COPY.spot, { n: spot.n, item: spot.item })
        : fill(RESULTS_COPY.spot, { n: spot.n, item: "?" });
      return `<tr class="${open ? "is-open" : "is-hidden"}${index === shown - 1 ? " is-latest" : ""}"><th scope="row">${esc(label)}</th>${cells}</tr>`;
    })
    .join("");
  const total = spots.length;
  const chip = shown >= 1 && total ? fill(RESULTS_COPY.spotChip, { k: Math.min(shown, total), n: total }) : "";
  return `${resultsHead(RESULTS_COPY.revealTitle, stem, chip)}
    <table class="race-reveal"><thead><tr><td></td>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}

/**
 * Points frame: one base-only bar per team, fixed team order.
 * @param {any} results
 * @returns {string}
 */
function pointsFrame(results) {
  const teams = Array.isArray(results?.teams) ? results.teams : [];
  const total = Math.max(1, Number(results?.total) || 1);
  const rows = teams
    .map((team, i) => {
      const slot = Number.isFinite(Number(team.slot)) ? Number(team.slot) : i;
      const mark = teamMark(slot);
      const name = `<span class="race-chip" aria-hidden="true">${mark.shape}</span><span class="race-lane-name">${esc(team.team_name)}</span>`;
      if (!team.scored) {
        return `<li class="race-points-row is-absent" style="--team:${mark.colour}">${name}<span class="race-bar-empty">${esc(
          RESULTS_COPY.absent
        )}</span><span class="race-points-total">—</span></li>`;
      }
      const width = Math.max(18, Math.round((Number(team.right) / total) * 100));
      // The bar is the team's share of spots (max 50% of the track); the
      // "{k} of {n} spots right." label sits after it so it never clips.
      // Wonder: a team that never locked in was scored on its draft; say so
      // under its bar (never for a locked-in team).
      const draft = team.locked ? "" : `<span class="race-bar-note">${esc(RESULTS_COPY.fromDraft)}</span>`;
      return `<li class="race-points-row" style="--team:${mark.colour}">${name}<span class="race-bar-cell"><span class="race-bar-track"><span class="race-bar" style="width:${Math.round(
        width * 0.5
      )}%"></span><span class="race-bar-label">${esc(rightText(team.right, team.total))}</span></span>${draft}</span><span class="race-points-total">${esc(
        pointsText(team.points)
      )}</span></li>`;
    })
    .join("");
  return `${resultsHead(RESULTS_COPY.pointsTitle, "", "")}<ol class="race-points">${rows}</ol>`;
}

/**
 * Podium frame: top 3 distinct totals; a tie shares a step ("{n} teams
 * tied"); everyone else listed alphabetically under "Also on the board"
 * with no place number.
 * @param {any} results
 * @returns {string}
 */
function podiumFrame(results) {
  const teams = Array.isArray(results?.teams) ? results.teams : [];
  const byId = new Map(teams.map((team, i) => [Number(team.team_id), { ...team, slot: Number.isFinite(Number(team.slot)) ? Number(team.slot) : i }]));
  const steps = Array.isArray(results?.podium?.steps) ? results.podium.steps : [];
  const stepHtml = (step) => {
    if (!step) return `<div class="race-step is-empty"></div>`;
    const ids = Array.isArray(step.team_ids) ? step.team_ids : [];
    const names = ids
      .map((id) => byId.get(Number(id)))
      .filter(Boolean)
      .map((team) => {
        const mark = teamMark(team.slot);
        return `<span class="race-step-team" style="--team:${mark.colour}"><span class="race-chip" aria-hidden="true">${mark.shape}</span>${esc(
          team.team_name
        )}</span>`;
      })
      .join("");
    const tie = ids.length > 1 ? `<span class="race-step-tie">${esc(fill(RESULTS_COPY.tie, { n: ids.length }))}</span>` : "";
    return `<div class="race-step is-step-${step.step}">
        <div class="race-step-who">${tie}${names}<span class="race-step-pts">${esc(pointsText(step.points))}</span></div>
        <div class="race-step-block"><span>${step.step}</span></div>
      </div>`;
  };
  const byStep = new Map(steps.map((step) => [Number(step.step), step]));
  const others = (Array.isArray(results?.podium?.others) ? results.podium.others : [])
    .map((id) => byId.get(Number(id)))
    .filter(Boolean)
    // Each team its own points ("Vectors 2 points each · Sines 0 points each").
    .map((team) => `${esc(team.team_name)} ${esc(pointsText(team.scored ? team.points : 0))}`)
    .join(" · ");
  const confetti = Array.from({ length: 18 }, (_, i) => {
    const colour = teamMark(byId.get(Number(steps[i % Math.max(1, steps.length)]?.team_ids?.[0]))?.slot ?? i).colour;
    return `<i style="--i:${i};--c:${colour}"></i>`;
  }).join("");
  return `${resultsHead(RESULTS_COPY.podiumTitle, "", "")}
    <div class="race-podium">
      <div class="race-confetti" aria-hidden="true">${steps.length ? confetti : ""}</div>
      ${stepHtml(byStep.get(2))}${stepHtml(byStep.get(1))}${stepHtml(byStep.get(3))}
    </div>
    ${others ? `<p class="race-others">${esc(RESULTS_COPY.others)}: ${others}</p>` : ""}`;
}

/**
 * The teacher's results moment on the rank card.
 * @param {any} results Teacher ``race.results``.
 * @param {number} liveItemId
 * @param {{step?: number, stem?: string, busy?: boolean}} [opts] ``step``:
 *   0..n rows shown, n+1 points, n+2 podium (the server's step; Next asks
 *   for exactly step+1). ``busy``: a Next POST is in flight, so Next is
 *   disabled (a double-click can't skip a screen).
 * @returns {string}
 */
export function raceResultsHtml(results, liveItemId, opts = {}) {
  const id = Number(liveItemId) || 0;
  const n = Number(results?.total) || 0;
  const last = lastResultsStep(results);
  const step = Math.max(0, Math.min(last, Number(opts.step) || 0));
  let frame;
  let phase;
  if (step <= n) {
    frame = rowsFrame(results, step, String(opts.stem || ""));
    phase = "rows";
  } else if (step === n + 1) {
    frame = pointsFrame(results);
    phase = "points";
  } else {
    frame = podiumFrame(results);
    phase = "podium";
  }
  const button =
    step < last
      ? `<button type="button" class="race-view-close" data-race-next="${id}"${opts.busy ? " disabled" : ""}>${esc(RESULTS_COPY.next)} ▸</button>`
      : `<button type="button" class="secondary race-view-exit" data-race-done="${id}">${esc(RESULTS_COPY.done)}</button>`;
  return `<section class="race-view race-results is-${phase}" data-race-results="${id}" data-race-step="${step}" aria-label="Team challenge results">
    ${frame}
    <footer class="race-view-foot">${button}</footer>
  </section>`;
}
