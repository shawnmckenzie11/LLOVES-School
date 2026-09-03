import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("gradebook-root");
const classId = Number(root?.dataset.classId || 0);

/**
 * Roster label is the Codename only.
 * @param {{codename?: string, first_name?: string}} student
 * @returns {string}
 */
function displayName(student) {
  return String(student?.codename || student?.first_name || "").trim();
}

/**
 * Format a category score for the sheet.
 * @param {unknown} value
 * @returns {string}
 */
function scoreLabel(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return String(Math.round(n * 10) / 10);
}

/**
 * Load and paint the weighted gradebook scaffold.
 */
async function refresh() {
  hideError("#gradebook-error");
  const data = await api(`/api/classes/${classId}/gradebook?sort=az`);
  paint(data);
}

/**
 * Render weights summary and student category columns.
 * @param {any} data
 */
function paint(data) {
  const weightsEl = document.getElementById("gradebook-weights");
  const table = document.getElementById("gradebook-sheet");
  if (!weightsEl || !table) return;
  const categories = data.categories || [];
  const weights = data.weights || {};
  weightsEl.innerHTML = categories
    .map((cat) => {
      const pct = weights[cat.id] ?? cat.weight_pct;
      const note = cat.placeholder ? " (placeholder)" : "";
      return `<span class="weight-chip" data-category="${escapeHtml(cat.id)}"><strong>${escapeHtml(cat.label)}</strong> ${escapeHtml(String(pct))}%${note}</span>`;
    })
    .join(" ");
  // Extension point: a future edit UI can POST data.weight_edit_endpoint.

  let head = "<tr><th class='name'>Student</th>";
  for (const cat of categories) {
    head += `<th>${escapeHtml(cat.label)} <span class="weight-pct">${escapeHtml(String(cat.weight_pct))}%</span></th>`;
  }
  head += "</tr>";

  let body = "";
  for (const student of data.students || []) {
    const sid = String(student.id);
    body += `<tr><td class="name">${escapeHtml(displayName(student))}</td>`;
    for (const cat of categories) {
      const val = cat.scores ? cat.scores[sid] : null;
      body += `<td class="cell">${escapeHtml(scoreLabel(val))}</td>`;
    }
    body += "</tr>";
  }
  table.innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}

refresh().catch((err) => showError("#gradebook-error", err));
