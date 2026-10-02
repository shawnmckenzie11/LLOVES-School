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
 * View model for the module groups, in server (Module select) order.
 *
 * The class's current live module opens first; the others start closed so
 * eight modules of six rows stay scannable.
 *
 * @param {Array<Record<string, unknown>>} groups ``groups`` from the API.
 * @param {string} currentModule Run Live Class module, such as ``M2``.
 * @param {number} [perModule] Cap from the API (6).
 * @returns {Array<{module: string, label: string, heading: string, open: boolean,
 *   current: boolean, empty: string, rows: Array<{id: number, module: string,
 *   rank: number, meta: string, item: Record<string, unknown>}>}>}
 */
export function contentGroupView(groups, currentModule, perModule = 6) {
  const current = contentModuleKey(currentModule);
  const cap = Math.max(1, Number(perModule) || 6);
  return (Array.isArray(groups) ? groups : [])
    .map((group) => {
      const module = contentModuleKey(group?.module);
      if (!module) return null;
      const label = String(group?.label || `Module ${module.slice(1)}`);
      const items = Array.isArray(group?.items) ? group.items.slice(0, cap) : [];
      const rows = items
        .map((item, index) => ({
          id: Number(item?.question_id || 0),
          module,
          rank: Number(item?.content_rank || index + 1),
          meta: contentQuestionMeta(item || {}),
          item: item || {},
        }))
        .filter((row) => row.id > 0);
      const isCurrent = module === current;
      const count = rows.length;
      const countText = count
        ? `${count} question${count === 1 ? "" : "s"}`
        : "none yet";
      let empty = "";
      if (!count) {
        empty = group?.linked
          ? `${label} has no Content Questions yet.`
          : `No banks linked to ${label} yet. Pick ${label} under Bank scope to link them.`;
      }
      return {
        module,
        label,
        heading: `${label}${isCurrent ? " · this class" : ""} · ${countText}`,
        open: isCurrent,
        current: isCurrent,
        empty,
        rows,
      };
    })
    .filter(Boolean);
}

/**
 * Selection summary for the Import selected button.
 * @param {Array<{id: number, module: string}>} picks
 * @returns {{label: string, disabled: boolean, count: number}}
 */
export function contentPickSummary(picks) {
  const count = Array.isArray(picks) ? picks.length : 0;
  if (!count) return { label: "Import selected", disabled: true, count: 0 };
  return { label: `Import selected (${count})`, disabled: false, count };
}

/**
 * Picks in on-screen order (module groups, then rank), de-duplicated.
 * @param {Array<{id: unknown, module: unknown}>} checked
 * @returns {Array<{question_id: number, module: string}>}
 */
export function contentPicksPayload(checked) {
  const seen = new Set();
  const out = [];
  for (const row of Array.isArray(checked) ? checked : []) {
    const id = Number(row?.id || 0);
    const module = contentModuleKey(row?.module);
    if (!id || !module) continue;
    const key = `${module}:${id}`;
    if (seen.has(key)) continue;
    seen.add(key);
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
