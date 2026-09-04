/**
 * Attendance & Participation overlays: mark attendance, date validation,
 * individual vs team participation scoring.
 */
import {
  api,
  displayName as displayNameFn,
  escapeHtml,
  formatCountdown,
  formatPoints,
  hideError,
  lockRoundDeadline,
  openScoreboardOverlay,
  remainingUntilMs,
  reserveScoreboardOverlay,
  showError,
  sortStudents,
} from "/static/common.js";
import { bindSchoolDayPicker, defaultSchoolDay, pickerValue, syncOverlayPickers } from "/static/ap_calendar.js";
import { nameWithMood } from "/static/mood_faces.js";

const root = document.getElementById("ap-root");
const classId = Number(root?.dataset.classId || 0);
const scoreboardKey = `lloves-scoreboard-${classId}`;
const rankKey = `lloves-rank-${classId}`;
const nameSort = localStorage.getItem(`lloves-sort-${classId}`) === "za" ? "za" : "az";
const STUDENT_AMOUNTS = [1, 5, 10, -1];
const TEAM_AMOUNTS = [1, 5, 10];
const TEAM_RULES = [
  { id: "each_member", label: "Each member" },
  { id: "split_members", label: "Split" },
  { id: "team_only", label: "Small Team Bonus" },
];

let overlayState = null;
let pendingAction = null;
let logContext = null;
let lastAssignMode = null;
let pendingTeam = null;
let roundEndsAtMs = 0;
let liveStamp = "";
let overlayPopout = false;
let pendingScoreboard = false;
let draftRounds = [
  { kind: "open", minutes: 20 },
  { kind: "challenge", minutes: 10 },
  { kind: "formative", minutes: 10 },
];

const ROUND_KIND_OPTIONS = [
  { kind: "open", label: "Open Question", defaultMin: 20 },
  { kind: "challenge", label: "Team Challenge", defaultMin: 10 },
  { kind: "formative", label: "Formative", defaultMin: 10 },
];

const $ = (id) => document.getElementById(id);

/**
 * Ask the attendance grid to reload (when the attendance tab is open).
 */
function notifyAttendanceRefresh() {
  window.dispatchEvent(new CustomEvent("lloves-attendance-refresh"));
}

/**
 * Open the overlay as modal or pop-out (non-modal, resizable).
 */
function openOverlayDialog() {
  const dialog = $("ap-overlay");
  if (!dialog || dialog.open) return;
  if (overlayPopout && typeof dialog.show === "function") dialog.show();
  else if (typeof dialog.showModal === "function") dialog.showModal();
}

/**
 * Show one overlay panel and open the dialog.
 * @param {string} name
 */
function showPanel(name) {
  const dialog = $("ap-overlay");
  if (!dialog) return;
  document.querySelectorAll(".ap-panel").forEach((el) => {
    el.classList.toggle("hidden", el.id !== `ap-panel-${name}`);
  });
  dialog.classList.toggle("ap-overlay-wide", name === "live");
  hideError("#ap-overlay-error");
  openOverlayDialog();
}

/**
 * Close the overlay dialog.
 */
function closeOverlay() {
  const dialog = $("ap-overlay");
  if (dialog?.open) dialog.close();
  pendingAction = null;
  pendingTeam = null;
  liveStamp = "";
}

/**
 * Discard open setup/live game and close without saving.
 */
async function cancelOverlay() {
  try {
    await api(`/api/classes/${classId}/game/cancel`, { method: "POST", body: "{}" });
  } catch (_) {
    /* still close */
  }
  closeOverlay();
}

/**
 * Local calendar day as YYYY-MM-DD.
 * @returns {string}
 */
function todayISO() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/**
 * Roster label is the Codename only.
 * @param {any} student
 * @returns {string}
 */
function displayName(student) {
  return displayNameFn(student, nameSort);
}

/**
 * Load schedule + Admin live-day gate.
 * @returns {Promise<any>}
 */
async function loadContext() {
  logContext = await api(`/api/classes/${classId}/log-context`);
  bindAllPickers();
  return logContext;
}

/**
 * Wire semester school-day pickers after log context loads.
 * @param {string} [preferred]
 * @param {{forceValue?: boolean}} [opts]
 */
function bindAllPickers(preferred, opts = {}) {
  const onInvalid = (msg) => showError("#ap-overlay-error", new Error(msg));
  for (const id of ["ap-valid-date", "ap-meeting-date", "ap-gamify-date"]) {
    const el = $(id);
    const keep = pickerValue(el, logContext);
    const iso = opts.forceValue
      ? preferred || defaultSchoolDay(logContext)
      : keep || preferred || defaultSchoolDay(logContext);
    bindSchoolDayPicker(el, logContext, {
      onInvalid,
      value: iso,
      forceValue: Boolean(opts.forceValue),
    });
  }
}

/**
 * Whether the chosen ISO date needs the live-day picker.
 * @param {string} iso
 * @returns {boolean}
 */
function dateIsAllowed(iso) {
  if (!logContext) return true;
  const allowed = new Set((logContext.valid_dates || []).map((d) => d.iso));
  return allowed.has(iso);
}

/**
 * Update validate hint from log context.
 */
function fillValidateHint() {
  const hint = $("ap-validate-hint");
  if (!hint) return;
  const sched = logContext?.days || "this course's live-class days";
  hint.textContent = logContext?.only_live_class_days
    ? `This is not a live class day (${sched}). Pick a valid date to continue.`
    : "This date is not a secondary school day. Pick a date in the semester.";
}

/**
 * Prompt for a valid date, then run ``pendingAction``.
 * @param {() => Promise<void>} after
 */
function promptValidDate(after) {
  pendingAction = after;
  fillValidateHint();
  const picker = $("ap-valid-date");
  if (picker && overlayState?.session?.meeting_date) {
    picker.value = overlayState.session.meeting_date;
  }
  showPanel("validate");
}

/**
 * Ensure meeting_date is allowed, prompting if needed.
 * @param {string} iso
 * @param {() => Promise<void>} proceed
 */
async function withValidatedDate(iso, proceed) {
  pendingAction = null;
  await loadContext();
  const chosen = iso || sessionIso();
  if (dateIsAllowed(chosen)) {
    await proceed(chosen);
    return;
  }
  pendingAction = () => proceed(pickerValue($("ap-valid-date"), logContext) || chosen);
  fillValidateHint();
  const picker = $("ap-valid-date");
  if (picker && chosen) picker.value = chosen;
  showPanel("validate");
}

/**
 * Apply meeting date from a picker to the open game.
 * @param {HTMLInputElement|null} input
 */
async function applyMeetingFromPicker(input) {
  const iso = pickerValue(input, logContext);
  if (!iso) return;
  overlayState = await api(`/api/classes/${classId}/game/meeting`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
}

/**
 * Present ids for the session date: attendance grid first, then overlay UI.
 * @param {string} iso
 * @returns {Promise<number[]>}
 */
async function resolvePresentIds(iso) {
  const day = await api(
    `/api/classes/${classId}/attendance-day?date=${encodeURIComponent(iso)}`
  );
  if (day.logged && day.present_ids?.length) {
    return day.present_ids.map(Number);
  }
  const fromUi = selectedPresent();
  if (fromUi.length) return fromUi;
  if (overlayState?.present_ids?.length) {
    return overlayState.present_ids.map(Number);
  }
  throw new Error(
    "No students marked present for this date. Take attendance first, or mark at least one student present."
  );
}

/**
 * Checked present student ids in the Mark Attendance overlay.
 * @returns {number[]}
 */
function selectedPresent() {
  return [...document.querySelectorAll("#ap-att-list input:checked")].map((el) =>
    Number(el.value)
  );
}

/**
 * Draw attendance checkboxes from current game state.
 */
function renderAttendanceList() {
  const checked = new Set(
    (overlayState?.present_ids && overlayState.present_ids.length
      ? overlayState.present_ids
      : overlayState?.default_present_ids) || []
  );
  const list = $("ap-att-list");
  if (!list) return;
  list.innerHTML = "";
  for (const student of sortStudents(overlayState.students || [], nameSort)) {
    const id = `ap-att-${student.id}`;
    const row = document.createElement("label");
    row.innerHTML = `<input type="checkbox" id="${id}" value="${student.id}"> ${nameWithMood(displayName(student), student.mood)}`;
    list.appendChild(row);
    row.querySelector("input").checked = checked.has(student.id);
  }
  updateAttCount();
}

/**
 * Live present count in the overlay heading.
 */
function updateAttCount() {
  const el = $("ap-att-count");
  if (el) el.textContent = `Attendance: ${selectedPresent().length}`;
}

/**
 * Route to the correct live scoring panel after begin/rename.
 * @param {any} state
 */
function openLiveScoring(state) {
  overlayState = state;
  const teams = state.teams || [];
  const isIndividual = teams.length === 1 && teams[0]?.name === "Class";
  if (isIndividual) {
    renderScoreList();
    showPanel("score");
    return;
  }
  renderLiveTeams(state);
  showPanel("live");
}

/**
 * Persist the picker date on the open setup game before saving attendance.
 * @param {string} iso
 */
async function ensureMeetingDate(iso) {
  if (!iso) return;
  overlayState = await api(`/api/classes/${classId}/game/meeting`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
}

/**
 * Begin a session for today and open Mark Attendance.
 */
export async function openTakeAttendance() {
  hideError("#att-error");
  hideError("#ap-overlay-error");
  try {
    await loadContext();
    const meeting = defaultSchoolDay(logContext);
    syncOverlayPickers(logContext, meeting);
    overlayState = await api(`/api/classes/${classId}/begin`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: meeting }),
    });
    if (overlayState.game?.status === "live") {
      showError(
        "#ap-overlay-error",
        new Error("End the open participation session before taking attendance for another day.")
      );
      return;
    }
    await ensureMeetingDate(meeting);
    syncOverlayPickers(logContext, meeting);
    renderAttendanceList();
    showPanel("att");
  } catch (err) {
    showError("#att-error", err);
    showError("#ap-overlay-error", err);
  }
}

/**
 * Meeting date from the visible attendance/gamify picker (not a stale session).
 * @returns {string}
 */
function sessionIso() {
  const attPanel = $("ap-panel-att");
  const gamifyPanel = $("ap-panel-gamify");
  const attVisible = attPanel && !attPanel.classList.contains("hidden");
  const gamifyVisible = gamifyPanel && !gamifyPanel.classList.contains("hidden");
  if (attVisible) {
    const fromAtt = pickerValue($("ap-meeting-date"), logContext);
    if (fromAtt) return fromAtt;
  }
  if (gamifyVisible) {
    const fromGamify = pickerValue($("ap-gamify-date"), logContext);
    if (fromGamify) return fromGamify;
  }
  const fromAtt = pickerValue($("ap-meeting-date"), logContext);
  if (fromAtt) return fromAtt;
  const fromGamify = pickerValue($("ap-gamify-date"), logContext);
  if (fromGamify) return fromGamify;
  return overlayState?.session?.meeting_date || defaultSchoolDay(logContext) || todayISO();
}

/**
 * Finalize attendance only (Done).
 * @param {string} [iso]
 */
async function submitDone(iso) {
  const sessionMeeting = overlayState?.session?.meeting_date || null;
  // Prefer explicit iso (from Done before loadContext), then live session (picker may be rebound).
  const meeting =
    iso ||
    sessionMeeting ||
    pickerValue($("ap-meeting-date"), logContext) ||
    sessionIso();
  syncOverlayPickers(logContext, meeting);
  await ensureMeetingDate(meeting);
  if (overlayState?.session?.meeting_date && overlayState.session.meeting_date !== meeting) {
    throw new Error(
      `Could not set meeting date to ${meeting} (session is ${overlayState.session.meeting_date}).`
    );
  }
  await api(`/api/classes/${classId}/game/finalize-attendance`, {
    method: "POST",
    body: JSON.stringify({ present_ids: selectedPresent(), meeting_date: meeting }),
  });
  closeOverlay();
  await loadContext();
  notifyAttendanceRefresh();
}

/**
 * Save attendance and open the tracking choice.
 * @param {string} [iso]
 */
async function submitLogParticipation(iso) {
  const sessionMeeting = overlayState?.session?.meeting_date || null;
  const meeting =
    iso ||
    sessionMeeting ||
    pickerValue($("ap-meeting-date"), logContext) ||
    sessionIso();
  syncOverlayPickers(logContext, meeting);
  await ensureMeetingDate(meeting);
  overlayState = await api(`/api/classes/${classId}/game/attendance`, {
    method: "POST",
    body: JSON.stringify({ present_ids: selectedPresent(), meeting_date: meeting }),
  });
  syncOverlayPickers(logContext, meeting);
  notifyAttendanceRefresh();
  showPanel("gamify");
}

$("ap-att-all")?.addEventListener("click", () => {
  document.querySelectorAll("#ap-att-list input").forEach((el) => {
    el.checked = true;
  });
  updateAttCount();
});
$("ap-att-none")?.addEventListener("click", () => {
  document.querySelectorAll("#ap-att-list input").forEach((el) => {
    el.checked = false;
  });
  updateAttCount();
});
$("ap-att-list")?.addEventListener("change", updateAttCount);

$("ap-meeting-date")?.addEventListener("change", async () => {
  try {
    hideError("#ap-overlay-error");
    const iso = pickerValue($("ap-meeting-date"), logContext);
    syncOverlayPickers(logContext, iso);
    await applyMeetingFromPicker($("ap-meeting-date"));
    renderAttendanceList();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-gamify-date")?.addEventListener("change", async () => {
  try {
    hideError("#ap-overlay-error");
    await applyMeetingFromPicker($("ap-gamify-date"));
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

for (const id of ["ap-att-cancel", "ap-validate-cancel", "ap-gamify-cancel", "ap-teams-cancel", "ap-names-cancel", "ap-rounds-cancel", "ap-score-cancel", "ap-live-cancel"]) {
  $(id)?.addEventListener("click", () => cancelOverlay());
}

$("ap-att-done")?.addEventListener("click", async () => {
  try {
    // Capture date before withValidatedDate → loadContext rebinds pickers.
    await withValidatedDate(sessionIso(), (d) => submitDone(d));
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-att-log")?.addEventListener("click", async () => {
  try {
    await withValidatedDate(sessionIso(), (d) => submitLogParticipation(d));
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-validate-apply")?.addEventListener("click", async () => {
  const iso = pickerValue($("ap-valid-date"), logContext);
  if (!iso) return;
  try {
    overlayState = await api(`/api/classes/${classId}/game/meeting`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: iso }),
    });
    const attDate = $("ap-meeting-date");
    const gamifyDate = $("ap-gamify-date");
    if (attDate) attDate.value = iso;
    if (gamifyDate) gamifyDate.value = iso;
    const fn = pendingAction;
    pendingAction = null;
    if (fn) await fn();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-gamify-yes")?.addEventListener("click", async () => {
  try {
    hideError("#ap-overlay-error");
    const meeting = sessionIso();
    await withValidatedDate(meeting, async () => {
      await applyMeetingFromPicker($("ap-gamify-date"));
      const ids = await resolvePresentIds(meeting);
      const status = overlayState?.game?.status;
      if (status === "attendance") {
        overlayState = await api(`/api/classes/${classId}/game/attendance`, {
          method: "POST",
          body: JSON.stringify({ present_ids: ids, meeting_date: meeting }),
        });
      } else {
        overlayState = await api(`/api/classes/${classId}/game`);
      }
      renderTeamsPanel();
      showPanel("teams");
    });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-gamify-no")?.addEventListener("click", async () => {
  try {
    hideError("#ap-overlay-error");
    const meeting = sessionIso();
    await withValidatedDate(meeting, async () => {
      await applyMeetingFromPicker($("ap-gamify-date"));
      const ids = await resolvePresentIds(meeting);
      overlayState = await api(`/api/classes/${classId}/game/ungamified`, {
        method: "POST",
        body: JSON.stringify({ present_ids: ids, meeting_date: meeting }),
      });
      renderScoreList();
      showPanel("score");
    });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

/**
 * Team-count bounds from present students.
 * @returns {{min:number, max:number}}
 */
function nTeamsBounds() {
  const present = (overlayState?.present_ids || []).length;
  return { min: 2, max: Math.max(2, present) };
}

/**
 * Clamp team count in the overlay stepper.
 * @param {number} value
 */
function setNTeams(value) {
  const { min, max } = nTeamsBounds();
  const n = Math.min(max, Math.max(min, Number(value) || min));
  const el = $("ap-n-teams");
  if (el) {
    el.value = String(n);
    el.max = String(max);
  }
}

/**
 * Refresh the teams overlay from game state.
 */
function renderTeamsPanel() {
  const box = $("ap-scoreboard-toggle");
  if (box) box.checked = localStorage.getItem(scoreboardKey) === "1";
  const rankBox = $("ap-rank-toggle");
  if (rankBox) {
    const fromState = overlayState && typeof overlayState.show_rank === "boolean";
    rankBox.checked = fromState
      ? Boolean(overlayState.show_rank)
      : localStorage.getItem(rankKey) === "1";
  }
  setNTeams(Number($("ap-n-teams")?.value) || 2);
}

$("ap-n-teams-down")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) - 1));
$("ap-n-teams-up")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) + 1));
$("ap-scoreboard-toggle")?.addEventListener("change", (event) => {
  const box = event.target;
  if (box instanceof HTMLInputElement) {
    localStorage.setItem(scoreboardKey, box.checked ? "1" : "0");
  }
});
$("ap-rank-toggle")?.addEventListener("change", (event) => {
  const box = event.target;
  if (!(box instanceof HTMLInputElement)) return;
  localStorage.setItem(rankKey, box.checked ? "1" : "0");
  api(`/api/classes/${classId}/show-rank`, {
    method: "POST",
    body: JSON.stringify({ enabled: box.checked }),
  }).catch((err) => showError("#ap-overlay-error", err));
});

/**
 * Assign teams in the overlay.
 * @param {"random"|"balanced"|"manual"} mode
 */
async function assign(mode) {
  lastAssignMode = mode;
  const payload = { n_teams: Number($("ap-n-teams").value), mode };
  if (mode === "manual") {
    payload.assignments = [...document.querySelectorAll("#ap-manual-list .team-step")].map((el) => ({
      student_id: Number(el.dataset.studentId),
      team_index: Number(el.dataset.teamIndex),
    }));
  }
  overlayState = await api(`/api/classes/${classId}/game/assign`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  $("ap-manual-assign")?.classList.add("hidden");
  renderNamesPanel();
  showPanel("names");
}

$("ap-assign-random")?.addEventListener("click", () => assign("random").catch((err) => showError("#ap-overlay-error", err)));
$("ap-assign-balanced")?.addEventListener("click", () => assign("balanced").catch((err) => showError("#ap-overlay-error", err)));
$("ap-assign-manual")?.addEventListener("click", () => {
  const nTeams = Number($("ap-n-teams").value);
  const present = new Set(overlayState.present_ids || []);
  const students = sortStudents(
    (overlayState.students || []).filter((s) => present.has(s.id)),
    nameSort
  );
  const list = $("ap-manual-list");
  if (!list) return;
  list.innerHTML = students
    .map((student, index) => {
      const teamIndex = index % Math.max(1, nTeams);
      return `<div class="manual-row">
        <span>${nameWithMood(displayName(student), student.mood)}</span>
        <div class="team-step" data-student-id="${student.id}" data-team-index="${teamIndex}">
          <button type="button" data-step="-1">−</button>
          <span class="team-n">Team ${teamIndex + 1}</span>
          <button type="button" data-step="1">+</button>
        </div>
      </div>`;
    })
    .join("");
  $("ap-manual-assign")?.classList.remove("hidden");
});
$("ap-manual-cancel")?.addEventListener("click", () => $("ap-manual-assign")?.classList.add("hidden"));
$("ap-manual-confirm")?.addEventListener("click", () => assign("manual").catch((err) => showError("#ap-overlay-error", err)));
$("ap-manual-list")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-step]");
  if (!btn) return;
  const row = btn.closest(".team-step");
  const nTeams = Math.max(2, Number($("ap-n-teams").value) || 2);
  const next = Math.min(nTeams - 1, Math.max(0, Number(row.dataset.teamIndex) + Number(btn.dataset.step)));
  row.dataset.teamIndex = String(next);
  row.querySelector(".team-n").textContent = `Team ${next + 1}`;
});

/**
 * Paint team rename fields.
 */
function renderNamesPanel() {
  const box = $("ap-name-list");
  if (!box) return;
  box.innerHTML = "";
  for (const team of overlayState.teams || []) {
    const wrap = document.createElement("div");
    wrap.className = "team-preview";
    wrap.innerHTML = `<label class="field">Team ${team.sort_order + 1}<input type="text" data-team-id="${team.id}" value="${escapeHtml(team.name)}"></label>`;
    box.appendChild(wrap);
  }
}

$("ap-names-back")?.addEventListener("click", () => showPanel("teams"));

$("ap-start-game")?.addEventListener("click", async () => {
  pendingScoreboard = Boolean($("ap-scoreboard-toggle")?.checked);
  localStorage.setItem(scoreboardKey, pendingScoreboard ? "1" : "0");
  const teams = [...document.querySelectorAll("#ap-name-list input")].map((el) => ({
    id: Number(el.dataset.teamId),
    name: el.value,
  }));
  try {
    overlayState = await api(`/api/classes/${classId}/game/rename`, {
      method: "POST",
      body: JSON.stringify({ teams, go_live: false }),
    });
    draftRounds = [
      { kind: "open", minutes: 20 },
      { kind: "challenge", minutes: 10 },
      { kind: "formative", minutes: 10 },
    ];
    renderRoundsPanel();
    showPanel("rounds");
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

/**
 * Build the Set up Rounds editor.
 */
function renderRoundsPanel() {
  const box = $("ap-rounds-list");
  if (!box) return;
  const used = new Set(draftRounds.map((r) => r.kind));
  box.innerHTML = draftRounds
    .map((row, index) => {
      const options = ROUND_KIND_OPTIONS.map((opt) => {
        const taken = used.has(opt.kind) && opt.kind !== row.kind;
        return `<option value="${opt.kind}" ${opt.kind === row.kind ? "selected" : ""} ${taken ? "disabled" : ""}>${escapeHtml(opt.label)}</option>`;
      }).join("");
      return `<div class="ap-round-row" data-index="${index}">
        <label class="field">Round ${index + 1}
          <select data-round-kind>${options}</select>
        </label>
        <label class="field">Length (minutes)
          <input type="number" min="1" max="180" value="${Number(row.minutes) || 10}" data-round-minutes>
        </label>
        <button type="button" class="secondary" data-round-remove ${draftRounds.length <= 1 ? "disabled" : ""}>Remove</button>
      </div>`;
    })
    .join("");
  const addBtn = $("ap-rounds-add");
  if (addBtn) addBtn.disabled = draftRounds.length >= 3 || used.size >= 3;
}

$("ap-rounds-list")?.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const row = target.closest(".ap-round-row");
  if (!row) return;
  const index = Number(row.dataset.index);
  if (!draftRounds[index]) return;
  if (target.matches("[data-round-kind]") && target instanceof HTMLSelectElement) {
    draftRounds[index].kind = target.value;
    const meta = ROUND_KIND_OPTIONS.find((o) => o.kind === target.value);
    if (meta && !row.querySelector("[data-round-minutes]")?.matches(":focus")) {
      draftRounds[index].minutes = meta.defaultMin;
    }
  }
  if (target.matches("[data-round-minutes]") && target instanceof HTMLInputElement) {
    draftRounds[index].minutes = Math.max(1, Math.min(180, Number(target.value) || 1));
  }
  renderRoundsPanel();
});

$("ap-rounds-list")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-round-remove]");
  if (!btn) return;
  const row = btn.closest(".ap-round-row");
  const index = Number(row?.dataset.index);
  if (draftRounds.length <= 1 || Number.isNaN(index)) return;
  draftRounds.splice(index, 1);
  renderRoundsPanel();
});

$("ap-rounds-add")?.addEventListener("click", () => {
  if (draftRounds.length >= 3) return;
  const used = new Set(draftRounds.map((r) => r.kind));
  const next = ROUND_KIND_OPTIONS.find((o) => !used.has(o.kind));
  if (!next) return;
  draftRounds.push({ kind: next.kind, minutes: next.defaultMin });
  renderRoundsPanel();
});

$("ap-rounds-back")?.addEventListener("click", () => showPanel("names"));

$("ap-rounds-start")?.addEventListener("click", async () => {
  const overlay = pendingScoreboard ? reserveScoreboardOverlay() : null;
  try {
    const rounds = draftRounds.map((row) => ({
      kind: row.kind,
      minutes: Number(row.minutes) || 10,
    }));
    overlayState = await api(`/api/classes/${classId}/game/start-rounds`, {
      method: "POST",
      body: JSON.stringify({ rounds }),
    });
    if (pendingScoreboard) openScoreboardOverlay(overlay);
    else overlay?.close();
    pendingScoreboard = false;
    openLiveScoring(overlayState);
  } catch (err) {
    overlay?.close();
    showError("#ap-overlay-error", err);
  }
});

/**
 * Vertical individual scoring list.
 */
function renderScoreList() {
  const box = $("ap-score-list");
  if (!box) return;
  const teams = overlayState.teams || [];
  const members = teams.flatMap((t) => t.members || []);
  const rows = sortStudents(members.length ? members : overlayState.students || [], nameSort);
  box.innerHTML = rows
    .map((row) => {
      const buttons = STUDENT_AMOUNTS.map(
        (n) =>
          `<button type="button" data-kind="student" data-id="${row.id}" data-amount="${n}">${n > 0 ? "+" : ""}${n}</button>`
      ).join("");
      return `<div class="ap-score-row">
        <span>${nameWithMood(displayName(row), row.mood)}</span>
        <span class="ap-score-pts">${escapeHtml(formatPoints(row.session_points || 0))}</span>
        <span class="pm">${buttons}</span>
      </div>`;
    })
    .join("");
}

/**
 * +/- buttons for team scoring in the live overlay.
 * @param {number} teamId
 */
function teamControls(teamId) {
  if (pendingTeam && pendingTeam.id === teamId) {
    const amount = pendingTeam.amount;
    const choices = TEAM_RULES.map(
      (rule) =>
        `<button type="button" data-kind="team" data-id="${teamId}" data-amount="${amount}" data-rule="${rule.id}">${escapeHtml(rule.label)}</button>`
    ).join("");
    return `<div class="rule-pick">
      <span>Apply +${amount} as:</span>
      ${choices}
      <button type="button" class="secondary" data-cancel-rule="1">Cancel</button>
    </div>`;
  }
  return `<div class="pm">${TEAM_AMOUNTS.map(
    (n) =>
      `<button type="button" class="team-amt" data-team-amt="1" data-id="${teamId}" data-amount="${n}">+${n}</button>`
  ).join("")}<button type="button" data-kind="team" data-id="${teamId}" data-amount="-5" data-rule="team_only">−5</button></div>`;
}

/**
 * Student +/- buttons for live team overlay.
 * @param {number} id
 */
function studentButtons(id) {
  return STUDENT_AMOUNTS.map(
    (n) =>
      `<button type="button" data-kind="student" data-id="${id}" data-amount="${n}">${n > 0 ? "+" : ""}${n}</button>`
  ).join("");
}

/**
 * Paint team live scoring inside the staff overlay.
 * @param {any} state
 */
function renderLiveTeams(state) {
  overlayState = state;
  const meta = $("ap-live-meta");
  if (meta) {
    meta.textContent = `${state.class?.course_code || ""} · ${state.session?.header_label || ""}`.trim();
  }
  const game = state.game || {};
  const n = Number(game.round) || 1;
  const label = $("ap-round-label");
  if (label) label.textContent = `ROUND ${n} · ${game.round_title || ""}`;
  roundEndsAtMs = lockRoundDeadline(roundEndsAtMs, game.round_ends_at_ms);
  paintLiveClock();
  const btn = $("ap-start-round");
  if (btn) {
    const count = Number(game.round_count) || (state.game?.rounds || []).length || 3;
    if (n < count) {
      btn.hidden = false;
      const next = n + 1;
      const nextTitle =
        (state.game?.rounds || [])[next - 1]?.title || `Round ${next}`;
      btn.textContent = `Start Round ${next} · ${nextTitle}`;
      btn.dataset.round = String(next);
    } else {
      btn.hidden = true;
      delete btn.dataset.round;
    }
  }
  const stamp = JSON.stringify({
    pending: pendingTeam,
    round: game.round,
    teams: (state.teams || []).map((t) => [t.id, t.score, t.members?.length]),
  });
  if (stamp === liveStamp) return;
  liveStamp = stamp;
  const rootEl = $("ap-live-teams");
  if (!rootEl) return;
  rootEl.innerHTML = (state.teams || [])
    .map((team) => {
      const members = sortStudents(team.members || [], nameSort);
      const playerBlocks = members
        .map(
          (s) => `<div class="ap-live-player">
            <div class="ap-live-row">
              <span class="who">${nameWithMood(displayName(s), s.mood)}</span>
              <span class="now">${escapeHtml(formatPoints(s.session_points || 0))}</span>
            </div>
            <div class="pm">${studentButtons(s.id)}</div>
          </div>`
        )
        .join("");
      return `<section class="ap-live-team" style="--team:${escapeHtml(team.color)}">
        <div class="ap-live-row team-head">
          <span class="who">${escapeHtml(team.name)}</span>
          <span class="now">${escapeHtml(formatPoints(team.score))}</span>
        </div>
        ${teamControls(team.id)}
        ${playerBlocks}
      </section>`;
    })
    .join("");
}

/**
 * Update round countdown in the live overlay.
 */
function paintLiveClock() {
  const clock = $("ap-round-clock");
  if (clock) clock.textContent = formatCountdown(remainingUntilMs(roundEndsAtMs));
}

setInterval(paintLiveClock, 1000);

$("ap-start-round")?.addEventListener("click", async () => {
  const btn = $("ap-start-round");
  const next = Number(btn?.dataset.round);
  if (!next) return;
  try {
    overlayState = await api(`/api/classes/${classId}/game/round`, {
      method: "POST",
      body: JSON.stringify({ round: next }),
    });
    liveStamp = "";
    renderLiveTeams(overlayState);
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-live-teams")?.addEventListener("click", async (event) => {
  const cancel = event.target.closest("[data-cancel-rule]");
  if (cancel) {
    pendingTeam = null;
    liveStamp = "";
    renderLiveTeams(overlayState);
    return;
  }
  const amtBtn = event.target.closest("[data-team-amt]");
  if (amtBtn) {
    pendingTeam = { id: Number(amtBtn.dataset.id), amount: Number(amtBtn.dataset.amount) };
    liveStamp = "";
    renderLiveTeams(overlayState);
    return;
  }
  const btn = event.target.closest("button[data-kind]");
  if (!btn) return;
  try {
    const payload = {
      kind: btn.dataset.kind,
      id: Number(btn.dataset.id),
      amount: Number(btn.dataset.amount),
    };
    if (btn.dataset.rule) payload.team_rule = btn.dataset.rule;
    overlayState = await api(`/api/classes/${classId}/game/score`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    pendingTeam = null;
    liveStamp = "";
    renderLiveTeams(overlayState);
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-score-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-kind]");
  if (!btn) return;
  try {
    overlayState = await api(`/api/classes/${classId}/game/score`, {
      method: "POST",
      body: JSON.stringify({
        kind: btn.dataset.kind,
        id: Number(btn.dataset.id),
        amount: Number(btn.dataset.amount),
      }),
    });
    renderScoreList();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

async function endGame() {
  try {
    await api(`/api/classes/${classId}/game/end`, { method: "POST", body: "{}" });
  } catch (err) {
    showError("#ap-overlay-error", err);
    return;
  }
  closeOverlay();
  location.href = `/staff/class/${classId}?tab=ap&view=participation`;
}

$("ap-score-end")?.addEventListener("click", endGame);
$("ap-live-end")?.addEventListener("click", endGame);

/**
 * Log Participation from the Participation tab (skip Mark Attendance).
 */
export async function openLogParticipation() {
  hideError("#error");
  try {
    await loadContext();
    const meeting = defaultSchoolDay(logContext);
    syncOverlayPickers(logContext, meeting);
    overlayState = await api(`/api/classes/${classId}/begin`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: meeting }),
    });
    if (overlayState.game?.status === "live") {
      openLiveScoring(overlayState);
      return;
    }
    await ensureMeetingDate(meeting);
    syncOverlayPickers(logContext, meeting);
    const go = async () => {
      showPanel("gamify");
    };
    await withValidatedDate(pickerValue($("ap-gamify-date"), logContext), go);
  } catch (err) {
    showError("#error", err);
    showError("#ap-overlay-error", err);
  }
}

$("ap-popout")?.addEventListener("click", () => {
  const dialog = $("ap-overlay");
  if (!dialog) return;
  overlayPopout = !overlayPopout;
  dialog.classList.toggle("ap-overlay--popout", overlayPopout);
  $("ap-popout")?.setAttribute("aria-pressed", overlayPopout ? "true" : "false");
  if (dialog.open) {
    dialog.close();
    openOverlayDialog();
  }
});

document.getElementById("take-attendance")?.addEventListener("click", () => {
  openTakeAttendance().catch((err) => showError("#att-error", err));
});

document.getElementById("begin")?.addEventListener("click", () => {
  openLogParticipation().catch((err) => showError("#error", err));
});

if (root?.dataset.take === "1") {
  openTakeAttendance().catch((err) => showError("#att-error", err));
}

if (new URLSearchParams(location.search).get("continue") === "gamify") {
  loadContext()
    .then(() => api(`/api/classes/${classId}/game`))
    .then((state) => {
      overlayState = state;
      const gamifyDate = $("ap-gamify-date");
      if (gamifyDate && state.session?.meeting_date) {
        gamifyDate.value = state.session.meeting_date;
      }
      showPanel("gamify");
    })
    .catch((err) => showError("#error", err));
}
