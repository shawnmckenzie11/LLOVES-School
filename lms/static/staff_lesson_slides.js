/**
 * Lesson Slides table (MCK-229): one row per live class, latest real run.
 * Test runs are extra rows with a TEST tag (MCK-269).
 */
import {
  deleteTestClass,
  DELETE_TEST_CLASS_ERROR,
} from "/static/staff_test_class.js";

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
 * Close any open Lesson Slides row menus except one optional wrapper.
 * @param {Element|null} exceptWrap
 * @returns {void}
 */
function closeLessonSlideRowMenus(exceptWrap = null) {
  for (const menu of document.querySelectorAll(".ls-row-menu")) {
    const wrap = menu.closest(".ls-row-menu-wrap");
    if (wrap && wrap !== exceptWrap) {
      menu.hidden = true;
      const btn = wrap.querySelector(".ls-row-menu-btn");
      if (btn instanceof HTMLButtonElement) btn.setAttribute("aria-expanded", "false");
    }
  }
}

/**
 * Wire the ⋯ overflow menu for one test row.
 * @param {HTMLElement} wrap
 * @param {number} sessionId
 * @returns {void}
 */
function bindTestRowMenu(wrap, sessionId) {
  const btn = wrap.querySelector(".ls-row-menu-btn");
  const menu = wrap.querySelector(".ls-row-menu");
  const deleteBtn = wrap.querySelector(".ls-row-menu-delete");
  if (!(btn instanceof HTMLButtonElement) || !(menu instanceof HTMLElement)) return;
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = menu.hidden;
    closeLessonSlideRowMenus(wrap);
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  deleteBtn?.addEventListener("click", () => {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    deleteTestClass(Number(classId), Number(sessionId))
      .then((gone) => {
        if (gone) window.location.reload();
      })
      .catch(() => {
        const status = document.getElementById("ls-table-status");
        if (status) {
          status.hidden = false;
          status.textContent = DELETE_TEST_CLASS_ERROR;
        }
      });
  });
}

document.addEventListener("click", () => closeLessonSlideRowMenus());

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
    tr.className = row.is_test ? "ls-run-row is-test-run" : "ls-run-row";
    const label = document.createElement("td");
    label.className = "ls-class-cell";
    const labelWrap = document.createElement("div");
    labelWrap.className = "ls-row-label-wrap";
    const title = document.createElement("div");
    title.className = "ls-row-title";
    const name = document.createElement("span");
    name.className = "ls-row-name";
    if (row.is_test) {
      name.textContent = String(row.line_label || row.label || "TEST");
    } else {
      name.textContent = row.not_in_template
        ? String(row.line_label || row.label || `${row.module}${row.slot}`)
        : String(row.label || `${row.module}${row.slot}`);
    }
    title.appendChild(name);
    if (row.is_test) {
      const tag = document.createElement("span");
      tag.className = "badge-test-run";
      tag.textContent = "TEST";
      title.appendChild(tag);
    }
    labelWrap.appendChild(title);
    if (row.is_test && row.live_session_id) {
      const menuWrap = document.createElement("div");
      menuWrap.className = "ls-row-menu-wrap";
      const menuBtn = document.createElement("button");
      menuBtn.type = "button";
      menuBtn.className = "ls-row-menu-btn";
      menuBtn.setAttribute("aria-label", "Test class actions");
      menuBtn.setAttribute("aria-haspopup", "menu");
      menuBtn.setAttribute("aria-expanded", "false");
      menuBtn.textContent = "⋯";
      const menu = document.createElement("div");
      menu.className = "ls-row-menu";
      menu.setAttribute("role", "menu");
      menu.hidden = true;
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "ls-row-menu-delete";
      deleteBtn.setAttribute("role", "menuitem");
      deleteBtn.textContent = "Delete test class";
      menu.appendChild(deleteBtn);
      menuWrap.appendChild(menuBtn);
      menuWrap.appendChild(menu);
      labelWrap.appendChild(menuWrap);
      bindTestRowMenu(menuWrap, Number(row.live_session_id));
    }
    label.appendChild(labelWrap);
    if (!row.is_test && row.not_in_template) {
      const tag = document.createElement("span");
      tag.className = "ls-not-in-template";
      tag.textContent = String(row.status_label || "Not in template");
      title.appendChild(tag);
    }
    tr.appendChild(label);
    const ran = document.createElement("td");
    ran.textContent = row.has_run ? formatRunTime(row.last_run_at) : "Not run yet";
    tr.appendChild(ran);
    const code = document.createElement("td");
    code.className = "ls-join-code";
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
    tr.appendChild(catchupCell(row));
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
 * Catch-up status and Close or Reopen for one live class.
 * A Quit row and a class that never ended show "none" and no button.
 * The request names the lesson slot. It does not send a join code or token.
 * @param {Record<string, unknown>} row
 * @returns {HTMLTableCellElement}
 */
function catchupCell(row) {
  const td = document.createElement("td");
  td.className = "ls-catchup-cell";
  const access = String(row.catchup_access || "none");
  td.dataset.catchupAccess = access;
  const label = document.createElement("span");
  label.className = "ls-catchup-label";
  label.textContent = String(row.catchup_label || "none");
  td.appendChild(label);
  if (access !== "open" && access !== "closed") {
    return td;
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn secondary ls-catchup-toggle";
  const opening = access !== "open";
  button.textContent = opening ? "Reopen" : "Close";
  const lesson = String(row.label || `${row.module || ""}${row.slot || ""}`);
  button.setAttribute(
    "aria-label",
    opening ? `Reopen catch-up for ${lesson}` : `Close catch-up for ${lesson}`,
  );
  button.addEventListener("click", () => {
    toggleCatchup(row, button).catch(() => {});
  });
  td.appendChild(button);
  return td;
}

/**
 * Ask the server to close or reopen one row, then repaint from its reply.
 * @param {Record<string, unknown>} row
 * @param {HTMLButtonElement} button
 * @returns {Promise<void>}
 */
async function toggleCatchup(row, button) {
  const status = document.getElementById("ls-table-status");
  button.disabled = true;
  try {
    const res = await fetch(`/api/classes/${classId}/lesson-slides/catchup-access`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        module: row.module,
        slot: row.slot,
        is_test: Boolean(row.is_test),
        open: row.catchup_access !== "open",
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.catchup_access) {
      if (status) {
        status.hidden = false;
        status.textContent = res.status === 403
          ? "You can't change catch-up for this class."
          : "Could not update catch-up.";
      }
      return;
    }
    row.catchup_access = data.catchup_access;
    row.catchup_label = data.catchup_label;
    row.catchup_can_toggle = true;
    if (status) status.hidden = true;
    paintTable();
  } catch {
    if (status) {
      status.hidden = false;
      status.textContent = "Could not update catch-up.";
    }
  } finally {
    button.disabled = false;
  }
}

/**
 * Paint the auto-close day count from the table payload.
 * @param {number} days
 * @returns {void}
 */
function paintAutoCloseDays(days) {
  const input = document.getElementById("ls-catchup-days");
  if (!(input instanceof HTMLInputElement)) return;
  const value = Number(days);
  if (Number.isInteger(value) && value >= 1 && value <= 90) {
    input.value = String(value);
  }
}

/**
 * Save the auto-close setting. Existing classes keep their window.
 * @param {HTMLInputElement} input
 * @returns {Promise<void>}
 */
async function saveAutoCloseDays(input) {
  const status = document.getElementById("ls-catchup-days-status");
  const days = Number(input.value);
  if (!Number.isInteger(days) || days < 1 || days > 90) {
    if (status) {
      status.hidden = false;
      status.textContent = "Enter a number from 1 to 90.";
    }
    return;
  }
  try {
    const res = await fetch(`/api/classes/${classId}/lesson-slides/catchup-auto-close`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (status) {
        status.hidden = false;
        status.textContent = String(data.error || "Could not save.");
      }
      return;
    }
    input.value = String(data.days);
    if (status) {
      status.hidden = false;
      status.textContent = "Saved. Classes that already ended keep their window.";
    }
  } catch {
    if (status) {
      status.hidden = false;
      status.textContent = "Could not save.";
    }
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
    paintAutoCloseDays(data.catchup_auto_close_days);
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

/**
 * Save the day count when the teacher leaves the field or presses Enter.
 * @returns {void}
 */
function bindAutoCloseDays() {
  const input = document.getElementById("ls-catchup-days");
  const form = document.getElementById("ls-catchup-setting");
  if (!(input instanceof HTMLInputElement)) return;
  input.addEventListener("change", () => {
    saveAutoCloseDays(input).catch(() => {});
  });
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    saveAutoCloseDays(input).catch(() => {});
  });
}

if (root && classId) {
  bindFilters();
  bindAutoCloseDays();
  loadTable().catch(() => {});
}
