/**
 * MCK-154 S1: one vertical rank results stack (spec:
 * group-questions-mck154-155-ia-v0.md §1.3, copy: Wonder v2).
 *
 * One table, one row per option, ordered by class place (Borda display
 * order from ``live_rank.borda_class_order``; ties share a place). The
 * response count is printed once, above the table. Option labels are
 * printed once. Each group is one narrow column holding that group's
 * place for the option, so team orders no longer repeat every label.
 *
 * Teacher: place, option, score bar + Borda points, one column per group.
 * Student / projector (closed, results on): place and option only (no
 * score), with the student's own group column first and headed "You".
 *
 * Pure: no DOM, no fetch, so the node harness can import it.
 */

/** Final copy (Wonder v2 keys ``rank.results.*``). */
export const RANK_STACK_COPY = Object.freeze({
  statusGroups: "{k} of {n} groups sent",
  statusStudents: "{k} of {n} answered",
  you: "You",
  option: "Option",
  place: "#",
  score: "Score",
  waiting: "waiting",
  last: "last: {name}",
  caption: "Class order",
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
 * Fill ``{k}`` style tokens.
 * @param {string} template
 * @param {Record<string, unknown>} values
 * @returns {string}
 */
export function fillCopy(template, values) {
  return String(template || "").replace(/\{(\w+)\}/g, (_m, key) =>
    values && values[key] != null ? String(values[key]) : ""
  );
}

/**
 * Short column header for one group: the name when it is short, else
 * its first 6 characters. Full name stays in ``title``.
 * @param {unknown} name
 * @returns {string}
 */
export function shortTeamLabel(name) {
  const text = String(name || "").trim() || "Group";
  return text.length <= 7 ? text : text.slice(0, 6);
}

/**
 * The one status line: "3 of 4 groups sent" / "18 of 23 answered".
 * @param {any} rank
 * @returns {string}
 */
export function rankStackStatus(rank) {
  const team = String(rank?.unit || "student") === "team";
  const teams = Array.isArray(rank?.teams) ? rank.teams : [];
  const k = Math.max(0, Number(rank?.responded) || 0);
  const n = Math.max(k, Number(rank?.present) || (team ? teams.length : 0) || 0);
  return fillCopy(team ? RANK_STACK_COPY.statusGroups : RANK_STACK_COPY.statusStudents, { k, n });
}

/**
 * Group columns in display order. On the student side the student's own
 * group comes first and is headed "You".
 * @param {any[]} teams
 * @param {{audience?: string, ownTeamId?: unknown, lastByTeam?: Map<number, string>}} opts
 * @returns {{teamId: number, label: string, title: string, places: Map<string, number>, waiting: boolean, own: boolean}[]}
 */
export function rankStackColumns(teams, opts = {}) {
  const student = opts.audience === "student";
  const own = Number(opts.ownTeamId) || 0;
  const last = opts.lastByTeam instanceof Map ? opts.lastByTeam : new Map();
  const cols = (Array.isArray(teams) ? teams : []).map((row) => {
    const teamId = Number(row?.team_id) || 0;
    const order = Array.isArray(row?.order) ? row.order.map((id) => String(id)) : [];
    const places = new Map(order.map((id, index) => [id, index + 1]));
    const waiting = order.length === 0;
    const isOwn = student && own > 0 && teamId === own;
    const name = String(row?.team_name || "Group");
    const lastName = !student ? String(last.get(teamId) || "").trim() : "";
    const titleParts = [name];
    if (waiting) titleParts.push(RANK_STACK_COPY.waiting);
    else if (lastName) titleParts.push(fillCopy(RANK_STACK_COPY.last, { name: lastName }));
    return {
      teamId,
      label: isOwn ? RANK_STACK_COPY.you : shortTeamLabel(name),
      title: titleParts.join(" · "),
      places,
      waiting,
      own: isOwn,
    };
  });
  if (student && own > 0) {
    cols.sort((a, b) => Number(b.own) - Number(a.own));
  }
  return cols;
}

/**
 * Render the one vertical stack.
 *
 * @param {any} rank Server rank collate: ``unit``, ``responded``,
 *   ``present``, ``class_order`` rows and (group) ``teams[].order``.
 * @param {{
 *   audience?: "teacher"|"student",
 *   rows?: any[],
 *   ownTeamId?: unknown,
 *   lastByTeam?: Map<number, string>,
 *   showStatus?: boolean,
 * }} [opts] ``rows`` overrides ``rank.class_order`` (the teacher's 1s
 *   display hold).
 * @returns {string} Empty when there are no option rows.
 */
export function rankStackHtml(rank, opts = {}) {
  if (!rank || typeof rank !== "object") return "";
  const student = opts.audience === "student";
  const rows = Array.isArray(opts.rows)
    ? opts.rows
    : Array.isArray(rank.class_order)
      ? rank.class_order
      : Array.isArray(rank.rows)
        ? rank.rows
        : [];
  if (!rows.length) return "";
  const team = String(rank.unit || "student") === "team";
  const cols = team ? rankStackColumns(rank.teams, opts) : [];
  const C = RANK_STACK_COPY;
  const head = `<thead><tr>
      <th scope="col" class="rank-stack-place">${esc(C.place)}</th>
      <th scope="col" class="rank-stack-option">${esc(C.option)}</th>
      ${student ? "" : `<th scope="col" class="rank-stack-score">${esc(C.score)}</th>`}
      ${cols
        .map(
          (col) =>
            `<th scope="col" class="rank-stack-team${col.waiting ? " is-waiting" : ""}${
              col.own ? " is-own" : ""
            }" title="${esc(col.title)}" data-rank-team="${col.teamId}">${esc(col.label)}</th>`
        )
        .join("")}
    </tr></thead>`;
  const body = rows
    .map((row) => {
      const optionId = String(row?.option_id || "");
      const label = String(row?.label || optionId);
      const place = Number(row?.rank) || 0;
      const points = Number(row?.points) || 0;
      const firsts = Number(row?.first_picks) || 0;
      const pct = Math.max(0, Math.min(100, Number(row?.bar_pct) || 0));
      const score = student
        ? ""
        : `<td class="rank-stack-score"><span class="rank-stack-score-inner" aria-label="${points} points, ${firsts} first picks"><span class="rank-bar" aria-hidden="true"><span style="width:${pct}%"></span></span><span class="rank-stack-points" aria-hidden="true">${points}</span></span></td>`;
      const cells = cols
        .map((col) => {
          const at = col.places.get(optionId);
          return `<td class="rank-stack-team${col.waiting ? " is-waiting" : ""}${
            col.own ? " is-own" : ""
          }">${at ? at : `<span aria-label="${esc(C.waiting)}">·</span>`}</td>`;
        })
        .join("");
      return `<tr data-rank-option="${esc(optionId)}">
        <td class="rank-stack-place">${place}</td>
        <th scope="row" class="rank-stack-option" title="${esc(label)}"><span>${esc(label)}</span></th>
        ${score}${cells}
      </tr>`;
    })
    .join("");
  const status =
    opts.showStatus === false ? "" : `<p class="rank-stack-status">${esc(rankStackStatus(rank))}</p>`;
  return `<div class="rank-stack-wrap${student ? " is-student" : ""}" data-rank-cols="${cols.length}">
    ${status}
    <div class="rank-stack-scroll"><table class="rank-stack">
      <caption class="rank-stack-caption">${esc(C.caption)}</caption>
      ${head}<tbody>${body}</tbody>
    </table></div>
  </div>`;
}
