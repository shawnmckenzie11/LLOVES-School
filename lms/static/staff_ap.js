/**
 * Run Live Class accordion: join-only attendance, teams, dense scoring.
 */
import {
  api,
  displayName as displayNameFn,
  escapeHtml,
  formatCountdown,
  formatPoints,
  hideError,
  lockRoundDeadline,
  openLiveSessionOverlay,
  openScoreboardOverlay,
  remainingUntilMs,
  reserveScoreboardOverlay,
  showError,
  sortStudents,
} from "/static/common.js";
import {
  bindSchoolDayPicker,
  defaultSchoolDay,
  gridSelectedIso,
  pickerValue,
  renderSemesterDayGrid,
  resolveLogDate,
  suggestedLogDay,
  syncOverlayPickers,
} from "/static/ap_calendar.js";
import { moodGlyph, nameWithMood } from "/static/mood_faces.js";

const root = document.getElementById("ap-root");
const classId = Number(root?.dataset.classId || 0);
const scoreboardKey = `lloves-scoreboard-${classId}`;
const rankKey = `lloves-rank-${classId}`;
const nameSort = localStorage.getItem(`lloves-sort-${classId}`) === "za" ? "za" : "az";
const STUDENT_AMOUNTS = [1, 5, 10, -1];
const TEAM_AMOUNTS = [1, 5, 10];
const TEAM_RULES = [
  { id: "each_member", label: "Each member" },
  { id: "split_members", label: "Split across team" },
  { id: "team_only", label: "Team bonus only" },
];

/** Open Question action chips → point deltas (staff scoring UX). */
const OPEN_QUESTION_ACTIONS = [
  { id: "asks_hwk", label: "Asks Q re: Hwk", amount: 2 },
  { id: "asks_followup", label: "Asks follow-up Q", amount: 1 },
  { id: "asks_prior_group", label: "Asks Q re: Prior Live Class Group Problem", amount: 3 },
  { id: "asks_formative", label: "Asks Q about Teacher’s Formative Question Feedback", amount: 2 },
  { id: "answers_peer", label: "Answers another student’s Q", amount: 2 },
  { id: "asks_first", label: "Asks Q for first time", amount: 1 },
];

let overlayState = null;
let pendingAction = null;
let logContext = null;
let lastAssignMode = null;
let pendingTeam = null;
let roundEndsAtMs = 0;
let liveStamp = "";
let pendingScoreboard = false;
let liveSessionId = Number(root?.dataset.liveSessionId || 0) || 0;
let sessionPollTimer = null;
let sessionPresentIds = new Set();
/** @type {Set<number>} */
let sessionLateIds = new Set();
let scoringLocked = false;
let trackMode = null;
let currentStep = "att";
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
 * Whether the game is in live scoring (locks prior accordion steps).
 * @returns {boolean}
 */
function isScoringLive() {
  return scoringLocked || overlayState?.game?.status === "live";
}

/**
 * Expand one accordion step; collapse others; apply lock chrome.
 * @param {string} name
 */
function showPanel(name) {
  currentStep = name === "live" ? "score" : name;
  const locked = isScoringLive();
  scoringLocked = locked;
  hideError("#ap-overlay-error");

  document.querySelectorAll(".track-step.ap-panel").forEach((el) => {
    const step = el.dataset.step || "";
    const isScore = step === "score";
    const isCurrent = step === currentStep || (currentStep === "score" && isScore);
    const isSetup = !isScore && step !== "validate";
    el.classList.toggle("hidden", step === "validate" && currentStep !== "validate");
    el.classList.toggle("is-current", isCurrent);
    el.classList.toggle("is-collapsed", !isCurrent);
    el.classList.toggle("is-locked", locked && isSetup);
    const body = el.querySelector(".track-step-body");
    const summary = el.querySelector(".track-step-summary");
    if (body) body.hidden = !isCurrent;
    if (summary) summary.setAttribute("aria-expanded", isCurrent ? "true" : "false");
    const lockEl = el.querySelector(".track-step-lock");
    if (lockEl) lockEl.hidden = !(locked && isSetup);
  });

  if (name === "gamify" && !trackMode) {
    selectTrackMode("individual");
  }
  updateStepSummaries();
}

/**
 * Refresh collapsed-step summary labels.
 */
function updateStepSummaries() {
  const att = $("ap-att-summary");
  if (att) {
    const n = selectedPresent().length;
    const late = sessionLateIds.size;
    att.textContent = n
      ? `${n} present${late ? ` · ${late} late` : ""}`
      : "";
  }
  const gamify = $("ap-gamify-summary");
  if (gamify) {
    gamify.textContent =
      trackMode === "team" ? "Team" : trackMode === "individual" ? "Individual" : "";
  }
  const teams = $("ap-teams-summary");
  if (teams) {
    const n = (overlayState?.teams || []).filter((t) => t.name !== "Class").length;
    teams.textContent = n ? `${n} teams · ${lastAssignMode || ""}` : "";
  }
  const names = $("ap-names-summary");
  if (names) {
    const list = (overlayState?.teams || []).map((t) => t.name).filter(Boolean);
    names.textContent = list.length ? list.join(", ") : "";
  }
  const rounds = $("ap-rounds-summary");
  if (rounds) {
    rounds.textContent = draftRounds
      .map((r) => ROUND_KIND_OPTIONS.find((o) => o.kind === r.kind)?.label || r.kind)
      .join(" · ");
  }
  const score = $("ap-score-summary");
  if (score && isScoringLive()) {
    const game = overlayState?.game || {};
    score.textContent = game.round_title
      ? `Round ${game.round || 1} · ${game.round_title}`
      : "Live";
  }
}

/**
 * Navigate away from Track Live Class after quit/end.
 */
function closeOverlay() {
  pendingAction = null;
  pendingTeam = null;
  liveStamp = "";
  stopLiveSessionPolling();
}

/**
 * Discard open setup/live game, end the live-class session, and leave the tab.
 */
async function cancelOverlay() {
  try {
    await api(`/api/classes/${classId}/game/cancel`, { method: "POST", body: "{}" });
  } catch (_) {
    /* still leave */
  }
  stopLiveSessionPolling();
  liveSessionId = 0;
  sessionPresentIds = new Set();
  sessionLateIds = new Set();
  closeOverlay();
  location.href = `/staff/class/${classId}?tab=ap&view=attendance`;
}

/**
 * Resolve live_session_id from the page URL or ap-root dataset.
 * @returns {number}
 */
function readLiveSessionId() {
  const fromData = Number(root?.dataset.liveSessionId || 0);
  if (Number.isFinite(fromData) && fromData > 0) return fromData;
  const fromQuery = Number(new URLSearchParams(location.search).get("live_session_id") || 0);
  return Number.isFinite(fromQuery) && fromQuery > 0 ? fromQuery : 0;
}

/**
 * Open the narrow Zoom-share live overlay for the current session.
 * @returns {Window|null}
 */
function ensureLiveSessionOverlay() {
  const id = liveSessionId || readLiveSessionId();
  if (!id) return null;
  liveSessionId = id;
  return openLiveSessionOverlay(id, null, { classId });
}

/**
 * Stop polling live-session attendees.
 */
function stopLiveSessionPolling() {
  if (sessionPollTimer) {
    clearInterval(sessionPollTimer);
    sessionPollTimer = null;
  }
}

/**
 * Tick join-only roster for students currently in the live session.
 * After scoring starts, refresh game state so late joiners appear on teams.
 * @param {Iterable<number>} ids
 * @param {Array<{student_id?:number,mood?:string}>} [attendees]
 */
async function applySessionPresentTicks(ids, attendees) {
  const next = new Set([...ids].map(Number).filter((n) => Number.isFinite(n) && n > 0));
  const prevSize = sessionPresentIds.size;
  for (const id of next) sessionPresentIds.add(id);
  if (Array.isArray(attendees) && overlayState?.students) {
    const moodById = new Map(
      attendees
        .map((row) => [Number(row.student_id), row.mood || null])
        .filter(([sid]) => Number.isFinite(sid) && sid > 0)
    );
    for (const student of overlayState.students) {
      const sid = Number(student.id);
      if (moodById.has(sid)) student.mood = moodById.get(sid);
    }
  }
  renderAttendanceList();
  updateStepSummaries();

  if (isScoringLive() && next.size > prevSize) {
    try {
      overlayState = await api(`/api/classes/${classId}/game`);
      liveStamp = "";
      openLiveScoring(overlayState, { stayOnScore: true });
      notifyAttendanceRefresh();
    } catch (_) {
      /* keep polling */
    }
  }
}

/**
 * Fetch live-session state and auto-mark present attendees on the roster.
 */
async function pollLiveSessionAttendees() {
  const id = liveSessionId || readLiveSessionId();
  if (!id) return;
  liveSessionId = id;
  try {
    const payload = await api(`/api/live-sessions/${id}/state`);
    if (payload?.phase === "ended" || payload?.session?.status === "ended") {
      stopLiveSessionPolling();
      return;
    }
    const rows = Array.isArray(payload?.attendees) ? payload.attendees : [];
    const present = rows.filter((row) => !row?.left_at);
    await applySessionPresentTicks(
      present.map((row) => Number(row.student_id)),
      present
    );
  } catch (_) {
    /* keep polling */
  }
}

/**
 * Begin polling session joins until End Game / cancel.
 */
function startLiveSessionPolling() {
  stopLiveSessionPolling();
  pollLiveSessionAttendees();
  sessionPollTimer = window.setInterval(pollLiveSessionAttendees, 2000);
}

/**
 * Clear leftover MGS live/setup so Mark Attendance can open for a live session.
 * Always preserves the live_class_sessions row — never ends the join code.
 */
async function clearStuckGameForLiveSession() {
  const preserveBody = JSON.stringify({ preserve_live_session: true });
  try {
    await api(`/api/classes/${classId}/game/end`, {
      method: "POST",
      body: preserveBody,
    });
    return;
  } catch (_) {
    /* end only works while MGS status is live — fall through to cancel */
  }
  try {
    await api(`/api/classes/${classId}/game/cancel`, {
      method: "POST",
      body: preserveBody,
    });
  } catch (_) {
    /* ignore — no open game is fine */
  }
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
 * Fetch whether attendance is logged for a date.
 * @param {string} iso
 * @returns {Promise<boolean>}
 */
async function isDateLogged(iso) {
  if (!iso) return false;
  const day = await api(`/api/classes/${classId}/attendance-day?date=${encodeURIComponent(iso)}`);
  return Boolean(day.logged);
}

/**
 * Paint the semester day-grid on the validate step.
 * @param {string} [selected]
 */
function showValidateGrid(selected) {
  const box = $("ap-day-grid");
  if (!box || !logContext) return;
  const iso = renderSemesterDayGrid(box, logContext, {
    selected: selected || suggestedLogDay(logContext),
    minIso: String(logContext.today || ""),
    onSelect: (value) => {
      const hidden = $("ap-valid-date");
      if (hidden) hidden.value = value;
    },
  });
  const hidden = $("ap-valid-date");
  if (hidden) hidden.value = iso;
}

/**
 * Begin Run Live Class for a resolved meeting date (session mint deferred).
 * @param {string} iso
 */
async function proceedRunLiveBegin(iso) {
  syncOverlayPickers(logContext, iso);
  const meetingInput = $("ap-meeting-date");
  if (meetingInput) meetingInput.value = iso;
  overlayState = await api(`/api/classes/${classId}/begin`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
  if (overlayState.game?.status === "live") {
    openLiveScoring(overlayState);
    if (liveSessionId) {
      ensureLiveSessionOverlay();
      startLiveSessionPolling();
    }
    return;
  }
  await ensureMeetingDate(iso);
  syncOverlayPickers(logContext, iso);
  if (liveSessionId) sessionPresentIds = new Set();
  scoringLocked = false;
  trackMode = null;
  renderAttendanceList();
  showPanel("att");
  if (liveSessionId) {
    ensureLiveSessionOverlay();
    startLiveSessionPolling();
  }
}

/**
 * Open Run Live Class with semester date gating (deferred live-session mint).
 */
export async function openRunLiveClass() {
  hideError("#ap-overlay-error");
  try {
    liveSessionId = readLiveSessionId() || liveSessionId;
    await loadContext();

    if (overlayState?.game?.status === "live") {
      openLiveScoring(overlayState);
      if (liveSessionId) {
        ensureLiveSessionOverlay();
        startLiveSessionPolling();
      }
      return;
    }

    const today = String(logContext.today || "").trim();
    const todayLogged = await isDateLogged(today);
    const decision = resolveLogDate(logContext, { todayLogged }, { flow: "run_live" });

    if (decision.mode === "confirm_override") {
      const ok = window.confirm(
        "Stored attendance and participation for today will be overridden. Continue?"
      );
      if (!ok) return;
      await proceedRunLiveBegin(decision.iso);
      return;
    }

    if (decision.mode === "auto") {
      await proceedRunLiveBegin(decision.iso);
      return;
    }

    pendingAction = async () => {
      const iso = gridSelectedIso($("ap-day-grid")) || $("ap-valid-date")?.value || decision.iso;
      await proceedRunLiveBegin(iso);
    };
    fillValidateHint();
    showValidateGrid(decision.iso);
    showPanel("validate");
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
}

/**
 * @deprecated Use openRunLiveClass on tab=live; inline take uses staff_attendance_take.js.
 */
export async function openTakeAttendance() {
  return openRunLiveClass();
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
  for (const id of ["ap-valid-date", "ap-meeting-date"]) {
    const el = $(id);
    if (!el || el.type === "hidden") {
      if (el && el.type === "hidden") {
        el.value = preferred || defaultSchoolDay(logContext) || el.value;
      }
      continue;
    }
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
  pendingAction = async () => {
    const picked =
      gridSelectedIso($("ap-day-grid")) ||
      pickerValue($("ap-valid-date"), logContext) ||
      chosen;
    await proceed(picked);
  };
  fillValidateHint();
  showValidateGrid(chosen);
  showPanel("validate");
}

/**
 * Apply meeting date from a picker to the open game.
 * @param {HTMLInputElement|null} input
 */
async function applyMeetingFromPicker(input) {
  const iso = pickerValue(input, logContext) || input?.value;
  if (!iso) return;
  overlayState = await api(`/api/classes/${classId}/game/meeting`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
}

/**
 * Present ids for the session date: join set, then attendance grid, then state.
 * @param {string} iso
 * @returns {Promise<number[]>}
 */
async function resolvePresentIds(iso) {
  if (sessionPresentIds.size) return [...sessionPresentIds];
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
    "No students have joined yet. Share the live session code, then continue when someone is present."
  );
}

/**
 * Present student ids from join-only roster (and late set).
 * @returns {number[]}
 */
function selectedPresent() {
  if (sessionPresentIds.size) return [...sessionPresentIds];
  return [...document.querySelectorAll("#ap-att-list .ap-att-row.is-present")].map((el) =>
    Number(el.dataset.studentId)
  );
}

/**
 * Draw join-only attendance rows (display-only; no click toggles).
 */
function renderAttendanceList() {
  const checked = new Set(
    sessionPresentIds.size
      ? [...sessionPresentIds]
      : (overlayState?.present_ids && overlayState.present_ids.length
          ? overlayState.present_ids
          : []) || []
  );
  const list = $("ap-att-list");
  if (!list) return;
  list.innerHTML = "";
  for (const student of sortStudents(overlayState?.students || [], nameSort)) {
    const present = checked.has(student.id);
    const late = sessionLateIds.has(student.id) || Boolean(student.late);
    const row = document.createElement("div");
    row.className = `ap-att-row${present ? " is-present" : ""}${late ? " is-late" : ""}`;
    row.dataset.studentId = String(student.id);
    row.setAttribute("aria-pressed", present ? "true" : "false");
    const mark = late ? "L" : present ? "✓" : "";
    const face = student.mood ? moodGlyph(student.mood) : "";
    row.innerHTML = `<span class="ap-att-check" aria-hidden="true">${mark}</span><span class="ap-att-name">${escapeHtml(displayName(student))}</span><span class="ap-att-mood" aria-hidden="true">${face}</span>`;
    list.appendChild(row);
  }
  updateAttCount();
}

/**
 * Live present count in the accordion heading.
 */
function updateAttCount() {
  const el = $("ap-att-count");
  if (el) el.textContent = `Attendance: ${selectedPresent().length}`;
}

/**
 * True when current round kind is Open Question.
 * @returns {boolean}
 */
function isOpenQuestionRound() {
  const game = overlayState?.game || {};
  if (game.round_kind) return String(game.round_kind) === "open";
  const rounds = game.rounds || [];
  const n = Number(game.round) || 1;
  const row = rounds[n - 1];
  if (row?.kind) return row.kind === "open";
  const title = String(game.round_title || "").toLowerCase();
  return title.includes("open question");
}

/**
 * Route to the scoring accordion step after begin/rename/start.
 * @param {any} state
 * @param {{stayOnScore?: boolean}} [opts]
 */
function openLiveScoring(state, opts = {}) {
  overlayState = state;
  scoringLocked = true;
  const teams = state.teams || [];
  const isIndividual = teams.length === 1 && teams[0]?.name === "Class";
  trackMode = isIndividual ? "individual" : "team";
  for (const s of state.students || []) {
    if (s.late) sessionLateIds.add(Number(s.id));
  }
  for (const t of teams) {
    for (const m of t.members || []) {
      if (m.late) sessionLateIds.add(Number(m.id));
    }
  }
  const scoreEnd = $("ap-score-end");
  const scoreCancel = $("ap-score-cancel");
  if (isIndividual) {
    $("ap-live-teams") && ($("ap-live-teams").innerHTML = "");
    renderScoreList();
  } else {
    $("ap-score-list") && ($("ap-score-list").innerHTML = "");
    renderLiveTeams(state);
  }
  if (scoreEnd) scoreEnd.hidden = false;
  if (scoreCancel) scoreCancel.hidden = false;
  showPanel("score");
  renderAttendanceList();
}

/**
 * Meeting date from hidden field or open session.
 * @returns {string}
 */
function sessionIso() {
  const fromAtt = $("ap-meeting-date")?.value || pickerValue($("ap-meeting-date"), logContext);
  if (fromAtt) return fromAtt;
  return overlayState?.session?.meeting_date || defaultSchoolDay(logContext) || todayISO();
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
    $("ap-meeting-date")?.value ||
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

/**
 * Mark one tracking-mode button selected (checkmark) and clear the other.
 * @param {"individual"|"team"} mode
 */
function selectTrackMode(mode) {
  trackMode = mode;
  const individual = $("ap-gamify-no");
  const team = $("ap-gamify-yes");
  for (const [btn, on] of [
    [individual, mode === "individual"],
    [team, mode === "team"],
  ]) {
    if (!(btn instanceof HTMLElement)) continue;
    btn.classList.toggle("is-selected", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    const check = btn.querySelector(".ap-att-check");
    if (check) check.textContent = on ? "✓" : "";
  }
  updateStepSummaries();
}

for (const id of ["ap-validate-cancel", "ap-score-cancel"]) {
  $(id)?.addEventListener("click", () => {
    if (id === "ap-score-cancel") {
      if (!window.confirm("Quit scoring? Scores already logged stay registered.")) return;
    }
    cancelOverlay();
  });
}

document.querySelectorAll("[data-track-nav='quit']").forEach((btn) => {
  if (btn.id === "ap-score-cancel" || btn.id === "ap-validate-cancel") return;
  btn.addEventListener("click", () => cancelOverlay());
});

$("ap-share-code")?.addEventListener("click", async () => {
  try {
    liveSessionId = readLiveSessionId() || liveSessionId;
    if (!liveSessionId) {
      const res = await fetch(`/staff/class/${classId}/run-live`, {
        method: "POST",
        credentials: "same-origin",
        redirect: "manual",
      });
      const loc = res.headers.get("Location") || "";
      const match = /live_session_id=(\d+)/.exec(loc);
      if (match) {
        liveSessionId = Number(match[1]);
        if (root) root.dataset.liveSessionId = String(liveSessionId);
        const url = new URL(window.location.href);
        url.searchParams.set("tab", "live");
        url.searchParams.set("live_session_id", String(liveSessionId));
        window.history.replaceState({}, "", url.toString());
      } else if (res.status >= 300 && res.status < 400 && loc) {
        window.location.assign(loc);
        return;
      } else {
        const errText = await res.text();
        throw new Error(errText.slice(0, 160) || "Could not mint a join code.");
      }
    }
    ensureLiveSessionOverlay();
    startLiveSessionPolling();
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

$("ap-gamify-next")?.addEventListener("click", async () => {
  try {
    if (trackMode === "team") {
      await withValidatedDate(sessionIso(), async () => {
        renderTeamsPanel();
        showPanel("teams");
      });
      return;
    }
    await withValidatedDate(sessionIso(), async (meeting) => {
      const ids = selectedPresent();
      overlayState = await api(`/api/classes/${classId}/game/ungamified`, {
        method: "POST",
        body: JSON.stringify({ present_ids: ids, meeting_date: meeting }),
      });
      openLiveScoring(overlayState);
    });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-validate-apply")?.addEventListener("click", async () => {
  const iso =
    gridSelectedIso($("ap-day-grid")) ||
    pickerValue($("ap-valid-date"), logContext) ||
    $("ap-valid-date")?.value;
  if (!iso) return;
  try {
    overlayState = await api(`/api/classes/${classId}/game/meeting`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: iso }),
    });
    const attDate = $("ap-meeting-date");
    if (attDate) attDate.value = iso;
    const fn = pendingAction;
    pendingAction = null;
    if (fn) await fn();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-gamify-yes")?.addEventListener("click", () => {
  if (isScoringLive()) return;
  selectTrackMode("team");
});

$("ap-gamify-no")?.addEventListener("click", () => {
  if (isScoringLive()) return;
  selectTrackMode("individual");
});

/**
 * Team-count bounds from present students.
 * @returns {{min:number, max:number}}
 */
function nTeamsBounds() {
  const present = (overlayState?.present_ids || selectedPresent()).length;
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
  if (box) {
    const stored = localStorage.getItem(scoreboardKey);
    box.checked = stored === null ? true : stored === "1";
    localStorage.setItem(scoreboardKey, box.checked ? "1" : "0");
  }
  syncScoreboardPreview();
  const rankBox = $("ap-rank-toggle");
  if (rankBox) {
    const fromState = overlayState && typeof overlayState.show_rank === "boolean";
    rankBox.checked = fromState
      ? Boolean(overlayState.show_rank)
      : localStorage.getItem(rankKey) === "1";
  }
  setNTeams(Number($("ap-n-teams")?.value) || 2);
  selectAssignMode(lastAssignMode || "balanced");
}

/**
 * Show/hide the scoreboard mock preview from the checkbox.
 */
function syncScoreboardPreview() {
  const box = $("ap-scoreboard-toggle");
  const wrap = $("ap-scoreboard-preview-wrap");
  if (wrap) wrap.hidden = !(box instanceof HTMLInputElement && box.checked);
}

/**
 * Highlight Assign Balanced / Random / Manual choice.
 * @param {"random"|"balanced"|"manual"} mode
 */
function selectAssignMode(mode) {
  lastAssignMode = mode;
  for (const [id, key] of [
    ["ap-assign-balanced", "balanced"],
    ["ap-assign-random", "random"],
    ["ap-assign-manual", "manual"],
  ]) {
    const btn = $(id);
    if (!(btn instanceof HTMLElement)) continue;
    const on = key === mode;
    btn.classList.toggle("is-selected", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    const check = btn.querySelector(".ap-att-check");
    if (check) check.textContent = on ? "✓" : "";
  }
}

$("ap-n-teams-down")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) - 1));
$("ap-n-teams-up")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) + 1));
$("ap-scoreboard-toggle")?.addEventListener("change", (event) => {
  const box = event.target;
  if (box instanceof HTMLInputElement) {
    localStorage.setItem(scoreboardKey, box.checked ? "1" : "0");
    syncScoreboardPreview();
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
 * Assign teams and show roster preview with prior participation.
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

$("ap-assign-random")?.addEventListener("click", () => {
  selectAssignMode("random");
  $("ap-manual-assign")?.classList.add("hidden");
});
$("ap-assign-balanced")?.addEventListener("click", () => {
  selectAssignMode("balanced");
  $("ap-manual-assign")?.classList.add("hidden");
});
$("ap-assign-manual")?.addEventListener("click", () => {
  selectAssignMode("manual");
  const nTeams = Number($("ap-n-teams").value);
  const present = new Set(overlayState.present_ids || selectedPresent());
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
        <span>${escapeHtml(displayName(student))}</span>
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
/**
 * True when manual team picker sizes differ by at most one student.
 * @returns {boolean}
 */
function manualTeamsBalanced() {
  const nTeams = Math.max(2, Number($("ap-n-teams")?.value) || 2);
  const counts = Array.from({ length: nTeams }, () => 0);
  for (const el of document.querySelectorAll("#ap-manual-list .team-step")) {
    const idx = Number(el.dataset.teamIndex);
    if (idx >= 0 && idx < nTeams) counts[idx] += 1;
  }
  if (!counts.some((n) => n > 0)) return false;
  const min = Math.min(...counts);
  const max = Math.max(...counts);
  return max - min <= 1;
}

$("ap-teams-next")?.addEventListener("click", () => {
  const mode = lastAssignMode || "balanced";
  if (mode === "manual") {
    const open = $("ap-manual-assign");
    if (!open || open.classList.contains("hidden")) {
      showError(
        "#ap-overlay-error",
        new Error("Choose Assign Manually and set each student's team, then Next.")
      );
      return;
    }
    if (!manualTeamsBalanced()) {
      showError(
        "#ap-overlay-error",
        new Error(
          "Balance teams so sizes are equal or off by one, or pick Assign Balanced / Assign Randomly."
        )
      );
      return;
    }
    assign("manual").catch((err) => showError("#ap-overlay-error", err));
    return;
  }
  assign(mode).catch((err) => showError("#ap-overlay-error", err));
});
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
 * Paint team rename fields plus roster preview with career totals.
 */
function renderNamesPanel() {
  const hint = $("ap-names-hint");
  if (hint && lastAssignMode === "balanced") {
    hint.textContent =
      "Balanced preview — even rosters (sizes equal or off by one), inspect prior participation side by side, then rename if you want.";
  } else if (hint && lastAssignMode === "random") {
    hint.textContent =
      "Random preview — inspect names and prior participation, then rename teams if you want.";
  } else if (hint) {
    hint.textContent =
      "Rename teams and inspect roster strength (prior participation) before creating teams.";
  }
  const box = $("ap-name-list");
  if (!box) return;
  box.innerHTML = "";
  for (const team of overlayState.teams || []) {
    const members = sortStudents(team.members || [], nameSort);
    const strength = members.reduce((sum, row) => sum + (Number(row.career_total) || 0), 0);
    const wrap = document.createElement("div");
    wrap.className = "team-preview";
    wrap.innerHTML = `<label class="field">Team ${team.sort_order + 1}<input type="text" data-team-id="${team.id}" value="${escapeHtml(team.name)}"></label>
      <p class="hint">Strength ${escapeHtml(formatPoints(strength))} · ${members.length} students</p>
      <ul class="preview-roster">${members
        .map(
          (row) =>
            `<li><span class="prior">${escapeHtml(formatPoints(row.career_total))}</span> ${escapeHtml(displayName(row))}</li>`
        )
        .join("")}</ul>`;
    box.appendChild(wrap);
  }
  updateStepSummaries();
}

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
        <label class="field ap-round-type">Round ${index + 1}
          <select data-round-kind>${options}</select>
        </label>
        <label class="field ap-round-len">Length (min)
          <input type="number" min="1" max="180" value="${Number(row.minutes) || 10}" data-round-minutes>
        </label>
        <button type="button" class="secondary ap-round-remove" data-round-remove ${draftRounds.length <= 1 ? "disabled" : ""}>Remove</button>
      </div>`;
    })
    .join("");
  const addBtn = $("ap-rounds-add");
  if (addBtn) addBtn.disabled = draftRounds.length >= 3 || used.size >= 3;
  updateStepSummaries();
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

$("ap-rounds-start")?.addEventListener("click", async () => {
  const hasLiveOverlay = Boolean(liveSessionId || readLiveSessionId());
  // Live-overlay is primary for Track Live Class — skip separate ESPN window.
  const wantEspn = pendingScoreboard && !hasLiveOverlay;
  const overlay = wantEspn ? reserveScoreboardOverlay() : null;
  try {
    const rounds = draftRounds.map((row) => ({
      kind: row.kind,
      minutes: Number(row.minutes) || 10,
    }));
    overlayState = await api(`/api/classes/${classId}/game/start-rounds`, {
      method: "POST",
      body: JSON.stringify({ rounds }),
    });
    if (wantEspn) openScoreboardOverlay(overlay);
    else overlay?.close();
    if (hasLiveOverlay) ensureLiveSessionOverlay();
    pendingScoreboard = false;
    openLiveScoring(overlayState);
    startLiveSessionPolling();
  } catch (err) {
    overlay?.close();
    showError("#ap-overlay-error", err);
  }
});

/**
 * Compact student point / Open Question action chips.
 * @param {number} id
 * @returns {string}
 */
function studentButtons(id) {
  if (isOpenQuestionRound()) {
    return OPEN_QUESTION_ACTIONS.map(
      (action) =>
        `<button type="button" class="ap-action-chip" data-kind="student" data-id="${id}" data-amount="${action.amount}" data-label="${escapeHtml(action.label)}" title="+${action.amount}">${escapeHtml(action.label)}</button>`
    ).join("");
  }
  return STUDENT_AMOUNTS.map(
    (n) =>
      `<button type="button" class="ap-score-chip" data-kind="student" data-id="${id}" data-amount="${n}">${n > 0 ? "+" : ""}${n}</button>`
  ).join("");
}

/**
 * Dense individual scoring list (Class / ungamified).
 */
function renderScoreList() {
  const box = $("ap-score-list");
  if (!box) return;
  const teams = overlayState.teams || [];
  const members = teams.flatMap((t) => t.members || []);
  const rows = sortStudents(members.length ? members : overlayState.students || [], nameSort);
  box.innerHTML = rows
    .map((row) => {
      const late = sessionLateIds.has(row.id) || Boolean(row.late);
      return `<div class="ap-score-row">
        <span class="ap-score-who">${nameWithMood(displayName(row), row.mood)}${late ? ' <span class="ap-late-tag">L</span>' : ""}</span>
        <span class="ap-score-pts">${escapeHtml(formatPoints(row.session_points || 0))}</span>
        <span class="pm">${studentButtons(row.id)}</span>
      </div>`;
    })
    .join("");
  updateStepSummaries();
}

/**
 * +/- buttons for team scoring.
 * @param {number} teamId
 */
function teamControls(teamId) {
  if (pendingTeam && pendingTeam.id === teamId) {
    const amount = pendingTeam.amount;
    const choices = TEAM_RULES.map(
      (rule) =>
        `<button type="button" class="ap-score-chip rule-pick-btn" data-kind="team" data-id="${teamId}" data-amount="${amount}" data-rule="${rule.id}">${escapeHtml(rule.label)}</button>`
    ).join("");
    return `<div class="rule-pick">
      <p class="rule-pick-label">Apply +${amount} as</p>
      <div class="rule-pick-actions">
        ${choices}
        <button type="button" class="secondary rule-pick-cancel" data-cancel-rule="1">Cancel</button>
      </div>
    </div>`;
  }
  return `<div class="pm">${TEAM_AMOUNTS.map(
    (n) =>
      `<button type="button" class="ap-score-chip team-amt" data-team-amt="1" data-id="${teamId}" data-amount="${n}">+${n}</button>`
  ).join("")}<button type="button" class="ap-score-chip" data-kind="team" data-id="${teamId}" data-amount="-5" data-rule="team_only">−5</button></div>`;
}

/**
 * Paint dense team live scoring.
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
    open: isOpenQuestionRound(),
    activeTeam: $("ap-score-team-tabs")?.dataset.activeTeam || "",
    teams: (state.teams || []).map((t) => [
      t.id,
      t.score,
      t.members?.map((m) => [m.id, m.session_points]),
    ]),
  });
  if (stamp === liveStamp) return;
  liveStamp = stamp;
  const tabs = $("ap-score-team-tabs");
  const rootEl = $("ap-live-teams");
  if (!rootEl) return;
  const teams = state.teams || [];
  if (tabs) {
    tabs.hidden = teams.length < 2;
    if (!tabs.dataset.activeTeam && teams[0]) {
      tabs.dataset.activeTeam = String(teams[0].id);
    }
    const activeId = Number(tabs.dataset.activeTeam || teams[0]?.id || 0);
    tabs.innerHTML = teams
      .map((team) => {
        const on = Number(team.id) === activeId;
        return `<button type="button" class="ap-score-team-tab${on ? " is-on" : ""}" data-team-tab="${team.id}" style="--team:${escapeHtml(team.color)}">${escapeHtml(team.name)}</button>`;
      })
      .join("");
  }
  const activeId = Number(tabs?.dataset.activeTeam || teams[0]?.id || 0);
  const visible = teams.filter((t) => Number(t.id) === activeId);
  const paintTeams = visible.length ? visible : teams;
  rootEl.innerHTML = paintTeams
    .map((team) => {
      const members = sortStudents(team.members || [], nameSort);
      const playerBlocks = members
        .map((s) => {
          const late = sessionLateIds.has(s.id) || Boolean(s.late);
          return `<div class="ap-live-player">
            <div class="ap-live-row">
              <span class="who">${nameWithMood(displayName(s), s.mood)}${late ? ' <span class="ap-late-tag">L</span>' : ""}</span>
              <span class="now">${escapeHtml(formatPoints(s.session_points || 0))}</span>
            </div>
            <div class="pm">${studentButtons(s.id)}</div>
          </div>`;
        })
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
  updateStepSummaries();
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
    openLiveScoring(overlayState, { stayOnScore: true });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

/**
 * POST a score mutation (supports Open Question ``label``).
 * @param {HTMLElement} btn
 */
async function postScoreFromButton(btn) {
  const payload = {
    kind: btn.dataset.kind,
    id: Number(btn.dataset.id),
    amount: Number(btn.dataset.amount),
  };
  if (btn.dataset.rule) payload.team_rule = btn.dataset.rule;
  if (btn.dataset.label) payload.label = btn.dataset.label;
  overlayState = await api(`/api/classes/${classId}/game/score`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  pendingTeam = null;
  liveStamp = "";
  openLiveScoring(overlayState, { stayOnScore: true });
}

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
    await postScoreFromButton(btn);
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-score-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-kind]");
  if (!btn) return;
  try {
    await postScoreFromButton(btn);
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
  stopLiveSessionPolling();
  liveSessionId = 0;
  sessionPresentIds = new Set();
  sessionLateIds = new Set();
  closeOverlay();
  location.href = `/staff/class/${classId}?tab=ap&view=participation`;
}

$("ap-score-team-tabs")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-team-tab]");
  if (!btn) return;
  const tabs = $("ap-score-team-tabs");
  if (tabs) tabs.dataset.activeTeam = String(btn.getAttribute("data-team-tab") || "");
  liveStamp = "";
  if (overlayState) renderLiveTeams(overlayState);
});

$("ap-score-end")?.addEventListener("click", endGame);

/**
 * Resume an in-progress live class when returning to tab=live with a session id.
 */
async function resumeLiveClassIfNeeded() {
  liveSessionId = readLiveSessionId() || liveSessionId;
  if (!liveSessionId) return false;
  try {
    await loadContext();
    overlayState = await api(`/api/classes/${classId}/begin`, {
      method: "POST",
      body: JSON.stringify({
        meeting_date: defaultSchoolDay(logContext),
      }),
    });
    if (overlayState.game?.status === "live") {
      openLiveScoring(overlayState);
      ensureLiveSessionOverlay();
      startLiveSessionPolling();
      return true;
    }
    renderAttendanceList();
    showPanel("att");
    ensureLiveSessionOverlay();
    startLiveSessionPolling();
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Log Participation shortcut (skip Mark Attendance when possible).
 */
export async function openLogParticipation() {
  hideError("#error");
  hideError("#ap-overlay-error");
  try {
    liveSessionId = readLiveSessionId() || liveSessionId;
    await loadContext();
    const meeting = defaultSchoolDay(logContext);
    syncOverlayPickers(logContext, meeting);
    const meetingInput = $("ap-meeting-date");
    if (meetingInput) meetingInput.value = meeting;
    overlayState = await api(`/api/classes/${classId}/begin`, {
      method: "POST",
      body: JSON.stringify({ meeting_date: meeting }),
    });
    if (overlayState.game?.status === "live") {
      openLiveScoring(overlayState);
      if (liveSessionId) {
        ensureLiveSessionOverlay();
        startLiveSessionPolling();
      }
      return;
    }
    await ensureMeetingDate(meeting);
    syncOverlayPickers(logContext, meeting);
    const go = async () => {
      showPanel("gamify");
    };
    await withValidatedDate(sessionIso(), go);
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
}

document.querySelectorAll("[data-accordion-toggle]").forEach((btn) => {
  btn.addEventListener("click", (event) => {
    // Accordion headers are display-only; navigate with Prev / Next footers.
    event.preventDefault();
  });
});

document.querySelectorAll("[data-track-nav='prev']").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.hasAttribute("disabled") || isScoringLive()) return;
    const current = btn.closest(".track-step.ap-panel")?.getAttribute("data-step");
    const back = {
      gamify: "att",
      teams: "gamify",
      names: "teams",
      rounds: "names",
      validate: "att",
    };
    const target = back[current || ""];
    if (target) showPanel(target);
  });
});

selectTrackMode("individual");
if (localStorage.getItem(scoreboardKey) === null) {
  localStorage.setItem(scoreboardKey, "1");
}

if (root?.dataset.apView === "live") {
  (async () => {
    const resumed = await resumeLiveClassIfNeeded();
    if (!resumed) {
      await openRunLiveClass();
    }
  })().catch((err) => showError("#ap-overlay-error", err));
}
