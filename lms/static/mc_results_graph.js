/**
 * Shareable multiple-choice results: one options list, one bar graph.
 *
 * Counts come from a tally the page already has. Nothing here reads a
 * student name, email, or join code. The correct answer stays off the
 * graph until the teacher turns that toggle on.
 */

/**
 * Escape text for HTML.
 * @param {unknown} value
 * @returns {string}
 */
export function escapeGraphText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Plain text of one option, whether it is a string or a label object.
 * @param {unknown} option
 * @returns {string}
 */
export function optionLabelText(option) {
  if (option && typeof option === "object") {
    const row = /** @type {{label?: unknown, text?: unknown, choice?: unknown}} */ (option);
    return String(row.label || row.text || row.choice || "").trim();
  }
  return String(option ?? "").trim();
}

/**
 * Collapse whitespace so a stem cell can be compared with an option label.
 * @param {unknown} value
 * @returns {string}
 */
function compactText(value) {
  return String(value ?? "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/**
 * Drop a stem table that repeats the multiple-choice options.
 *
 * A data table stays. A table is an options table only when every
 * non-empty cell is one of the option labels and at least two different
 * options appear. The authored options list then remains the one table.
 *
 * @param {unknown} html Stem or text_html fragment.
 * @param {unknown[]} labels Option labels in authored order.
 * @returns {string}
 */
export function stripDuplicateOptionTable(html, labels) {
  const raw = String(html || "");
  const wanted = new Set(
    (Array.isArray(labels) ? labels : []).map((label) => compactText(label)).filter(Boolean)
  );
  if (wanted.size < 2 || !/<table\b/i.test(raw)) return raw;
  return raw.replace(/<table\b[^>]*>[\s\S]*?<\/table>/gi, (table) => {
    const cells = [...table.matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)]
      .map((match) => compactText(match[1]))
      .filter(Boolean);
    if (cells.length < 2) return table;
    const matched = cells.filter((cell) => wanted.has(cell));
    if (matched.length === cells.length && new Set(matched).size >= 2) return "";
    return table;
  });
}

/**
 * Keep authored order, or rank by count. Ties stay in authored order.
 * @param {any[]} choices
 * @param {"default"|"ranked"} order
 * @returns {any[]}
 */
export function orderMcChoices(choices, order) {
  const rows = (Array.isArray(choices) ? choices : []).map((row, index) => ({
    ...(row && typeof row === "object" ? row : { label: row }),
    _authored: index,
  }));
  if (order !== "ranked") return rows;
  return rows.sort((left, right) => {
    const diff = (Number(right.count) || 0) - (Number(left.count) || 0);
    if (diff) return diff;
    return left._authored - right._authored;
  });
}

/**
 * Percent for one bar. A stored percent wins; otherwise count / total.
 * @param {any} row
 * @param {number} count
 * @param {number} total
 * @returns {number}
 */
function barPercent(row, count, total) {
  const given = Number(row?.pct);
  if (Number.isFinite(given)) return Math.max(0, Math.min(100, Math.round(given)));
  if (!total) return 0;
  return Math.round((100 * count) / total);
}

/**
 * Bar graph of answer counts. No names. The key highlight is off unless
 * ``showKey`` is true. Controls are the teacher toggles.
 *
 * @param {any} tally Existing results tally (``choices`` with count and pct).
 * @param {{
 *   order?: "default"|"ranked",
 *   showKey?: boolean,
 *   controls?: boolean,
 *   itemKey?: string,
 * }} [opts]
 * @returns {string} Empty when there is nothing to graph.
 */
export function mcResultsGraphHtml(tally, opts = {}) {
  const choices = Array.isArray(tally?.choices) ? tally.choices : [];
  if (!choices.length) return "";
  const order = opts.order === "ranked" ? "ranked" : "default";
  const showKey = Boolean(opts.showKey);
  const rows = orderMcChoices(choices, order);
  const total = rows.reduce((sum, row) => sum + (Number(row.count) || 0), 0);
  const keyed = rows.some((row) => row?.correct === true);
  const itemKey = escapeGraphText(opts.itemKey || "");
  const controls = opts.controls
    ? `<div class="mc-results-controls">
        <div class="mc-results-order" role="group" aria-label="Result order">
          <button type="button" data-mc-order="default" data-mc-item="${itemKey}" aria-pressed="${
            order === "default" ? "true" : "false"
          }">Option order</button>
          <button type="button" data-mc-order="ranked" data-mc-item="${itemKey}" aria-pressed="${
            order === "ranked" ? "true" : "false"
          }">Most picked</button>
        </div>
        ${
          keyed
            ? `<button type="button" data-mc-key="1" data-mc-item="${itemKey}" aria-pressed="${
                showKey ? "true" : "false"
              }">${showKey ? "Hide answer" : "Show answer"}</button>`
            : ""
        }
      </div>`
    : "";
  const bars = rows
    .map((row) => {
      const count = Number(row.count) || 0;
      const pct = barPercent(row, count, total);
      const letter = escapeGraphText(row.id || "");
      const label = escapeGraphText(row.label ?? row.id ?? "");
      const marked = showKey && row.correct === true;
      return `<div class="mc-results-row${marked ? " is-correct" : ""}">
        <span class="mc-results-letter">${letter}</span>
        <span class="mc-results-label">${label}</span>
        <span class="mc-results-track"><span class="mc-results-fill" style="width:${pct}%"></span></span>
        <span class="mc-results-count">${count} · ${pct}%</span>
        <span class="mc-results-key">${marked ? "Correct" : ""}</span>
      </div>`;
    })
    .join("");
  return `<section class="mc-results-graph" data-mc-graph="${itemKey}" data-mc-order="${order}" data-mc-key="${
    showKey ? "shown" : "hidden"
  }" aria-label="Answer results">${controls}<div class="mc-results-bars">${bars}</div></section>`;
}

/**
 * Build a tally from options plus answers already on the card.
 *
 * Reads choice values only (``value`` / ``answer``). Name fields on those
 * rows are ignored, so the graph cannot list students.
 *
 * @param {unknown[]} options Authored options.
 * @param {any} result Lifecycle results already loaded for the card.
 * @param {unknown} correctLetter Authored key, such as ``"B"``.
 * @returns {any|null}
 */
export function tallyFromChoiceLabels(options, result, correctLetter) {
  const labels = (Array.isArray(options) ? options : []).map(optionLabelText).filter(Boolean);
  if (labels.length < 2) return null;
  const counts = labels.map(() => 0);
  const add = (value, times = 1) => {
    const text = String(value ?? "").trim();
    if (!text || !times) return;
    const index = labels.findIndex((label) => label === text);
    if (index >= 0) counts[index] += times;
  };
  const board = Array.isArray(result?.status_board) ? result.status_board : [];
  for (const row of board) {
    const picks = Array.isArray(row?.picks) ? row.picks : [];
    for (const pick of picks) add(pick?.value);
  }
  const reveal = Array.isArray(result?.reveal) ? result.reveal : [];
  for (const row of reveal) add(row?.answer);
  const teams = Array.isArray(result?.teams) ? result.teams : [];
  for (const team of teams) {
    const finalAnswer = team?.final_answer;
    if (finalAnswer && typeof finalAnswer === "object") add(finalAnswer.value);
    else add(finalAnswer);
    const votes = Array.isArray(team?.vote_summary) ? team.vote_summary : [];
    for (const vote of votes) {
      const answer = vote?.answer;
      const value = answer && typeof answer === "object" ? answer.value : answer;
      add(value, Number(vote?.count) || 0);
    }
  }
  const total = counts.reduce((sum, count) => sum + count, 0);
  const key = String(correctLetter || "").trim().toUpperCase();
  return {
    kind: "mc",
    choices: labels.map((label, index) => ({
      id: "ABCDEFGH"[index] || String(index + 1),
      label,
      count: counts[index],
      pct: total ? Math.round((100 * counts[index]) / total) : 0,
      correct: Boolean(key) && key === ("ABCDEFGH"[index] || ""),
    })),
  };
}

/**
 * Second graph only when this card does not already show one.
 * @param {unknown} existingHtml Results HTML already chosen for the card.
 * @param {unknown} fallbackHtml Graph for a card that has no tally yet.
 * @returns {string}
 */
export function mcGraphSlot(existingHtml, fallbackHtml) {
  if (String(existingHtml || "").includes("mc-results-graph")) return "";
  return String(fallbackHtml || "");
}
