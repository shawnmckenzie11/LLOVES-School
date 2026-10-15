/**
 * Lesson Slides table (MCK-229): one row per live class, latest real run.
 */

const root = document.getElementById("lesson-slides-root");
const classId = root?.dataset.classId;
let tableRows = [];
let activeFilter = "all";

/**
 * Format an ISO timestamp for teachers (local date + time).
 * @param {string} iso
 * @returns {string}
 */
/**
 * Parse stored lesson run timestamps (naive UTC ISO) for display.
 * @param {string} iso
 * @returns {Date|null}
 */
function parseStoredUtcIso(iso) {
  const text = String(iso || "").trim();
  if (!text) return null;
  let normalized = text;
  if (normalized.endsWith("Z")) {
    normalized = text;
  } else if (!/[+-]\d{2}:\d{2}$/.test(normalized)) {
    normalized = `${normalized}Z`;
  }
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Show last-run time in America/Toronto (storage stays naive UTC).
 * @param {string} iso
 * @returns {string}
 */
function formatRunTime(iso) {
  const date = parseStoredUtcIso(iso);
  if (!date) {
    const text = String(iso || "").trim();
    return text || "—";
  }
  return date.toLocaleString("en-CA", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Toronto",
  });
}

/**
 * Summary strip text for expanded questions.
 * @param {Record<string, unknown>} summary
 * @returns {string}
 */
function summaryStrip(summary) {
  const modes = summary?.by_mode || {};
  const types = summary?.by_type || {};
  const typeBits = Object.entries(types)
    .map(([label, count]) => `${count} ${label}`)
    .join(" · ");
  return [
    `${modes.individual || 0} individual`,
    `${modes.group || 0} group`,
    typeBits,
    `total weight ${summary?.total_weight ?? 0}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Filter question rows client-side.
 * @param {Array<Record<string, unknown>>} questions
 * @returns {Array<Record<string, unknown>>}
 */
function filteredQuestions(questions) {
  const list = Array.isArray(questions) ? questions : [];
  if (activeFilter === "individual") {
    return list.filter((q) => String(q.mode || "").toLowerCase() === "individual");
  }
  if (activeFilter === "group") {
    return list.filter((q) => String(q.mode || "").toLowerCase() === "group");
  }
  if (activeFilter === "weighted") {
    return list.filter((q) => Number(q.weight || 0) > 0);
  }
  return list;
}

/**
 * Build the questions detail block for one row.
 * @param {Record<string, unknown>} row
 * @returns {HTMLTableRowElement}
 */
function questionsDetailRow(row) {
  const tr = document.createElement("tr");
  tr.className = "ls-questions-row";
  const td = document.createElement("td");
  td.colSpan = 6;
  const questions = filteredQuestions(row.questions);
  const strip = document.createElement("p");
  strip.className = "ls-summary-strip hint compact";
  strip.textContent = summaryStrip(row.summary);
  td.appendChild(strip);
  if (!questions.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = row.has_run
      ? "No questions match this filter."
      : "Run this class to capture the live deck.";
    td.appendChild(empty);
    tr.appendChild(td);
    return tr;
  }
  const table = document.createElement("table");
  table.className = "ls-questions-sheet sheet";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Page</th>
        <th>Stem</th>
        <th>Type</th>
        <th>Mode</th>
        <th>Weight</th>
        <th>Catch-up</th>
        <th>Lint</th>
        <th>Live done</th>
        <th>Catch-up done</th>
      </tr>
    </thead>
  `;
  const body = document.createElement("tbody");
  for (const q of questions) {
    const qtr = document.createElement("tr");
    const lint = q.lint_ok ? "OK" : (Array.isArray(q.lint_codes) ? q.lint_codes.join(", ") : "—");
    const cells = [
      q.page ?? "",
      String(q.stem || "").slice(0, 120),
      q.type_label || q.type || "",
      q.mode || "",
      q.weight ?? 0,
      q.in_catchup ? "yes" : "no",
      lint,
      q.live_completed ?? 0,
      q.catchup_completed ?? 0,
    ];
    for (const value of cells) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      qtr.appendChild(cell);
    }
    body.appendChild(qtr);
  }
  table.appendChild(body);
  td.appendChild(table);
  tr.appendChild(td);
  return tr;
}

/**
 * Paint the main table from cached payload rows.
 * @returns {void}
 */
function paintTable() {
  const body = document.getElementById("ls-run-table-body");
  if (!body) return;
  body.replaceChildren();
  if (!tableRows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.textContent = "No live classes found for this course.";
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const row of tableRows) {
    const tr = document.createElement("tr");
    tr.className = "ls-run-row";
    const label = document.createElement("td");
    const name = document.createElement("span");
    name.textContent = row.not_in_template
      ? String(row.line_label || row.label || `${row.module}${row.slot}`)
      : String(row.label || `${row.module}${row.slot}`);
    label.appendChild(name);
    if (row.not_in_template) {
      const tag = document.createElement("span");
      tag.className = "ls-not-in-template";
      tag.textContent = String(row.status_label || "Not in template");
      label.appendChild(tag);
    }
    tr.appendChild(label);
    const ran = document.createElement("td");
    ran.textContent = row.has_run ? formatRunTime(row.last_run_at) : "Not run yet";
    tr.appendChild(ran);
    const code = document.createElement("td");
    code.textContent = row.has_run ? String(row.join_code || "—") : "—";
    tr.appendChild(code);
    const summary = document.createElement("td");
    const details = document.createElement("details");
    const sum = document.createElement("summary");
    sum.textContent = String(row.question_summary || "—");
    details.appendChild(sum);
    summary.appendChild(details);
    tr.appendChild(summary);
    const deck = document.createElement("td");
    if (row.deck_url) {
      const link = document.createElement("a");
      link.href = String(row.deck_url);
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "btn secondary";
      link.textContent = "Deck";
      deck.appendChild(link);
    } else {
      deck.textContent = "—";
    }
    tr.appendChild(deck);
    const catchup = document.createElement("td");
    if (row.catchup_enabled && row.catchup_url) {
      const link = document.createElement("a");
      link.href = String(row.catchup_url);
      link.className = "btn secondary";
      link.textContent = "Catch-up";
      catchup.appendChild(link);
    } else {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn secondary";
      btn.disabled = true;
      btn.textContent = "Catch-up";
      catchup.appendChild(btn);
    }
    tr.appendChild(catchup);
    body.appendChild(tr);
    details.addEventListener("toggle", () => {
      const next = tr.nextElementSibling;
      if (details.open) {
        if (!next || !next.classList.contains("ls-questions-row")) {
          body.insertBefore(questionsDetailRow(row), tr.nextSibling);
        } else {
          next.replaceWith(questionsDetailRow(row));
        }
      } else if (next?.classList.contains("ls-questions-row")) {
        next.remove();
      }
    });
  }
}

/**
 * Load table payload from the API.
 * @returns {Promise<void>}
 */
async function loadTable() {
  const status = document.getElementById("ls-table-status");
  if (!classId) return;
  try {
    const res = await fetch(`/api/classes/${classId}/lesson-slides/table`, {
      credentials: "same-origin",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      tableRows = [];
      if (status) {
        status.hidden = false;
        status.textContent = String(data.error || "Could not load Lesson Slides.");
      }
      paintTable();
      return;
    }
    tableRows = Array.isArray(data.rows) ? data.rows : [];
    if (status) status.hidden = true;
    paintTable();
  } catch {
    tableRows = [];
    if (status) {
      status.hidden = false;
      status.textContent = "Could not load Lesson Slides.";
    }
    paintTable();
  }
}

/**
 * Wire filter chips and initial load.
 * @returns {void}
 */
function bindFilters() {
  for (const button of document.querySelectorAll("[data-ls-filter]")) {
    button.addEventListener("click", () => {
      activeFilter = String(button.getAttribute("data-ls-filter") || "all");
      for (const peer of document.querySelectorAll(".ls-filter")) {
        peer.classList.toggle("on", peer === button);
      }
      paintTable();
    });
  }
}

if (root && classId) {
  bindFilters();
  loadTable().catch(() => {});
}
