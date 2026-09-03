import { api, escapeHtml, formatPoints, hideError, openScoreboardOverlay, reserveScoreboardOverlay, showError } from "/static/common.js";

const root = document.getElementById("grades-root");
const classId = Number(root?.dataset.classId || 0);
const sortKey = `lloves-sort-${classId}`;
const roundViewKey = `mgs-round-view-${classId}`;
const ROUND_VIEWS = ["total", "r1", "r2", "r3", "all"];
const STAT_WINDOWS = ["last_class", "last_week", "year"];
const SLICE_KEYS = { total: "points", r1: "points_r1", r2: "points_r2", r3: "points_r3" };
const STACK_LABELS = { r1: "Open", r2: "Challenge", r3: "Formative" };

let sort = localStorage.getItem(sortKey) === "za" ? "za" : "az";
let roundView = loadRoundView(localStorage.getItem(roundViewKey));
let latest = null;

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
  if (raw === "overall") return "total";
  if (raw === "rounds") return "all";
  if (ROUND_VIEWS.includes(raw)) return raw;
  return "total";
}

/**
 * Load and paint the class spreadsheet.
 */
async function refresh() {
  hideError("#error");
  const data = await api(`/api/classes/${classId}/dashboard?sort=${sort}`);
  paint(data);
}

/**
 * Saved scoreboard stats period from the dashboard payload.
 * @param {any} data
 * @returns {"last_class"|"last_week"|"year"}
 */
function payloadStatWindow(data) {
  const raw = data?.stat_window;
  return STAT_WINDOWS.includes(raw) ? raw : "last_class";
}

/**
 * Apply a dashboard payload to the page.
 * @param {any} data
 */
function paint(data) {
  latest = data;
  const cls = data.class;
  document.querySelectorAll("[data-round-view]").forEach((btn) => {
    const on = btn.dataset.roundView === roundView;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const statWindow = payloadStatWindow(data);
  document.querySelectorAll("[data-stat-window]").forEach((btn) => {
    const on = btn.dataset.statWindow === statWindow;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  renderSheet(data);
}

/**
 * Build the spreadsheet: class columns, frozen subtotals, live SUBTOTAL, TOTAL.
 * @param {any} data
 */
function renderSheet(data) {
  const table = document.getElementById("sheet");
  const columns = data.columns || (data.sessions || []).map((session) => ({
    kind: "session",
    ...session,
  }));
  const students = data.students || [];
  const sortLabel = sort === "za" ? "Sort: Z–A" : "Sort: A–Z";
  let html = `<thead><tr><th class='name'>Student <button type="button" class="secondary" id="sort-toggle">${sortLabel}</button></th>`;
  for (const column of columns) {
    if (column.kind === "subtotal") {
      html += `<th class="subtotal-col">${escapeHtml(column.name || column.header_label)}
        <button type="button" class="icon-btn" data-del-subtotal="${column.id}" title="Delete subtotal">×</button>
      </th>`;
      continue;
    }
    html += `<th>${escapeHtml(column.header_label)}`;
    if (column.status === "ended" && column.log_path) {
      html += `<a class="log-link" href="/api/sessions/${column.id}/log" target="_blank" rel="noopener">log</a>`;
    }
    html += `<button type="button" class="icon-btn" data-del-session="${column.id}" title="Delete column">×</button>`;
    html += "</th>";
  }
  html += "<th class='live-sub'>SUBTOTAL</th><th class='total'>TOTAL SCORE</th></tr></thead><tbody>";
  for (const student of students) {
    html += `<tr><td class="name">${escapeHtml(displayName(student))}</td>`;
    for (const column of columns) {
      if (column.kind === "subtotal") {
        const frozen = data.cells[`sub:${column.id}:${student.id}`] || { points: 0 };
        html += `<td class="cell frozen">${escapeHtml(formatPoints(frozen.points))}</td>`;
        continue;
      }
      const cell = data.cells[`${column.id}:${student.id}`] || {
        present: false,
        points: 0,
        points_r1: 0,
        points_r2: 0,
        points_r3: 0,
      };
      const kind = cell.present ? "present" : "absent";
      html += sessionCellHtml(cell, kind);
    }
    const liveCols = liveSessionColumns(columns);
    const allCols = sessionColumns(columns);
    html += summaryCellHtml(data, student.id, liveCols, "live-sub");
    html += summaryCellHtml(data, student.id, allCols, "total");
  }
  html += "</tbody>";
  table.innerHTML = html;
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
  const data = await api(url, {
    method: "POST",
    body: JSON.stringify({ sort, ...extra }),
  });
  paint(data);
}

document.querySelector("[aria-label='Lesson score view']")?.addEventListener("click", (event) => {
  const btn = event.target instanceof Element ? event.target.closest("[data-round-view]") : null;
  if (!btn) return;
  const next = btn.dataset.roundView;
  if (!ROUND_VIEWS.includes(next)) return;
  roundView = next;
  localStorage.setItem(roundViewKey, roundView);
  if (latest) paint(latest);
});

document.getElementById("stat-window-toggle")?.addEventListener("click", (event) => {
  const btn = event.target instanceof Element ? event.target.closest("[data-stat-window]") : null;
  if (!btn) return;
  const next = btn.dataset.statWindow;
  if (!STAT_WINDOWS.includes(next)) return;
  mutate(`/api/classes/${classId}/stat-window`, { window: next }).catch((err) =>
    showError("#error", err)
  );
});

document.getElementById("begin").addEventListener("click", async () => {
  hideError("#error");
  const overlay = reserveScoreboardOverlay();
  try {
    const state = await api(`/api/classes/${classId}/begin`, {
      method: "POST",
      body: "{}",
    });
    const status = state.game && state.game.status;
    if (status === "live") {
      openScoreboardOverlay(overlay);
      location.href = `/class/${classId}/game`;
      return;
    }
    overlay?.close();
    location.href = `/class/${classId}/setup`;
  } catch (err) {
    overlay?.close();
    showError("#error", err);
  }
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
  if (target.closest("#sort-toggle")) {
    event.preventDefault();
    sort = sort === "az" ? "za" : "az";
    localStorage.setItem(sortKey, sort);
    refresh().catch((err) => showError("#error", err));
    return;
  }
  const delSession = target.closest("[data-del-session]");
  if (delSession) {
    event.preventDefault();
    const id = Number(delSession.dataset.delSession);
    const column = (latest?.columns || latest?.sessions || []).find((c) => Number(c.id) === id);
    const header = column?.header_label || "this class column";
    askToDelete(
      `Delete class column “${header}” and all scores in it?\n\nThis cannot be undone.`
    ).then((ok) => {
      if (!ok) return;
      mutate(`/api/classes/${classId}/sessions/delete`, { session_id: id }).catch((err) =>
        showError("#error", err)
      );
    });
    return;
  }
  const delSub = target.closest("[data-del-subtotal]");
  if (delSub) {
    event.preventDefault();
    const id = Number(delSub.dataset.delSubtotal);
    const column = (latest?.columns || []).find(
      (c) => c.kind === "subtotal" && Number(c.id) === id
    );
    const header = column?.name || column?.header_label || "this subtotal";
    askToDelete(
      `Delete subtotal column “${header}”?\n\nClass scores stay. Live SUBTOTAL will recount from any remaining freeze.`
    ).then((ok) => {
      if (!ok) return;
      mutate(`/api/classes/${classId}/subtotals/delete`, { id }).catch((err) =>
        showError("#error", err)
      );
    });
  }
});

refresh().catch((err) => showError("#error", err));
