import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("feedback-root");
const classId = Number(root?.dataset.classId || 0);
let clearMode = false;
let latestGrid = null;

/**
 * Plain mood label. Empty when the cell has no mood.
 * @param {string|null|undefined} mood
 * @returns {string}
 */
function moodLabel(mood) {
  return String(mood || "").trim().toLowerCase();
}

/**
 * Signed ledger score: +N, 0, or −N. Empty when the score is absent.
 * @param {number|string|null|undefined} score
 * @returns {string}
 */
function formatScore(score) {
  if (score == null || score === "") return "";
  const n = Number(score);
  if (!Number.isFinite(n)) return "";
  if (n > 0) return `+${n}`;
  if (n < 0) return `−${Math.abs(n)}`;
  return "0";
}

/**
 * Summary segments for one live-class cell, excluding the note link.
 * @param {any} cell
 * @returns {string[]}
 */
function summarySegments(cell) {
  const before = moodLabel(cell?.before_mood);
  const after = moodLabel(cell?.after_mood);
  const score = formatScore(cell?.score);
  const parts = [];
  if (before && after) parts.push(`${before} → ${after}`);
  else if (before || after) parts.push(before || after);
  if (score !== "") parts.push(score);
  return parts;
}

/**
 * One text summary cell, or an em dash when the intersection is empty.
 * @param {any} student
 * @param {any} col
 * @param {any} cell
 * @returns {string}
 */
function summaryCell(student, col, cell) {
  const name = String(student?.codename || "Student");
  const key = String(col?.key || "");
  const parts = summarySegments(cell || {});
  const hasNote = Boolean(cell?.has_comment);
  if (!parts.length && !hasNote) {
    return `<td class="feedback-summary-cell">—</td>`;
  }
  const visible = parts.join(" · ");
  const ariaDetail = visible && hasNote ? `${visible}, note` : hasNote ? "note" : visible;
  const aria = `${name}, ${key}, ${ariaDetail}`;
  const textHtml = visible
    ? `<span class="feedback-summary-text">${escapeHtml(visible)}</span>`
    : "";
  const noteHtml = hasNote
    ? `${visible ? " · " : ""}<span class="feedback-summary-note">note</span>`
    : "";
  return (
    `<td class="feedback-summary-cell">` +
    `<button type="button" class="feedback-summary" aria-haspopup="dialog" ` +
    `aria-label="${escapeHtml(aria)}" ` +
    `data-name="${escapeHtml(name)}" data-live-key="${escapeHtml(key)}" ` +
    `data-before="${escapeHtml(moodLabel(cell?.before_mood))}" ` +
    `data-after="${escapeHtml(moodLabel(cell?.after_mood))}" ` +
    `data-score="${escapeHtml(formatScore(cell?.score))}" ` +
    `data-comment="${escapeHtml(cell?.comment || "")}">` +
    `${textHtml}${noteHtml}</button></td>`
  );
}

/**
 * Fill the reused comment dialog as a text-only detail sheet.
 * @param {HTMLElement} button
 */
function openFeedbackDetail(button) {
  const dialog = document.getElementById("feedback-comment-dialog");
  const title = document.getElementById("feedback-comment-title");
  const beforeEl = document.getElementById("feedback-detail-before");
  const afterEl = document.getElementById("feedback-detail-after");
  const scoreEl = document.getElementById("feedback-detail-score");
  const commentEl = document.getElementById("feedback-detail-comment");
  const name = button.getAttribute("data-name") || "Student";
  const key = button.getAttribute("data-live-key") || "";
  const before = button.getAttribute("data-before") || "";
  const after = button.getAttribute("data-after") || "";
  const score = button.getAttribute("data-score") || "";
  const comment = button.getAttribute("data-comment") || "";
  if (title) title.textContent = key ? `${name} · ${key}` : name;
  if (beforeEl) beforeEl.textContent = before || "—";
  if (afterEl) afterEl.textContent = after || "—";
  if (scoreEl) scoreEl.textContent = score || "—";
  if (commentEl) commentEl.textContent = comment.trim() ? comment : "No comment";
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
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
  let groups = "";
  for (const col of columns) {
    const key = String(col.key || "");
    const clickable =
      clearMode && key ? ` data-clear-key="${escapeHtml(key)}"` : "";
    groups += `<th class="feedback-live-group${clickable ? " clear-target" : ""}" data-live-key="${escapeHtml(key)}"${clickable}>${escapeHtml(key)}</th>`;
  }
  let body = "";
  if (!students.length) {
    const span = 2 + columns.length;
    body = `<tr><td colspan="${span}" class="hint">No student feedback yet.</td></tr>`;
  }
  for (const student of students) {
    body += `<tr><td class="name">${escapeHtml(student.codename || "Student")}</td>`;
    for (const col of columns) {
      const cell = (student.cells || {})[col.key] || {};
      body += summaryCell(student, col, cell);
    }
    body += `<td class="total">${escapeHtml(formatScore(student.total == null ? 0 : student.total))}</td></tr>`;
  }
  let foot = "";
  if (columns.length) {
    foot = `<tfoot><tr><th class="name">Class total</th>`;
    for (const col of columns) {
      foot += `<td class="feedback-score">${escapeHtml(formatScore(totals[col.key] ?? 0))}</td>`;
    }
    foot += `<th class="total">${escapeHtml(formatScore(data.grand_total || 0))}</th></tr></tfoot>`;
  }
  table.innerHTML = `<thead><tr><th class="name">Student</th>${groups}<th class="total">Total</th></tr></thead><tbody>${body}</tbody>${foot}`;
  document.getElementById("fb-clear-hint")?.toggleAttribute("hidden", !clearMode);
  document.getElementById("fb-clear")?.classList.toggle("on", clearMode);
}

/**
 * Open a summary detail sheet, or clear one live-class column.
 * @param {MouseEvent} event
 */
async function onRootClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const summary = target.closest(".feedback-summary");
  if (summary instanceof HTMLElement) {
    openFeedbackDetail(summary);
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
