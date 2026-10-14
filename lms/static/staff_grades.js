import { api, escapeHtml, formatPoints, hideError, showError } from "/static/common.js";

const root = document.getElementById("grades-root");
const classId = Number(root?.dataset.classId || 0);
const courseSimple = root?.dataset.courseSimple === "1";
const sortKey = `lloves-sort-${classId}`;
const includeIndividualKey = `lloves-ap-include-individual-${classId}`;
const includeGroupKey = `lloves-ap-include-group-${classId}`;
const displayModeKey = `lloves-ap-display-mode-${classId}`;
const DISPLAY_MODES = ["points", "fraction", "percent"];

let sort = localStorage.getItem(sortKey) === "za" ? "za" : "az";
let includeIndividual = loadBool(includeIndividualKey, true);
let includeGroup = loadBool(includeGroupKey, true);
let displayMode = loadDisplayMode(localStorage.getItem(displayModeKey));
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
  return DISPLAY_MODES.includes(raw || "") ? raw : "points";
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
  const ind = document.getElementById("ap-include-individual");
  const grp = document.getElementById("ap-include-group");
  if (ind instanceof HTMLInputElement) ind.checked = includeIndividual;
  if (grp instanceof HTMLInputElement) grp.checked = includeGroup;
  const modeSelect = document.getElementById("ap-display-mode-select");
  if (modeSelect instanceof HTMLSelectElement) modeSelect.value = displayMode;
  updateFilterChrome();
  renderSheet(data);
}

/** Toggle evidence panel visibility for pack courses. */
function updateFilterChrome() {
  document.getElementById("ap-evidence-coverage")?.toggleAttribute(
    "hidden",
    courseSimple || (!includeGroup && !includeIndividual),
  );
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

/** MC prompt / item types that weigh 1 (mirrors lms/participation_weight.py). */
const MC_KINDS = new Set(["mc", "multiple_choice", "multiple_choice_question"]);

/**
 * @param {string} promptKind
 * @param {object|null|undefined} payload
 * @returns {boolean}
 */
function itemIsMultipleChoice(promptKind, payload) {
  const kind = String(promptKind || "").toLowerCase();
  if (MC_KINDS.has(kind)) return true;
  const qtype = String(payload?.type || payload?.question_type || "").toLowerCase();
  return MC_KINDS.has(qtype) || qtype === "multiple_choice";
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
  return itemIsMultipleChoice(row.prompt_kind, payload) ? 1 : 0;
}

/**
 * @param {number} score
 * @param {number} opportunities
 * @param {string|null|undefined} endedAt
 * @returns {string}
 */
function formatParticipationCell(score, opportunities, endedAt) {
  if (!includeIndividual && !includeGroup) return "—";
  if (displayMode === "fraction") {
    if (opportunities <= 0) return "—";
    return `${formatScore(score)}/${opportunities}`;
  }
  if (displayMode === "percent") {
    if (opportunities <= 0) return "—";
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
  let score = 0;
  if (includeGroup) {
    const cell = data.cells?.[`${sessionId}:${studentId}`] || {};
    score += numericPoints(cell.points);
  }
  if (includeIndividual) {
    const key = `${sessionId}:${studentId}`;
    score += numericPoints(data.individual_cells?.[key] || 0);
  }
  return { score, opportunities, ended_at: String(meta?.ended_at || "") };
}

/**
 * Build the live-points session spreadsheet.
 * @param {any} data
 */
function renderSheet(data) {
  const table = document.getElementById("sheet");
  if (!table) return;
  table.classList.remove("attendance-grid", "ap-module-grid");
  const columns = data.columns || [];
  const students = data.students || [];
  const sessionMeta = data.session_meta || {};
  const modules = data.module_participation?.modules || [];
  const completeByModule = new Map(modules.map((m) => [String(m.module), Boolean(m.complete)]));
  const sortLabel = sort === "za" ? "Sort: Z–A" : "Sort: A–Z";

  const moduleBands = buildModuleBands(columns, sessionMeta);
  let head = `<tr><th class="name">Student</th>`;
  for (const column of columns) {
    if (column.kind === "subtotal") {
      head += `<th class="subtotal-col">${escapeHtml(column.name || column.header_label)}</th>`;
      continue;
    }
    head += `<th>${escapeHtml(column.header_label || "")}</th>`;
  }
  head += `<th class="live-sub">SUBTOTAL</th><th class="total">TOTAL SCORE</th></tr>`;

  let moduleRow = `<tr class="ap-module-band"><th class="name"></th>`;
  for (const band of moduleBands) {
    const colspan = band.span || 1;
    const mod = band.module;
    if (!mod) {
      moduleRow += `<th colspan="${colspan}"></th>`;
      continue;
    }
    const complete = completeByModule.get(mod);
    const label = complete ? `Module ${moduleNumber(mod)} total` : `Finish Module ${moduleNumber(mod)}`;
    if (complete) {
      moduleRow += `<th colspan="${colspan}" class="ap-module-finish done">${escapeHtml(label)}</th>`;
    } else {
      moduleRow += `<th colspan="${colspan}" class="ap-module-finish">
        <button type="button" class="btn secondary ap-finish-module" data-module="${escapeHtml(mod)}">${escapeHtml(label)}</button>
      </th>`;
    }
  }
  moduleRow += `<th class="live-sub"></th><th class="total"></th></tr>`;

  let body = "";
  const liveCols = liveSessionColumns(columns);
  const allCols = sessionColumns(columns);
  for (const student of students) {
    const sid = Number(student.id);
    body += `<tr><td class="name">${escapeHtml(displayName(student))}</td>`;
    for (const column of columns) {
      if (column.kind === "subtotal") {
        const frozen = data.cells?.[`sub:${column.id}:${sid}`] || { points: 0 };
        body += `<td class="cell frozen">${escapeHtml(formatPoints(frozen.points))}</td>`;
        continue;
      }
      const meta = sessionMeta[String(column.id)] || {};
      const { score, opportunities, ended_at } = cellScore(data, Number(column.id), sid, meta);
      const text = formatParticipationCell(score, opportunities, ended_at);
      const kind = score > 0 ? "present" : "absent";
      body += `<td class="cell ${kind}">${text}</td>`;
    }
    body += summaryCellHtml(data, sid, liveCols);
    body += summaryCellHtml(data, sid, allCols, "total");
    body += "</tr>";
  }

  table.innerHTML = `<thead>${moduleRow}${head}</thead><tbody>${body}</tbody>`;
  const sortBtn = document.getElementById("sort-toggle");
  if (sortBtn) sortBtn.textContent = sortLabel;
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
 * @param {Array<any>} columns
 * @param {Record<string, any>} sessionMeta
 * @returns {Array<{module?: string, span: number}>}
 */
function buildModuleBands(columns, sessionMeta) {
  const bands = [];
  let current = "";
  let span = 0;
  const flush = () => {
    if (span > 0) bands.push({ module: current || undefined, span });
    span = 0;
  };
  for (const column of columns) {
    if (column.kind === "subtotal") {
      flush();
      bands.push({ span: 1 });
      current = "";
      continue;
    }
    const mod = String(sessionMeta[String(column.id)]?.live_module || "");
    if (mod && mod !== current) {
      flush();
      current = mod;
    }
    if (!mod) current = "";
    span += 1;
  }
  flush();
  return bands;
}

/**
 * Lesson columns only (frozen SUBTOTAL snapshots skipped).
 * @param {Array<{kind?: string}>} columns
 * @returns {Array<any>}
 */
function sessionColumns(columns) {
  return (columns || []).filter((column) => column.kind !== "subtotal");
}

/**
 * Sessions that count toward the live SUBTOTAL (after the last freeze).
 * @param {Array<{kind?: string}>} columns
 * @returns {Array<any>}
 */
function liveSessionColumns(columns) {
  const before = [];
  const after = [];
  let seenFreeze = false;
  for (const column of columns || []) {
    if (column.kind === "subtotal") {
      seenFreeze = true;
      after.length = 0;
      continue;
    }
    if (seenFreeze) after.push(column);
    else before.push(column);
  }
  return seenFreeze ? after : before;
}

/**
 * @param {any} data
 * @param {number} studentId
 * @param {Array<{id: number, kind?: string}>} columns
 * @param {string} [extraClass]
 * @returns {string}
 */
function summaryCellHtml(data, studentId, columns, extraClass = "live-sub") {
  let sum = 0;
  for (const column of columns) {
    if (column.kind === "subtotal") continue;
    const meta = data.session_meta?.[String(column.id)] || {};
    sum += cellScore(data, Number(column.id), studentId, meta).score;
  }
  return `<td class="${extraClass}">${escapeHtml(formatPoints(sum))}</td>`;
}

/**
 * POST a dashboard mutation and repaint.
 * @param {string} url
 * @param {object} extra
 */
async function mutate(url, extra) {
  hideError("#error");
  await api(url, {
    method: "POST",
    body: JSON.stringify({ sort, ...extra }),
  });
  await refresh();
}

document.getElementById("ap-include-individual")?.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;
  includeIndividual = input.checked;
  localStorage.setItem(includeIndividualKey, includeIndividual ? "1" : "0");
  if (latest) paint(latest);
});

document.getElementById("ap-include-group")?.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;
  includeGroup = input.checked;
  localStorage.setItem(includeGroupKey, includeGroup ? "1" : "0");
  if (latest) paint(latest);
});

document.getElementById("ap-display-mode-select")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  if (!DISPLAY_MODES.includes(select.value)) return;
  displayMode = select.value;
  localStorage.setItem(displayModeKey, displayMode);
  if (latest) paint(latest);
});

document.getElementById("sort-toggle")?.addEventListener("click", (event) => {
  event.preventDefault();
  sort = sort === "az" ? "za" : "az";
  localStorage.setItem(sortKey, sort);
  refresh().catch((err) => showError("#error", err));
});

root?.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : event.target?.parentElement;
  if (!target) return;
  const finishBtn = target.closest(".ap-finish-module");
  if (finishBtn instanceof HTMLButtonElement) {
    const module = finishBtn.dataset.module;
    if (!module) return;
    if (
      !window.confirm(
        `Finish ${module}? This logs a locked Module total column and marks the module complete.`,
      )
    ) {
      return;
    }
    hideError("#error");
    api(`/api/classes/${classId}/participation/modules/${encodeURIComponent(module)}/finish`, {
      method: "POST",
      body: JSON.stringify({ sort }),
    })
      .then(() => refresh())
      .catch((err) => showError("#error", err));
  }
});

refresh().catch((err) => showError("#error", err));
