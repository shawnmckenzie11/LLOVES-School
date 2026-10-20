import { api, escapeHtml, formatPoints, hideError, showError } from "/static/common.js";
import { deleteTestClass } from "/static/staff_test_class.js";
import { bindStaffInfoPopovers, staffInfoTriggerHtml } from "/static/staff_info_popover.js";

const root = document.getElementById("grades-root");
const classId = Number(root?.dataset.classId || 0);
const sortKey = `lloves-sort-${classId}`;
const includeIndividualKey = `lloves-ap-include-individual-${classId}`;
const includeGroupKey = `lloves-ap-include-group-${classId}`;
const displayModeKey = `lloves-ap-display-mode-${classId}`;
const DISPLAY_MODES = ["points", "fraction", "percent"];
const teacherId = String(root?.dataset.teacherId || "0");
const expandKey = `lloves-ap-module-expand-${teacherId}-${classId}`;

let sort = localStorage.getItem(sortKey) === "za" ? "za" : "az";
let includeIndividual = loadBool(includeIndividualKey, true);
let includeGroup = loadBool(includeGroupKey, true);
if (!includeIndividual && !includeGroup) {
  includeIndividual = true;
  includeGroup = true;
}
let displayMode = loadDisplayMode(localStorage.getItem(displayModeKey));
let expanded = loadExpandMap(localStorage.getItem(expandKey));
let latest = null;

/**
 * @param {string} key
 * @param {boolean} fallback
 * @returns {boolean}
 */
function loadBool(key, fallback) {
  const raw = localStorage.getItem(key);
  if (raw === "0" || raw === "false") return false;
  if (raw === "1" || raw === "true") return true;
  return fallback;
}

/**
 * @param {string|null} raw
 * @returns {"points"|"fraction"|"percent"}
 */
function loadDisplayMode(raw) {
  return DISPLAY_MODES.includes(raw || "") ? raw : "percent";
}

/**
 * Per-teacher expand map for finished modules. Missing keys stay collapsed.
 * @param {string|null} raw
 * @returns {Record<string, boolean>}
 */
function loadExpandMap(raw) {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out = {};
    for (const [key, value] of Object.entries(parsed)) {
      out[String(key).toUpperCase()] = Boolean(value);
    }
    return out;
  } catch (_err) {
    return {};
  }
}

/** Persist finished-module expand choices for this teacher and class. */
function saveExpandMap() {
  localStorage.setItem(expandKey, JSON.stringify(expanded));
}

/**
 * @param {string} module
 * @param {string} role
 * @returns {boolean}
 */
function moduleExpanded(module, role) {
  if (role !== "finished") return true;
  return Boolean(expanded[String(module || "").toUpperCase()]);
}

/**
 * Roster label is the Codename only.
 * @param {{codename?: string, first_name?: string}} student
 * @returns {string}
 */
function displayName(student) {
  return String(student?.codename || student?.first_name || "").trim();
}

/**
 * Load and paint the class spreadsheet.
 */
async function refresh() {
  hideError("#error");
  const data = await api(`/api/classes/${classId}/participation-grid?sort=${sort}`);
  paint(data);
}

/**
 * Apply a dashboard payload to the page.
 * @param {any} data
 */
function paint(data) {
  latest = data;
  const modeSelect = document.getElementById("ap-display-mode-select");
  if (modeSelect instanceof HTMLSelectElement) modeSelect.value = displayMode;
  const slice = !includeIndividual && includeGroup
    ? "group"
    : includeIndividual && !includeGroup
      ? "individual"
      : "all";
  document.querySelectorAll("[data-display]").forEach((btn) => {
    const on = btn.getAttribute("data-display") === displayMode;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  document.querySelectorAll("[data-slice]").forEach((btn) => {
    const on = btn.getAttribute("data-slice") === slice;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  renderSheet(data);
  requestAnimationFrame(scrollCurrentModuleIntoView);
}

/**
 * Put the current module just to the right of the pinned name column.
 */
function scrollCurrentModuleIntoView() {
  const wrap = document.querySelector("#grades-root .ap-mod-wrap");
  const anchor = document.querySelector("#sheet [data-scroll-anchor='current']");
  if (!(wrap instanceof HTMLElement) || !(anchor instanceof HTMLElement)) return;
  const name = document.querySelector("#sheet th.name");
  const nameWidth = name instanceof HTMLElement ? name.offsetWidth : 0;
  wrap.scrollLeft = Math.max(0, anchor.offsetLeft - nameWidth);
}

/**
 * @param {unknown} value
 * @returns {number}
 */
function numericPoints(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.round(n * 10) / 10;
}

/**
 * @param {Array<{kind?: string, publish_mode?: string, prompt_kind?: string, item_json?: any}>} deckItems
 * @returns {number}
 */
function participationQuestionKey(row) {
  const explicit = String(row.question_key || "").trim();
  if (explicit) return explicit;
  const placement = String(row.placement_key || "").trim();
  if (placement) return `item:${placement}`;
  return "";
}

function sessionOpportunities(deckItems, liveAnsweredKeys) {
  const items = deckItems || [];
  const liveKeys = liveAnsweredKeys == null
    ? null
    : new Set((liveAnsweredKeys || []).map((k) => String(k)));
  let total = 0;
  for (const row of items) {
    const mode = String(row.publish_mode || "individual").toLowerCase();
    if (mode === "individual" && !includeIndividual) continue;
    if (mode !== "individual" && !includeGroup) continue;
    if (liveKeys) {
      const qkey = participationQuestionKey(row);
      if (!qkey || !liveKeys.has(qkey)) continue;
    }
    total += participationWeight(row);
  }
  return total;
}

/** Counted prompt kinds (mirrors lms/participation_weight.py). */
const COUNTED_PROMPT_KINDS = new Set([
  "mc",
  "multiple_choice",
  "multiple_choice_question",
  "poll",
  "numeric",
  "number",
  "rank",
  "ranking",
  "text",
  "short_answer",
  "open",
]);
const COUNTED_JSON_TYPES = new Set([
  "mc",
  "multiple_choice",
  "multiple_choice_question",
  "numeric",
  "number",
  "rank",
  "ranking",
  "text",
  "short_answer",
]);

/**
 * @param {string} promptKind
 * @param {object|null|undefined} payload
 * @returns {boolean}
 */
function itemCountsForParticipation(promptKind, payload) {
  const kind = String(promptKind || "").toLowerCase();
  if (COUNTED_PROMPT_KINDS.has(kind)) return true;
  const qtype = String(payload?.type || payload?.question_type || "").toLowerCase();
  return COUNTED_JSON_TYPES.has(qtype);
}

/**
 * @param {{prompt_kind?: string, publish_mode?: string, item_json?: any}} row
 * @returns {number}
 */
function participationWeight(row) {
  let payload = row.item_json;
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload);
    } catch (_err) {
      payload = {};
    }
  }
  const mode = String(row.publish_mode || "individual").toLowerCase();
  if (mode !== "individual" && mode !== "group_consensus" && mode !== "group_shared") {
    return 0;
  }
  return itemCountsForParticipation(row.prompt_kind, payload) ? 1 : 0;
}

/**
 * @param {number} score
 * @param {number} opportunities
 * @param {string|null|undefined} endedAt
 * @returns {string}
 */
function formatParticipationCell(score, opportunities, endedAt) {
  if (!includeIndividual && !includeGroup) return "—";
  if (opportunities <= 0) {
    return score > 0 ? escapeHtml(formatPoints(score)) : "—";
  }
  if (displayMode === "fraction") {
    return `${formatScore(score)}/${opportunities}`;
  }
  if (displayMode === "percent") {
    let pct = Math.round((100 * score) / opportunities);
    if (endedAt && legacyCap(endedAt) && pct > 100) pct = 100;
    return `${pct}%`;
  }
  return escapeHtml(formatPoints(score));
}

/**
 * @param {number} value
 * @returns {string}
 */
function formatScore(value) {
  const n = numericPoints(value);
  return String(n);
}

/**
 * @param {string} endedAt
 * @returns {boolean}
 */
function legacyCap(endedAt) {
  const day = String(endedAt || "").slice(0, 10);
  return day && day < "2026-10-14";
}

/**
 * @param {any} data
 * @param {number} sessionId
 * @param {number} studentId
 * @param {{deck_items?: any[], ended_at?: string}} meta
 * @returns {{score: number, opportunities: number, ended_at: string}}
 */
function cellScore(data, sessionId, studentId, meta) {
  const opportunities = sessionOpportunities(meta?.deck_items, meta?.live_answered_keys);
  const cell = data.cells?.[`${sessionId}:${studentId}`] || {};
  const groupPoints = numericPoints(cell.points);
  const individualKey = `${sessionId}:${studentId}`;
  const individualAnswered = numericPoints(data.individual_cells?.[individualKey] || 0);
  let score = 0;
  if (opportunities <= 0) {
    if (includeGroup) score += groupPoints;
    if (includeIndividual) score += individualAnswered;
    return { score, opportunities, ended_at: String(meta?.ended_at || "") };
  }
  if (displayMode === "points") {
    if (includeGroup) score += groupPoints;
    if (includeIndividual) score += individualAnswered;
  } else if (includeIndividual) {
    score = individualAnswered;
  } else if (includeGroup) {
    score = groupPoints;
  }
  return { score, opportunities, ended_at: String(meta?.ended_at || "") };
}

/**
 * Visible columns for one module band (one summary, or every class).
 * @param {any} band
 * @returns {Array<{kind: string, band: any, column?: any}>}
 */
function visibleBandColumns(band) {
  const open = moduleExpanded(band.module, band.role);
  if (band.role === "finished" && !open) {
    return [{ kind: "summary", band }];
  }
  return (band.columns || []).map((column) => ({ kind: "class", band, column }));
}

/**
 * Sum one student's scores across a module's class columns.
 * @param {any} data
 * @param {number} studentId
 * @param {Array<any>} columns
 * @returns {{score: number, opportunities: number, ended_at: string}}
 */
function moduleRollup(data, studentId, columns) {
  let score = 0;
  let opportunities = 0;
  let ended = "";
  for (const column of columns || []) {
    const meta = data.session_meta?.[String(column.id)] || {};
    if (meta.is_test || column.is_test) continue;
    const cell = cellScore(data, Number(column.id), studentId, meta);
    score += cell.score;
    opportunities += cell.opportunities;
    if (cell.ended_at && cell.ended_at > ended) ended = cell.ended_at;
  }
  return { score, opportunities, ended_at: ended };
}

/**
 * Class cell shows the score fraction. Office cells are a check or a dash.
 * A check is not repeated beside the fraction.
 * @param {any} data
 * @param {number} sessionId
 * @param {number} studentId
 * @param {{score: number, opportunities: number}} scored
 * @param {boolean} office
 * @returns {string}
 */
function classCellHtml(data, sessionId, studentId, scored, office) {
  const cell = data.cells?.[`${sessionId}:${studentId}`] || {};
  const hit = scored.score > 0 || (office && Boolean(cell.present));
  if (!office && scored.opportunities > 0) {
    const fraction = `${formatScore(scored.score)}/${scored.opportunities}`;
    return `<span class="ap-frac">${escapeHtml(fraction)}</span>`;
  }
  if (!office && scored.score > 0) {
    return `<span class="ap-frac">${escapeHtml(formatScore(scored.score))}</span>`;
  }
  const mark = hit ? "✓" : "—";
  const markClass = hit ? "ap-mark-yes" : "ap-mark-no";
  return `<span class="${markClass}">${mark}</span>`;
}

/**
 * Collapsed or expanded finished-module control (M2 ▸).
 * @param {string} module
 * @param {boolean} open
 * @returns {string}
 */
function summaryHeaderHtml(module, open) {
  const caret = open ? "▾" : "▸";
  return `<button type="button" class="ap-mod-toggle" data-module="${escapeHtml(module)}" aria-expanded="${open ? "true" : "false"}">
    <span class="ap-mod-code">${escapeHtml(module || "Module")}</span>
    <span class="ap-mod-caret" aria-hidden="true">${caret}</span>
  </button>`;
}

/**
 * Build the module-grouped participation sheet.
 * @param {any} data
 */
function renderSheet(data) {
  const table = document.getElementById("sheet");
  if (!table) return;
  table.classList.add("ap-mod-table");
  table.classList.remove("attendance-grid", "ap-module-grid");
  const students = data.students || [];
  const bands = (data.module_bands && data.module_bands.bands) || [];
  const currentBand = bands.find((band) => band.role === "current") || null;

  const pieces = [];
  /** @type {Array<{kind: string, band: any, column?: any}>} */
  const testColumns = [];
  for (const band of bands) {
    const untagged = band.role === "untagged" || String(band.module || "") === "Untagged";
    if (!untagged && moduleNumber(band.module) <= 0) continue;
    const cols = visibleBandColumns(band).filter((col) => {
      if (col.kind !== "class") return true;
      if (col.column?.is_test) return false;
      if (untagged) return true;
      return moduleNumber(col.column?.module) > 0;
    });
    for (const col of visibleBandColumns(band)) {
      if (col.kind === "class" && col.column?.is_test) {
        testColumns.push(col);
      }
    }
    if (!cols.length) continue;
    pieces.push({ band, cols });
  }
  const runs = [];
  for (const piece of pieces) {
    const role = piece.band.role === "finished" ? "finished" : piece.band.module;
    const last = runs[runs.length - 1];
    if (last && last.key === role && role === "finished") {
      last.pieces.push(piece);
      last.span += piece.cols.length;
    } else {
      runs.push({ key: role, role: piece.band.role, pieces: [piece], span: piece.cols.length });
    }
  }

  let groupRow = `<tr class="ap-mod-groups"><th class="name" rowspan="2">Student</th>`;
  let head = `<tr class="ap-mod-cols">`;
  for (const run of runs) {
    const first = run.pieces[0].band;
    const mod = String(first.module || "");
    let groupLabel = "";
    let groupClass = "ap-mod-group";
    if (run.role === "finished") groupLabel = "Finished modules";
    else if (run.role === "current") {
      groupLabel = `Module ${moduleNumber(mod)} · current`;
      groupClass += " is-current";
    } else if (run.role === "open") {
      groupLabel = `Module ${moduleNumber(mod)}`;
    } else if (run.role === "untagged") {
      groupLabel = "Untagged";
    }
    groupRow += `<th class="${groupClass}" colspan="${run.span}">${escapeHtml(groupLabel)}</th>`;
    for (const piece of run.pieces) {
      const pieceMod = String(piece.band.module || "");
      piece.cols.forEach((col, index) => {
        if (col.kind === "summary") {
          head += `<th class="ap-mod-summary" data-module="${escapeHtml(pieceMod)}">${summaryHeaderHtml(pieceMod, false)}</th>`;
          return;
        }
        const office = Boolean(col.column?.office);
        const anchor = piece.band.role === "current" && index === 0
          ? ` data-scroll-anchor="current"`
          : "";
        const toggle = piece.band.role === "finished" && index === 0
          ? summaryHeaderHtml(pieceMod, true)
          : "";
        const officePill = office ? `<span class="ap-office-pill">Open office</span>` : "";
        head += `<th class="${office ? "is-office ap-office-header" : "ap-class-col"}"${anchor}>
          ${toggle}<span class="ap-class-label">${escapeHtml(col.column?.label || "")}</span>${officePill}
        </th>`;
      });
    }
  }
  if (currentBand) {
    groupRow += `<th class="ap-mod-so-far-group" rowspan="2">This module so far</th>`;
  }
  if (testColumns.length) {
    groupRow += `<th class="ap-test-divider" rowspan="2" aria-hidden="true"></th>`;
  }
  groupRow += `</tr>`;
  if (testColumns.length) {
    for (const col of testColumns) {
      const label = String(col.column?.label || "TEST");
      head += `<th class="ap-class-col is-test-run">
        <span class="ap-class-label">${escapeHtml(label)}</span>
        <span class="ap-not-counted-wrap"><span class="ap-not-counted">not counted</span>${staffInfoTriggerHtml("info-popover-participation-test-col", "Test class column help")}</span>
      </th>`;
    }
  }
  head += `</tr>`;

  let body = "";
  for (const student of students) {
    const sid = Number(student.id);
    const groupText = String((data.group_engagement || {})[String(sid)] || "").trim();
    const groupLine = groupText
      ? `<span class="ap-group-engagement">Group engagement ${escapeHtml(groupText)}</span>`
      : "";
    body += `<tr><td class="name">${escapeHtml(displayName(student))}${groupLine}</td>`;
    for (const piece of pieces) {
      for (const col of piece.cols) {
        if (col.kind === "summary") {
          const roll = moduleRollup(data, sid, piece.band.columns);
          const text = formatParticipationCell(roll.score, roll.opportunities, roll.ended_at);
          body += `<td class="ap-summary-cell">${text}</td>`;
          continue;
        }
        const column = col.column;
        const meta = data.session_meta?.[String(column.id)] || {};
        const scored = cellScore(data, Number(column.id), sid, meta);
        const office = Boolean(column.office);
        const html = classCellHtml(data, Number(column.id), sid, scored, office);
        body += `<td class="ap-class-cell${office ? " is-office" : ""}">${html}</td>`;
      }
    }
    if (currentBand) {
      const roll = moduleRollup(data, sid, currentBand.columns);
      const text = formatParticipationCell(roll.score, roll.opportunities, roll.ended_at);
      body += `<td class="ap-so-far">${text}</td>`;
    }
    if (testColumns.length) {
      body += `<td class="ap-test-divider-cell" aria-hidden="true"></td>`;
      for (const col of testColumns) {
        const column = col.column;
        const meta = data.session_meta?.[String(column.id)] || {};
        const scored = cellScore(data, Number(column.id), sid, meta);
        const html = classCellHtml(data, Number(column.id), sid, scored, false);
        body += `<td class="ap-class-cell is-test-run">${html}</td>`;
      }
    }
    body += `</tr>`;
  }
  if (!students.length) {
    const span =
      1 +
      pieces.reduce((sum, piece) => sum + piece.cols.length, 0) +
      (testColumns.length ? testColumns.length + 1 : 0) +
      (currentBand ? 1 : 0);
    body = `<tr><td class="ap-mod-empty" colspan="${span}">No students on this roster yet.</td></tr>`;
  }
  table.innerHTML = `<thead>${groupRow}${head}</thead><tbody>${body}</tbody>`;
}

/**
 * @param {string} liveModule
 * @returns {number}
 */
function moduleNumber(liveModule) {
  const digits = String(liveModule || "").replace(/^M/i, "");
  return digits && /^\d+$/.test(digits) ? Number(digits) : 0;
}

/**
 * Remember one display mode and repaint.
 * @param {string} mode
 */
function setDisplayMode(mode) {
  if (!DISPLAY_MODES.includes(mode)) return;
  displayMode = mode;
  localStorage.setItem(displayModeKey, displayMode);
  if (latest) paint(latest);
}

/**
 * Remember All / Individual / Group and repaint.
 * @param {string} slice
 */
function setSlice(slice) {
  if (slice === "individual") {
    includeIndividual = true;
    includeGroup = false;
  } else if (slice === "group") {
    includeIndividual = false;
    includeGroup = true;
  } else {
    includeIndividual = true;
    includeGroup = true;
  }
  localStorage.setItem(includeIndividualKey, includeIndividual ? "1" : "0");
  localStorage.setItem(includeGroupKey, includeGroup ? "1" : "0");
  if (latest) paint(latest);
}

/**
 * Expand or collapse every finished module for this teacher.
 * @param {boolean} open
 */
function setAllFinished(open) {
  const bands = latest?.module_bands?.bands || [];
  for (const band of bands) {
    if (band.role !== "finished") continue;
    expanded[String(band.module || "").toUpperCase()] = open;
  }
  saveExpandMap();
  if (latest) paint(latest);
}

document.querySelectorAll("[data-display]").forEach((btn) => {
  btn.addEventListener("click", () => {
    setDisplayMode(String(btn.getAttribute("data-display") || ""));
  });
});

document.querySelectorAll("[data-slice]").forEach((btn) => {
  btn.addEventListener("click", () => {
    setSlice(String(btn.getAttribute("data-slice") || "all"));
  });
});

document.getElementById("ap-display-mode-select")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  setDisplayMode(select.value);
});

document.getElementById("ap-expand-all")?.addEventListener("click", () => setAllFinished(true));
document.getElementById("ap-collapse-all")?.addEventListener("click", () => setAllFinished(false));

root?.addEventListener("click", async (event) => {
  const target = event.target instanceof Element ? event.target : event.target?.parentElement;
  if (!target) return;
  const deleteBtn = target.closest(".ap-delete-test");
  if (deleteBtn instanceof HTMLButtonElement) {
    const sid = Number(deleteBtn.dataset.liveSessionId || 0);
    try {
      const gone = await deleteTestClass(classId, sid);
      if (gone) await refresh();
    } catch (err) {
      showError("#error", err);
    }
    return;
  }
  const toggle = target.closest(".ap-mod-toggle");
  if (!(toggle instanceof HTMLButtonElement)) return;
  const module = String(toggle.dataset.module || "").toUpperCase();
  if (!module) return;
  expanded[module] = !Boolean(expanded[module]);
  saveExpandMap();
  if (latest) paint(latest);
});

bindStaffInfoPopovers();
refresh().catch((err) => showError("#error", err));
