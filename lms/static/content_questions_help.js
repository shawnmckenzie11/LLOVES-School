/**
 * MCK-79: pure helpers for the Contest Questions section of Import from bank.
 * Rows are contest ``live_problems`` (see ``live_content_questions.py``),
 * grouped by module or in one course-wide ``COURSE`` "Contest" group.
 * Identifiers keep the earlier ``content`` spelling.
 *
 * bank_mc_picker.js owns the DOM. These stay pure so grouping, labels and
 * the selection summary can be checked in node without a browser.
 */

/**
 * Normalize a group token to ``M1``–``M8``, ``COURSE`` (the Contest group), or "".
 * @param {unknown} raw
 * @returns {string}
 */
export function contentModuleKey(raw) {
  const token = String(raw || "").trim().toUpperCase();
  return /^M[1-8]$/.test(token) || token === "COURSE" ? token : "";
}

/**
 * One-line meta for a Contest Question row.
 * @param {Record<string, unknown>} item
 * @returns {string}
 */
export function contentQuestionMeta(item) {
  const key = String(item.correct_answer || "").trim();
  const openPrompt = Boolean(item.curriculum_open) || String(item.type || "") === "poll";
  if (openPrompt && !key) return "Open prompt";
  const points = Number(item.points || 1);
  return `Answer ${key || "?"} · ${points} pt`;
}

/**
 * Summary line for one group.
 * @param {string} label ``Module 2`` or ``Contest``.
 * @param {boolean} current The class's Run Live Class module.
 * @param {number} count Contest Questions in the group (capped at 6).
 * @returns {string}
 */
export function contentGroupHeading(label, current, count) {
  const parts = [label];
  if (current) parts.push("this class");
  const n = Number(count) || 0;
  parts.push(n ? `${n} question${n === 1 ? "" : "s"}` : "none yet");
  return parts.join(" · ");
}

/**
 * Collapsed-group view model from the group list, in server order.
 *
 * MCK-175: groups with no Contest Questions are never listed (the server
 * still sends the class's module at 0). The class's current module starts
 * open. When it has none, the course-wide Contest group opens instead, or
 * else the first listed group. Rows load when a group opens. An empty
 * list means "show ``contestEmptyText`` instead".
 *
 * @param {Array<Record<string, unknown>>} modules ``modules`` from the API.
 * @param {string} currentModule Run Live Class module, such as ``M2``.
 * @returns {Array<{module: string, label: string, count: number,
 *   current: boolean, open: boolean, heading: string}>}
 */
export function contentModuleView(modules, currentModule) {
  const current = contentModuleKey(currentModule);
  const rows = (Array.isArray(modules) ? modules : [])
    .map((row) => {
      const module = contentModuleKey(row?.module);
      if (!module) return null;
      const label = String(
        row?.label || (module === "COURSE" ? "Contest" : `Module ${module.slice(1)}`)
      );
      const count = Math.max(0, Number(row?.count) || 0);
      const isCurrent = module === current;
      return {
        module,
        label,
        count,
        current: isCurrent,
        open: isCurrent,
        heading: contentGroupHeading(label, isCurrent, count),
      };
    })
    .filter((row) => row && row.count > 0);
  if (rows.length && !rows.some((row) => row.current)) {
    const course = rows.find((row) => row.module === "COURSE");
    (course || rows[0]).open = true;
  }
  return rows;
}

/**
 * Rows for one loaded group, capped, with "on deck" marks.
 *
 * @param {Record<string, unknown> | null} group ``group`` from the API.
 * @param {number} [perModule] Cap (6).
 * @param {Iterable<number>} [onDeck] Live problem ids already on this deck.
 * @returns {{rows: Array<{id: number, module: string, rank: number, meta: string,
 *   title: string, onDeck: boolean, item: Record<string, unknown>}>, empty: string}}
 */
export function contentRowsView(group, perModule = 6, onDeck = []) {
  const module = contentModuleKey(group?.module);
  const label = String(
    group?.label ||
      (module === "COURSE" ? "Contest" : module ? `Module ${module.slice(1)}` : "This module")
  );
  const cap = Math.max(1, Number(perModule) || 6);
  const deck = new Set([...(onDeck || [])].map((id) => Number(id)));
  const items = Array.isArray(group?.items) ? group.items.slice(0, cap) : [];
  const rows = items
    .map((item, index) => {
      const id = Number(item?.question_id || 0);
      const isOnDeck = deck.has(id);
      const meta = contentQuestionMeta(item || {});
      return {
        id,
        module,
        rank: Number(item?.content_rank || index + 1),
        meta: isOnDeck ? `${meta} · on deck` : meta,
        title: String(item?.question_title || "").trim(),
        onDeck: isOnDeck,
        item: item || {},
      };
    })
    .filter((row) => row.id > 0 && row.module);
  // MCK-161: capital Q, and no "Contest has no Contest Questions".
  // MCK-175: a module says it with bank.contest.empty.
  const empty = rows.length
    ? ""
    : module === "COURSE"
      ? "No course-wide Contest Questions yet."
      : contestEmptyText(module || label);
  return { rows, empty };
}

/**
 * MCK-175 ``bank.contest.empty``: the one line shown when no group has
 * Contest Questions (or a module group comes back empty).
 * @param {unknown} module Module token (``M2``), number, or label.
 * @returns {string}
 */
export function contestEmptyText(module) {
  const digits = String(module ?? "").match(/[1-8]/);
  return digits
    ? `No Contest Questions in Module ${digits[0]} yet.`
    : "No Contest Questions in this module yet.";
}

/** MCK-175: value of the Kind select that shows Contest Questions. */
export const CONTEST_KIND = "contest";

/**
 * MCK-175: what Import from bank shows for a Kind value. Contest
 * Questions render only while Kind is "Contest Questions", in place of
 * the type chips and the bank list. Without the Contest block (browse
 * mode, or no loader) the Kind still filters bank rows as before.
 * @param {unknown} kind Kind select value.
 * @param {boolean} hasContest The picker was given Contest Questions.
 * @returns {{contest: boolean, chips: boolean, list: boolean}}
 */
export function contestKindView(kind, hasContest) {
  const contest = Boolean(hasContest) && String(kind || "").trim().toLowerCase() === CONTEST_KIND;
  return { contest, chips: !contest, list: !contest };
}

/**
 * Selection summary for the Import selected button.
 * @param {Array<{question_id: number, module: string}>} picks
 * @returns {{label: string, disabled: boolean, count: number}}
 */
export function contentPickSummary(picks) {
  const count = Array.isArray(picks) ? picks.length : 0;
  if (!count) return { label: "Import selected", disabled: true, count: 0 };
  return { label: `Import selected (${count})`, disabled: false, count };
}

/**
 * Picks in on-screen order, one per question id (the first tick wins
 * when the same problem is ticked twice).
 * @param {Array<{id: unknown, module: unknown}>} checked
 * @returns {Array<{question_id: number, module: string}>}
 */
export function contentPicksPayload(checked) {
  const seen = new Set();
  const out = [];
  for (const row of Array.isArray(checked) ? checked : []) {
    const id = Number(row?.id || 0);
    const module = contentModuleKey(row?.module);
    if (!id || !module || seen.has(id)) continue;
    seen.add(id);
    out.push({ question_id: id, module });
  }
  return out;
}

/**
 * Status line after an import.
 * @param {number} count
 * @returns {string}
 */
export function contentImportDoneText(count) {
  const n = Number(count) || 0;
  return `Imported ${n} Contest Question${n === 1 ? "" : "s"} onto this page.`;
}

/**
 * Live problem ids already on a deck, from live metadata ``questions``.
 * Imported Contest Questions carry ``live_problem_id`` on the card.
 * @param {Array<Record<string, unknown>>} questions
 * @returns {Set<number>}
 */
export function deckLiveProblemIds(questions) {
  const ids = new Set();
  for (const question of Array.isArray(questions) ? questions : []) {
    if (!question || typeof question !== "object") continue;
    if (question.removed || question.hidden) continue;
    const id = Number(question.live_problem_id || 0);
    if (id > 0) ids.add(id);
  }
  return ids;
}

/**
 * MCK-175 ``bank.contest.title``: the section heading.
 * @param {number} [perModule]
 * @returns {string}
 */
export function contentSectionHeading(perModule = 6) {
  const n = Math.max(1, Number(perModule) || 6);
  return `Contest Questions · top ${n} per module`;
}

/**
 * MCK-161 LOW-8: status after a refused import whose picks went stale.
 * The picker has already reloaded the open groups and unticked them.
 * @param {number} count Picks that were unticked.
 * @returns {string}
 */
export function contentStaleText(count) {
  const n = Math.max(1, Number(count) || 1);
  if (n === 1) {
    return "One pick is no longer in its group's top 6, so nothing was imported. The list is refreshed and that pick is unticked. Check your picks and import again.";
  }
  return `${n} picks are no longer in their group's top 6, so nothing was imported. The list is refreshed and those picks are unticked. Check your picks and import again.`;
}

/**
 * MCK-161 LOW-8: which picks are gone after the open groups reload.
 * @param {Array<{question_id: number, module: string}>} picks What was sent.
 * @param {Map<string, Iterable<number>> | Record<string, Iterable<number>>} present
 *   Reloaded ids per group token. Groups not reloaded are left alone.
 * @returns {Array<{question_id: number, module: string}>}
 */
export function contentStalePicks(picks, present) {
  const lookup =
    present instanceof Map ? present : new Map(Object.entries(present || {}));
  const out = [];
  for (const pick of Array.isArray(picks) ? picks : []) {
    const module = contentModuleKey(pick?.module);
    if (!module || !lookup.has(module)) continue;
    const ids = new Set([...(lookup.get(module) || [])].map((id) => Number(id)));
    if (!ids.has(Number(pick?.question_id || 0))) out.push(pick);
  }
  return out;
}

/**
 * MCK-161 LOW-6: how far to scroll the picker so typed-search results
 * start just under the sticky search row. 0 when they are already visible.
 * @param {{resultsTop: number, headBottom: number, viewBottom: number}} box
 *   Client-rect numbers: top of the results count line, bottom of the
 *   sticky head, bottom of the scrolling picker.
 * @returns {number}
 */
export function searchResultsScroll(box) {
  const top = Number(box?.resultsTop);
  const head = Number(box?.headBottom);
  const bottom = Number(box?.viewBottom);
  if (![top, head, bottom].every(Number.isFinite)) return 0;
  // Visible enough: the results line sits between the head and ~2 rows
  // above the bottom edge.
  if (top >= head && top <= bottom - 96) return 0;
  return Math.round(top - head - 6);
}
