/**
 * Inline Take Attendance card on the A&P Attendance tab (manual name-click marking).
 */
import { api, escapeHtml, hideError, showError, sortStudents } from "/static/common.js";
import {
  gridSelectedIso,
  renderSemesterDayGrid,
  resolveLogDate,
  suggestedLogDay,
  syncOverlayPickers,
} from "/static/ap_calendar.js";
import { refreshAttendanceGrid } from "/static/staff_attendance.js";

const root = document.getElementById("attendance-root");
const classId = Number(root?.dataset.classId || 0);
const sortKey = `lloves-sort-${classId}`;
const nameSort = localStorage.getItem(sortKey) === "za" ? "za" : "az";

/** @type {any|null} */
let logContext = null;
/** @type {any|null} */
let overlayState = null;
let meetingIso = "";
let pickerVisible = false;

const $ = (id) => document.getElementById(id);

/**
 * Whether the inline take card is visible.
 * @returns {boolean}
 */
function cardIsOpen() {
  const card = $("att-take-card");
  return Boolean(card && !card.hidden);
}

/**
 * Show or hide the take card vs semester grid table.
 * @param {boolean} open
 */
function setCardOpen(open) {
  const card = $("att-take-card");
  const wrap = document.querySelector(".attendance-wrap");
  const toolbar = document.querySelector("#attendance-root .ap-toolbar");
  if (card) card.hidden = !open;
  if (wrap) wrap.hidden = open;
  if (toolbar) toolbar.hidden = open;
}

/**
 * Load log context for this class.
 * @returns {Promise<any>}
 */
async function loadContext() {
  logContext = await api(`/api/classes/${classId}/log-context`);
  return logContext;
}

/**
 * Fetch whether attendance is already logged for an ISO date.
 * @param {string} iso
 * @returns {Promise<boolean>}
 */
async function isDateLogged(iso) {
  if (!iso) return false;
  const day = await api(`/api/classes/${classId}/attendance-day?date=${encodeURIComponent(iso)}`);
  return Boolean(day.logged);
}

/**
 * Present student ids from clicked rows.
 * @returns {number[]}
 */
function selectedPresent() {
  return [...document.querySelectorAll("#att-take-list .ap-att-row.is-present")].map((el) =>
    Number(el.dataset.studentId)
  );
}

/**
 * Update heading count for present students.
 */
function updateAttCount() {
  const el = $("att-take-count");
  if (el) el.textContent = `Attendance: ${selectedPresent().length}`;
}

/**
 * Paint clickable attendance rows.
 */
function renderTakeList() {
  const list = $("att-take-list");
  if (!list) return;
  const checked = new Set(
    (overlayState?.present_ids && overlayState.present_ids.length
      ? overlayState.present_ids
      : overlayState?.default_present_ids) || []
  );
  list.innerHTML = "";
  for (const student of sortStudents(overlayState?.students || [], nameSort)) {
    const present = checked.has(student.id);
    const row = document.createElement("button");
    row.type = "button";
    row.className = `ap-att-row${present ? " is-present" : ""}`;
    row.dataset.studentId = String(student.id);
    row.setAttribute("aria-pressed", present ? "true" : "false");
    row.innerHTML = `<span class="ap-att-check" aria-hidden="true">${present ? "✓" : ""}</span><span class="ap-att-name">${escapeHtml(
      String(student.codename || student.first_name || "").trim()
    )}</span>`;
    list.appendChild(row);
  }
  updateAttCount();
}

/**
 * Show or hide the semester day-grid picker.
 * @param {boolean} show
 * @param {string} [selected]
 */
function showDayPicker(show, selected) {
  pickerVisible = show;
  const box = $("att-day-grid");
  const pickerWrap = $("att-take-picker");
  if (pickerWrap) pickerWrap.hidden = !show;
  if (!show || !box) return;
  meetingIso = renderSemesterDayGrid(box, logContext, {
    selected: selected || meetingIso || suggestedLogDay(logContext),
    minIso: String(logContext?.today || ""),
    onSelect: (iso) => {
      meetingIso = iso;
      paintDateLabel();
    },
  });
  paintDateLabel();
}

/**
 * Update the selected date label above the roster.
 */
function paintDateLabel() {
  const el = $("att-take-date-label");
  if (el) el.textContent = meetingIso ? `Class date: ${meetingIso}` : "";
}

/**
 * Begin game state for the chosen meeting date and render roster.
 * @param {string} iso
 */
async function beginForDate(iso) {
  meetingIso = iso;
  syncOverlayPickers(logContext, iso);
  const hidden = $("ap-meeting-date-inline");
  if (hidden) hidden.value = iso;
  overlayState = await api(`/api/classes/${classId}/begin`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
  if (overlayState.game?.status === "live") {
    throw new Error(
      "End the open participation session before taking attendance for another day."
    );
  }
  await api(`/api/classes/${classId}/game/meeting`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
  renderTakeList();
}

/**
 * Resolve date, optionally show picker, then open the card with roster.
 */
export async function openTakeAttendanceCard() {
  hideError("#att-error");
  hideError("#att-take-error");
  try {
    await loadContext();
    const today = String(logContext.today || "").trim();
    const todayLogged = await isDateLogged(today);
    const decision = resolveLogDate(logContext, { todayLogged }, { flow: "take" });

    setCardOpen(true);
    $("att-take-done")?.removeAttribute("disabled");

    if (decision.mode === "auto") {
      showDayPicker(false);
      await beginForDate(decision.iso);
      return;
    }

    meetingIso = decision.iso;
    showDayPicker(true, decision.iso);
    paintDateLabel();

    const applyBtn = $("att-take-apply-date");
    if (applyBtn) {
      applyBtn.onclick = async () => {
        try {
          hideError("#att-take-error");
          const iso = gridSelectedIso($("att-day-grid")) || meetingIso;
          if (!iso) throw new Error("Pick a valid school day.");
          await beginForDate(iso);
          showDayPicker(false);
        } catch (err) {
          showError("#att-take-error", err);
        }
      };
    }
  } catch (err) {
    setCardOpen(false);
    showError("#att-error", err);
  }
}

/**
 * Close the card without saving grid changes.
 */
async function cancelTake() {
  try {
    await api(`/api/classes/${classId}/game/cancel`, { method: "POST", body: "{}" });
  } catch (_) {
    /* no open game is fine */
  }
  overlayState = null;
  meetingIso = "";
  pickerVisible = false;
  setCardOpen(false);
}

$("att-take-list")?.addEventListener("click", (event) => {
  const row = event.target.closest(".ap-att-row");
  if (!(row instanceof HTMLElement)) return;
  const on = !row.classList.contains("is-present");
  row.classList.toggle("is-present", on);
  row.setAttribute("aria-pressed", on ? "true" : "false");
  const check = row.querySelector(".ap-att-check");
  if (check) check.textContent = on ? "✓" : "";
  updateAttCount();
});

$("att-take-all")?.addEventListener("click", () => {
  for (const row of document.querySelectorAll("#att-take-list .ap-att-row")) {
    row.classList.add("is-present");
    row.setAttribute("aria-pressed", "true");
    const check = row.querySelector(".ap-att-check");
    if (check) check.textContent = "✓";
  }
  updateAttCount();
});

$("att-take-none")?.addEventListener("click", () => {
  for (const row of document.querySelectorAll("#att-take-list .ap-att-row")) {
    row.classList.remove("is-present");
    row.setAttribute("aria-pressed", "false");
    const check = row.querySelector(".ap-att-check");
    if (check) check.textContent = "";
  }
  updateAttCount();
});

$("att-take-cancel")?.addEventListener("click", () => {
  cancelTake().catch((err) => showError("#att-take-error", err));
});

$("att-take-done")?.addEventListener("click", async () => {
  try {
    hideError("#att-take-error");
    if (pickerVisible && !overlayState) {
      showError("#att-take-error", new Error("Choose a class date and continue."));
      return;
    }
    const iso = meetingIso || gridSelectedIso($("att-day-grid"));
    if (!iso) throw new Error("Choose a valid class date.");
    await api(`/api/classes/${classId}/game/meeting`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: iso }),
    });
    await api(`/api/classes/${classId}/game/finalize-attendance`, {
      method: "POST",
      body: JSON.stringify({ present_ids: selectedPresent(), meeting_date: iso }),
    });
    overlayState = null;
    meetingIso = "";
    pickerVisible = false;
    setCardOpen(false);
    await refreshAttendanceGrid();
    window.dispatchEvent(new CustomEvent("lloves-attendance-refresh"));
  } catch (err) {
    showError("#att-take-error", err);
  }
});

const takeLink = document.getElementById("take-attendance");
takeLink?.addEventListener("click", (event) => {
  event.preventDefault();
  openTakeAttendanceCard().catch((err) => showError("#att-error", err));
});

if (new URLSearchParams(window.location.search).get("take") === "1") {
  openTakeAttendanceCard().catch((err) => showError("#att-error", err));
}
