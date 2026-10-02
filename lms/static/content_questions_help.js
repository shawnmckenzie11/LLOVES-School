/**
 * MCK-79: pure helpers for the Content Questions section of Import from bank.
 *
 * bank_mc_picker.js owns the DOM. These stay pure so grouping, labels and
 * the selection summary can be checked in node without a browser.
 */

/**
 * Normalize a module token to ``M1``–``M8``, or "".
 * @param {unknown} raw
 * @returns {string}
 */
export function contentModuleKey(raw) {
  const token = String(raw || "").trim().toUpperCase();
  return /^M[1-8]$/.test(token) ? token : "";
}

/**
 * One-line meta for a Content Question row.
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
 * Summary line for one module group.
 * @param {string} label ``Module 2``.
 * @param {boolean} current The class's Run Live Class module.
 * @param {boolean} linked A content-pack bank is linked.
 * @param {number | null} count Rows loaded, or null before loading.
 * @returns {string}
 */
export function contentGroupHeading(label, current, linked, count) {
  const parts = [label];
  if (current) parts.push("this class");
  if (!linked) parts.push("no bank linked yet");
  else if (count === null || count === undefined) parts.push("open to load");
  else parts.push(count ? `${count} question${count === 1 ? "" : "s"}` : "none yet");
  return parts.join(" · ");
}

/**
 * Collapsed-group view model from the cheap module list, in server order.
 *
 * Only the class's current module starts open (and is loaded on open).
 * The others load when the teacher expands them.
 *
 * @param {Array<Record<string, unknown>>} modules ``modules`` from the API.
 * @param {string} currentModule Run Live Class module, such as ``M2``.
 * @returns {Array<{module: string, label: string, linked: boolean,
 *   current: boolean, open: boolean, heading: string}>}
 */
export function contentModuleView(modules, currentModule) {
  const current = contentModuleKey(currentModule);
  return (Array.isArray(modules) ? modules : [])
    .map((row) => {
      const module = contentModuleKey(row?.module);
      if (!module) return null;
      const label = String(row?.label || `Module ${module.slice(1)}`);
      const linked = Boolean(row?.linked);
      const isCurrent = module === current;
      return {
        module,
        label,
        linked,
        current: isCurrent,
        open: isCurrent,
        heading: contentGroupHeading(label, isCurrent, linked, null),
      };
    })
    .filter(Boolean);
}

/**
 * Rows for one loaded group, capped, with "on deck" marks.
 *
 * @param {Record<string, unknown> | null} group ``group`` from the API.
 * @param {number} [perModule] Cap (6).
 * @param {Iterable<number>} [onDeck] Question ids already on this deck.
 * @returns {{rows: Array<{id: number, module: string, rank: number, meta: string,
 *   onDeck: boolean, item: Record<string, unknown>}>, empty: string, linked: boolean}}
 */
export function contentRowsView(group, perModule = 6, onDeck = []) {
  const module = contentModuleKey(group?.module);
  const label = String(group?.label || (module ? `Module ${module.slice(1)}` : "This module"));
  const linked = Boolean(group?.linked);
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
        onDeck: isOnDeck,
        item: item || {},
      };
    })
    .filter((row) => row.id > 0 && row.module);
  let empty = "";
  if (!rows.length) {
    empty = linked
      ? `${label} has no Content Questions yet.`
      : `No bank linked yet for ${label}. Pick ${label} under Bank scope to link it.`;
  }
  return { rows, empty, linked };
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
 * Picks in on-screen order, one per question id (the first module wins
 * when two modules' banks share a question).
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
  return `Imported ${n} Content Question${n === 1 ? "" : "s"} onto this page.`;
}

/**
 * Bank question ids already on a deck, from live metadata ``questions``.
 * Bank imports carry ``bank-import-<question id>`` item ids.
 * @param {Array<Record<string, unknown>>} questions
 * @returns {Set<number>}
 */
export function deckBankQuestionIds(questions) {
  const ids = new Set();
  for (const question of Array.isArray(questions) ? questions : []) {
    if (!question || typeof question !== "object") continue;
    if (question.removed || question.hidden) continue;
    const source = Number(question.source_question_id || 0);
    if (source > 0) ids.add(source);
    for (const key of ["id", "item_id"]) {
      const match = /^bank-import-(\d+)/.exec(String(question[key] || ""));
      if (match) ids.add(Number(match[1]));
    }
  }
  return ids;
}
