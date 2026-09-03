import {
  api,
  classIdFromPath,
  dashboardSort,
  displayName,
  escapeHtml,
  formatPoints,
  hideError,
  openScoreboardOverlay,
  reserveScoreboardOverlay,
  showError,
  sortStudents,
} from "./common.js";

const classId = classIdFromPath();
const nameSort = dashboardSort(classId);
const scoreboardKey = `lloves-scoreboard-${classId}`;
let state = null;
let lastAssignMode = null;

const $ = (id) => document.getElementById(id);

/**
 * Course URL after Done / Cancel (Attendance or Participation sub-tab).
 * @returns {string}
 */
function returnHref() {
  const view = sessionStorage.getItem(`lloves-ap-return-${classId}`) || "participation";
  const safe = view === "attendance" ? "attendance" : "participation";
  return `/staff/class/${classId}?tab=ap&view=${safe}`;
}

/**
 * Whether Create Teams should open the scoreboard overlay.
 * @returns {boolean}
 */
function scoreboardEnabled() {
  const box = $("scoreboard-toggle");
  if (box) return Boolean(box.checked);
  return localStorage.getItem(scoreboardKey) === "1";
}

/**
 * Keep the Scoreboard checkbox in sync with localStorage.
 */
function syncScoreboardPref() {
  const box = $("scoreboard-toggle");
  if (!box) return;
  localStorage.setItem(scoreboardKey, box.checked ? "1" : "0");
}


/**
 * Fetch open-game state and show the matching setup step.
 */
async function load() {
  hideError("#error");
  state = await api(`/api/classes/${classId}/game`);
  const status = state.game.status;
  $("meta").textContent = `${state.class.course_code} · ${state.session.header_label}`;
  $("back-dash").href = returnHref();
  fillTimeOptions(state);
  const dateEl = $("meeting-date");
  constrainMeetingDate();
  if (dateEl && state.session.meeting_date) {
    dateEl.value = state.session.meeting_date;
    constrainMeetingDate();
  }
  const timeEl = $("meeting-time");
  if (timeEl && state.session.time) {
    timeEl.value = state.session.time;
  }
  if (status === "live") {
    location.replace(`/class/${classId}/game`);
    return;
  }
  $("step-attendance").classList.toggle("hidden", status !== "attendance");
  $("step-teams").classList.toggle("hidden", status !== "teams");
  $("step-names").classList.toggle("hidden", status !== "names");
  if (status !== "teams") hideManualAssign();
  if (status === "attendance") renderAttendance();
  if (status === "teams") {
    const present = (state.present_ids || []).length;
    $("n-teams").max = String(Math.max(2, present));
    const current = Number($("n-teams").value) || 2;
    setNTeams(current);
  }
  if (status === "names") renderNames();
  const boardBox = $("scoreboard-toggle");
  if (boardBox && boardBox.dataset.bound !== "1") {
    boardBox.checked = localStorage.getItem(scoreboardKey) === "1";
    boardBox.addEventListener("change", syncScoreboardPref);
    boardBox.dataset.bound = "1";
  }
}

/**
 * Local calendar day as YYYY-MM-DD.
 * @returns {string}
 */
function todayISO() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * Block past days on the setup date picker.
 */
function constrainMeetingDate() {
  const el = $("meeting-date");
  if (!el) return;
  const min = todayISO();
  el.min = min;
  if (el.value && el.value < min) el.value = min;
}

/**
 * Allowed team-count range from present students.
 * @returns {{min:number, max:number}}
 */
function nTeamsBounds() {
  const present = (state?.present_ids || []).length;
  return { min: 2, max: Math.max(2, present) };
}

/**
 * Clamp and store the team count, then refresh manual pickers if open.
 * @param {number} value
 */
function setNTeams(value) {
  const { min, max } = nTeamsBounds();
  const n = Math.min(max, Math.max(min, Number(value) || min));
  $("n-teams").value = String(n);
  $("n-teams").max = String(max);
  if (!$("manual-assign").classList.contains("hidden")) renderManualAssign();
}

/**
 * Present students in dashboard name order.
 * @returns {Array<{id:number, first_name:string, last_display:string}>}
 */
function presentStudents() {
  const present = new Set(state.present_ids || []);
  return sortStudents(
    (state.students || []).filter((student) => present.has(student.id)),
    nameSort
  );
}

/**
 * Draw attendance checkboxes. First game: all unchecked. Later: previous column.
 */
function renderAttendance() {
  const checked = new Set(
    (state.present_ids && state.present_ids.length
      ? state.present_ids
      : state.default_present_ids) || []
  );
  const list = $("att-list");
  list.innerHTML = "";
  for (const student of sortStudents(state.students, nameSort)) {
    const id = `att-${student.id}`;
    const row = document.createElement("label");
    row.innerHTML = `<input type="checkbox" id="${id}" value="${student.id}"> ${escapeHtml(displayName(student, nameSort))}`;
    list.appendChild(row);
    row.querySelector("input").checked = checked.has(student.id);
  }
  updateAttCount();
}

/**
 * Live present count beside Mark Attendance.
 */
function updateAttCount() {
  const el = $("att-count");
  if (!el) return;
  el.textContent = `Attendance: ${selectedPresent().length}`;
}

/**
 * Collect checked student ids from the attendance list.
 * @returns {number[]}
 */
function selectedPresent() {
  return [...document.querySelectorAll("#att-list input:checked")].map((el) => Number(el.value));
}

/**
 * Team rename inputs plus a score preview for inspection.
 */
function renderNames() {
  const hint = $("names-hint");
  if (hint && lastAssignMode === "balanced") {
    hint.textContent =
      "Balanced preview — even rosters (sizes equal or off by one), inspect career scores side by side, then rename if you want. Create Teams opens the teacher view and Zoom scoreboard.";
  }
  const box = $("name-list");
  box.innerHTML = "";
  for (const team of state.teams) {
    const members = sortStudents(team.members || [], nameSort);
    const strength = members.reduce((sum, row) => sum + (Number(row.career_total) || 0), 0);
    const wrap = document.createElement("div");
    wrap.className = "team-preview";
    wrap.innerHTML = `<label class="field">Team ${team.sort_order + 1}<input type="text" data-team-id="${team.id}" value="${escapeHtml(team.name)}"></label>
      <p class="hint">Strength ${escapeHtml(formatPoints(strength))} · ${members.length} students</p>
      <ul class="preview-roster">${members
        .map(
          (row) =>
            `<li><span class="prior">${escapeHtml(formatPoints(row.career_total))}</span> ${escapeHtml(displayName(row, nameSort))}</li>`
        )
        .join("")}</ul>`;
    box.appendChild(wrap);
  }
}

/**
 * Hide the per-student team picker.
 */
function hideManualAssign() {
  const panel = $("manual-assign");
  if (panel) panel.classList.add("hidden");
}

/**
 * Draw a large +/- team stepper for each present student.
 */
function renderManualAssign() {
  const nTeams = Number($("n-teams").value);
  const list = $("manual-list");
  const previous = new Map(
    [...document.querySelectorAll("#manual-list [data-student-id]")].map((el) => [
      Number(el.dataset.studentId),
      Number(el.dataset.teamIndex),
    ])
  );
  list.innerHTML = presentStudents()
    .map((student, index) => {
      const fallback = index % Math.max(1, nTeams);
      const stored = previous.get(student.id);
      const teamIndex = Math.min(nTeams - 1, stored >= 0 ? stored : fallback);
      return `<div class="manual-row">
        <span>${escapeHtml(displayName(student, nameSort))}</span>
        <div class="team-step" data-student-id="${student.id}" data-team-index="${teamIndex}">
          <button type="button" data-step="-1" aria-label="Previous team">−</button>
          <span class="team-n">Team ${teamIndex + 1}</span>
          <button type="button" data-step="1" aria-label="Next team">+</button>
        </div>
      </div>`;
    })
    .join("");
  $("manual-assign").classList.remove("hidden");
}

/**
 * Read the manual picker into API assignment rows.
 * @returns {{student_id:number, team_index:number}[]}
 */
function manualAssignments() {
  return [...document.querySelectorAll("#manual-list .team-step")].map((el) => ({
    student_id: Number(el.dataset.studentId),
    team_index: Number(el.dataset.teamIndex),
  }));
}

$("att-all").addEventListener("click", () => {
  document.querySelectorAll("#att-list input").forEach((el) => {
    el.checked = true;
  });
  updateAttCount();
});
$("att-none").addEventListener("click", () => {
  document.querySelectorAll("#att-list input").forEach((el) => {
    el.checked = false;
  });
  updateAttCount();
});
$("att-list").addEventListener("change", updateAttCount);

$("att-next").addEventListener("click", async () => {
  try {
    state = await api(`/api/classes/${classId}/game/attendance`, {
      method: "POST",
      body: JSON.stringify({ present_ids: selectedPresent() }),
    });
    sessionStorage.setItem(`lloves-ap-return-${classId}`, "participation");
    await load();
  } catch (err) {
    showError("#error", err);
  }
});

$("att-done")?.addEventListener("click", async () => {
  hideError("#error");
  try {
    await api(`/api/classes/${classId}/game/finalize-attendance`, {
      method: "POST",
      body: JSON.stringify({ present_ids: selectedPresent() }),
    });
    sessionStorage.setItem(`lloves-ap-return-${classId}`, "attendance");
    location.href = `/staff/class/${classId}?tab=ap&view=attendance`;
  } catch (err) {
    showError("#error", err);
  }
});

$("teams-back").addEventListener("click", async () => {
  try {
    await api(`/api/classes/${classId}/game/step`, {
      method: "POST",
      body: JSON.stringify({ status: "attendance" }),
    });
    await load();
  } catch (err) {
    showError("#error", err);
  }
});

/**
 * Assign present students randomly, balanced, or from the manual picker.
 * @param {"random"|"balanced"|"manual"} mode
 */
async function assign(mode) {
  hideError("#error");
  lastAssignMode = mode;
  const payload = { n_teams: Number($("n-teams").value), mode };
  if (mode === "manual") payload.assignments = manualAssignments();
  try {
    state = await api(`/api/classes/${classId}/game/assign`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    hideManualAssign();
    await load();
  } catch (err) {
    showError("#error", err);
  }
}

$("assign-random").addEventListener("click", () => assign("random"));
$("assign-balanced").addEventListener("click", () => assign("balanced"));
$("assign-manual").addEventListener("click", () => {
  hideError("#error");
  renderManualAssign();
});
$("manual-cancel").addEventListener("click", hideManualAssign);
$("manual-confirm").addEventListener("click", () => assign("manual"));
$("n-teams-down").addEventListener("click", () => setNTeams(Number($("n-teams").value) - 1));
$("n-teams-up").addEventListener("click", () => setNTeams(Number($("n-teams").value) + 1));
$("n-teams").addEventListener("change", () => setNTeams($("n-teams").value));
$("manual-list").addEventListener("click", (event) => {
  const btn = event.target.closest("[data-step]");
  if (!btn) return;
  const row = btn.closest(".team-step");
  const nTeams = Math.max(2, Number($("n-teams").value) || 2);
  const next = Math.min(nTeams - 1, Math.max(0, Number(row.dataset.teamIndex) + Number(btn.dataset.step)));
  row.dataset.teamIndex = String(next);
  row.querySelector(".team-n").textContent = `Team ${next + 1}`;
});

$("names-back").addEventListener("click", async () => {
  try {
    await api(`/api/classes/${classId}/game/step`, {
      method: "POST",
      body: JSON.stringify({ status: "teams" }),
    });
    await load();
  } catch (err) {
    showError("#error", err);
  }
});

$("start-game").addEventListener("click", async () => {
  hideError("#error");
  syncScoreboardPref();
  const wantBoard = scoreboardEnabled();
  const overlay = wantBoard ? reserveScoreboardOverlay() : null;
  const teams = [...document.querySelectorAll("#name-list input")].map((el) => ({
    id: Number(el.dataset.teamId),
    name: el.value,
  }));
  try {
    await api(`/api/classes/${classId}/game/rename`, {
      method: "POST",
      body: JSON.stringify({ teams }),
    });
    if (wantBoard) openScoreboardOverlay(overlay);
    else overlay?.close();
    location.href = `/class/${classId}/game`;
  } catch (err) {
    overlay?.close();
    showError("#error", err);
  }
});

/**
 * Abort setup and leave the Begin a New Game flow.
 * @param {string} href
 */
async function cancelSetup(href) {
  hideError("#error");
  try {
    await api(`/api/classes/${classId}/game/cancel`, { method: "POST", body: "{}" });
    location.href = href;
  } catch (err) {
    showError("#error", err);
  }
}

["cancel-setup-att", "cancel-setup-teams", "cancel-setup-names"].forEach((id) => {
  const el = $(id);
  if (el) el.addEventListener("click", () => cancelSetup(returnHref()));
});

$("back-dash").addEventListener("click", (event) => {
  event.preventDefault();
  cancelSetup(returnHref());
});

$("home-link").addEventListener("click", (event) => {
  event.preventDefault();
  cancelSetup("/");
});

/**
 * Fill the manual time dropdown from the server's wizard times.
 * @param {any} payload
 */
function fillTimeOptions(payload) {
  const select = $("meeting-time");
  const options = payload.time_options || [];
  if (!select || select.dataset.filled === "1") {
    if (select && payload.session && payload.session.time) {
      select.value = payload.session.time;
    }
    return;
  }
  select.innerHTML = options
    .map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`)
    .join("");
  select.dataset.filled = "1";
  if (payload.session && payload.session.time) {
    select.value = payload.session.time;
  }
}

$("select-manually").addEventListener("click", () => {
  $("manual-picker").classList.remove("hidden");
});

$("close-picker").addEventListener("click", () => {
  $("manual-picker").classList.add("hidden");
});

$("apply-meeting").addEventListener("click", async () => {
  hideError("#error");
  const meetingDate = $("meeting-date").value;
  const time = $("meeting-time").value;
  if (!meetingDate) {
    showError("#error", new Error("Choose a date"));
    return;
  }
  if (meetingDate < todayISO()) {
    showError("#error", new Error("Choose today or a future date"));
    return;
  }
  try {
    state = await api(`/api/classes/${classId}/game/meeting`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: meetingDate, time }),
    });
    $("manual-picker").classList.add("hidden");
    await load();
  } catch (err) {
    showError("#error", err);
  }
});

load().catch((err) => showError("#error", err));
