import { api, escapeHtml, hideError, showError } from "/static/common.js";
import { moodGlyph } from "/static/mood_faces.js";

const root = document.getElementById("mood-root");
const classId = Number(root?.dataset.classId || 0);
const sortKey = `lloves-mood-sort-${classId}`;
let sort = localStorage.getItem(sortKey) === "za" ? "za" : "az";

/**
 * Roster label is the Codename only.
 * @param {{codename?: string, first_name?: string}} student
 * @returns {string}
 */
function displayName(student) {
  return String(student?.codename || student?.first_name || "").trim();
}

/**
 * Load and paint the Mood week grid (same columns as Attendance).
 */
async function refresh() {
  hideError("#mood-error");
  try {
    const data = await api(`/api/classes/${classId}/mood-grid?sort=${sort}`);
    paint(data);
  } catch (err) {
    showError("#mood-error", err);
  }
}

/**
 * Render date labels and mood faces in attendance-shaped cells.
 * @param {any} data
 */
function paint(data) {
  const table = document.getElementById("mood-grid");
  if (!table) return;
  const weeks = data.weeks || [];
  const labels = data.date_labels || [];
  const meta = data.day_meta || [];
  const headers = data.weekday_headers || ["M", "T", "W", "T", "F"];
  const students = data.students || [];
  const dayTotals = data.day_totals || {};
  const sortLabel = sort === "za" ? "Sort: Z–A" : "Sort: A–Z";
  const sortBtn = document.getElementById("mood-sort");
  if (sortBtn) sortBtn.textContent = sortLabel;

  let dates = `<tr class="att-dates"><th class="name"></th>`;
  weeks.forEach((week, weekIndex) => {
    week.forEach((iso, dayIndex) => {
      const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
      const cell = meta[weekIndex]?.[dayIndex];
      const label = labels[weekIndex]?.[dayIndex] || cell?.label || "";
      const closed = cell && cell.school_day === false;
      dates += `<th class="att-day att-date${edge}${closed ? " closed" : ""}">${escapeHtml(label)}</th>`;
    });
  });
  dates += `<th class="total"></th></tr>`;

  let head = `<tr><th class="name">Student</th>`;
  weeks.forEach((week, weekIndex) => {
    headers.forEach((label, dayIndex) => {
      const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
      const cell = meta[weekIndex]?.[dayIndex];
      const closed = cell && cell.school_day === false;
      head += `<th class="att-day${edge}${closed ? " closed" : ""}">${escapeHtml(label)}</th>`;
    });
  });
  head += `<th class="total">Total</th></tr>`;

  let body = "";
  for (const student of students) {
    const sid = Number(student.id);
    const face = moodGlyph(student.mood);
    const nameHtml = face
      ? `${face} ${escapeHtml(displayName(student))}`
      : escapeHtml(displayName(student));
    body += `<tr><td class="name">${nameHtml}</td>`;
    weeks.forEach((week, weekIndex) => {
      week.forEach((iso, dayIndex) => {
        const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
        const cell = meta[weekIndex]?.[dayIndex];
        if (!iso || !cell) {
          body += `<td class="att-day empty${edge}"></td>`;
          return;
        }
        if (cell.school_day === false) {
          body += `<td class="att-day closed${edge}"></td>`;
          return;
        }
        const mood = data.cells[`${sid}:${iso}`];
        const glyph = moodGlyph(mood);
        const kind = mood ? ` mood-${mood}` : " unset";
        body += `<td class="att-day${kind}${edge}">${glyph}</td>`;
      });
    });
    const total = data.totals?.[String(sid)] ?? 0;
    body += `<td class="total">${escapeHtml(String(total))}</td></tr>`;
  }

  let dayTotalRow = `<tr class="att-day-total"><td class="name">Total</td>`;
  weeks.forEach((week, weekIndex) => {
    week.forEach((iso, dayIndex) => {
      const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
      const cell = meta[weekIndex]?.[dayIndex];
      if (!iso || !cell || cell.school_day === false) {
        dayTotalRow += `<td class="att-day total-cell${edge}"></td>`;
        return;
      }
      const count = dayTotals[iso] ?? 0;
      dayTotalRow += `<td class="att-day total-cell${edge}">${count ? escapeHtml(String(count)) : ""}</td>`;
    });
  });
  dayTotalRow += `<td class="total"></td></tr>`;

  table.innerHTML = `<thead>${dates}${head}</thead><tbody>${body}${dayTotalRow}</tbody>`;
}

document.getElementById("mood-sort")?.addEventListener("click", () => {
  sort = sort === "az" ? "za" : "az";
  localStorage.setItem(sortKey, sort);
  refresh();
});

refresh();
