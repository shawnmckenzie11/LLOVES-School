import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("feedback-root");
const classId = Number(root?.dataset.classId || 0);
let clearMode = false;
let latestGrid = null;

/**
 * Mood face cell, or an em dash when missing.
 * @param {string|null|undefined} mood
 * @returns {string}
 */
function moodCell(mood) {
  const key = String(mood || "").trim().toLowerCase();
  if (!key) return "—";
  return `<img class="mood-face" src="/static/mood/${escapeHtml(key)}.svg" alt="${escapeHtml(key)}" width="32" height="32">`;
}

/**
 * Render the How-was-class sheet from a grid payload.
 * @param {any} data
 */
function paint(data) {
  const table = document.getElementById("feedback-sheet");
  if (!table) return;
  const columns = data.columns || [];
  const students = data.students || [];
  const totals = data.totals || {};
  const nameSpan = columns.length ? ' rowspan="2"' : "";
  let groups = "";
  for (const col of columns) {
    const key = String(col.key || "");
    const clickable =
      clearMode && key ? ` data-clear-key="${escapeHtml(key)}"` : "";
    groups += `<th colspan="4" class="feedback-live-group${clickable ? " clear-target" : ""}"${clickable}>${escapeHtml(key)}</th>`;
  }
  let sub = "";
  if (columns.length) {
    sub = "<tr>";
    for (const _col of columns) {
      sub += "<th>Before</th><th>After</th><th>Score</th><th>Note</th>";
    }
    sub += "</tr>";
  }
  let body = "";
  if (!students.length) {
    const span = 2 + columns.length * 4;
    body = `<tr><td colspan="${span}" class="hint">No student feedback yet.</td></tr>`;
  }
  for (const student of students) {
    body += `<tr><td class="name">${escapeHtml(student.codename || "Student")}</td>`;
    for (const col of columns) {
      const cell = (student.cells || {})[col.key] || {};
      const score = cell.score;
      const note = cell.has_comment
        ? `<button type="button" class="feedback-note-link" data-comment="${escapeHtml(cell.comment || "")}" data-name="${escapeHtml(student.codename || "Student")}">View</button>`
        : "—";
      body += `<td class="feedback-mood">${moodCell(cell.before_mood)}</td>`;
      body += `<td class="feedback-mood">${moodCell(cell.after_mood)}</td>`;
      body += `<td class="feedback-score">${score == null ? "—" : escapeHtml(String(score))}</td>`;
      body += `<td>${note}</td>`;
    }
    const total = student.total == null ? 0 : student.total;
    body += `<td class="total">${escapeHtml(String(total))}</td></tr>`;
  }
  let foot = "";
  if (columns.length) {
    foot = `<tfoot><tr><th class="name">Class total</th>`;
    for (const col of columns) {
      foot += `<td colspan="3"></td><td class="feedback-score">${escapeHtml(String(totals[col.key] ?? 0))}</td>`;
    }
    foot += `<th class="total">${escapeHtml(String(data.grand_total || 0))}</th></tr></tfoot>`;
  }
  table.innerHTML = `<thead><tr><th class="name"${nameSpan}>Student</th>${groups}<th class="total"${nameSpan}>Total</th></tr>${sub}</thead><tbody>${body}</tbody>${foot}`;
  document.getElementById("fb-clear-hint")?.toggleAttribute("hidden", !clearMode);
  document.getElementById("fb-clear")?.classList.toggle("on", clearMode);
}

/**
 * Toggle clear mode or delete one live-class column.
 * @param {MouseEvent} event
 */
async function onRootClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const note = target.closest(".feedback-note-link");
  if (note) {
    const dialog = document.getElementById("feedback-comment-dialog");
    const title = document.getElementById("feedback-comment-title");
    const body = document.getElementById("feedback-comment-body");
    if (title) title.textContent = note.getAttribute("data-name") || "Comment";
    if (body) body.textContent = note.getAttribute("data-comment") || "";
    dialog?.showModal();
    return;
  }
  const header = target.closest("[data-clear-key]");
  if (!header || !clearMode) return;
  const key = header.getAttribute("data-clear-key");
  if (!key) return;
  if (!window.confirm(`Clear feedback for ${key}?`)) return;
  hideError("#fb-error");
  const data = await api(`/api/classes/${classId}/feedback-live/clear`, {
    method: "POST",
    body: JSON.stringify({ key }),
  });
  latestGrid = data;
  clearMode = false;
  paint(data);
}

document.getElementById("fb-clear")?.addEventListener("click", (event) => {
  event.preventDefault();
  clearMode = !clearMode;
  if (latestGrid) {
    paint(latestGrid);
    return;
  }
  document.getElementById("fb-clear-hint")?.toggleAttribute("hidden", !clearMode);
  document.getElementById("fb-clear")?.classList.toggle("on", clearMode);
  document.querySelectorAll("#feedback-sheet .feedback-live-group").forEach((el) => {
    const key = el.getAttribute("data-live-key") || el.textContent.trim();
    if (clearMode && key) {
      el.classList.add("clear-target");
      el.setAttribute("data-clear-key", key);
    } else {
      el.classList.remove("clear-target");
      el.removeAttribute("data-clear-key");
    }
  });
});

root?.addEventListener("click", (event) => {
  onRootClick(event).catch((err) => showError("#fb-error", err));
});

const seed = document.getElementById("feedback-grid-data");
if (seed) {
  try {
    latestGrid = JSON.parse(seed.textContent || "{}");
  } catch (_err) {
    latestGrid = null;
  }
}
