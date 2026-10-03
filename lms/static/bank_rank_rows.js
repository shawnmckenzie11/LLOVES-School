/**
 * MCK-169 S1: rank rows in Import from bank. Pure helpers (no DOM) so the
 * chip, preview, sort and hint rules can be checked in node.
 *
 * Copy is Wonder's, word for word (alc-three-fixes-copy-wonder-v1.md).
 */

/** Wonder copy for rank rows (keys from the copy doc). */
export const RANK_ROW_COPY = Object.freeze({
  "bank.rank.chip.key": "Answer order",
  "bank.rank.chip.key.help": "Has a correct order. Can run as a Team challenge.",
  "bank.rank.chip.opinion": "Opinion",
  "bank.rank.chip.opinion.help": "No right order. Shows how the class voted.",
  "bank.rank.meta": "Rank · {n} items · {chip}",
  "bank.rank.import_default": "Imports as Group · take turns. You can change it.",
  "bank.rank.more": "+{k} more",
  "bank.rank.none_keyed": "No answer-order rank questions in Module {m} yet.",
});

/** Options shown in a rank row's preview before "+{k} more". */
export const RANK_PREVIEW_COUNT = 3;

/**
 * True for a normalized bank search row of type rank.
 * @param {Record<string, unknown> | null | undefined} item
 * @returns {boolean}
 */
export function isRankRow(item) {
  return String(item?.type || "") === "rank";
}

/**
 * True when a rank row has an answer order (``rank_key``).
 * @param {Record<string, unknown> | null | undefined} item
 * @returns {boolean}
 */
export function hasAnswerOrder(item) {
  return isRankRow(item) && Array.isArray(item?.rank_key) && item.rank_key.length > 0;
}

/**
 * The one chip a rank row carries: Answer order (keyed) or Opinion.
 * @param {Record<string, unknown>} item
 * @returns {{kind: "key"|"opinion", label: string, help: string}}
 */
export function rankChip(item) {
  if (hasAnswerOrder(item)) {
    return {
      kind: "key",
      label: RANK_ROW_COPY["bank.rank.chip.key"],
      help: RANK_ROW_COPY["bank.rank.chip.key.help"],
    };
  }
  return {
    kind: "opinion",
    label: RANK_ROW_COPY["bank.rank.chip.opinion"],
    help: RANK_ROW_COPY["bank.rank.chip.opinion.help"],
  };
}

/**
 * ``bank.rank.meta`` split around the chip, so the chip can be markup.
 * @param {number} n Option count.
 * @returns {{before: string, after: string}}
 */
export function rankMetaParts(n) {
  const text = RANK_ROW_COPY["bank.rank.meta"].replace("{n}", String(Math.max(0, Number(n) || 0)));
  const [before, after = ""] = text.split("{chip}");
  return { before, after };
}

/**
 * Option labels for a rank row, in stored display order (never the key's
 * order: the projector may be showing this screen).
 * @param {Record<string, unknown>} item
 * @returns {string[]}
 */
export function rankOptionLabels(item) {
  const rows = Array.isArray(item?.rank_options) && item.rank_options.length
    ? item.rank_options.map((row) => (row && typeof row === "object" ? row.label : row))
    : Array.isArray(item?.options)
      ? item.options
      : [];
  return rows.map((label) => String(label ?? "").trim()).filter(Boolean);
}

/**
 * Preview line: the first three options joined by " · ", then "+{k} more".
 * @param {Record<string, unknown>} item
 * @returns {{text: string, more: string}}
 */
export function rankPreview(item) {
  const labels = rankOptionLabels(item);
  const shown = labels.slice(0, RANK_PREVIEW_COUNT);
  const extra = labels.length - shown.length;
  return {
    text: shown.join(" · "),
    more: extra > 0 ? RANK_ROW_COPY["bank.rank.more"].replace("{k}", String(extra)) : "",
  };
}

/**
 * With the Rank type chip on, Answer order rows come first, then Opinion
 * rows, each group keeping its order (stable partition). Any other type
 * leaves the server order alone.
 * @param {Array<Record<string, unknown>>} items
 * @param {string} typeValue Selected Type chip (``rank``, ``mc``, "" for All).
 * @returns {Array<Record<string, unknown>>}
 */
export function sortRankRows(items, typeValue) {
  const rows = Array.isArray(items) ? items : [];
  if (String(typeValue || "") !== "rank") return rows;
  const keyed = rows.filter((item) => hasAnswerOrder(item));
  if (!keyed.length) return rows;
  return [...keyed, ...rows.filter((item) => !hasAnswerOrder(item))];
}

/**
 * Hints around the list with the Rank chip on.
 * - ``importDefault``: show ``bank.rank.import_default`` under the count
 *   (import mode, at least one Answer order row listed).
 * - ``noneKeyed``: ``bank.rank.none_keyed`` above any Opinion rows, for a
 *   module with no Answer order rows (not for a typed search or Course Wide).
 * @param {{typeValue: string, items: Array<Record<string, unknown>>,
 *   scope: string, query?: string, mode?: string}} opts
 * @returns {{importDefault: string, noneKeyed: string}}
 */
export function rankListHints(opts) {
  const rankOn = String(opts?.typeValue || "") === "rank";
  const items = Array.isArray(opts?.items) ? opts.items : [];
  const keyed = items.some((item) => hasAnswerOrder(item));
  const scope = String(opts?.scope || "").toUpperCase();
  const module = /^M([1-8])$/.exec(scope);
  const typed = Boolean(String(opts?.query || "").trim());
  return {
    importDefault:
      rankOn && keyed && String(opts?.mode || "import") === "import"
        ? RANK_ROW_COPY["bank.rank.import_default"]
        : "",
    noneKeyed:
      rankOn && !keyed && module && !typed
        ? RANK_ROW_COPY["bank.rank.none_keyed"].replace("{m}", module[1])
        : "",
  };
}
