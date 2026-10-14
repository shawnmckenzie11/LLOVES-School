import { api, escapeHtml, formatPoints, hideError, showError } from "/static/common.js";

const root = document.getElementById("grades-root");
const classId = Number(root?.dataset.classId || 0);
const courseSimple = root?.dataset.courseSimple === "1";
const sortKey = `lloves-sort-${classId}`;
const roundViewKey = `mgs-round-view-${classId}`;
const dataModeKey = `lloves-ap-data-mode-${classId}`;
const individualMetricKey = `lloves-ap-individual-metric-${classId}`;
const ROUND_VIEWS = ["total", "r1", "r2", "r3"];
const DATA_MODES = ["group", "individual"];
const INDIVIDUAL_METRICS = ["level", "percent"];
const SLICE_KEYS = { total: "points", r1: "points_r1", r2: "points_r2", r3: "points_r3" };
const STACK_LABELS = { r1: "Open", r2: "Challenge", r3: "Formative" };

let sort = localStorage.getItem(sortKey) === "za" ? "za" : "az";
let roundView = loadRoundView(localStorage.getItem(roundViewKey));
// MCK-183: pack-less non-math courses have no group slice picker — Total only.
if (!document.getElementById("ap-group-slice-select")) roundView = "total";
let dataMode = loadDataMode(localStorage.getItem(dataModeKey));
let individualMetric = loadIndividualMetric(localStorage.getItem(individualMetricKey));
let latest = null;
let clearMode = false;

/**
 * Roster label is the Codename only.
 * @param {{codename?: string, first_name?: string}} student
 * @returns {string}
 */
function displayName(student) {
  return String(student?.codename || student?.first_name || "").trim();
}

/**
 * Restore the lesson-score view. Old Overall/By-round prefs still work.
 * @param {string|null} raw
 * @returns {"total"|"r1"|"r2"|"r3"|"all"}
 */
function loadRoundView(raw) {
  if (raw === "overall" || raw === "all" || raw === "rounds") return "total";
  if (ROUND_VIEWS.includes(raw)) return raw;
  return "total";
}

/**
 * @param {string|null} raw
 * @returns {"group"|"individual"}
 */
function loadDataMode(raw) {
  return DATA_MODES.includes(raw || "") ? raw : "group";
}

/**
 * @param {string|null} raw
 * @returns {"level"|"percent"}
 */
function loadIndividualMetric(raw) {
  return INDIVIDUAL_METRICS.includes(raw || "") ? raw : "level";
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
  const sliceSelect = document.getElementById("ap-group-slice-select");
  if (sliceSelect instanceof HTMLSelectElement) sliceSelect.value = roundView;
  const modeSelect = document.getElementById("ap-data-mode-select");
  if (modeSelect instanceof HTMLSelectElement) modeSelect.value = dataMode;
  const metricSelect = document.getElementById("ap-individual-metric-select");
  if (metricSelect instanceof HTMLSelectElement) metricSelect.value = individualMetric;
  updateFilterChrome();
  renderSheet(data);
}

/** Show group vs individual controls and hide calendar-only tools when needed. */
function updateFilterChrome() {
  const individual = dataMode === "individual";
  document.getElementById("ap-individual-metric-wrap")?.toggleAttribute("hidden", !individual);
  document.getElementById("ap-individual-hint")?.toggleAttribute("hidden", !individual);
  document.getElementById("ap-group-slice-wrap")?.toggleAttribute("hidden", individual);
  document.getElementById("ap-evidence-coverage")?.toggleAttribute("hidden", individual || courseSimple);
  document.querySelector(".grades-panel")?.toggleAttribute("hidden", individual);
  document.getElementById("part-clear")?.toggleAttribute("hidden", individual);
  if (individual && clearMode) {
    clearMode = false;
  }
}

/**
 * Points for one calendar cell for the active Class Data View slice.
 * @param {{points?: number, points_r1?: number, points_r2?: number, points_r3?: number}|null|undefined} cell
 * @returns {number}
 */
function gridCellPoints(cell) {
  if (!cell) return 0;
  const slice = roundView === "r1" || roundView === "r2" || roundView === "r3" ? roundView : "total";
  return cellSlice(cell, slice);
}

/**
 * Build the semester calendar sheet (same columns as attendance).
 * @param {any} data
 */
function renderSheet(data) {
  const table = document.getElementById("sheet");
  if (!table) return;
  table.classList.remove("attendance-grid", "ap-module-grid");
  if (dataMode === "individual") {
    renderIndividualSheet(data, table);
    return;
  }
  const weeks = data.weeks || [];
  const labels = data.date_labels || [];
  const meta = data.day_meta || [];
  const headers = data.weekday_headers || ["M", "T", "W", "T", "F"];
  const students = data.students || [];
  const sortLabel = sort === "za" ? "Sort: Z–A" : "Sort: A–Z";
  const daySums = {};

  let dates = `<tr class="att-dates"><th class="name"></th>`;
  weeks.forEach((week, weekIndex) => {
    week.forEach((iso, dayIndex) => {
      const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
      const cell = meta[weekIndex]?.[dayIndex];
      const label = labels[weekIndex]?.[dayIndex] || cell?.label || "";
      const closed = cell && cell.school_day === false;
      const clickable =
        clearMode && iso && cell?.school_day ? ` data-clear-date="${escapeHtml(iso)}"` : "";
      const title = cell?.iso
        ? ` title="${escapeHtml(cell.iso)}${cell.reason ? ` · ${escapeHtml(cell.reason)}` : ""}"`
        : "";
      dates += `<th class="att-day att-date${edge}${closed ? " closed" : ""}${clickable ? " clear-target" : ""}"${title}${clickable}>${escapeHtml(label)}</th>`;
    });
  });
  dates += `<th class="live-sub">SUBTOTAL</th><th class="total">TOTAL</th></tr>`;

  let head = `<tr><th class="name">Student</th>`;
  weeks.forEach((week, weekIndex) => {
    headers.forEach((label, dayIndex) => {
      const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
      const cell = meta[weekIndex]?.[dayIndex];
      const closed = cell && cell.school_day === false;
      const title = cell?.iso ? ` title="${escapeHtml(cell.iso)}"` : "";
      head += `<th class="att-day${edge}${closed ? " closed" : ""}"${title}>${escapeHtml(label)}</th>`;
    });
  });
  head += `<th class="live-sub">SUBTOTAL</th><th class="total">TOTAL SCORE</th></tr>`;

  let body = "";
  for (const student of students) {
    const sid = Number(student.id);
    body += `<tr><td class="name">${escapeHtml(displayName(student))}</td>`;
    weeks.forEach((week, weekIndex) => {
      week.forEach((iso, dayIndex) => {
        const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
        const cellMeta = meta[weekIndex]?.[dayIndex];
        if (!iso || !cellMeta) {
          body += `<td class="att-day empty${edge}"></td>`;
          return;
        }
        if (cellMeta.school_day === false) {
          body += `<td class="att-day closed${edge}"></td>`;
          return;
        }
        const cell = data.cells?.[`${sid}:${iso}`];
        const pts = gridCellPoints(cell);
        if (pts) daySums[iso] = numericPoints((daySums[iso] || 0) + pts);
        const text = pts ? escapeHtml(formatPoints(pts)) : "";
        body += `<td class="att-day cell${edge}">${text}</td>`;
      });
    });
    const liveSub = data.live_subtotals?.[String(sid)] ?? 0;
    const total = data.totals?.[String(sid)] ?? 0;
    body += `<td class="live-sub">${escapeHtml(formatPoints(liveSub))}</td>`;
    body += `<td class="total">${escapeHtml(formatPoints(total))}</td></tr>`;
  }

  let dayTotalRow = `<tr class="att-day-total"><td class="name">Total</td>`;
  weeks.forEach((week, weekIndex) => {
    week.forEach((iso, dayIndex) => {
      const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
      const cellMeta = meta[weekIndex]?.[dayIndex];
      if (!iso || !cellMeta || cellMeta.school_day === false) {
        dayTotalRow += `<td class="att-day total-cell${edge}"></td>`;
        return;
      }
      const sum = daySums[iso] ?? 0;
      dayTotalRow += `<td class="att-day total-cell${edge}">${sum ? escapeHtml(formatPoints(sum)) : ""}</td>`;
    });
  });
  dayTotalRow += `<td class="live-sub"></td><td class="total"></td></tr>`;

  table.innerHTML = `<thead>${dates}${head}</thead><tbody>${body}${dayTotalRow}</tbody>`;
  table.classList.add("attendance-grid");
  document.getElementById("part-clear-hint")?.toggleAttribute("hidden", !clearMode);
  const clearBtn = document.getElementById("part-clear");
  if (clearBtn) clearBtn.classList.toggle("on", clearMode);
  const sortBtn = document.getElementById("sort-toggle");
  if (sortBtn) sortBtn.textContent = sortLabel;
}

/**
 * Per-module individual participation (MCK-198 / MCK-220).
 * @param {any} data
 * @param {HTMLTableElement} table
 */
function renderIndividualSheet(data, table) {
  const students = data.students || [];
  const part = data.module_participation || { modules: [], students: [] };
  const modules = part.modules || [];
  const byStudent = new Map(
    (part.students || []).map((row) => [Number(row.student_id), row.modules || {}]),
  );
  const sortLabel = sort === "za" ? "Sort: Z–A" : "Sort: A–Z";

  let head = `<tr><th class="name">Student</th>`;
  for (const mod of modules) {
    const complete = Boolean(mod.complete);
    const markLabel = complete
      ? `Module ${escapeHtml(mod.module)} complete · Undo`
      : `Mark Module ${escapeHtml(mod.module)} Complete`;
    head += `<th class="ap-module-col">
      <div class="ap-module-head">${escapeHtml(mod.module)} (${Number(mod.offered) || 0})</div>
      <button type="button" class="btn secondary ap-module-mark" data-module="${escapeHtml(mod.module)}" data-complete="${complete ? "0" : "1"}">${markLabel}</button>
      <label class="ap-module-l4"><input type="checkbox" class="ap-module-l4-input" data-module="${escapeHtml(mod.module)}" ${mod.l4_needs_group ? "checked" : ""} ${complete ? "disabled" : ""}> Level 4 needs group work</label>
    </th>`;
  }
  head += "</tr>";

  let body = "";
  for (const student of students) {
    const sid = Number(student.id);
    const cells = byStudent.get(sid) || {};
    body += `<tr><td class="name">${escapeHtml(displayName(student))}</td>`;
    for (const mod of modules) {
      const cell = cells[mod.module] || { answered: 0, offered: mod.offered, percent: null, level: null };
      body += `<td class="ap-module-cell">${escapeHtml(formatIndividualCell(cell))}</td>`;
    }
    body += "</tr>";
  }

  table.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  table.classList.add("ap-module-grid");
  document.getElementById("part-clear-hint")?.toggleAttribute("hidden", true);
  document.getElementById("part-clear")?.classList.remove("on");
  const sortBtn = document.getElementById("sort-toggle");
  if (sortBtn) sortBtn.textContent = sortLabel;
}

/**
 * @param {{answered?: number, offered?: number, percent?: number|null, level?: number|null, catchup?: number}} cell
 * @returns {string}
 */
function formatIndividualCell(cell) {
  const answered = Number(cell.answered) || 0;
  const offered = Number(cell.offered) || 0;
  const base = `${answered}/${offered}`;
  if (offered <= 0) return "—";
  if (individualMetric === "percent") {
    const pct = cell.percent;
    return pct == null ? base : `${pct}%`;
  }
  const level = cell.level;
  if (level == null) return base;
  if (level === 0) return "below L1";
  return `L${level}`;
}

/**
 * Credited points for one slice of a lesson cell.
 * @param {{points?: number, points_r1?: number, points_r2?: number, points_r3?: number}|null|undefined} cell
 * @param {"total"|"r1"|"r2"|"r3"} slice
 * @returns {number}
 */
function cellSlice(cell, slice) {
  const key = SLICE_KEYS[slice] || "points";
  return numericPoints(cell?.[key]);
}

/**
 * Round a credit the same way the sheet prints it.
 * @param {unknown} value
 * @returns {number}
 */
function numericPoints(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.round(n * 10) / 10;
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
 * Sum one slice across the given lesson columns.
 * @param {any} data
 * @param {number} studentId
 * @param {Array<{id: number}>} columns
 * @param {"total"|"r1"|"r2"|"r3"} slice
 * @returns {number}
 */
function sumStudentSlice(data, studentId, columns, slice) {
  let sum = 0;
  for (const column of columns) {
    const cell = data.cells[`${column.id}:${studentId}`];
    if (!cell) continue;
    sum = numericPoints(sum + cellSlice(cell, slice));
  }
  return sum;
}

/**
 * Live SUBTOTAL or TOTAL SCORE for the active slice (or stacked All).
 * @param {any} data
 * @param {number} studentId
 * @param {Array<{id: number}>} columns
 * @param {string} extraClass
 * @returns {string}
 */
function summaryCellHtml(data, studentId, columns, extraClass) {
  if (roundView === "all") {
    const r1 = formatPoints(sumStudentSlice(data, studentId, columns, "r1"));
    const r2 = formatPoints(sumStudentSlice(data, studentId, columns, "r2"));
    const r3 = formatPoints(sumStudentSlice(data, studentId, columns, "r3"));
    return `<td class="${extraClass} by-round"><div class="round-stack">
      <span>${STACK_LABELS.r1} ${escapeHtml(r1)}</span>
      <span>${STACK_LABELS.r2} ${escapeHtml(r2)}</span>
      <span>${STACK_LABELS.r3} ${escapeHtml(r3)}</span>
    </div></td>`;
  }
  const slice = roundView === "r1" || roundView === "r2" || roundView === "r3" ? roundView : "total";
  return `<td class="${extraClass}">${escapeHtml(formatPoints(sumStudentSlice(data, studentId, columns, slice)))}</td>`;
}

/**
 * True when this round is the student's strongest slice that day.
 * Skips single-round (all-in-R1) days so every scorer is not highlighted.
 * @param {{points_r1?: number, points_r2?: number, points_r3?: number}} cell
 * @param {"total"|"r1"|"r2"|"r3"|"all"} view
 * @returns {boolean}
 */
function sliceIsLead(cell, view) {
  if (view !== "r1" && view !== "r2" && view !== "r3") return false;
  const r1 = numericPoints(cell.points_r1);
  const r2 = numericPoints(cell.points_r2);
  const r3 = numericPoints(cell.points_r3);
  const scored = [r1, r2, r3].filter((v) => v > 0).length;
  if (scored < 2) return false;
  const val = view === "r1" ? r1 : view === "r2" ? r2 : r3;
  return val > 0 && val >= r1 && val >= r2 && val >= r3;
}

/**
 * Thin Open / Challenge / Formative mix bar. Zero rounds are omitted.
 * @param {{points_r1?: number, points_r2?: number, points_r3?: number}} cell
 * @returns {string}
 */
function mixBarHtml(cell) {
  const r1 = Math.max(0, numericPoints(cell.points_r1));
  const r2 = Math.max(0, numericPoints(cell.points_r2));
  const r3 = Math.max(0, numericPoints(cell.points_r3));
  if (r1 + r2 + r3 <= 0) return "";
  const parts = [];
  if (r1 > 0) parts.push(`<span class="mix-r1" style="flex:${r1}"></span>`);
  if (r2 > 0) parts.push(`<span class="mix-r2" style="flex:${r2}"></span>`);
  if (r3 > 0) parts.push(`<span class="mix-r3" style="flex:${r3}"></span>`);
  return `<div class="round-mix" aria-hidden="true">${parts.join("")}</div>`;
}

/**
 * One lesson cell: a single slice, or stacked Open / Challenge / Formative.
 * @param {{present?: boolean, points?: number, points_r1?: number, points_r2?: number, points_r3?: number}} cell
 * @param {string} kind
 * @returns {string}
 */
function sessionCellHtml(cell, kind) {
  if (roundView === "all") {
    const r1 = formatPoints(cell.points_r1);
    const r2 = formatPoints(cell.points_r2);
    const r3 = formatPoints(cell.points_r3);
    const mix = kind === "present" ? mixBarHtml(cell) : "";
    return `<td class="cell ${kind} by-round">${mix}<div class="round-stack">
      <span>${STACK_LABELS.r1} ${escapeHtml(r1)}</span>
      <span>${STACK_LABELS.r2} ${escapeHtml(r2)}</span>
      <span>${STACK_LABELS.r3} ${escapeHtml(r3)}</span>
    </div></td>`;
  }
  const slice = roundView === "r1" || roundView === "r2" || roundView === "r3" ? roundView : "total";
  const value = formatPoints(cellSlice(cell, slice));
  const lead = kind === "present" && sliceIsLead(cell, slice) ? " slice-lead" : "";
  return `<td class="cell ${kind}${lead}"><span class="cell-score">${escapeHtml(value)}</span></td>`;
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

document.getElementById("ap-group-slice-select")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  const next = select.value;
  if (!ROUND_VIEWS.includes(next)) return;
  roundView = next;
  localStorage.setItem(roundViewKey, roundView);
  if (latest) paint(latest);
});

document.getElementById("ap-data-mode-select")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  if (!DATA_MODES.includes(select.value)) return;
  dataMode = select.value;
  localStorage.setItem(dataModeKey, dataMode);
  if (latest) paint(latest);
});

document.getElementById("ap-individual-metric-select")?.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  if (!INDIVIDUAL_METRICS.includes(select.value)) return;
  individualMetric = select.value;
  localStorage.setItem(individualMetricKey, individualMetric);
  if (latest) paint(latest);
});

document.getElementById("freeze-sub").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = document.getElementById("sub-name").value;
  mutate(`/api/classes/${classId}/subtotals`, { name }).catch((err) =>
    showError("#error", err)
  );
});

/**
 * Ask the teacher to confirm a destructive delete.
 * @param {string} message
 * @returns {Promise<boolean>}
 */
function askToDelete(message) {
  const dialog = document.getElementById("confirm-dialog");
  const text = document.getElementById("confirm-message");
  if (!dialog || typeof dialog.showModal !== "function") {
    return Promise.resolve(window.confirm(message));
  }
  text.textContent = message;
  dialog.returnValue = "cancel";
  dialog.showModal();
  return new Promise((resolve) => {
    const finish = () => {
      dialog.removeEventListener("close", finish);
      resolve(dialog.returnValue === "ok");
    };
    dialog.addEventListener("close", finish);
  });
}

document.getElementById("confirm-dialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) {
    event.currentTarget.close("cancel");
  }
});

document.getElementById("sheet").addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : event.target.parentElement;
  if (!target) return;
  const markBtn = target.closest(".ap-module-mark");
  if (markBtn instanceof HTMLButtonElement) {
    const module = markBtn.dataset.module;
    if (!module) return;
    const complete = markBtn.dataset.complete === "1";
    hideError("#error");
    api(`/api/classes/${classId}/participation/modules/${encodeURIComponent(module)}/complete`, {
      method: "POST",
      body: JSON.stringify({ complete }),
    })
      .then(() => refresh())
      .catch((err) => showError("#error", err));
    return;
  }
  const l4Input = target.closest(".ap-module-l4-input");
  if (l4Input instanceof HTMLInputElement) {
    const module = l4Input.dataset.module;
    if (!module) return;
    hideError("#error");
    api(`/api/classes/${classId}/participation/modules/${encodeURIComponent(module)}/settings`, {
      method: "POST",
      body: JSON.stringify({ l4_needs_group: l4Input.checked }),
    })
      .then(() => refresh())
      .catch((err) => showError("#error", err));
    return;
  }
  const dateCell = target.closest("[data-clear-date]");
  if (!dateCell || !clearMode) return;
  const iso = dateCell.getAttribute("data-clear-date");
  if (!iso) return;
  if (!window.confirm(`Clear participation and attendance for ${iso}?`)) return;
  hideError("#error");
  api(`/api/classes/${classId}/attendance-day/clear`, {
    method: "POST",
    body: JSON.stringify({ date: iso, sort }),
  })
    .then(() => {
      clearMode = false;
      window.dispatchEvent(new CustomEvent("lloves-attendance-refresh"));
      return refresh();
    })
    .catch((err) => showError("#error", err));
});

document.getElementById("sort-toggle")?.addEventListener("click", (event) => {
  event.preventDefault();
  sort = sort === "az" ? "za" : "az";
  localStorage.setItem(sortKey, sort);
  refresh().catch((err) => showError("#error", err));
});

document.getElementById("part-clear")?.addEventListener("click", (event) => {
  event.preventDefault();
  clearMode = !clearMode;
  if (latest) paint(latest);
});

refresh().catch((err) => showError("#error", err));
