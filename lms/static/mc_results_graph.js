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

/** A label longer than this is shortened until the row is tapped. */
export const RESULTS_LABEL_ELLIPSIS = 24;

/**
 * One results row: name, bar, count, and the correct mark.
 *
 * Short answers stay on one line. Text longer than
 * {@link RESULTS_LABEL_ELLIPSIS} is marked so a tap can show it all.
 *
 * @param {{letter?: string, label: string, pct: number, count: number, marked: boolean}} row
 * @returns {string}
 */
function resultsRowHtml(row) {
  const label = String(row.label ?? "");
  const long = [...label].length > RESULTS_LABEL_ELLIPSIS;
  const letter = row.letter
    ? `<span class="mc-results-letter">${escapeGraphText(row.letter)}</span>`
    : "";
  const marked = row.marked ? " is-correct" : "";
  const expandable = long ? " is-long" : "";
  const open = long ? ' tabindex="0" role="button" aria-expanded="false"' : "";
  return `<div class="mc-results-row${marked}${expandable}"${open}>
        <span class="mc-results-name">${letter}<span class="mc-results-label">${escapeGraphText(label)}</span></span>
        <span class="mc-results-track"><span class="mc-results-fill" style="width:${row.pct}%"></span></span>
        <span class="mc-results-count">${row.count} · ${row.pct}%</span>
        <span class="mc-results-key">${row.marked ? "Correct" : ""}</span>
      </div>`;
}

/**
 * Show the full answer when a shortened row is tapped or activated.
 *
 * Numbers and short answers are not expandable. Safe to call more than
 * once; the listener is installed on the document once.
 */
export function installMcResultsLabelExpand() {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (!root || root.dataset.mcResultsExpand === "1") return;
  root.dataset.mcResultsExpand = "1";
  /**
   * Toggle one long row between the ellipsis and the full answer.
   * @param {Element} row
   */
  const toggle = (row) => {
    const open = !row.classList.contains("is-expanded");
    row.classList.toggle("is-expanded", open);
    row.setAttribute("aria-expanded", open ? "true" : "false");
  };
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("button, a")) return;
    const row = target.closest(".mc-results-row.is-long");
    if (!row) return;
    toggle(row);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (!target.classList.contains("is-long")) return;
    event.preventDefault();
    toggle(target);
  });
}

installMcResultsLabelExpand();

/**
 * Bar graph of answer counts. No names. The key highlight follows
 * ``markKey`` (the teacher always marks it; a phone follows Show answer).
 * ``showKey`` is only the Show answer button. Controls are the teacher toggles.
 *
 * @param {any} tally Existing results tally (``choices`` with count and pct).
 * @param {{
 *   order?: "default"|"ranked",
 *   showKey?: boolean,
 *   markKey?: boolean,
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
  const markKey = opts.markKey == null ? showKey : Boolean(opts.markKey);
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
      return resultsRowHtml({
        letter: String(row.id || ""),
        label: String(row.label ?? row.id ?? ""),
        pct,
        count,
        marked: markKey && row.correct === true,
      });
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

/** At most this many distinct numbers stay as one bar each. More become bins. */
export const NUMERIC_VALUE_BAR_MAX = 8;

/**
 * Parse one tally choice as a number. Other and non-numeric labels are null.
 * @param {any} row
 * @returns {number|null}
 */
function numericChoiceValue(row) {
  if (!row || row.other === true || String(row.id || "") === "other") return null;
  const raw = String(row.label ?? row.id ?? "").replace(/^≈/, "").trim();
  if (!raw || raw.toLowerCase() === "other") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/**
 * Short axis label without trailing zeros.
 * @param {number} n
 * @returns {string}
 */
function formatAxis(n) {
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  const places = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  let text = n.toFixed(places);
  if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
  return text === "-0" ? "0" : text;
}

/**
 * True when a number sits in the authored window.
 * @param {number} value
 * @param {unknown} correctValue
 * @param {unknown} tolerance
 * @param {unknown} toleranceKind
 * @returns {boolean}
 */
function valueIsCorrect(value, correctValue, tolerance, toleranceKind) {
  const expected = Number(correctValue);
  if (!Number.isFinite(expected) || !Number.isFinite(value)) return false;
  const window = Number(tolerance);
  if (!Number.isFinite(window) || window < 0) return value === expected;
  if (window === 0) return value === expected;
  const kind = String(toleranceKind || "absolute").toLowerCase();
  if (kind.startsWith("percent")) {
    return Math.abs(value - expected) <= Math.abs(expected) * (window / 100);
  }
  return Math.abs(value - expected) <= window;
}

/**
 * 1, 2, 2.5, 5, or 10 times a power of ten, near ``rough``.
 * @param {number} rough
 * @returns {number}
 */
function niceStep(rough) {
  if (!(rough > 0) || !Number.isFinite(rough)) return 1;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const frac = rough / pow;
  let mult = 1;
  if (frac > 1) mult = 2;
  if (frac > 2) mult = 2.5;
  if (frac > 5) mult = 5;
  if (frac > 10) mult = 10;
  return mult * pow;
}

/**
 * Bin edges on a nice step, aiming for 6–10 bins that cover every value.
 * @param {number} min
 * @param {number} max
 * @returns {{start: number, step: number, count: number}}
 */
export function histogramEdges(min, max) {
  if (!(max > min)) return { start: min, step: 1, count: 1 };
  const span = max - min;
  let step = niceStep(span / 8);
  const edges = (size) => {
    const start = Math.floor(min / size + 1e-9) * size;
    let end = Math.ceil(max / size - 1e-9) * size;
    if (end <= start) end = start + size;
    const count = Math.max(1, Math.round((end - start) / size));
    return { start, step: size, count };
  };
  let plan = edges(step);
  let guard = 0;
  while (plan.count > 10 && guard < 8) {
    step *= 2;
    plan = edges(step);
    guard += 1;
  }
  guard = 0;
  while (plan.count < 6 && guard < 8) {
    const next = step / 2;
    if (!(next > 0)) break;
    const trial = edges(next);
    if (trial.count > 10) break;
    step = next;
    plan = trial;
    guard += 1;
  }
  return plan;
}

/**
 * Place numeric tally rows into value bars or histogram bins.
 *
 * Eight or fewer distinct numbers stay one bar each. Nine or more become
 * auto-binned ranges. ``Other`` is counted separately and is not a bar.
 *
 * @param {any} tally
 * @param {{
 *   order?: "default"|"ranked",
 *   correctValue?: unknown,
 *   tolerance?: unknown,
 *   toleranceKind?: unknown,
 * }} [opts]
 * @returns {{mode: "bars"|"histogram", rows: any[], other: number}}
 */
export function planNumericResults(tally, opts = {}) {
  const choices = Array.isArray(tally?.choices) ? tally.choices : [];
  /** @type {any[]} */
  const numeric = [];
  let other = 0;
  let sawOther = false;
  for (const row of choices) {
    const value = numericChoiceValue(row);
    if (value === null) {
      if (row?.other === true || String(row?.id || "") === "other") {
        other += Number(row?.count) || 0;
        sawOther = true;
      }
      continue;
    }
    const correct =
      row?.correct === true ||
      valueIsCorrect(value, opts.correctValue, opts.tolerance, opts.toleranceKind);
    numeric.push({
      label: String(row.label ?? row.id ?? value),
      count: Number(row.count) || 0,
      correct,
      value,
    });
  }
  if (!sawOther) other += Number(tally?.other_count) || 0;
  const order = opts.order === "ranked" ? "ranked" : "default";
  if (numeric.length <= NUMERIC_VALUE_BAR_MAX) {
    const rows = numeric.map((row, index) => ({ ...row, _authored: index }));
    rows.sort((left, right) => {
      if (order === "ranked") {
        const diff = right.count - left.count;
        if (diff) return diff;
      }
      return left.value - right.value || left._authored - right._authored;
    });
    return { mode: "bars", rows, other };
  }
  const min = Math.min(...numeric.map((row) => row.value));
  const max = Math.max(...numeric.map((row) => row.value));
  const plan = histogramEdges(min, max);
  /** @type {any[]} */
  const bins = [];
  for (let index = 0; index < plan.count; index += 1) {
    const start = plan.start + index * plan.step;
    const end = plan.start + (index + 1) * plan.step;
    bins.push({
      label: `${formatAxis(start)}–${formatAxis(end)}`,
      count: 0,
      correct: false,
      value: start,
      start,
      end,
      _authored: index,
    });
  }
  for (const row of numeric) {
    let index = Math.floor((row.value - plan.start) / plan.step + 1e-9);
    if (index < 0) index = 0;
    if (index >= bins.length) index = bins.length - 1;
    bins[index].count += row.count;
    if (row.correct) bins[index].correct = true;
  }
  const key = Number(opts.correctValue);
  if (Number.isFinite(key)) {
    let low = key;
    let high = key;
    const window = Number(opts.tolerance);
    if (Number.isFinite(window) && window > 0) {
      const kind = String(opts.toleranceKind || "absolute").toLowerCase();
      const delta = kind.startsWith("percent") ? Math.abs(key) * (window / 100) : window;
      low = key - delta;
      high = key + delta;
    }
    bins.forEach((bin, index) => {
      const end = index === bins.length - 1 ? bin.end + 1e-9 : bin.end;
      if (bin.start <= high && end > low) bin.correct = true;
    });
  }
  if (order === "ranked") {
    bins.sort((left, right) => right.count - left.count || left.value - right.value);
  }
  return { mode: "histogram", rows: bins, other };
}

/**
 * Numeric results in the same bar graph as multiple choice.
 *
 * Number order is the default. Most picked reorders by count. Show answer
 * highlights the correct value, or the bin that contains it.
 *
 * @param {any} tally Existing numeric tally.
 * @param {{
 *   order?: "default"|"ranked",
 *   showKey?: boolean,
 *   markKey?: boolean,
 *   controls?: boolean,
 *   itemKey?: string,
 *   correctValue?: unknown,
 *   tolerance?: unknown,
 *   toleranceKind?: unknown,
 * }} [opts]
 * @returns {string}
 */
export function numericResultsGraphHtml(tally, opts = {}) {
  const plan = planNumericResults(tally, opts);
  if (!plan.rows.length && !plan.other) return "";
  const order = opts.order === "ranked" ? "ranked" : "default";
  const showKey = Boolean(opts.showKey);
  const markKey = opts.markKey == null ? showKey : Boolean(opts.markKey);
  const keyed = plan.rows.some((row) => row.correct === true) || Number.isFinite(Number(opts.correctValue));
  const itemKey = escapeGraphText(opts.itemKey || "");
  const total = plan.rows.reduce((sum, row) => sum + (Number(row.count) || 0), 0) + plan.other;
  const controls = opts.controls
    ? `<div class="mc-results-controls">
        <div class="mc-results-order" role="group" aria-label="Result order">
          <button type="button" data-mc-order="default" data-mc-item="${itemKey}" aria-pressed="${
            order === "default" ? "true" : "false"
          }">Number order</button>
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
  const bars = plan.rows
    .map((row) => {
      const count = Number(row.count) || 0;
      const pct = total ? Math.round((100 * count) / total) : 0;
      return resultsRowHtml({
        label: String(row.label ?? ""),
        pct,
        count,
        marked: markKey && row.correct === true,
      });
    })
    .join("");
  const other = plan.other
    ? `<p class="mc-results-other">Other · ${plan.other}</p>`
    : "";
  return `<section class="mc-results-graph is-numeric" data-mc-graph="${itemKey}" data-mc-mode="${
    plan.mode
  }" data-mc-order="${order}" data-mc-key="${showKey ? "shown" : "hidden"}" aria-label="Numeric answer results">${controls}<div class="mc-results-bars is-numeric">${bars}</div>${other}</section>`;
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
