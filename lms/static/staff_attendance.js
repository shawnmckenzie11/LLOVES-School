import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("attendance-root");
const classId = Number(root?.dataset.classId || 0);
const sortKey = `lloves-att-sort-${classId}`;
let sort = localStorage.getItem(sortKey) === "za" ? "za" : "az";
let clearMode = false;
let latestGrid = null;

/**
 * Roster label is the Codename only.
 * @param {{codename?: string, first_name?: string}} student
 * @returns {string}
 */
function displayName(student) {
  return String(student?.codename || student?.first_name || "").trim();
}

/**
 * Load and paint the M–T–W–T–F attendance week grid.
 */
export async function refreshAttendanceGrid() {
  hideError("#att-error");
  const data = await api(`/api/classes/${classId}/attendance-grid?sort=${sort}`);
  latestGrid = data;
  paint(data);
}

/**
 * @deprecated Internal alias for initial load.
 */
async function refresh() {
  return refreshAttendanceGrid();
}

/**
 * Render date labels, weekday headers, grey closed days, daily totals, and row totals.
 * @param {any} data
 */
function paint(data) {
  const table = document.getElementById("attendance-grid");
  if (!table) return;
  const weeks = data.weeks || [];
  const labels = data.date_labels || [];
  const meta = data.day_meta || [];
  const headers = data.weekday_headers || ["M", "T", "W", "T", "F"];
  const students = data.students || [];
  const dayTotals = data.day_totals || {};
  const sortLabel = sort === "za" ? "Sort: Z–A" : "Sort: A–Z";

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
  dates += `<th class="total"></th></tr>`;

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
  head += `<th class="total">Total</th></tr>`;

  let body = "";
  for (const student of students) {
    const sid = Number(student.id);
    body += `<tr><td class="name">${escapeHtml(displayName(student))}</td>`;
    weeks.forEach((week, weekIndex) => {
      week.forEach((iso, dayIndex) => {
        const edge = dayIndex === 0 && weekIndex > 0 ? " week-start" : "";
        const cell = meta[weekIndex]?.[dayIndex];
        if (!iso || !cell) {
          body += `<td class="att-day empty${edge}"></td>`;
          return;
        }
        if (cell.school_day === false) {
          body += `<td class="att-day closed${edge}" title="${escapeHtml(cell.reason || "No school")}"></td>`;
          return;
        }
        const marked = data.cells[`${sid}:${iso}`];
        let kind = "unset";
        let text = "";
        if (marked === "L") {
          kind = "late";
          text = "L";
        } else if (marked === true) {
          kind = "present";
          text = "P";
        } else if (marked === false) {
          kind = "absent";
          text = "A";
        }
        body += `<td class="att-day ${kind}${edge}">${text}</td>`;
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
  document.getElementById("att-clear-hint")?.toggleAttribute("hidden", !clearMode);
  const clearBtn = document.getElementById("att-clear");
  if (clearBtn) {
    clearBtn.classList.toggle("on", clearMode);
  }
  const sortBtn = document.getElementById("att-sort");
  if (sortBtn) sortBtn.textContent = sortLabel;
}

/**
 * Toggle clear mode or clear one day column when a date header is clicked.
 * @param {MouseEvent} event
 */
async function onGridClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const dateCell = target.closest("[data-clear-date]");
  if (!dateCell || !clearMode) return;
  const iso = dateCell.getAttribute("data-clear-date");
  if (!iso) return;
  if (!window.confirm(`Clear attendance for ${iso}?`)) return;
  try {
    hideError("#att-error");
    latestGrid = await api(`/api/classes/${classId}/attendance-day/clear`, {
      method: "POST",
      body: JSON.stringify({ date: iso, sort }),
    });
    clearMode = false;
    paint(latestGrid);
  } catch (err) {
    showError("#att-error", err);
  }
}

document.getElementById("att-sort")?.addEventListener("click", (event) => {
  event.preventDefault();
  sort = sort === "az" ? "za" : "az";
  localStorage.setItem(sortKey, sort);
  refresh().catch((err) => showError("#att-error", err));
});

document.getElementById("att-clear")?.addEventListener("click", (event) => {
  event.preventDefault();
  clearMode = !clearMode;
  if (latestGrid) paint(latestGrid);
});

document.getElementById("attendance-grid")?.addEventListener("click", (event) => {
  onGridClick(event).catch((err) => showError("#att-error", err));
});

window.addEventListener("lloves-attendance-refresh", () => {
  refreshAttendanceGrid().catch((err) => showError("#att-error", err));
});

refreshAttendanceGrid().catch((err) => showError("#att-error", err));
