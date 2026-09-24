/**
 * Run Live Class IA v2 shell: single-line header, OptionsStrip, dual body.
 */
import {
  api,
  displayName as displayNameFn,
  escapeHtml,
  formatQuestionHtml,
  questionFieldHtml,
  questionImageHtml,
  renderLiveQuestionMath,
  formatCountdown,
  formatPoints,
  hideError,
  lockRoundDeadline,
  closeLiveSessionOverlay,
  openLiveSessionOverlay,
  openScoreboardOverlay,
  remainingUntilMs,
  reserveLiveSessionOverlay,
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
import { nameWithMood } from "/static/mood_faces.js";
import { bindWhiteboard } from "/static/live_whiteboard.js";

const root = document.getElementById("ap-root");
const classId = Number(root?.dataset.classId || 0);
const nameSort = localStorage.getItem(`lloves-sort-${classId}`) === "za" ? "za" : "az";
const STUDENT_AMOUNTS = [1, 5, 10, -1];
const TEAM_AMOUNTS = [1, 5, 10];
const TEAM_RULES = [
  { id: "each_member", label: "Each member" },
  { id: "split_members", label: "Split across team" },
  { id: "team_only", label: "Team bonus only" },
];

/** Builtin Open Question actions (fallback when profiles API is unavailable). */
const OPEN_QUESTION_ACTIONS = [
  { id: "asks_hwk", label: "Asks Q re: Hwk", amount: 2 },
  { id: "asks_followup", label: "Asks follow-up Q", amount: 1 },
  { id: "asks_prior_group", label: "Asks Q re: Prior Live Class Group Problem", amount: 3 },
  { id: "asks_formative", label: "Asks Q about Teacher’s Formative Question Feedback", amount: 2 },
  { id: "answers_peer", label: "Answers another student’s Q", amount: 2 },
  { id: "asks_first", label: "Asks Q for first time", amount: 1 },
];

/** @type {{open: {active_id: string, profiles: Array<{id: string, name: string, actions: Array<{id: string, label: string, amount: number}>}>}} | null} */
let openProfilesDoc = null;
let openProfilesLoadPromise = null;

let overlayState = null;
let pendingAction = null;
let logContext = null;
let lastAssignMode = "";
/** @type {number[]} */
let lastRandomPreviewIds = [];
let pendingTeam = null;
let roundEndsAtMs = 0;
let liveStamp = "";
let pendingScoreboard = false;
/** @type {Record<string, number>} */
let sessionGamePoints = {};
/** @type {Record<string, number>} */
let sessionCareerTotals = {};
let liveSessionId = Number(root?.dataset.liveSessionId || 0) || 0;
let joinBillboardCopyTimer = null;
let sessionPollTimer = null;
let sessionPollMs = 0;
let sessionPollInFlight = false;
let staffStateNeedsFull = true;
/** @type {any} */
let lastMcTally = null;
let lastMcBindKey = "";
/** C3 is text-only (no media). C2 CONS still uses text_ride. */
let textOnlyChallenge = "";
/** C2/C3 CONS ride (freeze + CONS-1…3). */
let textRideSlot = "";
let sessionPresentIds = new Set();
/** Unmatched guests currently present in the live session. */
let sessionGuests = [];
/** @type {Set<number>} */
let sessionLateIds = new Set();
let scoringLocked = false;
let trackMode = null;
let currentStep = "validate";
let draftRound = { kind: "open", minutes: 20, title: "" };
let nextDraftRound = { kind: "open", minutes: 10, title: "" };
let setupRoundNumber = 1;
let mediaPushTimer = 0;
/** Element that opened the rename dialog; focus returns here on close. */
let teamsRenameFocusEl = null;

const SEED_MEDIA_URL = "/static/live-media/m1c1-c1-real-slice.html";
const SEED_MEDIA_TITLE =
  "Consider the parabola represented by y = ax^2 + bx + c. What do you know about a, b, and c?";
const SEED_MEDIA_STEM = SEED_MEDIA_TITLE;
const MCR3U_M1C1_MEDIA_URL = "/static/live-media/mcr3u-m1c1-sqrt.html";
const MCR3U_M1C1_MEDIA_TITLE = "Nested Square-Root Range";
const MCR3U_M1C1_MEDIA_STEM =
  "Which inputs are allowed? What outputs can you actually get?";
const C2_TRANSFORM_MEDIA_URL = "/static/live-media/m1c2-transforms.html";
const C2_TRANSFORM_MEDIA_TITLE = "C2 Transformations";
let mediaSeedInFlight = false;

const ROUND_KIND_OPTIONS = [
  { kind: "open", label: "Open Question Round", defaultMin: 20 },
  { kind: "challenge", label: "Team Challenge", defaultMin: 10 },
  { kind: "formative", label: "Formative Question Round", defaultMin: 10 },
  { kind: "break", label: "Break", defaultMin: 5 },
];

/** Builtin Team Challenge look-fors (fallback when profiles API is unavailable). */
const TEAM_CHALLENGE_ACTIONS = [
  { id: "represented", label: "represented", amount: 1, lookfor_key: "represented" },
  { id: "connected", label: "connected", amount: 1, lookfor_key: "connected" },
  { id: "noticed_generalized", label: "noticed/generalized", amount: 1, lookfor_key: "noticed_generalized" },
  { id: "justified", label: "justified", amount: 1, lookfor_key: "justified" },
  { id: "checked_revised", label: "checked/revised", amount: 1, lookfor_key: "checked_revised" },
  { id: "transferred_extended", label: "transferred/extended", amount: 1, lookfor_key: "transferred_extended" },
  { id: "mathematical_language", label: "mathematical language", amount: 1, lookfor_key: "mathematical_language" },
];

const $ = (id) => document.getElementById(id);

/**
 * Ask the attendance grid to reload (when the attendance tab is open).
 */
function notifyAttendanceRefresh() {
  window.dispatchEvent(new CustomEvent("lloves-attendance-refresh"));
}

/**
 * Whether the game is in live scoring (locks prior setup actions).
 * @returns {boolean}
 */
function isScoringLive() {
  return scoringLocked || overlayState?.game?.status === "live";
}

const STAGE_BY_STEP = {
  validate: "join",
  att: "join",
  gamify: "teams",
  teams: "teams",
  names: "meet",
  rounds: "round",
  score: "play",
};

const TEACHER_STAGES = ["join", "teams", "meet", "round", "play", "round_3", "summary"];
const DEFAULT_LIVE_PAGES = [
  { id: "join", name: "Join", stage: "join" },
  { id: "welcome", name: "Welcome", stage: "teams" },
  { id: "meet", name: "Meet", stage: "meet" },
  { id: "round_1", name: "Round 1", stage: "round" },
  { id: "round_2", name: "Round 2", stage: "play" },
  { id: "round_3", name: "Round 3", stage: "round_3" },
  { id: "summary", name: "Summary", stage: "summary" },
];
const SETUP_STAGE = "set_class";
/** True on Set Class (date + module + slot) until the teacher confirms Next. */
let setupPhase = true;

/**
 * Home / remint Run Live Class (`?run=1`) always re-enters Set Class.
 * @returns {boolean}
 */
function wantsFreshSetClass() {
  return root?.dataset.run === "1";
}

/**
 * Drop leftover ``run=1`` after the live session is already class-set so a
 * hard refresh restores the current teacher view instead of reminting Set Class.
 */
function clearFreshSetClassFromUrl() {
  if (!root) return;
  root.dataset.run = "0";
  const url = new URL(window.location.href);
  if (!url.searchParams.has("run")) return;
  url.searchParams.delete("run");
  window.history.replaceState({}, "", url.toString());
}

/**
 * Whether date + module + live class were already confirmed for this session.
 * In-progress teams/rounds/live continue; leftover join shells do not.
 * @param {string} [gameStatus]
 * @param {{class_set?: boolean}|null} [teacher]
 * @returns {boolean}
 */
function classSetIsComplete(gameStatus, teacher) {
  const status = String(gameStatus || overlayState?.game?.status || "");
  if (["teams", "names", "rounds", "live"].includes(status)) return true;
  const state = teacher || teacherState;
  return Boolean(state?.class_set);
}

/**
 * Show Set Class and hide the join/attendance chrome.
 */
function enterSetClassPhase() {
  setupPhase = true;
  currentStep = "validate";
  lockClassListPane();
}

/**
 * Leave Set Class after date + module + slot are confirmed.
 */
function exitSetClassPhase() {
  setupPhase = false;
  teacherState.class_set = true;
  clearFreshSetClassFromUrl();
  lockClassListPane();
}

/**
 * Persist Set Class confirmation on the live session teacher state.
 * @returns {Promise<void>}
 */
async function markClassSetComplete() {
  teacherState.class_set = true;
  const module = $("live-module-select")?.value || teacherState.live_module || "M1";
  const slot = $("live-class-select")?.value || teacherState.live_slot || "C1";
  teacherState.live_module = String(module).toUpperCase();
  teacherState.live_slot = String(slot).toUpperCase();
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) return;
  await patchTeacherState(
    {
      class_set: true,
      live_module: teacherState.live_module,
      live_slot: teacherState.live_slot,
    },
    { silent: true }
  );
}
const LAYOUT_PRESETS = {
  media_full: { A: "media" },
  questions_full: { A: "questions" },
  canvas_full: { A: "canvas" },
  slides_full: { A: "slides" },
};

const FLAG_BY_STAGE = {
  join: "question",
  teams: "question",
  meet: "question",
  round: "question",
  play: "question",
  round_3: "question",
  summary: "question",
  challenge: "question",
  freeze: "score",
};

const REACHED_STAGES = new Set(["join"]);

/** @type {{stage: string, round?: string|null, round_flags?: {minds_on: boolean, action: boolean, consolidation: boolean}, teams_mode: string, groups_configured: boolean, run_as_group: boolean, scoreboard_visible: boolean, hide_absent: boolean, layout_preset: string, frames: Record<string, string>, active_tab: string, active_media_ref?: string|null, prompt_ref?: string|null, canvas_ephemeral: true, updated_at?: string, cue_id?: string|null, meet_chain?: any, state_seq?: number, student_frames?: Record<string, boolean>, unlocks?: Record<string, boolean>, mc_ui?: {prompt_ref: string, reveal: boolean, reveal_to_students?: boolean, poll_closed?: boolean}}} */
let teacherState = {
  stage: "join",
  round: null,
  round_flags: { minds_on: false, action: false, consolidation: false },
  teams_mode: "individual",
  groups_configured: false,
  run_as_group: false,
  scoreboard_visible: false,
  hide_absent: false,
  layout_preset: "questions_full",
  frames: { A: "questions" },
  active_tab: "questions",
  active_media_ref: null,
  prompt_ref: "minds_on",
  canvas_ephemeral: true,
  updated_at: "",
  cue_id: null,
  meet_chain: null,
  state_seq: 0,
  student_frames: { questions: true, media: false, canvas: false, slides: false },
  unlocks: { media: false, canvas: false, slides: false },
  student_view: { questions: "student", media: "none", canvas: "none", slides: "none" },
  question_views: {},
  canvas_align: "student",
  live_slot: "C1",
  live_module: "M1",
  class_set: false,
  page_id: "",
  text_ride: { frozen: false, cons_item: "", toast: "", toast_key: "" },
};

/** @type {any} */
let lastTeamsSpark = null;
let lastActivePrompt = null;
/** @type {any} */
let lastJoinPrompt = null;
/** @type {any[]} */
let lastQuestionCards = [];
/** @type {any} */
let lastLiveMetadata = null;
/** @type {any[]} */
let lastLiveItems = [];
/** Lifecycle id → Save to card the teacher just set. Stale polls must not flip it. */
const saveToCardHold = new Map();
/** @type {any[]} */
let lastActiveQuestions = [];
/** @type {any[]} */
let lastClassList = [];
/** Full roster so Hide Absent can toggle without waiting for a refetch. */
/** @type {any[]} */
let lastClassListFull = [];
/** @type {any[]} */
let lastGroups = [];
/** @type {any} */
let lastScoreboard = null;
/** @type {Map<number, any>} */
const lifecycleResults = new Map();

/**
 * True when a lifecycle row or its last results payload is a rank question.
 * @param {any} row
 * @param {any} prev
 * @returns {boolean}
 */
function lifecycleRowIsRank(row, prev) {
  const nested = row && row.item && typeof row.item === "object" ? row.item : {};
  const type = String(
    nested.type || nested.kind || row?.type || row?.kind || ""
  ).toLowerCase();
  if (type === "rank") return true;
  if (prev && prev.rank) return true;
  return String(prev?.tally?.kind || "") === "rank";
}

/**
 * Merge server-side per-item response counts into lifecycleResults.
 * Individual-in-Group counts come from private votes. When that count
 * moves, or the team feed is still missing, reload member answers.
 * Rank (group and individual) refetches the same way when the count or
 * the rank revision moves, so team rows and class order update on the
 * light poll. Class order itself stays on the ~1s hold in rankCollateHtml.
 * @param {Record<string, number>|null|undefined} counts
 * @param {Record<string, string>|null|undefined} [rankRevs]
 */
function adoptLifecycleResponseCounts(counts, rankRevs) {
  if (!counts || typeof counts !== "object") return;
  const revs = rankRevs && typeof rankRevs === "object" ? rankRevs : {};
  let refreshGroups = false;
  for (const [id, count] of Object.entries(counts)) {
    const liveItemId = Number(id);
    if (!liveItemId) continue;
    const n = Number(count) || 0;
    const prev = lifecycleResults.get(liveItemId) || {};
    const seen = Object.prototype.hasOwnProperty.call(prev, "response_count");
    const previous = Number(prev.response_count) || 0;
    const rev = Object.prototype.hasOwnProperty.call(revs, id)
      ? String(revs[id] ?? "")
      : null;
    const prevRev = prev.rank_rev == null ? null : String(prev.rank_rev);
    lifecycleResults.set(liveItemId, {
      ...prev,
      response_count: n,
      ...(rev == null ? {} : { rank_rev: rev }),
      tally: {
        ...(prev.tally && typeof prev.tally === "object" ? prev.tally : {}),
        responded: n,
        response_count: n,
      },
    });
    const row = lastLiveItems.find((item) => Number(item?.id) === liveItemId);
    if (lifecycleRowIsRank(row, prev)) {
      const missingCollate = !prev.rank && String(prev?.tally?.kind || "") !== "rank";
      const revChanged = rev != null && rev !== (prevRev ?? "");
      if (missingCollate || (seen && previous !== n) || revChanged) refreshGroups = true;
      continue;
    }
    const groupMode =
      String(row?.response_mode || prev.response_mode || "") === "group_consensus";
    if (!groupMode) continue;
    const missingTeams = !Array.isArray(prev.teams);
    if (missingTeams || (seen && previous !== n)) refreshGroups = true;
  }
  if (refreshGroups) void refreshLifecycleResults();
}


let openResponsePromptId = 0;

let teacherStateInFlight = false;
let lastTeacherMediaSrc = "";
/** Last active-media blob so Question-tab paints survive calls without media. */
let lastActiveMedia = null;

/**
 * Map a setup step onto the StageRail id.
 * @param {string} [step]
 * @returns {string}
 */
function stageForStep(step = currentStep) {
  return STAGE_BY_STEP[step === "live" ? "score" : step] || "join";
}

/**
 * Scroll the flagged right panel into view on stacked layouts.
 * @param {HTMLElement|null} [focusEl]
 * @param {ScrollLogicalPosition} [block]
 */
function scrollActiveTrackStepIntoView(focusEl = null, block = "start") {
  const flagged =
    document.querySelector(".live-flag-panel.is-flagged") ||
    document.querySelector(`.ap-panel[data-step="${currentStep}"]`);
  const target = focusEl instanceof HTMLElement ? focusEl : flagged;
  if (!(target instanceof HTMLElement) || target.hidden) return;
  const run = () => {
    try {
      target.scrollIntoView({ behavior: "smooth", block, inline: "nearest" });
    } catch (_) {
      target.scrollIntoView(true);
    }
  };
  requestAnimationFrame(() => requestAnimationFrame(run));
}

/**
 * Apply a server/client teacher-state object without remounting media.
 * @param {any} next
 */
function adoptTeacherState(next) {
  if (!next || typeof next !== "object") return;
  const prevSeq = Number(teacherState.state_seq);
  teacherState = {
    ...teacherState,
    ...next,
    frames: next.frames && typeof next.frames === "object" ? { ...next.frames } : teacherState.frames,
    canvas_ephemeral: true,
    class_set: Boolean(next.class_set ?? teacherState.class_set),
  };
  if (next.mc_ui && typeof next.mc_ui === "object") {
    teacherState.mc_ui = { ...next.mc_ui };
  } else {
    delete teacherState.mc_ui;
  }
  if (next.text_ride && typeof next.text_ride === "object") {
    teacherState.text_ride = { ...next.text_ride };
  }
  if (next.round_flags && typeof next.round_flags === "object") {
    teacherState.round_flags = { ...next.round_flags };
  }
  const slot = String(next.live_slot || teacherState.live_slot || "C1").toUpperCase();
  teacherState.live_slot = slot;
  teacherState.live_module = String(
    next.live_module || teacherState.live_module || "M1"
  ).toUpperCase();
  paintLiveLessonBadge();
  textRideSlot = slot === "C2" || slot === "C3" ? slot : "";
  textOnlyChallenge = isTextOnlyLiveSlot(slot) ? slot : "";
  trackMode = teacherState.run_as_group ? "team" : "individual";
  REACHED_STAGES.add(teacherState.stage);
  if (Number(teacherState.state_seq) !== prevSeq) {
    paintTeacherShell();
    paintResultsStrip();
    return;
  }
  paintMcResultsSlot();
}

/**
 * Mark StageRail from thin JSON stage (not the wizard step).
 */
/**
 * Named pages for the current live-lesson file, or the math default.
 * @returns {{id: string, name: string, stage: string, page_number?: number}[]}
 */
function liveLessonPages() {
  const rows = lastLiveMetadata?.pages;
  if (Array.isArray(rows) && rows.length) {
    return rows
      .filter((row) => row && row.stage)
      .map((row) => {
        const stored = Number(row.page_number);
        return {
          id: String(row.id || row.stage),
          name: String(row.name || row.stage),
          stage: String(row.stage),
          page_number: stored > 0 ? stored : undefined,
        };
      });
  }
  return DEFAULT_LIVE_PAGES;
}

/**
 * Index of the teacher's current named page in ``liveLessonPages()``.
 * @returns {number}
 */
/**
 * Named page row for the teacher's current rail position.
 * @returns {{id: string, name: string, stage: string, page_number?: number}|null}
 */
function currentLivePageRow() {
  const pages = liveLessonPages();
  const index = currentLivePageIndex();
  return index >= 0 ? pages[index] : null;
}

/**
 * True when the teacher is on a class-added overlay page with no authored questions.
 * @returns {boolean}
 */
function isBlankOverlayLivePage() {
  const page = currentLivePageRow();
  const token = String(page?.id || teacherState.page_id || "").trim();
  return token.startsWith("custom-");
}

function currentLivePageIndex() {
  const pages = liveLessonPages();
  const pageId = String(teacherState.page_id || "").trim();
  if (pageId) {
    const byId = pages.findIndex((row) => row.id === pageId);
    if (byId >= 0) return byId;
  }
  const stage = String(teacherState.stage || "round");
  return pages.findIndex((row) => row.stage === stage);
}

/**
 * Mark the page counter from thin JSON stage + page identity.
 */
function paintStageRail() {
  const stage = setupPhase ? SETUP_STAGE : teacherState.stage || stageForStep();
  if (!setupPhase) REACHED_STAGES.add(stage);
  const pages = liveLessonPages();
  const index = currentLivePageIndex();
  const counter = $("live-page-counter");
  const name = $("live-page-name");
  if (setupPhase) {
    if (counter) counter.textContent = "";
    if (name) name.textContent = "Set Class";
  } else {
    if (counter) {
      counter.textContent = index >= 0 ? `${index + 1} / ${pages.length}` : "";
    }
    if (name) name.textContent = index >= 0 ? pages[index].name : "";
  }
  const prev = $("live-stage-prev");
  const next = $("live-stage-next");
  if (prev instanceof HTMLButtonElement) prev.disabled = setupPhase;
  if (next instanceof HTMLButtonElement) {
    next.disabled = setupPhase ? false : index < 0 || index >= pages.length - 1;
  }
  const addBtn = $("live-add-page");
  const deleteBtn = $("live-delete-page");
  if (addBtn instanceof HTMLButtonElement) addBtn.disabled = setupPhase;
  if (deleteBtn instanceof HTMLButtonElement) {
    deleteBtn.disabled = setupPhase || pages.length <= 1;
  }
}

/**
 * Keep ClassListPane mounted and visible on every stage, including Prev.
 * Stage changes only swap OptionsStrip bodies and Active Content bindings.
 */
function lockClassListPane() {
  const stage = setupPhase ? SETUP_STAGE : teacherState.stage || stageForStep();
  if (root) {
    root.dataset.stage = stage;
    root.dataset.setup = setupPhase ? "1" : "0";
    root.classList.toggle("is-set-class", setupPhase);
  }
  const hideChrome = setupPhase;
  for (const id of [
    "live-shell-left",
    "session-timer",
    "class-list-pane",
    "live-shell-body",
    "live-shell-right",
    "live-option-card",
  ]) {
    const el = $(id);
    if (!(el instanceof HTMLElement)) continue;
    el.hidden = hideChrome;
    if (hideChrome) el.setAttribute("hidden", "");
    else {
      el.removeAttribute("hidden");
      el.classList.remove("hidden");
    }
  }
  const date = $("ap-panel-validate");
  if (date instanceof HTMLElement) {
    date.hidden = !setupPhase;
    date.classList.toggle("hidden", !setupPhase);
    if (setupPhase) date.classList.add("is-current");
  }
}

/**
 * True when an Options strip body still has a staff-facing control.
 * Hidden leftover wrappers (empty round/play cards) do not count.
 * @param {HTMLElement | null} card
 * @returns {boolean}
 */
function optionCardHasVisibleControls(card) {
  if (!(card instanceof HTMLElement)) return false;
  const controls = card.querySelectorAll("button, select, input, textarea, [role='group']");
  return [...controls].some((node) => {
    if (!(node instanceof HTMLElement)) return false;
    if (node.closest(".live-legacy-control")) return false;
    if (node.hidden || node.closest("[hidden]")) return false;
    return true;
  });
}

/**
 * Swap condensed OptionsStrip bodies. Never rebuild Left|Right chrome.
 * Same hidden-only swap for JOIN, TEAMS, MEET, ROUND, PLAY, and Prev.
 */
function paintOptionCard() {
  const stage = teacherState.stage;
  const card = $("live-option-card");
  const teams = $("teams-option-card");
  const meet = $("meet-option-card");
  const round = $("round-option-card");
  const play = $("play-option-card");
  const teamPane = $("team-assign-pane");
  const rounds = $("round-slide-settings");
  lockClassListPane();
  if (card) {
    card.hidden = false;
    card.removeAttribute("hidden");
  }
  if (teams) {
    const configured = Boolean(teacherState.groups_configured);
    teams.hidden = configured;
    if (configured) teams.setAttribute("hidden", "");
    else teams.removeAttribute("hidden");
  }
  if (meet) meet.hidden = true;
  applySessionTimerStageDefaults();
  applySessionTimerUi();
  const timer = $("session-timer");
  const timerToggle = $("live-timer-toggle");
  const timerRunning =
    Boolean(overlayState?.game?.round_ends_at_ms) ||
    Boolean(overlayState?.game?.timer_paused);
  if (timer) timer.hidden = !timerRunning && !Boolean(timerToggle?.checked);
  if (round) {
    round.hidden = stage !== "round" || !optionCardHasVisibleControls(round);
  }
  if (play) {
    play.hidden = !["play", "round_3", "summary"].includes(stage) || !optionCardHasVisibleControls(play);
  }
  paintGlobalGroupControls();
  paintTeamsStripEnabled();
  if (rounds) {
    rounds.hidden = true;
    rounds.setAttribute("hidden", "");
  }
  paintRoundStrip();
  paintStudentViewControls();
  paintSurfacePublishing();
}

/**
 * Paint one-time setup controls or the persisted group toggles.
 */
function paintGlobalGroupControls() {
  const configured = Boolean(teacherState.groups_configured);
  const setup = $("live-groups-setup");
  const active = $("live-groups-configured");
  if (setup) setup.hidden = configured;
  if (active) active.hidden = !configured;
  const run = $("live-run-as-group");
  if (run instanceof HTMLInputElement) run.checked = Boolean(teacherState.run_as_group);
  const hideAbsent = $("live-hide-absent");
  if (hideAbsent instanceof HTMLInputElement) {
    hideAbsent.checked = Boolean(teacherState.hide_absent);
  }
  const scoreboard = $("ap-scoreboard-toggle");
  if (scoreboard instanceof HTMLInputElement) {
    scoreboard.checked = Boolean(teacherState.scoreboard_visible);
    scoreboard.disabled = false;
  }
  syncScoreboardPreview();
  const rename = $("ap-teams-rename");
  if (rename instanceof HTMLButtonElement) {
    rename.hidden = !configured;
    rename.disabled = !configured;
    rename.setAttribute("aria-disabled", configured ? "false" : "true");
  }
}

/**
 * Current student-view modes, falling back to stage defaults.
 * @returns {{media: string, canvas: string, slides: string, questions: string}}
 */
function currentStudentView() {
  const view = teacherState.student_view || {};
  const asMode = (raw, fallback) =>
    raw === "student" || raw === "team" || raw === "none" ? raw : fallback;
  const stage = String(teacherState.stage || "join");
  const questionsDefault = stage === "join" || stage === "meet" ? "student" : "none";
  return {
    media: asMode(view.media, "none"),
    canvas: asMode(view.canvas, "none"),
    slides: asMode(view.slides, "none"),
    questions: asMode(view.questions, questionsDefault),
  };
}

/**
 * Sync per-content Student View dropdowns.
 */
function paintStudentViewControls() {
  const view = currentStudentView();
  for (const key of ["media", "canvas", "slides"]) {
    const el = $(`live-view-${key}`);
    if (!(el instanceof HTMLSelectElement)) continue;
    const wanted = view[key] === "team" ? "team" : "student";
    if ([...el.options].some((option) => option.value === wanted)) {
      el.value = wanted;
    }
    const team = [...el.options].find((option) => option.value === "team");
    if (team) {
      team.disabled =
        !Boolean(teacherState.run_as_group) || !surfaceSupportsGroup(key);
      if (team.disabled && el.value === "team") el.value = "student";
    }
  }
}

/**
 * Return the lifecycle row for one content surface on the current stage.
 * Canvas remains the compatibility API id for visible Whiteboard controls.
 * @param {"media"|"canvas"|"slides"} surface
 * @returns {any|null}
 */
function lifecycleItemForSurface(surface) {
  const itemType = surface === "canvas" ? "whiteboard" : surface;
  const stage = String(teacherState.stage || "");
  const matches = lastLiveItems.filter((row) => {
    const item = row?.item || {};
    return String(item.item_type || row.kind || "").toLowerCase() === itemType;
  });
  return (
    matches.find((row) => String(row.stage || "") === stage) || matches[0] || null
  );
}

/**
 * True when metadata explicitly allows shared group publication.
 * Whiteboard keeps its catalogue-level compatibility capability when it has
 * no placement row in an older playlist.
 * @param {"media"|"canvas"|"slides"} surface
 * @returns {boolean}
 */
function surfaceSupportsGroup(surface) {
  const item = lifecycleItemForSurface(surface);
  if (!item) return surface === "canvas";
  const modes = item.item?.publish_modes || item.item?.capabilities?.publish_modes || [];
  return Array.isArray(modes) && modes.includes("group_shared");
}

/**
 * Paint Active/Inactive/Closed language for Media, Whiteboard, and Slides.
 */
function paintSurfacePublishing() {
  const view = currentStudentView();
  for (const surface of ["media", "canvas", "slides"]) {
    const item = lifecycleItemForSurface(surface);
    const fallbackActive = view[surface] !== "none";
    const status = String(item?.status || (fallbackActive ? "active" : "inactive"));
    const label = document.querySelector(`[data-surface-status="${surface}"]`);
    if (label) {
      label.textContent = status.charAt(0).toUpperCase() + status.slice(1);
      label.className = `live-status-badge is-${status}`;
    }
    const publish = document.querySelector(`[data-surface-publish="${surface}"]`);
    const close = document.querySelector(`[data-surface-close="${surface}"]`);
    if (publish instanceof HTMLButtonElement) publish.disabled = status !== "inactive";
    if (close instanceof HTMLButtonElement) close.disabled = status !== "active";
  }
}

/**
 * Publish one content surface and project it to students.
 * @param {"media"|"canvas"|"slides"} surface
 */
async function publishSurface(surface) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) return;
  const select = $(`live-view-${surface}`);
  const selected =
    select instanceof HTMLSelectElement &&
    select.value === "team" &&
    teacherState.run_as_group &&
    surfaceSupportsGroup(surface)
      ? "team"
      : "student";
  const item = lifecycleItemForSurface(surface);
  if (item && item.status === "inactive") {
    const publishMode =
      surface === "canvas" && selected === "team" ? "group_shared" : "individual";
    const result = await api(
      `/api/live-sessions/${sessionId}/items/${Number(item.id)}/publish`,
      {
        method: "POST",
        body: JSON.stringify({ publish_mode: publishMode }),
      }
    );
    if (result?.item) {
      lastLiveItems = lastLiveItems.map((row) =>
        Number(row.id) === Number(result.item.id) ? result.item : row
      );
    }
  }
  if (surface === "media") {
    const file = String(item?.item?.file || "").trim();
    if (file) await postActiveMedia({ url: file });
  }
  const next = { ...(teacherState.student_view || {}), [surface]: selected };
  const hasActiveQuestions = lastLiveItems.some(
    (row) =>
      String(row?.item?.item_type || row?.kind || "") === "question" &&
      String(row.status || "") === "active"
  );
  if (hasActiveQuestions && next.questions === "none") {
    next.questions = "student";
  }
  teacherState.student_view = next;
  await patchTeacherState({ student_view: next });
  paintSurfacePublishing();
}

/**
 * Close one content surface and remove its student projection.
 * @param {"media"|"canvas"|"slides"} surface
 */
async function closeSurface(surface) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) return;
  const item = lifecycleItemForSurface(surface);
  if (item && item.status === "active") {
    const result = await api(
      `/api/live-sessions/${sessionId}/items/${Number(item.id)}/close`,
      { method: "POST", body: "{}" }
    );
    if (result?.item) {
      lastLiveItems = lastLiveItems.map((row) =>
        Number(row.id) === Number(result.item.id) ? result.item : row
      );
    }
  }
  if (surface === "media") await postActiveMedia({ clear: true });
  const next = { ...(teacherState.student_view || {}), [surface]: "none" };
  teacherState.student_view = next;
  await patchTeacherState({ student_view: next });
  paintSurfacePublishing();
}

/**
 * Map a teacher content tab to the single pane it owns.
 * @param {string} [tab]
 * @returns {"media"|"questions"|"canvas"|"slides"}
 */
function teacherPaneContent(tab) {
  const name = String(tab || teacherState.active_tab || "questions");
  if (name === "questions") return "questions";
  if (name === "canvas") return "canvas";
  if (name === "slides") return "slides";
  return "media";
}

/**
 * Named single-pane preset the teacher-state API already accepts.
 * @param {"media"|"questions"|"canvas"|"slides"} content
 * @returns {string}
 */
function teacherPanePreset(content) {
  if (content === "questions") return "questions_full";
  if (content === "canvas") return "canvas_full";
  if (content === "slides") return "slides_full";
  return "media_full";
}

/**
 * Show one teacher pane (media, questions, or canvas). Never split the body.
 * The media iframe stays mounted so the 3D slice is not recreated.
 */
function paintFrames() {
  const content = teacherPaneContent();
  const preset = teacherPanePreset(content) || "canvas_full";
  const host = $("live-frames");
  if (host) {
    host.dataset.preset = preset;
    host.dataset.slots = "A";
    host.dataset.pane = content;
  }
  document.querySelectorAll(".live-content-slot").forEach((el) => {
    const id = el.getAttribute("data-content-id") || "";
    const on = id === content;
    el.setAttribute("data-slot", on ? "A" : "");
    el.classList.toggle("is-parked", !on);
  });
  document.querySelectorAll("#live-preset-row [data-preset]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.getAttribute("data-preset") === preset);
  });
  document.querySelectorAll("#live-content-tabs [data-tab]").forEach((btn) => {
    const on = btn.getAttribute("data-tab") === teacherState.active_tab;
    btn.classList.toggle("is-active", on);
    if (on) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
}

/**
 * Paint the Slides pane from the selected live-class metadata.
 */
function paintSlidesMetadata() {
  const status = $("live-slides-status");
  const frame = $("live-slides-preview");
  const deckRef = String(lastLiveMetadata?.slides?.deck_ref || "").trim();
  if (status) {
    status.textContent = deckRef
      ? "Connected slide deck"
      : "No slide deck is connected to this live-class metadata yet.";
  }
  if (!(frame instanceof HTMLIFrameElement)) return;
  if (!deckRef || !/^https?:\/\//i.test(deckRef)) {
    frame.hidden = true;
    frame.removeAttribute("src");
    return;
  }
  frame.src = deckRef;
  frame.hidden = false;
}

/**
 * Flag Active Content; ResultsStrip stays inside TabQuestions only.
 * @param {string} [stage]
 */
function paintRightFlag(stage = teacherState.stage || stageForStep()) {
  const flag = FLAG_BY_STAGE[stage] || "question";
  const right = $("live-shell-right");
  if (right) right.dataset.flag = flag;
  paintResultsStrip();
}

/**
 * Show ResultsStrip when TabQuestions has scoring rows or a live MC tally.
 * MC LIVE/REVEAL is the primary slot; scoring stays a sibling, never a
 * second floating card.
 */
function paintResultsStrip() {
  const results = $("results-strip");
  if (!results) return;
  const questionZone = $("question-artifact-zone");
  const usingLifecycleCards = currentPageQuestionRows().length > 0;
  if (questionZone) {
    questionZone.classList.toggle("has-lifecycle-cards", usingLifecycleCards);
  }
  paintMcResultsSlot();
  if (usingLifecycleCards) {
    results.hidden = true;
    results.classList.remove("is-flagged");
    return;
  }
  const stage = teacherState.stage;
  const list = $("ap-score-list");
  const hasRows = Boolean(list && list.children.length);
  const showScore =
    stage !== "join" &&
    stage !== "teams" &&
    (hasRows || (stage === "play" && isScoringLive()));
  const scorePanel = $("ap-panel-score");
  if (scorePanel) scorePanel.hidden = !showScore;
  results.hidden = !showScore;
  results.classList.toggle("is-flagged", showScore);
}

/**
 * Bind key so tally paints update in place without remounting Questions.
 * @param {any} tally
 * @param {boolean} reveal
 * @returns {string}
 */
function mcBindKey(tally, reveal) {
  if (!tally || typeof tally !== "object") return "";
  return [
    tally.prompt_ref || "",
    tally.prompt_id || "",
    tally.response_seq ?? "",
    tally.response_count ?? "",
    reveal ? "1" : "0",
  ].join("|");
}

/**
 * True when staff Reveal is on for this tally's prompt_ref.
 * @param {any} [tally]
 * @returns {boolean}
 */
function mcRevealOn(tally = lastMcTally) {
  const ui = teacherState.mc_ui;
  if (!ui || !tally) return false;
  return Boolean(ui.reveal) && String(ui.prompt_ref || "") === String(tally.prompt_ref || "");
}

/**
 * True when lifecycle metadata supplies question cards for the current stage.
 * @returns {boolean}
 */
function currentStageHasLifecycleQuestionCards() {
  return currentPageQuestionRows().length > 0;
}

/**
 * Keep the leftover LIVE/REVEAL slot hidden. Results live on lifecycle cards.
 */
function paintMcResultsSlot() {
  const slot = $("mc-results-slot");
  const progress = $("mc-live-progress");
  const bars = $("mc-reveal-bars");
  if (!slot) return;
  slot.hidden = true;
  lastMcBindKey = "";
  if (progress) progress.textContent = "";
  if (bars) {
    bars.hidden = true;
    bars.innerHTML = "";
  }
}

/**
 * Adopt a staff /state mc_tally without remounting Active Content tabs.
 * @param {any} tally
 */
function applyMcTally(tally) {
  const ref = tally && typeof tally === "object" ? String(tally.prompt_ref || "") : "";
  if (!ref) {
    if (teacherState.stage !== "join" && teacherState.stage !== "teams") {
      lastMcTally = null;
      lastMcBindKey = "";
    }
    paintResultsStrip();
    syncLiveSessionPolling();
    return;
  }
  lastMcTally = tally && typeof tally === "object" && tally.prompt_ref ? tally : null;
  paintResultsStrip();
  syncLiveSessionPolling();
}

/**
 * Current MC prompt_ref for a reveal PATCH (source-agnostic).
 * @returns {string}
 */
function currentMcPromptRef() {
  if (String(teacherState.stage || "") === "teams") return "teams-spark";
  return String(lastMcTally?.prompt_ref || teacherState.prompt_ref || "minds_on");
}

/**
 * PATCH mc_ui only. Never sends a Wonder cue_id.
 * JOIN Reveal shares the class summary and closes the poll.
 * @param {boolean} reveal
 */
function patchMcReveal(reveal) {
  const joinShare = Boolean(reveal) && String(teacherState.stage || "") === "join";
  const sparkShare = Boolean(reveal) && String(teacherState.stage || "") === "teams";
  const alreadyClosed = Boolean(teacherState.mc_ui && teacherState.mc_ui.poll_closed);
  patchTeacherState({
    mc_ui: {
      prompt_ref: currentMcPromptRef(),
      reveal: Boolean(reveal),
      reveal_to_students: joinShare || sparkShare,
      poll_closed: joinShare || alreadyClosed,
    },
  });
}

/**
 * Paint rail, OptionsStrip, and CSS slots from teacherState.
 * Prev/Next and every stage keep Left ClassListPane mounted.
 */
function paintTeacherShell() {
  lockClassListPane();
  paintLiveLessonBadge();
  paintStageRail();
  paintOptionCard();
  paintFrames();
  paintRightFlag();
  paintMeetChainChrome();
  paintLiveSlotPicks();
  renderAttendanceList();
}

/**
 * Mirror the meeting date into the persistent header.
 */
function paintHeaderDate() {
  const el = $("live-header-date");
  if (!el) return;
  const iso = String($("ap-meeting-date")?.value || overlayState?.session?.meeting_date || "").trim();
  el.textContent = iso || "Date —";
}

/**
 * Flag Meet universal Q / CONS / QH from existing session fields (no new poll).
 * @param {any} [media]
 */
function slotsByModule() {
  const host = $("live-pack-strip");
  if (!host) return { M1: ["C1", "C2", "C3"] };
  try {
    const raw = JSON.parse(host.getAttribute("data-slots-by-module") || "{}");
    if (raw && typeof raw === "object") return raw;
  } catch (_) {
    /* keep default */
  }
  return { M1: ["C1", "C2", "C3"] };
}

/**
 * Fill the Live class select for the current catalogue module.
 * @param {string} [moduleId]
 */
function paintLiveClassOptions(moduleId) {
  const select = $("live-class-select");
  if (!select) return;
  const module = String(moduleId || teacherState.live_module || "M1").toUpperCase();
  const slots = slotsByModule()[module] || slotsByModule().M1 || ["C1", "C2", "C3"];
  const current = String(teacherState.live_slot || select.value || "C1").toUpperCase();
  select.innerHTML = slots
    .map(
      (slot) =>
        `<option value="${slot}"${slot === current ? " selected" : ""}>${slot}</option>`
    )
    .join("");
  if (![...select.options].some((opt) => opt.value === current) && slots[0]) {
    select.value = slots[0];
  }
}

/**
 * Sync Module + Live class dropdowns with teacher state.
 */
function paintLivePackStrip() {
  const moduleSelect = $("live-module-select");
  const classSelect = $("live-class-select");
  const module = String(teacherState.live_module || "M1").toUpperCase();
  if (moduleSelect && moduleSelect.value !== module) {
    moduleSelect.value = module;
  }
  paintLiveClassOptions(module);
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  if (classSelect && classSelect.value !== slot) {
    classSelect.value = slot;
  }
}

function paintLiveSlotPicks() {
  const slot = String(teacherState.live_slot || textRideSlot || textOnlyChallenge || "C1").toUpperCase();
  document.querySelectorAll("#live-slot-picks [data-live-slot]").forEach((btn) => {
    const on = btn.getAttribute("data-live-slot") === slot;
    btn.classList.toggle("is-active", on);
  });
  paintLivePackStrip();
  const textOnly = isTextOnlyLiveSlot(slot);
  textRideSlot = slot === "C2" || slot === "C3" ? slot : "";
  textOnlyChallenge = textOnly ? slot : "";
  const rideBox = $("text-ride-controls");
  if (rideBox) rideBox.hidden = !(slot === "C2" || slot === "C3");
  const preview = $("ap-media-preview");
  if (preview) {
    const hideMedia = textOnly || (!liveClassSeedMedia() && !lastTeacherMediaSrc);
    preview.hidden = hideMedia;
    if (hideMedia) {
      preview.setAttribute("hidden", "");
      if (!liveClassSeedMedia() && !lastTeacherMediaSrc) {
        preview.removeAttribute("src");
      }
    } else {
      preview.removeAttribute("hidden");
    }
  }
}

/**
 * Paint the integer warm-up on the Question frame.
 * @param {any} [card]
 * @param {{keepQuestionBody?: boolean}} [opts] When true, leave Q1 stem in place (Join).
 */
function paintTeamsSparkCard(card, opts) {
  const options = opts && typeof opts === "object" ? opts : {};
  const keepQuestionBody = Boolean(options.keepQuestionBody);
  const root = $("teams-spark-card");
  const promptEl = $("teams-spark-prompt");
  const keyEl = $("teams-spark-key");
  const choicesEl = $("teams-spark-choices");
  const labelEl = root && root.querySelector(".teams-spark-label");
  const revealBtn = $("teams-spark-reveal");
  const status = $("question-artifact-status");
  const flag = $("question-artifact-flag");
  if (!root || !promptEl) return;
  const row = card && typeof card === "object" ? card : lastTeamsSpark || {};
  const stem =
    row.prompt || "Type the integer you think most students will answer";
  promptEl.textContent = String(stem);
  if (labelEl) {
    labelEl.textContent = keepQuestionBody ? "Waiting room · 2" : "Shared spark";
  }
  if (choicesEl) {
    choicesEl.hidden = true;
    choicesEl.textContent = "";
  }
  if (keyEl) {
    keyEl.hidden = true;
    keyEl.textContent = "";
  }
  root.hidden = false;
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
  if (flag && !keepQuestionBody) {
    flag.hidden = false;
    flag.textContent = "Welcome · C2";
  }
  if (!keepQuestionBody) {
    paintLiveQuestionBody(stem, []);
  }
  if (revealBtn) revealBtn.hidden = true;
}

/**
 * Hide the TEAMS spark card when Question is on another ride.
 */
function hideTeamsSparkCard() {
  const root = $("teams-spark-card");
  if (root) root.hidden = true;
}

/**
 * Paint stem + choices into the teacher Question tab body.
 * @param {string} stem
 * @param {string[]} [choices]
 */
function paintLiveQuestionBody(stem, choices) {
  const body = $("live-question-body");
  const stemEl = $("live-question-stem");
  const choicesEl = $("live-question-choices");
  const text = String(stem || "").trim();
  if (stemEl) {
    stemEl.innerHTML = formatQuestionHtml(text);
    void renderLiveQuestionMath(stemEl);
  }
  if (choicesEl) {
    const labels = Array.isArray(choices) ? choices.filter(Boolean) : [];
    choicesEl.textContent = labels.length ? labels.join(" · ") : "";
    choicesEl.hidden = !labels.length;
  }
  if (body) body.hidden = !text;
}

/**
 * Hide the teacher Question-tab stem block.
 */
function hideLiveQuestionBody() {
  const body = $("live-question-body");
  if (body) body.hidden = true;
}

/**
 * Derive the question-binding page number for the current named page.
 * Overlay pages keep a unique stored ``page_number`` so authored bindings
 * do not shift when a page is inserted in the middle of the deck.
 * @returns {number}
 */
function currentLivePageNumber() {
  const pages = liveLessonPages();
  const index = currentLivePageIndex();
  if (index < 0) return 1;
  const stored = Number(pages[index].page_number);
  return stored > 0 ? stored : index + 1;
}

/**
 * Apply add/delete page API payload to local deck and teacher caches.
 * @param {any} payload
 */
function applyLivePageDeckPayload(payload) {
  if (payload?.live_metadata) {
    lastLiveMetadata = payload.live_metadata;
  }
  if (payload?.teacher_state) {
    adoptTeacherState(payload.teacher_state);
  } else if (payload?.land_page && typeof payload.land_page === "object") {
    teacherState = {
      ...teacherState,
      stage: String(payload.land_page.stage || teacherState.stage || "play"),
      page_id: String(payload.land_page.id || ""),
    };
  }
  paintStageRail();
  paintLiveQuestionCards();
  paintQuestionArtifact();
  if (liveClassSeedMedia()) paintActiveMediaStatus(null);
}

/**
 * Show the page-name field only for a blank overlay page.
 */
function syncAddLivePageKindFields() {
  const selected = document.querySelector('input[name="live-add-page-kind"]:checked');
  const kind = String(selected?.value || "blank").toLowerCase();
  const named = kind === "blank";
  const input = $("live-add-page-name");
  const label = $("live-add-page-name-label");
  if (input instanceof HTMLInputElement) {
    input.hidden = !named;
    input.disabled = !named;
    input.required = named;
    if (!named) input.value = "";
  }
  if (label instanceof HTMLElement) {
    label.hidden = !named;
  }
}

/**
 * Prompt for a page kind (and name when blank), then insert after the current page.
 * @returns {Promise<void>}
 */
async function addLiveLessonPage() {
  if (setupPhase) return;
  const dialog = $("live-add-page-dialog");
  const input = $("live-add-page-name");
  const err = $("live-add-page-error");
  const blank = document.querySelector(
    'input[name="live-add-page-kind"][value="blank"]'
  );
  if (err instanceof HTMLElement) {
    err.hidden = true;
    err.textContent = "";
  }
  if (blank instanceof HTMLInputElement) blank.checked = true;
  if (input instanceof HTMLInputElement) {
    input.value = "";
  }
  syncAddLivePageKindFields();
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
  input?.focus();
}

/**
 * Close the add-page dialog without saving.
 */
function closeAddLivePageDialog() {
  const dialog = $("live-add-page-dialog");
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
}

/**
 * POST a named overlay page after the current page and refresh the rail.
 * @param {string} name
 * @param {string} [kind]
 * @returns {Promise<void>}
 */
async function submitAddLiveLessonPage(name, kind) {
  const pageKind = String(kind || "blank").trim().toLowerCase() || "blank";
  const title = String(name || "").trim();
  if (pageKind === "blank" && !title) throw new Error("Page name required.");
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  if (!classId) throw new Error("Class is not loaded.");
  const pages = liveLessonPages();
  const index = currentLivePageIndex();
  const afterId = index >= 0 ? pages[index].id : pages[0]?.id || "";
  const payload = await api(
    `/api/staff/class/${classId}/live-lessons/${module}/${slot}/pages`,
    {
      method: "POST",
      body: JSON.stringify({
        name: title,
        after_page_id: afterId,
        kind: pageKind,
      }),
    }
  );
  applyLivePageDeckPayload(payload);
  const land = payload?.land_page;
  if (land && !payload?.teacher_state) {
    await patchTeacherState({
      stage: String(land.stage || "play"),
      page_id: String(land.id || ""),
    });
  }
  staffStateNeedsFull = true;
  const sessionId = liveSessionId || readLiveSessionId();
  if (sessionId) {
    await pollLiveSessionAttendees({ full: true, force: true });
  }
}

/**
 * Confirm deletion of the current page, then remove it from the overlay.
 * @returns {Promise<void>}
 */
async function deleteLiveLessonPage() {
  if (setupPhase) return;
  const pages = liveLessonPages();
  if (pages.length <= 1) {
    showError("#ap-overlay-error", new Error("cannot delete the last remaining page"));
    return;
  }
  const index = currentLivePageIndex();
  const page = index >= 0 ? pages[index] : null;
  if (!page) return;
  const dialog = $("live-delete-page-dialog");
  const message = $("live-delete-page-message");
  const err = $("live-delete-page-error");
  if (err instanceof HTMLElement) {
    err.hidden = true;
    err.textContent = "";
  }
  if (message instanceof HTMLElement) {
    message.textContent = `Delete “${page.name}” from this live class?`;
  }
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
}

/**
 * Close the delete-page confirmation dialog.
 */
function closeDeleteLivePageDialog() {
  const dialog = $("live-delete-page-dialog");
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
}

/**
 * DELETE the current named page and land on the next (or previous) page.
 * @returns {Promise<void>}
 */
async function confirmDeleteLiveLessonPage() {
  const pages = liveLessonPages();
  const index = currentLivePageIndex();
  const page = index >= 0 ? pages[index] : null;
  if (!page) throw new Error("No page to delete.");
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  if (!classId) throw new Error("Class is not loaded.");
  const payload = await api(
    `/api/staff/class/${classId}/live-lessons/${module}/${slot}/pages/${encodeURIComponent(page.id)}`,
    { method: "DELETE" }
  );
  applyLivePageDeckPayload(payload);
  const land = payload?.land_page;
  if (land && !payload?.teacher_state) {
    await patchTeacherState({
      stage: String(land.stage || teacherState.stage || "play"),
      page_id: String(land.id || ""),
    });
  }
  staffStateNeedsFull = true;
  const sessionId = liveSessionId || readLiveSessionId();
  if (sessionId) {
    await pollLiveSessionAttendees({ full: true, force: true });
  }
}

/**
 * Step one named lesson page forward or back using the merged page list.
 * @param {number} delta
 */
function advanceLivePage(delta) {
  if (setupPhase && Number(delta) > 0) {
    applyValidateDateChoice();
    return;
  }
  const pages = liveLessonPages();
  const index = currentLivePageIndex();
  if (index < 0) {
    patchTeacherState({ advance: Number(delta) > 0 ? "next" : "prev" });
    return;
  }
  const nextIndex = Math.max(0, Math.min(pages.length - 1, index + Number(delta)));
  const page = pages[nextIndex];
  if (!page || nextIndex === index) return;
  patchTeacherState({ stage: page.stage, page_id: page.id });
}

/**
 * POST one module-bank MC import and refresh lifecycle cards.
 * @param {Record<string, unknown>} item
 */
/**
 * Pull merged lesson-deck metadata (seed + bank imports) for the active module/slot.
 */
async function refreshLessonDeckMetadata() {
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  if (!classId) return;
  const payload = await api(
    `/api/staff/class/${classId}/live-lessons/${module}/${slot}/deck?fresh=1`
  );
  if (payload?.live_metadata) {
    lastLiveMetadata = payload.live_metadata;
  }
}

/**
 * Apply import-mc API payload to local deck/lifecycle caches.
 * @param {any} payload
 */
function applyLiveMcImportPayload(payload) {
  if (payload?.live_metadata) {
    lastLiveMetadata = payload.live_metadata;
  }
  if (Array.isArray(payload?.live_items)) {
    lastLiveItems = absorbSaveToCardSnapshot(payload.live_items);
  }
  if (Array.isArray(payload?.question_cards)) {
    lastQuestionCards = questionCardsFromMetadata(payload.question_cards);
  }
}

async function importLiveMcFromBank(item) {
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  const questionId = Number(item.question_id || 0);
  if (!classId || !questionId) {
    throw new Error("Choose a question to import.");
  }
  const payload = await api(
    `/api/staff/class/${classId}/live-lessons/${module}/${slot}/import-mc`,
    {
      method: "POST",
      body: JSON.stringify({
        question_id: questionId,
        page_number: currentLivePageNumber(),
        stage: String(teacherState.stage || "round"),
      }),
    }
  );
  applyLiveMcImportPayload(payload);
  const placementItem = payload?.placement?.item;
  if (placementItem && typeof placementItem === "object") {
    const meta = lastLiveMetadata && typeof lastLiveMetadata === "object"
      ? { ...lastLiveMetadata }
      : { questions: [], items: [] };
    const questions = Array.isArray(meta.questions) ? [...meta.questions] : [];
    const items = Array.isArray(meta.items) ? [...meta.items] : [];
    const token = String(placementItem.id || payload.placement?.item_id || "").trim();
    if (token && !questions.some((row) => String(row?.id || "") === token)) {
      questions.push({ ...placementItem, id: token, item_type: "question" });
      items.push({ ...placementItem, id: token, item_type: "question" });
      meta.questions = questions;
      meta.items = items;
      lastLiveMetadata = meta;
    }
  }
  await refreshLessonDeckMetadata();
  paintLiveQuestionCards();
  paintQuestionArtifact();
  staffStateNeedsFull = true;
  const sessionId = liveSessionId || readLiveSessionId();
  if (sessionId) {
    await pollLiveSessionAttendees({ full: true, force: true });
    paintLiveQuestionCards();
    paintQuestionArtifact();
  }
}

/**
 * Open the shared module-bank picker in import mode for the active lesson.
 */
function openLiveMcImportPicker() {
  if (!classId) return;
  refreshLessonDeckMetadata()
    .catch(() => {})
    .finally(() => {
      import("/static/bank_mc_picker.js")
        .then(({ openBankMcPicker }) =>
          openBankMcPicker({
            classId,
            moduleNumber: String(teacherState.live_module || "M1").toUpperCase(),
            mode: "import",
            onSelect: (item) => importLiveMcFromBank(item),
          })
        )
        .catch((err) => showError("#ap-overlay-error", err));
    });
}

/**
 * Build HTML options for moving a question onto any current deck page.
 * Option values are 1-based rail indexes into ``liveLessonPages()``.
 * The current page is included and selected so staff see the full list.
 * @param {number} currentPageIndex
 * @returns {string}
 */
function playlistMovePageOptions(currentPageIndex) {
  const pages = liveLessonPages();
  return pages
    .map((page, index) => {
      const pageIndex = index + 1;
      const name = String(page.name || page.stage || `Page ${pageIndex}`);
      const current = pageIndex === Number(currentPageIndex);
      const selected = current ? " selected" : "";
      return `<option value="${pageIndex}"${selected}>${escapeHtml(
        `${pageIndex}. ${name}`
      )}</option>`;
    })
    .join("");
}

/**
 * Pull a fresh question-card snapshot from the active live session.
 */
async function refreshLiveQuestionCards() {
  const id = liveSessionId || readLiveSessionId();
  if (!id) return;
  try {
    const snapshot = await api(`/api/live-sessions/${id}/state`);
    if (snapshot?.error === "state unavailable") {
      setLiveReconnectBanner(true);
      return;
    }
    if (snapshot?.live_metadata) {
      lastLiveMetadata = snapshot.live_metadata;
    }
    if (Array.isArray(snapshot?.live_items)) {
      lastLiveItems = absorbSaveToCardSnapshot(snapshot.live_items);
    }
    if (Array.isArray(snapshot?.question_cards)) {
      lastQuestionCards = questionCardsFromMetadata(snapshot.question_cards);
    }
    if (Array.isArray(snapshot?.active_questions)) {
      lastActiveQuestions = snapshot.active_questions;
    }
    paintLiveQuestionCards();
    setLiveReconnectBanner(false);
  } catch (_) {
    setLiveReconnectBanner(true);
  }
}

/**
 * Build staff cards from merged deck metadata so dest-page items survive
 * a current-page-only ``/state`` snapshot after a move.
 * @param {any[]} [serverCards]
 * @returns {any[]}
 */
function questionCardsFromMetadata(serverCards) {
  const incoming = Array.isArray(serverCards) ? serverCards : [];
  const metaQs =
    metadataMatchesCurrentPack() && Array.isArray(lastLiveMetadata?.questions)
      ? lastLiveMetadata.questions
      : [];
  if (!metaQs.length) return incoming;
  const byId = new Map();
  for (const question of metaQs) {
    if (!question || typeof question !== "object") continue;
    if (question.removed || question.hidden) continue;
    const kind = String(question.item_type || question.kind || question.type || "question")
      .trim()
      .toLowerCase();
    if (kind in { media: 1, whiteboard: 1, slides: 1 }) continue;
    const token = String(question.id || question.item_id || "").trim();
    if (!token) continue;
    const server = incoming.find(
      (row) => String(row?.id || row?.item_id || "").trim() === token
    );
    byId.set(token, {
      ...(server || {}),
      ...question,
      id: token,
      item_id: token,
      stage: question.stage || server?.stage,
      page_number: question.page_number ?? server?.page_number,
      text: question.text || question.prompt || server?.text,
      options: question.options || server?.options || [],
    });
  }
  for (const card of incoming) {
    const token = String(card?.id || card?.item_id || "").trim();
    if (token && !byId.has(token)) byId.set(token, card);
  }
  return [...byId.values()];
}

/**
 * Apply an immediate local card list change while the server snapshot catches up.
 * @param {string} itemId
 * @param {"remove"|"move"} mode
 */
function applyLocalPlaylistCardChange(itemId) {
  const token = String(itemId || "").trim();
  if (!token) return;
  lastQuestionCards = (Array.isArray(lastQuestionCards) ? lastQuestionCards : []).filter(
    (row) => String(row?.id || row?.item_id || "") !== token
  );
}

/**
 * PATCH one playlist item remove/move and refresh teacher question cards.
 * @param {string} itemId
 * @param {{action?: string, targetPageIndex?: number}} payload
 */
async function relocatePlaylistItem(itemId, payload) {
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  const token = String(itemId || "").trim();
  if (!classId) throw new Error("Class is not loaded.");
  if (!token) throw new Error("Question id is missing.");
  const result = await api(
    `/api/staff/class/${classId}/live-lessons/${module}/${slot}/playlist-item`,
    {
      method: "POST",
      body: JSON.stringify({
        item_id: token,
        action: payload.action,
        target_page_index: payload.targetPageIndex,
      }),
    }
  );
  applyLiveMcImportPayload(result);
  if (!result?.live_metadata) {
    applyLocalPlaylistCardChange(token);
  }
  await refreshLessonDeckMetadata();
  lastQuestionCards = questionCardsFromMetadata(
    Array.isArray(result?.question_cards) ? result.question_cards : lastQuestionCards
  );
  paintLiveQuestionCards();
  paintQuestionArtifact();
  staffStateNeedsFull = true;
  try {
    await refreshLiveQuestionCards();
  } catch {
    await pollLiveSessionAttendees({ full: true, force: true });
  }
}

/** @type {{ itemId: string, label: string }} */
let pendingRelocate = { itemId: "", label: "" };

/**
 * Return the display label for one destination page index.
 * @param {number} pageIndex
 * @returns {string}
 */
function playlistPageLabel(pageIndex) {
  const pages = liveLessonPages();
  const page = pages[Number(pageIndex) - 1];
  const name = page
    ? String(page.name || page.stage || `Page ${pageIndex}`)
    : `Page ${pageIndex}`;
  return `${pageIndex}. ${name}`;
}

/**
 * True when the item is a built-in engine ride (waiting-room, spark, Meet, challenge).
 * Used for badges and labels; does not block (Re)move.
 * @param {string} itemId
 * @returns {boolean}
 */
function isEngineRideItemId(itemId) {
  const token = String(itemId || "").trim().toLowerCase().replace(/-/g, "_");
  return (
    token === "minds_on" ||
    token === "teams_spark" ||
    token === "meet_team" ||
    token === "team_challenge"
  );
}

/**
 * Open the shared move/remove confirmation dialog for one playlist item.
 * Every question — bank, seed, or engine ride — gets the same rail page list.
 * @param {string} itemId
 * @param {string} label
 */
function openRelocateDialog(itemId, label) {
  const token = String(itemId || "").trim();
  if (!token) return;
  pendingRelocate = { itemId: token, label: String(label || "This question").trim() };
  const dialog = $("live-relocate-dialog");
  const preview = $("live-relocate-preview");
  const select = $("live-relocate-page");
  const err = $("live-relocate-error");
  if (err instanceof HTMLElement) {
    err.hidden = true;
    err.textContent = "";
  }
  if (preview instanceof HTMLElement) {
    preview.textContent = pendingRelocate.label;
  }
  if (select instanceof HTMLSelectElement) {
    select.innerHTML = playlistMovePageOptions(currentLivePageIndex() + 1);
    select.disabled = !select.options.length;
  }
  const moveBtn = $("live-relocate-move");
  if (moveBtn instanceof HTMLButtonElement) {
    moveBtn.disabled = !(
      select instanceof HTMLSelectElement && select.options.length
    );
  }
  const moveSection = select?.closest(".live-relocate-section");
  if (moveSection instanceof HTMLElement) moveSection.hidden = !select?.options?.length;
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
}

/**
 * Close the shared move/remove dialog without applying a change.
 */
function closeRelocateDialog() {
  const dialog = $("live-relocate-dialog");
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
  pendingRelocate = { itemId: "", label: "" };
}

/**
 * Resolve the playlist item id used by remove/move APIs for one card row.
 * @param {any} card
 * @param {any} [item]
 * @returns {string}
 */
function resolvePlaylistItemId(card, item) {
  const row = card && typeof card === "object" ? card : {};
  const payload = item && typeof item === "object" ? item : {};
  return String(
    payload.id ||
      payload.item_id ||
      row.item_id ||
      row.id ||
      row.prompt_ref ||
      row.placement_key ||
      ""
  ).trim();
}

/**
 * Merge metadata, lifecycle, and server cards into one lesson-deck list.
 * @returns {any[]}
 */
function deckQuestionRows() {
  const lifecycleByItemId = new Map(
    (Array.isArray(lastLiveItems) ? lastLiveItems : []).map((row) => [
      String(row.item_id || "").trim(),
      row,
    ])
  );
  const serverCards = Array.isArray(lastQuestionCards) ? lastQuestionCards : [];
  const metadataQs =
    metadataMatchesCurrentPack() && Array.isArray(lastLiveMetadata?.questions)
      ? lastLiveMetadata.questions
      : [];
  const rows = [];
  const seen = new Set();

  function pushRow(base) {
    if (!base || typeof base !== "object") return;
    const item = base.item && typeof base.item === "object" ? base.item : base;
    const token = resolvePlaylistItemId(base, item);
    if (!token || seen.has(token)) return;
    seen.add(token);
    rows.push({
      ...base,
      id: token,
      item_id: token,
      item,
    });
  }

  for (const question of metadataQs) {
    if (!question || typeof question !== "object") continue;
    const kind = String(question.item_type || question.kind || question.type || "question")
      .trim()
      .toLowerCase();
    if (kind in { media: 1, whiteboard: 1, slides: 1 }) continue;
    if (question.removed || question.hidden) continue;
    const itemId = String(question.id || question.item_id || "").trim();
    const lifecycle = lifecycleByItemId.get(itemId);
    const server = serverCards.find(
      (row) => String(row.id || row.item_id || "").trim() === itemId
    );
    pushRow({
      ...(server || question),
      ...(lifecycle || {}),
      item: {
        ...(lifecycle?.item || {}),
        ...question,
        ...(server || {}),
        ...(server?.item || {}),
        id: itemId,
        text:
          (server && (server.text || server.item?.text)) ||
          question.text ||
          question.prompt,
        options:
          (server && (server.options || server.item?.options)) ||
          question.options ||
          [],
      },
      live_item_id: lifecycle?.id || server?.live_item_id || null,
      stage: question.stage || lifecycle?.stage || server?.stage,
      page_number:
        question.page_number ?? lifecycle?.page_number ?? server?.page_number,
      order: question.order ?? server?.order ?? lifecycle?.order,
      text:
        (server && (server.text || server.item?.text)) ||
        question.text ||
        question.prompt,
    });
  }

  for (const card of serverCards) {
    pushRow({ ...card, item: card.item || card });
  }
  const joinCard = joinPromptCardRow();
  if (joinCard) pushRow(joinCard);

  return rows.sort((left, right) => {
    const leftPage = Number(left.page_number || 0);
    const rightPage = Number(right.page_number || 0);
    if (leftPage !== rightPage) return leftPage - rightPage;
    return Number(left.order || 0) - Number(right.order || 0);
  });
}

/**
 * True when cached live_metadata belongs to the teacher's current module/slot.
 * @returns {boolean}
 */
function metadataMatchesCurrentPack() {
  const meta = lastLiveMetadata;
  if (!meta || typeof meta !== "object") return false;
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const metaSlot = String(meta.live_class || meta.slot || "").toUpperCase();
  const metaModule = String(meta.module || "").toUpperCase();
  if (metaSlot && metaSlot !== slot) return false;
  if (metaModule && metaModule !== module) return false;
  return true;
}

/**
 * Waiting-room prompt as a deck row on the Join page only.
 * @returns {any|null}
 */
function joinPromptCardRow() {
  if (isBlankOverlayLivePage()) return null;
  if (String(teacherState.stage || "") !== "join") return null;
  const joinRow = lastJoinPrompt || lastActivePrompt;
  const action = joinRow && joinRow.payload;
  if (!action || !(action.prompt || action.question)) return null;
  const promptSlot = String(action.live_slot || "").toUpperCase();
  const currentSlot = String(teacherState.live_slot || "").toUpperCase();
  if (promptSlot && currentSlot && promptSlot !== currentSlot) return null;
  const token = String(action.item_id || action.pack || "minds_on").trim();
  if (!token) return null;
  const options = Array.isArray(action.choices) ? action.choices : [];
  return {
    id: token,
    item_id: token,
    stage: "join",
    page_number: 1,
    engine_ride: isEngineRideItemId(token),
    ride_label: "Waiting room",
    type: options.length ? "mc" : "open",
    text: String(action.prompt || action.question || "").trim(),
    options,
    item: {
      id: token,
      text: String(action.prompt || action.question || "").trim(),
      options,
      engine_ride: true,
      ride_label: "Waiting room",
    },
    prompt_id: Number(joinRow.id) || 0,
    status: joinRow.active ? "active" : "inactive",
  };
}

/**
 * Questions on the teacher's current lesson page (stored page_number, then stage).
 * Overlay pages still list items bound to that stored page_number.
 * @returns {any[]}
 */
function currentPageQuestionRows() {
  const pageIndex = currentLivePageNumber();
  const stageKey = String(teacherState.stage || "");
  const blankOverlay = isBlankOverlayLivePage();
  return deckQuestionRows().filter((card) => {
    const cardPage = Number(card.page_number || 0);
    const cardStage = String(card.stage || "");
    const token = String(card.id || card.item_id || "");
    if (
      stageKey === "join" &&
      (cardStage === "join" || isEngineRideItemId(token))
    ) {
      return cardStage === "join" || token.replace(/-/g, "_") === "minds_on";
    }
    // Class-added overlay pages still show items staff placed on this stored page.
    if (pageIndex > 0 && cardPage > 0) return cardPage === pageIndex;
    if (blankOverlay) return false;
    return cardStage === stageKey;
  });
}

/**
 * True when a live question is open-ended numeric/text and not a Meet card.
 * @param {any} item
 * @param {any} card
 * @returns {boolean}
 */
/**
 * Render optional LaTeX under a staff question stem.
 * @param {any} item
 * @param {any} card
 * @returns {string}
 */
function liveQuestionEquationHtml(item, card) {
  const latex = String(
    item?.equation_latex || item?.equation || card?.equation_latex || card?.equation || ""
  ).trim();
  if (!latex) return "";
  const raw = latex.replace(/^\$+|\$+$/g, "").trim();
  return `<p class="live-question-equation"><span class="math-latex" data-latex="${escapeHtml(
    raw
  )}"></span></p>`;
}

function liveQuestionIsOpenEnded(item, card) {
  const id = String(item?.id || card?.item_id || card?.id || "")
    .toLowerCase()
    .replace(/_/g, "-");
  if (["meet-team", "meet-a", "meet-b", "meet-c"].includes(id)) return false;
  const type = String(item?.type || card?.type || "").toLowerCase();
  return (
    ["numeric", "text", "open", "share", "poll", "artifact"].includes(type) ||
    Boolean(item?.integer_only || card?.integer_only)
  );
}

/**
 * Keep a teacher Save to card choice when a poll snapshot is older than the click.
 * @param {any[]} items
 * @returns {any[]}
 */
function absorbSaveToCardSnapshot(items) {
  const rows = Array.isArray(items) ? items : [];
  return rows.map((row) => {
    const id = Number(row?.id) || 0;
    if (!id || !saveToCardHold.has(id)) return row;
    const held = Boolean(saveToCardHold.get(id));
    if (Boolean(row?.save_to_card) === held) {
      saveToCardHold.delete(id);
      return row;
    }
    return { ...row, save_to_card: held };
  });
}

/**
 * Checkbox value for Save to card, preferring an in-flight teacher click.
 * @param {any} card
 * @param {number} liveItemId
 * @returns {boolean}
 */
function cardSaveToCardChecked(card, liveItemId) {
  const id = Number(liveItemId) || 0;
  if (id && saveToCardHold.has(id)) return Boolean(saveToCardHold.get(id));
  return Boolean(card?.save_to_card);
}

/**
 * Per-card Submission choice before publish. Group is the one switch.
 * @type {Map<number, "individual"|"group_submit">}
 */
const groupSubmissionIntent = new Map();

/**
 * Individual vs Individual in Group before Publish.
 * Inactive repaints read this so the dropdown survives the staff poll.
 * @type {Map<number, "individual"|"group_consensus">}
 */
const openPublishIntent = new Map();

/**
 * Mode shown on an open or numeric card.
 * A teacher click wins over the catalogue default until Publish.
 * @param {number} liveItemId
 * @param {any} card
 * @param {string} status
 * @returns {"individual"|"group_consensus"}
 */
function openPublishModeSelection(liveItemId, card, status) {
  const stored =
    String(card?.response_mode || "") === "group_consensus"
      ? "group_consensus"
      : "individual";
  if (status === "active" || status === "closed") {
    openPublishIntent.delete(liveItemId);
    return stored;
  }
  const held = openPublishIntent.get(liveItemId);
  if (held === "group_consensus" || held === "individual") return held;
  return stored;
}

/**
 * True for a keyed multiple-choice card. Numeric and open stays off this path.
 * @param {any} item
 * @param {any} card
 * @returns {boolean}
 */
function liveQuestionIsMultipleChoice(item, card) {
  if (liveQuestionIsOpenEnded(item, card)) return false;
  const type = String(item?.type || card?.type || "").toLowerCase();
  return type === "mc";
}

/**
 * True for a rank card. Group submission uses the same switch as MC.
 * @param {any} item
 * @param {any} card
 * @returns {boolean}
 */
function liveQuestionIsRank(item, card) {
  const type = String(item?.type || card?.type || item?.kind || "").toLowerCase();
  return type === "rank";
}

/** @type {Map<number, {seq: number, rows: any[], at: number}>} */
const rankDisplayHold = new Map();

/**
 * Keep class-order rows still for about a second after a rank change.
 * Reduced motion swaps immediately. Points on a held row still refresh.
 * @param {number} liveItemId
 * @param {any[]} rows
 * @param {number} seq
 * @returns {any[]}
 */
function heldRankRows(liveItemId, rows, seq) {
  const next = Array.isArray(rows) ? rows : [];
  const reduce =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const prev = rankDisplayHold.get(liveItemId);
  const now = Date.now();
  if (!prev || reduce || prev.seq === seq) {
    rankDisplayHold.set(liveItemId, { seq, rows: next, at: prev?.at || now });
    return next;
  }
  if (now - prev.at < 1000) {
    const byId = new Map(next.map((row) => [String(row.option_id || ""), row]));
    return prev.rows.map((row) => byId.get(String(row.option_id || "")) || row);
  }
  rankDisplayHold.set(liveItemId, { seq, rows: next, at: now });
  return next;
}

/**
 * Teacher rank strip: team rows first, class order underneath.
 * Individual mode omits the team list.
 * @param {any} rank
 * @param {number} liveItemId
 * @returns {string}
 */
function rankCollateHtml(rank, liveItemId) {
  if (!rank || typeof rank !== "object") return "";
  const unit = String(rank.unit || "student");
  const teams = unit === "team" && Array.isArray(rank.teams) ? rank.teams : [];
  const responded = Number(rank.responded) || 0;
  const present = Number(rank.present) || teams.length || 0;
  const rawRows = Array.isArray(rank.class_order) ? rank.class_order : [];
  const rows = heldRankRows(
    liveItemId,
    rawRows,
    Number(rank.seq ?? rank.response_seq) || 0
  );
  const done = teams.filter((row) => row.status === "submitted" || row.order).length;
  const teamHtml = teams.length
    ? `<p class="rank-collate-kicker">Teams <span>${done}/${teams.length}</span></p>
      <ul class="rank-team-rows">${teams
        .map((row) => {
          const waiting = !row.order;
          return `<li class="${waiting ? "is-waiting" : ""}">
            <span>${escapeHtml(row.team_name || "Team")}</span>
            <span>${waiting ? "Waiting" : "✓"}</span>
            <span>${waiting ? "" : escapeHtml(row.order_label || "")}</span>
          </li>`;
        })
        .join("")}</ul>`
    : "";
  const classHtml = `<p class="rank-collate-kicker">Class order <span>${
    unit === "team" ? "1 vote per team" : `${responded} responded / ${present} present`
  }</span></p>
    <ol class="rank-class-order">${rows
      .map((row) => {
        const pct = Math.max(0, Math.min(100, Number(row.bar_pct) || 0));
        return `<li data-rank-option="${escapeHtml(row.option_id || "")}">
          <span class="rank-place">${Number(row.rank) || 0}</span>
          <span>${escapeHtml(row.label || "")}</span>
          <span class="rank-bar" aria-hidden="true"><span style="width:${pct}%"></span></span>
          <span>${Number(row.points) || 0}</span>
          <span class="rank-first" aria-label="${Number(row.first_picks) || 0} first picks">★ ${
            Number(row.first_picks) || 0
          }</span>
        </li>`;
      })
      .join("")}</ol>
    <p class="rank-responded">${responded} responded / ${present} present</p>`;
  return `<div class="rank-collate" data-rank-seq="${Number(rank.seq) || 0}">${teamHtml}${classHtml}</div>`;
}

/**
 * Teacher status is waiting or a check. Answers stay off until reveal.
 * @param {any} result
 * @param {boolean} revealed
 * @returns {string}
 */
function groupSubmitTeacherHtml(result, revealed) {
  if (result?.rank) {
    const log = Array.isArray(result?.submitter_log) ? result.submitter_log : [];
    const rankHtml = rankCollateHtml(result.rank, Number(result?.item?.id) || 0);
    const logHtml = `<details class="group-submitter-log">
      <summary>Submitter log</summary>
      <table>
        <thead><tr><th>Group</th><th>Last submitter</th><th>Re-submits</th><th>No submit at reveal</th><th>Repeat submitter</th><th>Why</th></tr></thead>
        <tbody>${
          log.length
            ? log
                .map(
                  (row) => `<tr>
              <td>${escapeHtml(row.team_name || "")}</td>
              <td>${escapeHtml(row.last_submitter || "")}</td>
              <td>${Number(row.resubmit_count) || 0}</td>
              <td>${row.no_submit_at_reveal ? "Yes" : "No"}</td>
              <td>${row.repeat_submitter ? "Yes" : "No"}</td>
              <td>${row.why_present ? "Yes" : "No"}</td>
            </tr>`
                )
                .join("")
            : `<tr><td colspan="6"></td></tr>`
        }</tbody>
      </table>
    </details>`;
    return `<div class="group-submit-teacher">${rankHtml}${logHtml}</div>`;
  }
  const board = Array.isArray(result?.status_board) ? result.status_board : [];
  const log = Array.isArray(result?.submitter_log) ? result.submitter_log : [];
  const reveal = revealed && Array.isArray(result?.reveal) ? result.reveal : [];
  const statusHtml = board.length
    ? `<ul class="group-status-board" aria-label="Group status">${board
        .map((row) => {
          const mark = row.submitted ? "✓" : "Waiting";
          return `<li><span>${escapeHtml(row.team_name || "Group")}</span><span>${mark}</span></li>`;
        })
        .join("")}</ul>`
    : `<p class="hint compact">Groups appear here after publish.</p>`;
  const logHtml = `<details class="group-submitter-log">
      <summary>Submitter log</summary>
      <table>
        <thead><tr><th>Group</th><th>Last submitter</th><th>Re-submits</th><th>No submit at reveal</th><th>Repeat submitter</th><th>Why</th></tr></thead>
        <tbody>${
          log.length
            ? log
                .map(
                  (row) => `<tr>
              <td>${escapeHtml(row.team_name || "")}</td>
              <td>${escapeHtml(row.last_submitter || "")}</td>
              <td>${Number(row.resubmit_count) || 0}</td>
              <td>${row.no_submit_at_reveal ? "Yes" : "No"}</td>
              <td>${row.repeat_submitter ? "Yes" : "No"}</td>
              <td>${row.why_present ? "Yes" : "No"}</td>
            </tr>`
                )
                .join("")
            : `<tr><td colspan="6"></td></tr>`
        }</tbody>
      </table>
    </details>`;
  const revealHtml = reveal.length
    ? `<table class="group-reveal-board">
        <caption>Group answers</caption>
        <thead><tr><th>Group</th><th>Answer</th><th>Why</th></tr></thead>
        <tbody>${reveal
          .map((row) => {
            const missed = Boolean(row.missed);
            return `<tr class="${missed ? "is-missed" : ""}">
              <th>${escapeHtml(row.team_name || "Group")}</th>
              <td>${missed ? "" : escapeHtml(row.answer || "")}</td>
              <td>${missed ? "" : escapeHtml(row.why || "")}</td>
            </tr>`;
          })
          .join("")}</tbody>
      </table>`
    : "";
  return `<div class="group-submit-teacher">${statusHtml}${logHtml}${revealHtml}</div>`;
}

/**
 * Render the per-question strip in Mobbin group order.
 *
 * A Persist is Save to card. B Visibility is Show Live Results and stays
 * visible on an inactive card so the teacher can arm it before Publish.
 * Both stay toggleable after Close. C Lifecycle is Publish while inactive,
 * then Reveal or Responses and points, then Close. (Re)move stays in the
 * card head. Tablet CSS wraps the strip to at most two rows; under 720px
 * the groups stack in A→B→C order. Publish does not clear the booleans.
 *
 * @param {{
 *   liveItemId: number,
 *   card: any,
 *   status: string,
 *   publishHtml: string,
 *   revealHtml: string,
 *   pointsHtml: string,
 *   closeHtml: string,
 *   closedCopy: string,
 * }} parts
 * @returns {string} Strip HTML, or "" when the row has no lifecycle id.
 */
function liveQuestionControlStrip(parts) {
  const liveItemId = Number(parts?.liveItemId) || 0;
  if (!liveItemId) return "";
  const card = parts.card || {};
  const status = String(parts.status || "inactive").toLowerCase();
  const closed = status === "closed";
  const active = status === "active";
  const persist = `<div class="live-q-group" data-live-q-group="persist" role="group" aria-label="A Persist">
        <span class="live-q-group-kicker">A Persist</span>
        <label class="live-result-toggle">
          <input type="checkbox" data-save-to-card="${liveItemId}" ${
            cardSaveToCardChecked(card, liveItemId) ? "checked" : ""
          }>
          <span>Save to card</span>
        </label>
      </div>`;
  const visibility = `<div class="live-q-group" data-live-q-group="visibility" role="group" aria-label="B Visibility">
        <span class="live-q-group-kicker">B Visibility</span>
        <label class="live-result-toggle">
          <input type="checkbox" data-live-results-toggle="${liveItemId}" ${
            card.show_live_results !== false ? "checked" : ""
          }>
          <span>Show Live Results</span>
        </label>
      </div>`;
  let lifecycleBody = "";
  if (active) {
    lifecycleBody = parts.revealHtml
      ? `${parts.revealHtml}${parts.closeHtml || ""}`
      : `${parts.pointsHtml || ""}${parts.closeHtml || ""}`;
  } else if (closed) {
    lifecycleBody = `${parts.pointsHtml || ""}${parts.closedCopy || ""}`;
  } else {
    lifecycleBody = parts.publishHtml || "";
  }
  const lifecycle = lifecycleBody
    ? `<div class="live-q-group" data-live-q-group="lifecycle" role="group" aria-label="C Lifecycle">
        <span class="live-q-group-kicker">C Lifecycle</span>
        <div class="live-q-group-actions">${lifecycleBody}</div>
      </div>`
    : "";
  if (!persist && !visibility && !lifecycle) return "";
  return `<div class="live-q-strip" data-live-q-strip="1">${persist}${visibility}${lifecycle}</div>`;
}

/**
 * Render every question associated with the current stage as a vertical card.
 */
function paintLiveQuestionCards() {
  const host = $("live-question-list");
  if (!host) return;
  const lifecycleById = new Map(
    lastLiveItems.map((row) => [Number(row.id), row])
  );
  const activeByItem = new Map(
    lastActiveQuestions.map((row) => [
      String(row?.payload?.item_id || ""),
      row,
    ])
  );
  const cards = currentPageQuestionRows().map((card) => {
      const lifecycle = lifecycleById.get(Number(card.live_item_id));
      const prompt = activeByItem.get(String(card.id || card.item_id || ""));
      const pageNumber = card.page_number;
      const stage = card.stage;
      return {
        ...card,
        ...(lifecycle || {}),
        item: card.item || lifecycle?.item || card,
        text: card.text || card.item?.text || lifecycle?.item?.text,
        page_number: pageNumber ?? lifecycle?.page_number,
        stage: stage || lifecycle?.stage,
        prompt_id: Number(prompt?.id || lifecycle?.prompt_id || card.prompt_id) || 0,
      };
    });
  if (!cards.length) {
    const deckTotal = deckQuestionRows().length;
    host.innerHTML =
      deckTotal > 0
        ? `<p class="hint compact">No questions on this page yet. Use Add New or Import from bank to add one here.</p>`
        : `<p class="hint compact">No questions in this lesson deck yet.</p>`;
    return;
  }
  host.innerHTML = cards
    .map((card, index) => {
      const item = card.item || card;
      const options = Array.isArray(item.options)
        ? item.options
        : Array.isArray(card.options)
          ? card.options
          : [];
      const isRankCard = liveQuestionIsRank(item, card);
      const optionLabels = options.map((option) =>
        option && typeof option === "object" ? option.label || option.text || "" : option
      );
      const optionHtml = isRankCard
        ? optionLabels.length
          ? `<ol class="rank-authored-list">${optionLabels
              .map((option) => `<li>${escapeHtml(option)}</li>`)
              .join("")}</ol>`
          : ""
        : options.length
          ? `<ol class="live-question-card-options" type="A">${options
              .map((option, optIndex) => `<li>${questionFieldHtml(item, "option", optIndex) || formatQuestionHtml(option)}</li>`)
              .join("")}</ol>`
          : "";
      const answerKey = item.correct_answer ?? card.correct_answer;
      const hasAnswerKey = liveQuestionHasSingularKey(card);
      const key = answerKey != null && String(answerKey) !== ""
        ? `<span class="live-question-key">Answer ${escapeHtml(answerKey)}</span>`
        : "";
      const page =
        card.page_number == null
          ? ""
          : `<span>Page ${escapeHtml(card.page_number)}</span>`;
      const importSource =
        item.import_source === "artifact" || String(item.type || card.type || "") === "artifact"
          ? `<span class="hint">Artifact · Transformations</span>`
          : item.import_source === "module_bank" || String(card.id || "").startsWith("bank-import-")
          ? `<span class="hint">Bank · ${escapeHtml(
              item.bank_title || item.question_title || "Module MC"
            )}</span>`
          : item.engine_ride || card.engine_ride
            ? `<span class="hint">${escapeHtml(card.ride_label || item.ride_label || "Live class")}</span>`
            : "";
      const promptId = Number(card.prompt_id) || 0;
      const liveItemId = Number(card.live_item_id || card.id) || 0;
      const status = String(card.status || "inactive").toLowerCase();
      const active = status === "active";
      const closed = status === "closed";
      const publishModes = Array.isArray(item.publish_modes)
        ? item.publish_modes
        : Array.isArray(item.capabilities?.publish_modes)
          ? item.capabilities.publish_modes
          : ["individual"];
      const openEnded = liveQuestionIsOpenEnded(item, card);
      const isRank = liveQuestionIsRank(item, card);
      const multipleChoice = liveQuestionIsMultipleChoice(item, card) || isRank;
      const intent = groupSubmissionIntent.get(liveItemId);
      const groupChrome =
        multipleChoice &&
        (card.response_mode === "group_submit" ||
          (status !== "active" && status !== "closed" && intent === "group_submit"));
      const canGroup =
        !multipleChoice &&
        Boolean(teacherState.groups_configured) &&
        Boolean(teacherState.run_as_group) &&
        (openEnded || publishModes.includes("group_consensus"));
      const showGroupBadge =
        groupChrome ||
        card.response_mode === "group_submit" ||
        card.response_mode === "group_consensus";
      const result = lifecycleResults.get(liveItemId);
      const resultHtml = groupChrome
        ? groupSubmitTeacherHtml(result, closed)
        : card.response_mode === "group_consensus"
          ? groupConsensusResultsHtml(result)
          : result?.tally?.kind === "rank"
            ? rankCollateHtml(
                {
                  ...result.tally,
                  unit: "student",
                  present: Number(result?.eligible_count ?? result.tally.present) || 0,
                },
                liveItemId
              )
            : individualLifecycleResultsHtml(result?.tally);
      const tally = lastMcTally;
      const cardRef = String(card.id || card.item_id || "").replace(/_/g, "-");
      const tallyRef = String(tally?.item_id || tally?.prompt_ref || "").replace(/_/g, "-");
      const tallyCount =
        tallyRef && cardRef && tallyRef === cardRef
          ? Number(tally?.response_count ?? tally?.responded ?? 0)
          : 0;
      const openMode = openPublishModeSelection(liveItemId, card, status);
      const answered = Number(
        result?.response_count ??
          result?.tally?.responded ??
          tallyCount ??
          card.response_count ??
          0
      );
      const eligible = Number(result?.eligible_count ?? result?.tally?.present ?? 0);
      const onStage = String(card.stage || "") === String(teacherState.stage || "");
      const groupBoard = Array.isArray(result?.status_board) ? result.status_board : [];
      const groupDone = groupBoard.filter((row) => row.submitted).length;
      const progress =
        onStage && groupChrome && (active || closed)
          ? `<p class="live-question-progress">${groupDone} / ${groupBoard.length} groups</p>`
          : onStage && (active || closed)
            ? `<p class="live-question-progress">${answered} / ${Math.max(
                eligible,
                answered
              )} ${
                card.response_mode === "group_consensus" ? "responded" : "answered"
              }</p>`
            : "";
      let submissionValue =
        card.response_mode === "group_submit" || intent === "group_submit"
          ? "group_submit"
          : "individual";
      if (
        isRank &&
        status !== "active" &&
        status !== "closed" &&
        intent == null &&
        card.response_mode !== "group_submit" &&
        teacherState.groups_configured
      ) {
        submissionValue = "group_submit";
      }
      const groupStub = multipleChoice
        ? ""
        : `<span class="live-group-submit-stub" hidden>Group submission is multiple choice only.</span>`;
      const publish = !liveItemId || !onStage
        ? ""
        : multipleChoice
          ? `<div class="live-publish-split has-modes">
            <div class="live-submission-switch" role="group" aria-label="Submission">
              <span>Submission</span>
              <button type="button" class="live-submission-choice${submissionValue === "individual" ? " is-on" : ""}" data-submission-mode="${liveItemId}" data-submission-value="individual" aria-pressed="${submissionValue === "individual" ? "true" : "false"}">Individual</button>
              <button type="button" class="live-submission-choice${submissionValue === "group_submit" ? " is-on" : ""}" data-submission-mode="${liveItemId}" data-submission-value="group_submit" aria-pressed="${submissionValue === "group_submit" ? "true" : "false"}">Group</button>
              <input type="hidden" data-publish-live-mode="${liveItemId}" value="${submissionValue}">
            </div>
            <button type="button" class="live-q-btn" data-publish-live-item="${liveItemId}">Publish</button>
          </div>`
          : `<div class="live-publish-split${canGroup ? " has-modes" : ""}">
            <button type="button" class="live-q-btn" data-publish-live-item="${liveItemId}">Publish</button>
            ${
              canGroup
                ? `<select data-publish-live-mode="${liveItemId}" aria-label="Publish mode">
                    <option value="individual"${
                      openMode === "individual" ? " selected" : ""
                    }>Individual</option>
                    <option value="group_consensus"${
                      openMode === "group_consensus" ? " selected" : ""
                    }>Individual in Group</option>
                  </select>`
                : `<input type="hidden" data-publish-live-mode="${liveItemId}" value="individual">`
            }
            ${groupStub}
          </div>`;
      const pointsButton =
        card.response_mode === "group_consensus" || groupChrome
          ? ""
          : `<button type="button" class="secondary live-q-btn" data-view-responses="${promptId}" data-question-title="${escapeHtml(
              item.title || item.text || card.text
            )}" data-question-type="${escapeHtml(item.type || card.type || "poll")}" data-has-answer-key="${
              hasAnswerKey ? "1" : ""
            }" ${
              promptId ? "" : "disabled"
            }>Responses &amp; points</button>`;
      const playlistItemId = resolvePlaylistItemId(card, item);
      const questionLabel = String(
        item.title || item.text || item.prompt || card.text || "This question"
      ).trim();
      const relocateButton = playlistItemId
        ? `<button type="button" class="secondary live-q-btn live-question-relocate-btn" data-open-relocate-dialog="${escapeHtml(
            playlistItemId
          )}" aria-label="Move or remove question">(Re)move</button>`
        : "";
      const revealHtml =
        active && groupChrome
          ? `<button type="button" class="secondary live-q-btn" data-close-live-item="${liveItemId}">Reveal</button>`
          : active && card.response_mode === "group_consensus"
            ? `<button type="button" class="secondary live-q-btn" data-end-voting="${liveItemId}">Reveal answers</button>`
            : "";
      const closeHtml =
        active && liveItemId && !groupChrome
          ? `<button type="button" class="secondary live-q-btn" data-close-live-item="${liveItemId}">Close</button>`
          : "";
      const controlStrip = onStage
        ? liveQuestionControlStrip({
            liveItemId,
            card,
            status,
            publishHtml: publish,
            revealHtml,
            pointsHtml: pointsButton,
            closeHtml,
            closedCopy: `<span class="live-closed-copy">Final results</span>`,
          })
        : "";
      return `<article class="live-question-card is-${status}" data-question-id="${escapeHtml(
        item.id || card.item_id || card.id
      )}" data-live-item-id="${liveItemId}">
        <div class="live-question-card-main">
          <div class="live-question-card-head">
            <div class="live-question-card-meta">
              <span>${escapeHtml(
                String(
                  item.engine_ride || card.engine_ride
                    ? (options.length ? item.type || card.type || "poll" : "open")
                    : item.type || card.type || "poll"
                ).toUpperCase()
              )}</span>${key}${page}${importSource}
              <span class="live-status-badge is-${status}">${escapeHtml(
                status.charAt(0).toUpperCase() + status.slice(1)
              )}</span>${
                showGroupBadge
                  ? `<span class="live-status-badge is-group">Group</span>`
                  : ""
              }
            </div>
            ${relocateButton}
          </div>
          ${questionImageHtml(item.image_url || card.image_url, { variant: "thumb" })}
          <div class="live-question-card-text"><span class="live-question-order">${index + 1}</span>${questionFieldHtml(item, "text") || formatQuestionHtml(item.title || item.text || item.prompt || card.text || "")}</div>
          ${liveQuestionEquationHtml(item, card)}
          ${result?.rank || result?.tally?.kind === "rank" ? "" : optionHtml}
          ${resultHtml}
          ${progress}
        </div>
        <div class="live-question-card-actions">
          ${controlStrip}
        </div>
      </article>`;
    })
    .join("");
  void renderLiveQuestionMath(host);
}

/**
 * Render a bar chart or numeric histogram from a lifecycle tally.
 * @param {any} tally
 * @returns {string}
 */
function individualLifecycleResultsHtml(tally) {
  if (!tally || !Array.isArray(tally.choices)) return "";
  const numeric = String(tally.kind || "") === "numeric";
  return `<div class="live-item-chart${numeric ? " is-histogram" : ""}" aria-label="${
    numeric ? "Numeric answer histogram" : "Live answer distribution"
  }">${tally.choices
    .map((row) => {
      const pct = Math.max(0, Math.min(100, Number(row.pct) || 0));
      return `<div class="live-item-chart-row">
        <span class="live-item-chart-label">${escapeHtml(row.label ?? row.id ?? "")}</span>
        <span class="live-item-chart-track"><span style="width:${pct}%"></span></span>
        <span class="live-item-chart-value">${Number(row.count) || 0} · ${pct}%</span>
      </div>`;
    })
    .join("")}</div>`;
}

/**
 * Format a normalized group answer for compact result cards.
 * @param {any} answer
 * @returns {string}
 */
function groupAnswerLabel(answer) {
  if (!answer || typeof answer !== "object") return "—";
  return String(answer.value ?? "—");
}

/**
 * Expand anonymous member answers for a compact teacher team card.
 * @param {any} team
 * @returns {string[]}
 */
function groupMemberAnswerValues(team) {
  if (Array.isArray(team?.member_answers) && team.member_answers.length) {
    return team.member_answers.map((value) => String(value));
  }
  const expanded = [];
  for (const row of team?.vote_summary || []) {
    const label = groupAnswerLabel(row.answer);
    const count = Number(row.count) || 0;
    for (let index = 0; index < count; index += 1) {
      expanded.push(label);
    }
  }
  return expanded;
}

/**
 * Render compact per-team group-consensus cards for the teacher.
 * @param {any} result
 * @returns {string}
 */
function groupConsensusResultsHtml(result) {
  const teams = Array.isArray(result?.teams) ? result.teams : [];
  if (!teams.length) return "";
  return `<div class="live-consensus-teams">${teams
    .map((team) => {
      const status = String(team.status || "collecting_votes");
      const responded = Number(team.vote_count) || 0;
      const eligible = Math.max(Number(team.eligible_count) || 0, responded);
      const answers = groupMemberAnswerValues(team);
      const answerList = answers
        .map((value) => `<li>${escapeHtml(value)}</li>`)
        .join("");
      const inspect = `<details class="live-consensus-inspect">
        <summary>Member answers</summary>
        <ul>${answerList || "<li>Waiting</li>"}</ul>
      </details>`;
      let body = "";
      if (status === "collecting_votes") {
        const liveLine = answers.length
          ? answers.map((value) => escapeHtml(value)).join(" · ")
          : "Waiting";
        body = `<p class="live-consensus-team-name">${escapeHtml(
          team.team_name || "Team"
        )}</p>
        <p class="live-consensus-team-count">${responded}/${eligible} responded</p>
        <p class="live-consensus-team-answers">${liveLine}</p>
        <p class="live-consensus-team-note">${
          responded === eligible && eligible > 0
            ? "Ready to discuss"
            : "Private responses incoming…"
        }</p>`;
      } else if (status === "finalized") {
        body = `<p class="live-consensus-team-name">${escapeHtml(
          team.team_name || "Team"
        )}</p>
        <p class="live-consensus-team-answer">${escapeHtml(
          groupAnswerLabel(team.final_answer)
        )}</p>
        <p class="live-consensus-team-count">${responded}/${eligible} contributed</p>
        <p class="live-consensus-team-note">Group Answer ✓</p>
        <button type="button" data-award-consensus="${Number(
          result?.item?.id || 0
        )}" data-team-id="${Number(team.team_id)}">+1 team</button>`;
      } else {
        body = `<p class="live-consensus-team-name">${escapeHtml(
          team.team_name || "Team"
        )}</p>
        <p class="live-consensus-team-answers">Responses: ${
          answers.map((value) => escapeHtml(value)).join(" · ") || "—"
        }</p>
        <p class="live-consensus-team-note">Awaiting Group Answer</p>`;
      }
      return `<article class="live-consensus-team is-${escapeHtml(status)}">
        ${body}
        ${inspect}
      </article>`;
    })
    .join("")}</div>`;
}

/**
 * Show one question to individual students and hide all sibling questions.
 * @param {string} questionId
 * @param {string} mode
 */
async function setQuestionStudentView(questionId, mode) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) return;
  const result = await api(
    `/api/live-sessions/${sessionId}/questions/${encodeURIComponent(questionId)}/visibility`,
    {
      method: "POST",
      body: JSON.stringify({ mode }),
    }
  );
  if (result?.teacher_state) adoptTeacherState(result.teacher_state);
  if (Array.isArray(result?.question_cards)) {
    lastQuestionCards = questionCardsFromMetadata(result.question_cards);
    paintLiveQuestionCards();
  }
}

/**
 * Replace one cached lifecycle row from an item-mutation response.
 * @param {any} item
 */
function adoptLiveItem(item) {
  if (!item || !Number(item.id)) return;
  const index = lastLiveItems.findIndex((row) => Number(row.id) === Number(item.id));
  if (index >= 0) lastLiveItems[index] = item;
  else lastLiveItems.push(item);
}

/**
 * Fold spaces and hyphens so Group submission matches ``group_submit``.
 * A bare ``group`` stays bare for the numeric consensus alias.
 * @param {unknown} raw
 * @returns {string}
 */
function canonicalPublishMode(raw) {
  const token = String(raw || "individual")
    .trim()
    .toLowerCase()
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ");
  if (token === "group submit" || token === "group submission") return "group_submit";
  if (token === "group consensus") return "group_consensus";
  if (token === "group shared") return "group_shared";
  if (token === "individual in group") return "individual_in_group";
  return token.replace(/ /g, "_");
}

/**
 * Read the mode for one card. Multiple choice uses the pressed Submission
 * button so a stray publish-mode control cannot override Group.
 * @param {number} liveItemId
 * @returns {string}
 */
function selectedPublishMode(liveItemId) {
  const card = document.querySelector(
    `.live-question-card[data-live-item-id="${liveItemId}"]`
  );
  const pressed = card?.querySelector(
    'button[data-submission-value][aria-pressed="true"]'
  );
  if (pressed instanceof HTMLButtonElement) {
    return canonicalPublishMode(pressed.getAttribute("data-submission-value"));
  }
  const root = card instanceof HTMLElement ? card : document;
  const control = root.querySelector(`[data-publish-live-mode="${liveItemId}"]`);
  const raw =
    control instanceof HTMLSelectElement || control instanceof HTMLInputElement
      ? control.value
      : groupSubmissionIntent.get(Number(liveItemId)) || "individual";
  return canonicalPublishMode(raw);
}

/**
 * Publish one inactive question using its selected supported mode.
 * @param {number} liveItemId
 */
async function publishLifecycleItem(liveItemId) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || !liveItemId) return;
  const publishMode = selectedPublishMode(liveItemId);
  if (
    publishMode === "group_submit" &&
    teacherState.groups_configured &&
    !teacherState.run_as_group
  ) {
    teacherState.run_as_group = true;
    teacherState.teams_mode = "teams";
    await patchTeacherState({ run_as_group: true }, { silent: true });
  }
  const result = await api(
    `/api/live-sessions/${sessionId}/items/${liveItemId}/publish`,
    {
      method: "POST",
      body: JSON.stringify({ publish_mode: publishMode }),
    }
  );
  adoptLiveItem(result?.item);
  lastActiveQuestions = Array.isArray(result?.active_questions)
    ? result.active_questions
    : lastActiveQuestions;
  await refreshLifecycleResults();
  paintLiveQuestionCards();
}

/**
 * Close one active question and freeze its final results.
 * @param {number} liveItemId
 */
async function closeLifecycleItem(liveItemId) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || !liveItemId) return;
  const result = await api(
    `/api/live-sessions/${sessionId}/items/${liveItemId}/close`,
    { method: "POST", body: "{}" }
  );
  adoptLiveItem(result?.item);
  if (result?.results) lifecycleResults.set(liveItemId, result.results);
  paintLiveQuestionCards();
}

/**
 * Persist one question's governed live-results setting.
 * @param {number} liveItemId
 * @param {boolean} visible
 */
async function setLifecycleResultsVisible(liveItemId, visible) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || !liveItemId) return;
  const result = await api(
    `/api/live-sessions/${sessionId}/items/${liveItemId}/settings`,
    {
      method: "PATCH",
      body: JSON.stringify({ show_live_results: Boolean(visible) }),
    }
  );
  adoptLiveItem(result?.item);
  paintLiveQuestionCards();
}

/**
 * Persist Save to card so the student panel keeps this question after the beat.
 * @param {number} liveItemId
 * @param {boolean} enabled
 */
async function setLifecycleSaveToCard(liveItemId, enabled) {
  const sessionId = liveSessionId || readLiveSessionId();
  const id = Number(liveItemId) || 0;
  if (!sessionId || !id) return;
  const flag = Boolean(enabled);
  const index = lastLiveItems.findIndex((row) => Number(row.id) === id);
  const previous = index >= 0 ? lastLiveItems[index] : null;
  saveToCardHold.set(id, flag);
  if (index >= 0) {
    lastLiveItems[index] = { ...lastLiveItems[index], save_to_card: flag };
  }
  paintLiveQuestionCards();
  try {
    const result = await api(
      `/api/live-sessions/${sessionId}/items/${id}/settings`,
      {
        method: "PATCH",
        body: JSON.stringify({ save_to_card: flag }),
      }
    );
    adoptLiveItem(result?.item);
  } catch (err) {
    saveToCardHold.delete(id);
    if (previous) lastLiveItems[index] = previous;
    throw err;
  } finally {
    paintLiveQuestionCards();
  }
}

/**
 * Advance every unfinished team from private voting to discussion.
 * @param {number} liveItemId
 */
async function endLifecycleVoting(liveItemId) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || !liveItemId) return;
  const result = await api(
    `/api/live-sessions/${sessionId}/items/${liveItemId}/end-voting`,
    { method: "POST", body: "{}" }
  );
  lifecycleResults.set(liveItemId, result);
  paintLiveQuestionCards();
}

/**
 * Award one point to every current member of a finalized team.
 * @param {number} liveItemId
 * @param {number} teamId
 */
async function awardLifecycleTeam(liveItemId, teamId) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || !liveItemId || !teamId) return;
  await api(`/api/live-sessions/${sessionId}/items/${liveItemId}/points`, {
    method: "POST",
    body: JSON.stringify({ team_id: teamId, amount: 1 }),
  });
  await refreshLifecycleResults();
  renderAttendanceList();
}

/**
 * Refresh result summaries for current-stage active and closed questions.
 */
async function refreshLifecycleResults() {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) return;
  const ids = lastLiveItems
    .filter((row) => {
      const type = String(
        row?.item?.item_type || row?.item?.type || row?.kind || ""
      ).toLowerCase();
      const mode = String(row?.response_mode || "");
      return (
        (mode === "group_consensus" ||
          ["question", "poll", "mc", "numeric", "text", "open", "share", "rank"].includes(
            type
          )) &&
        String(row.stage || "") === String(teacherState.stage || "") &&
        ["active", "closed"].includes(String(row.status || ""))
      );
    })
    .map((row) => Number(row.id))
    .filter(Boolean);
  await Promise.all(
    ids.map(async (id) => {
      try {
        const payload = await api(
          `/api/live-sessions/${sessionId}/items/${id}/results`
        );
        if (payload?.results) lifecycleResults.set(id, payload.results);
      } catch (_) {
        /* next state poll retries */
      }
    })
  );
  paintLiveQuestionCards();
}

/**
 * Check response rows to match a teacher selection helper.
 * @param {"answered"|"correct"|"manual"} mode
 */
function applyQuestionResponseSelection(mode) {
  const token = String(mode || "");
  document.querySelectorAll("#live-responses-list [data-response-student]").forEach((input) => {
    if (!(input instanceof HTMLInputElement)) return;
    if (token === "manual") return;
    const mark =
      input.closest(".live-response-row")?.querySelector(".live-response-mark")
        ?.textContent || "";
    input.checked = token === "answered" || (token === "correct" && mark === "Correct");
  });
}

/**
 * Render response rows in the same alphabetical/team order as Class list.
 * @param {any[]} responses
 */
function paintQuestionResponses(responses) {
  const host = $("live-responses-list");
  if (!host) return;
  const rows = Array.isArray(responses) ? responses : [];
  const byStudent = new Map(
    rows
      .filter((row) => row.student_id != null)
      .map((row) => [Number(row.student_id), row])
  );
  const groups = classListRosterOrder(projectedClassListStudents());
  const chunks = [];
  for (const group of groups) {
    const groupRows = group.students
      .map((student) => byStudent.get(Number(student.id)))
      .filter(Boolean);
    if (!groupRows.length) continue;
    if (group.name) {
      chunks.push(
        `<h3 class="live-response-team" style="--team:${escapeHtml(group.color || "#64748b")}">${escapeHtml(group.name)}</h3>`
      );
    }
    chunks.push(
      ...groupRows.map(
        (row) => `<label class="live-response-row">
          <input type="checkbox" data-response-student="${Number(row.student_id)}"${
            Number(row.awarded_points || 0) ? " checked" : ""
          }>
          <span class="live-response-name">${escapeHtml(row.name)}</span>
          <span class="live-response-answer">${escapeHtml(row.answer || "—")}</span>
          <span class="live-response-mark">${row.correct === true ? "Correct" : row.correct === false ? "Incorrect" : "Answered"}</span>
          <span class="live-response-points">${row.awarded_points ? `+${escapeHtml(row.awarded_points)}` : ""}</span>
        </label>`
      )
    );
  }
  for (const row of rows.filter((item) => item.student_id == null)) {
    chunks.push(
      `<div class="live-response-row"><span></span><span class="live-response-name">${escapeHtml(row.name)}</span><span class="live-response-answer">${escapeHtml(row.answer || "—")}</span><span class="live-response-mark">Guest</span><span></span></div>`
    );
  }
  host.innerHTML =
    chunks.join("") || `<p class="hint compact">No responses yet.</p>`;
}

/**
 * True when a teacher card has exactly one authored answer key.
 * Keyed MC and keyed numeric qualify; polls and keyless numeric do not.
 * @param {any} card
 * @returns {boolean}
 */
function liveQuestionHasSingularKey(card) {
  const item = card && typeof card === "object" ? card.item || card : {};
  const raw =
    item.correct_answer ??
    card?.correct_answer ??
    item.key ??
    card?.key;
  if (Array.isArray(raw)) {
    return raw.filter((value) => String(value ?? "").trim() !== "").length === 1;
  }
  return raw != null && String(raw).trim() !== "";
}

/**
 * Fetch and open the ephemeral response viewer for one prompt.
 * @param {number} promptId
 * @param {string} title
 * @param {string} questionType
 * @param {boolean} [hasAnswerKey]
 */
async function openQuestionResponses(promptId, title, questionType, hasAnswerKey) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || !promptId) return;
  const result = await api(
    `/api/live-sessions/${sessionId}/questions/${promptId}/responses`
  );
  openResponsePromptId = promptId;
  const heading = $("live-responses-title");
  if (heading) heading.textContent = title || "Responses";
  paintQuestionResponses(result.responses);
  const dialog = $("live-responses-dialog");
  const correct = dialog?.querySelector('[data-response-select="correct"]');
  const keyed =
    questionType === "mc" ||
    Boolean(hasAnswerKey) ||
    (Array.isArray(result.responses) &&
      result.responses.some((row) => row && row.correct != null));
  if (correct instanceof HTMLButtonElement) {
    correct.disabled = !keyed;
    correct.title = keyed
      ? ""
      : "Polls and numeric questions have no answer key.";
  }
  if (dialog instanceof HTMLDialogElement && !dialog.open) dialog.showModal();
}

/**
 * Refresh the Questions pane: slot chips, lifecycle cards, minted Artifact, then hide relics.
 * Leftover Meet/Join/cons stems and 0/0 bars belong on cards, not this pane.
 * @param {any} [media]
 */
function paintQuestionArtifact(media) {
  if (media && typeof media === "object") lastActiveMedia = media;
  paintLiveSlotPicks();
  paintLiveQuestionCards();
  isBlankOverlayLivePage();
  hideLiveQuestionBody();
  hideTeamsSparkCard();
  paintMeetChainChrome();
  const status = $("question-artifact-status");
  const flag = $("question-artifact-flag");
  if (status) {
    status.hidden = true;
    status.textContent = "";
  }
  if (flag) {
    flag.hidden = true;
    flag.textContent = "";
  }
}

/**
 * Hide leftover Meet chain chrome. The teammate poll is an individual card.
 */
function paintMeetChainChrome() {
  const chrome = $("meet-chain-chrome");
  if (chrome) chrome.hidden = true;
  const host = $("meet-poll-totals");
  if (host) host.hidden = true;
  const dots = $("meet-chain-dots");
  if (dots) dots.innerHTML = "";
  const classEl = $("meet-poll-class");
  if (classEl) classEl.innerHTML = "";
  const teamsEl = $("meet-poll-teams");
  if (teamsEl) teamsEl.innerHTML = "";
}

/**
 * Compact poll bars from a label→count map (no soft-count line).
 * @param {Record<string, number>} counts
 * @returns {string}
 */
function countsToBarHtml(counts) {
  const keys = Object.keys(counts || {});
  const total = keys.reduce((sum, key) => sum + Number((counts || {})[key] || 0), 0);
  if (!keys.length) return `<span class="mc-reveal-meta">waiting</span>`;
  return keys
    .map((label) => {
      const count = Number((counts || {})[label] || 0);
      const pct = total ? Math.round((100 * count) / total) : 0;
      const safe = String(label || "").replace(/</g, "&lt;");
      return `<div class="mc-reveal-row"><p class="mc-reveal-label">${safe}</p><span class="mc-reveal-meta">${pct}%</span><span class="mc-reveal-track"><span class="mc-reveal-fill" style="width:${pct}%"></span></span></div>`;
    })
    .join("");
}

/**
 * Live Meet totals: whole-class plus each renamed team.
 * @param {string} step
 * @param {Record<string, string>} bag
 * @param {Record<string, number>} classCounts
 */
function paintMeetPollTotals(step, bag, classCounts) {
  paintMeetChainChrome();
  return;
  const host = $("meet-poll-totals");
  const classEl = $("meet-poll-class");
  const teamsEl = $("meet-poll-teams");
  if (!host) return;
  if (step === "C") {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  if (classEl) {
    classEl.innerHTML = `<span class="meet-poll-kicker">Class-wide</span>${countsToBarHtml(
      classCounts
    )}`;
  }
  if (!teamsEl) return;
  const teams = assignedRosterTeams();
  if (!teams.length) {
    teamsEl.innerHTML = "";
    return;
  }
  teamsEl.innerHTML = teams
    .map((team) => {
      const memberIds = new Set((team.members || []).map((row) => Number(row.id)));
      const counts = {};
      Object.entries(bag || {}).forEach(([key, choice]) => {
        const sid = Number(String(key).replace(/^student:/, ""));
        if (!memberIds.has(sid)) return;
        const label = String(choice || "").trim();
        if (!label) return;
        counts[label] = (counts[label] || 0) + 1;
      });
      const name = String(team.name || "Team").trim() || "Team";
      return `<p class="meet-poll-team"><span class="meet-poll-kicker">Team · ${escapeHtml(
        name
      )}</span>${countsToBarHtml(counts)}</p>`;
    })
    .join("");
}

/**
 * Flag the current stage; left panes stay mounted. Advance never collapses left.
 * @param {string} name
 * @param {{scroll?: boolean, forceScroll?: boolean, scrollTarget?: HTMLElement|null, scrollBlock?: ScrollLogicalPosition}} [opts]
 */
function showPanel(name, opts = {}) {
  const prevStep = currentStep;
  currentStep = name === "live" ? "score" : name;
  const locked = isScoringLive();
  scoringLocked = locked;
  hideError("#ap-overlay-error");
  lockClassListPane();
  syncTeamFlowVisibility();

  document.querySelectorAll(".ap-panel[data-step]").forEach((el) => {
    const step = el.dataset.step || "";
    const isCurrent = step === currentStep || (currentStep === "score" && step === "score");
    const isValidate = step === "validate";
    el.classList.toggle("is-current", isCurrent);
    el.classList.toggle("is-locked", locked && step !== "score" && step !== "validate");
    if (isValidate) {
      const hide = currentStep !== "validate";
      el.hidden = hide;
      el.classList.toggle("hidden", hide);
    } else {
      el.hidden = false;
      el.classList.remove("hidden", "is-collapsed");
    }
  });

  if (name === "gamify" && !trackMode) {
    selectTrackMode("individual");
  }
  paintTeacherShell();
  paintHeaderDate();
  paintQuestionArtifact();
  updateStepSummaries();

  const shouldScroll =
    opts.scroll !== false &&
    (Boolean(opts.forceScroll) || prevStep !== currentStep);
  if (shouldScroll) {
    scrollActiveTrackStepIntoView(opts.scrollTarget || null, opts.scrollBlock || "start");
  }
}

/**
 * Enable Assign / Track / Rename from team count (min 1 = no teams).
 */
function syncTeamFlowVisibility() {
  const team = currentTeamCount() > 1;
  trackMode = team ? "team" : "individual";
  const rounds = $("ap-panel-rounds");
  if (rounds) {
    rounds.hidden = false;
    rounds.removeAttribute("hidden");
  }
  paintTeamsStripEnabled();
  syncScoreboardPreview();
  renderTeamsPanel();
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
    const label =
      ROUND_KIND_OPTIONS.find((option) => option.kind === draftRound.kind)?.label ||
      draftRound.kind;
    rounds.textContent = `Round ${setupRoundNumber} · ${label}`;
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
 * @param {Window|null} [reservedWin]
 * @returns {Window|null}
 */
function ensureLiveSessionOverlay(reservedWin = null) {
  const id = liveSessionId || readLiveSessionId();
  if (!id) return null;
  liveSessionId = id;
  const win = openLiveSessionOverlay(id, reservedWin || null, { classId });
  setJoinStripVisible(!win);
  return win;
}

/**
 * Open the join strip from the banner code chip (the only open affordance).
 * @param {MouseEvent|Event} [event]
 * @returns {Window|null}
 */
function openJoinStrip(event) {
  if (event) event.preventDefault();
  const reservedWin = reserveLiveSessionOverlay();
  return ensureLiveSessionOverlay(reservedWin);
}

/**
 * Show or hide the Attendance soft-reopen link when the overlay popup was blocked.
 * @param {boolean} show
 */
function setJoinStripVisible(show) {
  const strip = $("ap-join-strip");
  if (strip) strip.hidden = !show;
}

/**
 * Normalize a live join code for display and clipboard copy.
 * @param {unknown} code
 * @returns {string}
 */
function copyableJoinCode(code) {
  return String(code || "").trim().toUpperCase().replace(/\s+/g, "");
}

/**
 * Pull a session join code from a live-session API payload.
 * @param {any} payload
 * @returns {string}
 */
function joinCodeFromPayload(payload) {
  return copyableJoinCode(
    payload?.code ||
      payload?.session?.session_code ||
      payload?.live_session?.session_code ||
      ""
  );
}

/**
 * Show or hide the in-tab join-code billboard while a session is active.
 * @param {unknown} code
 * @param {{ ended?: boolean }} [opts]
 */

/**
 * Build the course + lesson token shown beside the join code (e.g. MCF3M M1C3).
 * @returns {string}
 */
function liveLessonBadgeText() {
  const course = String(root?.dataset.ontarioCode || "").trim().toUpperCase();
  const module = String(
    teacherState.live_module || $("live-module-select")?.value || "M1"
  ).toUpperCase();
  const slot = String(
    teacherState.live_slot || $("live-class-select")?.value || "C1"
  ).toUpperCase();
  const lesson = `${module}${slot}`;
  return course ? `${course} ${lesson}` : lesson;
}

/**
 * Paint the course/lesson chip in the live header join area.
 */
function paintLiveLessonBadge() {
  const el = $("live-lesson-badge");
  if (!(el instanceof HTMLElement)) return;
  el.textContent = liveLessonBadgeText();
}

function paintJoinBillboard(code, opts = {}) {
  paintLiveLessonBadge();
  const board = $("ap-join-billboard");
  const codeEl = $("ap-join-billboard-code");
  const raw = opts.ended ? "" : copyableJoinCode(code);
  const show = Boolean(raw) && !opts.ended;
  if (codeEl) codeEl.textContent = show ? raw : "····";
  if (board) board.hidden = !show;
  if (root && show) root.dataset.liveCode = raw;
  else if (root && !show) delete root.dataset.liveCode;
  if (!show) {
    const copied = $("ap-join-billboard-copied");
    if (copied) copied.hidden = true;
  }
}

/**
 * Copy the in-tab join code and flash brief confirmation.
 * @returns {Promise<void>}
 */
async function copyJoinBillboardCode() {
  const code = copyableJoinCode($("ap-join-billboard-code")?.textContent || "");
  if (!code || code === "····" || code === "—") return;
  try {
    await navigator.clipboard.writeText(code);
  } catch {
    const area = document.createElement("textarea");
    area.value = code;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
    } finally {
      area.remove();
    }
  }
  const copied = $("ap-join-billboard-copied");
  const btn = $("ap-join-billboard-copy");
  if (copied) copied.hidden = false;
  if (btn) btn.textContent = "Copied";
  if (joinBillboardCopyTimer) window.clearTimeout(joinBillboardCopyTimer);
  joinBillboardCopyTimer = window.setTimeout(() => {
    if (copied) copied.hidden = true;
    if (btn) btn.textContent = "Copy";
  }, 1600);
}

/**
 * Stop polling live-session attendees.
 */
function stopLiveSessionPolling() {
  if (sessionPollTimer) {
    clearInterval(sessionPollTimer);
    sessionPollTimer = null;
  }
  sessionPollMs = 0;
}

/**
 * Poll interval: ≤1s while an MC prompt is live, else 2s.
 * @returns {number}
 */
function desiredSessionPollMs() {
  return lastMcTally ? 1000 : 2000;
}

/**
 * Restart the session poll only when the interval should change.
 */
function syncLiveSessionPolling() {
  if (!liveSessionId && !readLiveSessionId()) return;
  const ms = desiredSessionPollMs();
  if (sessionPollTimer && sessionPollMs === ms) return;
  if (sessionPollTimer) {
    clearInterval(sessionPollTimer);
    sessionPollTimer = null;
  }
  sessionPollMs = ms;
  sessionPollTimer = window.setInterval(pollLiveSessionAttendees, ms);
}

/**
 * Sync ClassList to students currently in the live session (join and leave).
 * Same presence channel updates TEAMS max (= presentCount) and the
 * division-strength meter on JOIN and TEAMS. No second poll.
 * After scoring starts, refresh game state so late joiners appear on teams.
 * @param {Iterable<number>} ids
 * @param {Array<{student_id?:number,mood?:string}>} [attendees]
 */
async function applySessionPresentTicks(ids, attendees) {
  const next = new Set([...ids].map(Number).filter((n) => Number.isFinite(n) && n > 0));
  const prevSize = sessionPresentIds.size;
  sessionPresentIds = next;
  if (Array.isArray(attendees) && overlayState?.students) {
    const moodById = new Map(
      attendees
        .map((row) => [Number(row.student_id), row.mood || null])
        .filter(([sid]) => Number.isFinite(sid) && sid > 0)
    );
    for (const student of overlayState.students) {
      const sid = Number(student.id);
      if (moodById.has(sid)) student.mood = moodById.get(sid);
      const key = String(sid);
      if (Object.prototype.hasOwnProperty.call(sessionGamePoints, key)) {
        student.game_points = sessionGamePoints[key];
        student.session_points = sessionGamePoints[key];
      }
      if (Object.prototype.hasOwnProperty.call(sessionCareerTotals, key)) {
        student.career_total = sessionCareerTotals[key];
      }
    }
  }
  renderAttendanceList();
  updateStepSummaries();
  setNTeams(currentTeamCount());

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
 * Apply a teacher-state patch locally before the POST returns.
 * @param {Record<string, unknown>} body
 * @returns {typeof teacherState}
 */
function optimisticTeacherState(body) {
  if (body.advance) {
    const delta = body.advance === "next" ? 1 : -1;
    const idx = TEACHER_STAGES.indexOf(teacherState.stage);
    const next =
      TEACHER_STAGES[
        Math.max(0, Math.min(TEACHER_STAGES.length - 1, idx + delta))
      ];
    return { ...teacherState, stage: next };
  }
  return { ...teacherState, ...body };
}

/**
 * Show or hide the live-state strip without touching the deck.
 * A fault replaces the calm Reconnecting… line so a dead Meet is visible.
 * @param {boolean} visible
 * @param {string} [message]
 */
function setLiveReconnectBanner(visible, message) {
  const el = $("live-reconnect");
  if (el instanceof HTMLElement) el.hidden = !visible;
  const copy = el?.querySelector(".live-reconnect-copy");
  if (copy) {
    copy.textContent = message || "Reconnecting…";
  }
}

/**
 * Fetch live-session state and auto-mark present attendees on the roster.
 * Interval ticks skip when a poll is already in flight. Light polls omit
 * cards and scoreboard until ``state_seq`` moves. A failed full snapshot
 * retries once with ``?light=1`` so attendees keep updating. Poll failure
 * keeps the last calm frame and shows Reconnecting… / Retry.
 * @param {{full?: boolean, force?: boolean}} [opts]
 */
async function pollLiveSessionAttendees(opts = {}) {
  const id = liveSessionId || readLiveSessionId();
  if (!id) return;
  if (sessionPollInFlight && !opts.force) return;
  liveSessionId = id;
  sessionPollInFlight = true;
  const wantFull = Boolean(opts.full) || staffStateNeedsFull;
  const prevSeq = Number(teacherState.state_seq);
  try {
    const qs = wantFull ? "" : "?light=1";
    let payload;
    try {
      payload = await api(`/api/live-sessions/${id}/state${qs}`);
    } catch (err) {
      if (!wantFull) throw err;
      payload = await api(`/api/live-sessions/${id}/state?light=1`);
    }
    if (payload?.phase === "ended" || payload?.session?.status === "ended") {
      const fault = String(payload?.fault || "").trim();
      setLiveReconnectBanner(Boolean(fault), fault);
      paintJoinBillboard("", { ended: true });
      stopLiveSessionPolling();
      return;
    }
    if (payload?.error === "state unavailable" || payload?.fault) {
      setLiveReconnectBanner(true, String(payload?.fault || "").trim() || "Reconnecting…");
      return;
    }
    paintJoinBillboard(joinCodeFromPayload(payload));
    if (payload?.teacher_state) adoptTeacherState(payload.teacher_state);
    if (payload?.canvas_sync) paintTeacherCanvas(payload.canvas_sync);
    if (Array.isArray(payload?.class_list)) adoptClassListRows(payload.class_list);
    if (Array.isArray(payload?.groups)) lastGroups = payload.groups;
    if (Object.prototype.hasOwnProperty.call(payload || {}, "scoreboard")) {
      lastScoreboard = payload.scoreboard || null;
      if (overlayState && typeof overlayState === "object") {
        overlayState.scoreboard = lastScoreboard;
      }
    }
    if (Array.isArray(payload?.live_items)) {
      lastLiveItems = absorbSaveToCardSnapshot(payload.live_items);
    }
    if (Array.isArray(payload?.active_questions)) {
      lastActiveQuestions = payload.active_questions;
    }
    if (payload?.teams_spark !== undefined) {
      lastTeamsSpark = payload.teams_spark || null;
    }
    if (payload?.join_prompt !== undefined) {
      lastJoinPrompt = payload.join_prompt || null;
    }
    if (payload?.active_prompt !== undefined) {
      lastActivePrompt = payload.active_prompt || null;
    }
    if (payload?.live_metadata) lastLiveMetadata = payload.live_metadata;
    if (Array.isArray(payload?.question_cards)) {
      lastQuestionCards = questionCardsFromMetadata(payload.question_cards);
    }
    paintStageRail();
    const rows = Array.isArray(payload?.attendees) ? payload.attendees : [];
    const present = rows.filter((row) => !row?.left_at);
    const guests = present.filter((row) => Boolean(row.unmatched) || row.student_id == null);
    sessionGuests = guests.map((row) => ({
      participant_uuid: String(row.participant_uuid || ""),
      codename: String(row.codename || "Guest").trim() || "Guest",
      unmatched: true,
    }));
    syncAllowGuestsCheckbox(
      payload?.allow_unmatched_guests ?? payload?.session?.allow_unmatched_guests
    );
    if (payload?.game_points && typeof payload.game_points === "object") {
      sessionGamePoints = payload.game_points;
    }
    if (payload?.career_totals && typeof payload.career_totals === "object") {
      sessionCareerTotals = payload.career_totals;
    }
    await applySessionPresentTicks(
      present.map((row) => Number(row.student_id)),
      present
    );
    paintSlidesMetadata();
    paintLiveQuestionCards();
    if (payload?.active_media || payload?.session?.active_media) {
      const media = payload?.active_media || payload?.session?.active_media;
      paintActiveMediaStatus(media);
      paintQuestionArtifact(media);
    } else if (
      wantFull &&
      Object.prototype.hasOwnProperty.call(payload || {}, "active_media")
    ) {
      if (liveClassSeedMedia()) paintActiveMediaStatus(null);
      else paintQuestionArtifact(null);
    }
    adoptLifecycleResponseCounts(
      payload?.lifecycle_response_counts,
      payload?.lifecycle_rank_revs
    );
    applyMcTally(payload?.mc_tally);
    if (wantFull) {
      refreshLifecycleResults();
      ensureC1MediaSeeded();
    }
    const nextSeq = Number(
      payload?.state_seq ?? payload?.teacher_state?.state_seq
    );
    staffStateNeedsFull = false;
    setLiveReconnectBanner(false);
    if (!wantFull && Number.isFinite(nextSeq) && nextSeq !== prevSeq) {
      sessionPollInFlight = false;
      return pollLiveSessionAttendees({ full: true, force: true });
    }
  } catch (_) {
    setLiveReconnectBanner(true, "Live class state failed. Retry, or end the Meet if it stays down.");
  } finally {
    sessionPollInFlight = false;
  }
}

/**
 * Begin polling session joins until End Game / cancel.
 */
function startLiveSessionPolling() {
  stopLiveSessionPolling();
  pollLiveSessionAttendees();
  syncLiveSessionPolling();
}

$("live-reconnect-retry")?.addEventListener("click", () => {
  void pollLiveSessionAttendees({ full: true, force: true });
});

/**
 * postMessage body for the teacher Real-slice iframe (peel X updates both).
 * @param {any} media
 * @param {any} [params]
 */
function staffLiveMediaState(media, params) {
  const row = media || {};
  return {
    source: "lloves-staff-live",
    type: "live-media-state",
    student_controls_unlocked: Boolean(row.student_controls_unlocked),
    param_push: row.param_push || { a: false, b: false, c: false },
    param_frozen: row.param_frozen || { a: true, b: true, c: true },
    reveal_axes: Boolean(row.reveal_axes),
    reveal_lateral: Boolean(row.reveal_lateral),
    allow_3d_limited: Boolean(row.allow_3d_limited),
    show_z_axis: Boolean(row.show_z_axis),
    student_zoom: Number(row.student_zoom ?? 0),
    freeze_zoom: Boolean(row.freeze_zoom),
    surface_transparency: Number(row.surface_transparency ?? 0.75),
    freeze_surface: Boolean(row.freeze_surface),
    student_yaw_range: Number(row.student_yaw_range ?? 0),
    freeze_yaw: Boolean(row.freeze_yaw),
    frozen: Boolean(row.frozen),
    unlock_flags: row.unlock_flags || {},
    params: params || row.params || { a: 1, b: 0, c: 0 },
    artifact: row.artifact || null,
    stem: row.stem || SEED_MEDIA_STEM,
    entry_chip: row.entry_chip || "",
  };
}

/**
 * Teacher preview iframe for the C1 Real-slice. Controls live in the frame.
 * @param {any} media
 */
/**
 * True when the session media URL is this slot's seed URL.
 * @param {any} row
 * @param {{url?: string} | null} seed
 * @returns {boolean}
 */
function activeMediaUrlMatchesSeed(row, seed) {
  const rowUrl = String(row?.url || "").trim();
  const seedUrl = String(seed?.url || "").trim();
  return Boolean(rowUrl && seedUrl && rowUrl === seedUrl);
}

/**
 * Fill the staff student-copy editor from this slot's overlay/seed, not a
 * leftover stem from a previous URL.
 * @param {any} media
 */
function paintActiveMediaCopyEditor(media) {
  const editor = $("ap-media-copy-editor");
  const stemInput = $("ap-media-stem");
  const captionInput = $("ap-media-caption");
  const seed = liveClassSeedMedia();
  const row = media && typeof media === "object" ? media : {};
  const hasMedia = Boolean(String(seed?.url || row.url || "").trim());
  if (editor instanceof HTMLElement) {
    editor.hidden = !hasMedia;
  }
  if (!(stemInput instanceof HTMLInputElement)) return;
  const urlMatches = activeMediaUrlMatchesSeed(row, seed);
  const nextStem = String(
    (urlMatches ? row.stem : "") ||
      seed?.stem ||
      (urlMatches ? row.title : "") ||
      seed?.title ||
      ""
  ).trim();
  const nextCaption = String(
    (urlMatches ? row.caption : "") || seed?.caption || ""
  ).trim();
  if (document.activeElement !== stemInput) {
    stemInput.value = nextStem;
  }
  if (captionInput instanceof HTMLInputElement && document.activeElement !== captionInput) {
    captionInput.value = nextCaption;
  }
}

function paintActiveMediaStatus(media) {
  const preview = $("ap-media-preview");
  if (!preview) return;
  const rawUrl = String((media && media.url) || "").trim();
  const realSlice = rawUrl.includes("m1c1-c1-real-slice.html");
  const seed = liveClassSeedMedia();
  const mediaUrl = seed
    ? seed.url
    : realSlice
      ? ""
      : rawUrl;
  const row = media && typeof media === "object" ? media : {};
  const copySource =
    seed && !activeMediaUrlMatchesSeed(row, seed) ? seed : media || seed;
  paintActiveMediaCopyEditor(mediaUrl ? copySource : null);
  if (!mediaUrl) {
    preview.hidden = true;
    preview.removeAttribute("src");
    lastTeacherMediaSrc = "";
    paintQuestionArtifact(media);
    return;
  }
  const teacherSrc = mediaUrl.includes("?")
    ? `${mediaUrl}&role=teacher`
    : `${mediaUrl}?role=teacher`;
  const currentSrc = preview.getAttribute("src") || "";
  if (currentSrc !== teacherSrc) {
    preview.src = teacherSrc;
  }
  lastTeacherMediaSrc = teacherSrc;
  preview.hidden = false;
  preview.removeAttribute("hidden");
  if (media && media.url) {
    teacherState.active_media_ref = media.url;
  }
  paintQuestionArtifact(media);
  const params = (media && media.params) || { a: 1, b: 0, c: 0 };
  try {
    preview.contentWindow?.postMessage(
      staffLiveMediaState(media, params),
      window.location.origin
    );
  } catch (_) {
    /* preview may still be loading */
  }
}

/**
 * True when this course/module/slot is the MCF3M M1C1 parabola ride.
 * @returns {boolean}
 */
function usesC1RealSlice() {
  const ontario = String(root?.dataset.ontarioCode || "").toUpperCase();
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  return ontario === "MCF3M" && module === "M1" && slot === "C1";
}

/**
 * True when this live slot should seed the C2 Transformations Artifact.
 * Non-MCR3U C2 slots seed that iframe. MCF3M M1 C3 reuses it.
 * MCR3U uses the playlist media file (exploratory parents on M1 C2, the
 * questions Artifact on M1 C3, multi-parent media on M1 C4).
 * @returns {boolean}
 */
function usesC2Transforms() {
  const ontario = String(root?.dataset.ontarioCode || "").toUpperCase();
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || textRideSlot || "C1").toUpperCase();
  if (ontario === "MCR3U") return false;
  if (slot === "C2") return true;
  return ontario === "MCF3M" && module === "M1" && slot === "C3";
}

/**
 * True when MCR3U M1C1 should mount the nested square-root graph.
 * @returns {boolean}
 */
function usesMcr3uM1C1Media() {
  const ontario = String(root?.dataset.ontarioCode || "").toUpperCase();
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  return ontario === "MCR3U" && module === "M1" && slot === "C1";
}

/**
 * True when this C3 slot has no authored playlist media.
 * C2 always seeds Transformations (Artifact). MCF3M M1 C3 reuses that
 * iframe. Other C3 slots stay text-only unless playlist media is authored.
 * @param {string} [slot]
 * @returns {boolean}
 */
function isTextOnlyLiveSlot(slot) {
  const token = String(slot || teacherState.live_slot || "C1").toUpperCase();
  if (token === "C2") return false;
  if (liveClassSeedMedia()) return false;
  if (token !== "C3") return false;
  const metaSlot = String(lastLiveMetadata?.live_class || "").toUpperCase();
  if (metaSlot && metaSlot !== token) return true;
  return true;
}

/**
 * Course-specific Join/Play seed media, or null when this slot is text-only.
 * @returns {{url: string, title: string, stem: string} | null}
 */
function liveClassSeedMedia() {
  if (usesC2Transforms()) {
    return {
      url: C2_TRANSFORM_MEDIA_URL,
      title: C2_TRANSFORM_MEDIA_TITLE,
      stem: C2_TRANSFORM_MEDIA_TITLE,
    };
  }
  const configured = lastLiveMetadata?.media;
  const configuredUrl = String(configured?.file || "").trim();
  if (configuredUrl) {
    if (configuredUrl === SEED_MEDIA_URL) {
      return { url: SEED_MEDIA_URL, title: SEED_MEDIA_TITLE, stem: SEED_MEDIA_STEM };
    }
    if (configuredUrl === MCR3U_M1C1_MEDIA_URL) {
      return {
        url: MCR3U_M1C1_MEDIA_URL,
        title: MCR3U_M1C1_MEDIA_TITLE,
        stem: MCR3U_M1C1_MEDIA_STEM,
      };
    }
    const title = String(configured.title || configured.stem || "Live class media").trim();
    return {
      url: configuredUrl,
      title,
      stem: String(configured.stem || title).trim(),
      caption: String(configured.caption || "").trim(),
    };
  }
  if (usesC1RealSlice()) {
    return { url: SEED_MEDIA_URL, title: SEED_MEDIA_TITLE, stem: SEED_MEDIA_STEM };
  }
  if (usesMcr3uM1C1Media()) {
    return {
      url: MCR3U_M1C1_MEDIA_URL,
      title: MCR3U_M1C1_MEDIA_TITLE,
      stem: MCR3U_M1C1_MEDIA_STEM,
    };
  }
  return null;
}

/**
 * Seed the current slot's media after teacher state is restored.
 *
 * Must not run on first paint: default ``live_slot`` is C1, and posting
 * ``challenge=C1`` would overwrite a persisted C2/C3 session on refresh.
 */
async function ensureC1MediaSeeded() {
  const seed = liveClassSeedMedia();
  if (!seed) {
    const sessionId = liveSessionId || readLiveSessionId();
    if (sessionId && !mediaSeedInFlight) {
      mediaSeedInFlight = true;
      try {
        const res = await api(`/api/live-sessions/${sessionId}/active-media`);
        const url = String(res.active_media?.url || "");
        if (url.includes("m1c1-c1-real-slice.html")) {
          await postActiveMedia({ clear: true });
        } else {
          paintActiveMediaStatus(res.active_media || null);
        }
      } catch (_) {
        paintActiveMediaStatus(null);
      } finally {
        mediaSeedInFlight = false;
      }
    }
    return;
  }
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || mediaSeedInFlight || isTextOnlyLiveSlot()) return;
  mediaSeedInFlight = true;
  try {
    const res = await api(`/api/live-sessions/${sessionId}/active-media`);
    const currentUrl = String(res.active_media?.url || "");
    if (currentUrl === seed.url) {
      paintActiveMediaStatus(res.active_media);
      return;
    }
    paintActiveMediaCopyEditor(seed);
    const body = {
      url: seed.url,
      title: seed.title,
      entry_chip: "",
      student_controls_unlocked: false,
      frozen: false,
      cons_item: "",
      toast: "",
      toast_key: "",
      answers: [],
    };
    if (usesC1RealSlice()) {
      body.param_push = { a: false, b: false, c: false };
      body.param_frozen = { a: true, b: true, c: true };
      body.reveal_axes = false;
      body.reveal_lateral = false;
      body.allow_3d_limited = false;
      body.challenge = "C1";
      body.unlock_flags = { L0: true, L1: false, L2: false, L3: false, L4: false };
      body.params = { a: 1, b: 0, c: 0 };
      body.show_z_axis = false;
      body.surface_transparency = 0.75;
    }
    await postActiveMedia(body);
  } catch (_err) {
    paintActiveMediaStatus(null);
  } finally {
    mediaSeedInFlight = false;
  }
}

/**
 * POST set/swap/clear/patch for the current live session's media.
 * @param {Record<string, unknown>} body
 */
async function postActiveMedia(body) {
  const id = liveSessionId || readLiveSessionId();
  if (!id) {
    await ensureLiveSessionMinted();
  }
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) {
    throw new Error("Start the live class before pushing media.");
  }
  const res = await api(`/api/live-sessions/${sessionId}/active-media`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  paintActiveMediaStatus(res.active_media);
  if (res?.teacher_state) adoptTeacherState(res.teacher_state);
  return res.active_media;
}

let mintToastTimer = 0;

/**
 * Staff-only first-mint line. Never writes ``active_media.toast``.
 * @param {string} line
 */
function showStaffMintToast(line) {
  const el = $("live-mint-toast");
  if (!(el instanceof HTMLElement)) return;
  const text = String(line || "").trim();
  if (!text) return;
  el.textContent = text;
  el.hidden = false;
  window.clearTimeout(mintToastTimer);
  mintToastTimer = window.setTimeout(() => {
    el.hidden = true;
    el.textContent = "";
  }, 2800);
}

/**
 * Mint an Artifact question from an in-media button.
 * @param {Record<string, unknown>} data
 */
async function mintArtifactFromMedia(data) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) {
    await ensureLiveSessionMinted();
  }
  const id = liveSessionId || readLiveSessionId();
  if (!id) throw new Error("Start the live class before minting an Artifact.");
  const res = await api(`/api/live-sessions/${id}/artifacts`, {
    method: "POST",
    body: JSON.stringify({
      artifact_id: data.artifact_id,
      snapshot: data.snapshot,
      target_mode: data.target_mode || "graph",
      parent: data.parent || null,
      page_number: currentLivePageNumber(),
      slide_index: currentLivePageNumber(),
      hot_cold_visible: Boolean(data.hot_cold_visible),
      group_q: Boolean(data.group_q),
      accuracy_margin: data.accuracy_margin,
    }),
  });
  paintActiveMediaStatus(res.active_media);
  if (res?.teacher_state) adoptTeacherState(res.teacher_state);
  if (Array.isArray(res?.live_items)) {
    lastLiveItems = absorbSaveToCardSnapshot(res.live_items);
  }
  if (Array.isArray(res?.question_cards)) {
    lastQuestionCards = questionCardsFromMetadata(res.question_cards);
  }
  applyLiveMcImportPayload(res);
  paintLiveQuestionCards();
  paintQuestionArtifact(res.active_media);
  staffStateNeedsFull = true;
  await pollLiveSessionAttendees({ full: true, force: true });
  paintLiveQuestionCards();
  if (res?.first_mint && res?.toast) {
    showStaffMintToast(String(res.toast));
  }
  return res;
}

/**
 * Merge Show hot/cold, Group Q, and accuracy onto the Artifact media blob.
 * @param {Record<string, unknown>} data
 */
async function patchArtifactTeacherFlags(data) {
  const id = liveSessionId || readLiveSessionId();
  if (!id) return;
  const artifact = {
    ...((lastActiveMedia && lastActiveMedia.artifact) || {}),
    hot_cold_visible: Boolean(data.hot_cold_visible),
    group_q: Boolean(data.group_q),
    accuracy_margin: data.accuracy_margin,
  };
  return postActiveMedia({ artifact });
}

/**
 * Bind iframe → session patches. Artifact mint and C1 peel tools live in-frame.
 */
function bindActiveMediaControls() {
  paintActiveMediaStatus(null);
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data;
    if (
      data &&
      data.type === "artifact-mint" &&
      (data.source === "lloves-m1c2-transforms" ||
        data.source === "lloves-mcr3u-m1c3-parents")
    ) {
      mintArtifactFromMedia(data).catch((err) => showError("#ap-overlay-error", err));
      return;
    }
    if (
      data &&
      data.type === "artifact-teacher-flags" &&
      (data.source === "lloves-m1c2-transforms" ||
        data.source === "lloves-mcr3u-m1c3-parents")
    ) {
      patchArtifactTeacherFlags(data).catch((err) => showError("#ap-overlay-error", err));
      return;
    }
    if (!data || data.source !== "lloves-m1c1-c1" || data.type !== "params") return;
    const body = { params: data.params || {} };
    if (data.param_push && typeof data.param_push === "object") {
      body.param_push = data.param_push;
    }
    if (data.param_frozen && typeof data.param_frozen === "object") {
      body.param_frozen = data.param_frozen;
    }
    if (typeof data.reveal_axes === "boolean") body.reveal_axes = data.reveal_axes;
    if (typeof data.reveal_lateral === "boolean") body.reveal_lateral = data.reveal_lateral;
    if (typeof data.student_controls_unlocked === "boolean") {
      body.student_controls_unlocked = data.student_controls_unlocked;
    }
    if (typeof data.allow_3d_limited === "boolean") {
      body.allow_3d_limited = data.allow_3d_limited;
    }
    if (typeof data.show_z_axis === "boolean") body.show_z_axis = data.show_z_axis;
    if (Number.isFinite(Number(data.surface_transparency))) {
      body.surface_transparency = Number(data.surface_transparency);
    }
    window.clearTimeout(mediaPushTimer);
    mediaPushTimer = window.setTimeout(() => {
      postActiveMedia(body).catch((err) => showError("#ap-overlay-error", err));
    }, 350);
  });
}

/**
 * Keep the Allow guests checkbox in sync with the live session row.
 * @param {unknown} raw
 */
function syncAllowGuestsCheckbox(raw) {
  const box = $("ap-allow-guests");
  const chip = $("ap-guest-on-chip");
  const on = raw === true || raw === 1 || raw === "1" || String(raw).toLowerCase() === "true";
  if (box) box.checked = on;
  if (chip) chip.hidden = !on;
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
  if (Array.isArray(logContext?.logged_dates)) {
    return logContext.logged_dates.includes(iso);
  }
  const day = await api(`/api/classes/${classId}/attendance-day?date=${encodeURIComponent(iso)}`);
  return Boolean(day.logged);
}

/**
 * Confirm before overriding an already-logged school day.
 * Cancel leaves the prior choice untouched (caller must not proceed).
 * @param {string} iso
 * @returns {Promise<boolean>}
 */
async function confirmOverrideIfLogged(iso) {
  if (!iso || !(await isDateLogged(iso))) return true;
  return window.confirm(
    `Stored attendance and participation for ${iso} will be overridden. Continue?`
  );
}

/**
 * Paint the semester day-grid on the validate step.
 * @param {string} [selected]
 */
function showValidateGrid(selected) {
  const box = $("ap-day-grid");
  if (!box || !logContext) return;
  const logged = new Set(
    Array.isArray(logContext.logged_dates) ? logContext.logged_dates : []
  );
  const iso = renderSemesterDayGrid(box, logContext, {
    selected: selected || suggestedLogDay(logContext),
    minIso: String(logContext.today || ""),
    loggedDates: logged,
    onSelect: (value) => {
      const hidden = $("ap-valid-date");
      if (hidden) hidden.value = value;
    },
  });
  const hidden = $("ap-valid-date");
  if (hidden) hidden.value = iso;
  return iso;
}

/**
 * Mint a live join session (if needed) and open the Zoom-share overlay once.
 * @param {{ reservedWin?: Window|null }} [opts]
 * @returns {Promise<number>}
 */
async function ensureLiveSessionMinted(opts = {}) {
  const reservedWin = opts.reservedWin || null;
  liveSessionId = readLiveSessionId() || liveSessionId;
  if (!liveSessionId) {
    const res = await api(`/api/classes/${classId}/live-session/start`, {
      method: "POST",
      body: JSON.stringify({
        live_module: $("live-module-select")?.value || teacherState.live_module || "M1",
        live_slot: $("live-class-select")?.value || teacherState.live_slot || "C1",
      }),
    });
    liveSessionId = Number(res.live_session_id || res.live_session?.id || 0);
    if (root && liveSessionId) {
      root.dataset.liveSessionId = String(liveSessionId);
    }
    const mintedCode = joinCodeFromPayload(res);
    if (mintedCode) paintJoinBillboard(mintedCode);
    if (liveSessionId) {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", "live");
      url.searchParams.set("live_session_id", String(liveSessionId));
      window.history.replaceState({}, "", url.toString());
    }
  }
  if (liveSessionId) {
    ensureLiveSessionOverlay(reservedWin);
    startLiveSessionPolling();
  } else if (reservedWin && !reservedWin.closed) {
    try {
      reservedWin.close();
    } catch (_) {
      /* ignore */
    }
  }
  return liveSessionId;
}

/**
 * Apply the validate-step date and continue the deferred flow.
 * @param {{ reservedWin?: Window|null }} [opts]
 * @returns {Promise<void>}
 */
async function applyValidateDateChoice(opts = {}) {
  const gridIso = gridSelectedIso($("ap-day-grid"));
  const hiddenIso = String($("ap-valid-date")?.value || "").trim();
  const iso = gridIso || pickerValue($("ap-valid-date"), logContext) || hiddenIso;
  if (!iso) {
    showError(
      "#ap-overlay-error",
      new Error("Pick a valid school day from the calendar, then try again.")
    );
    if (opts.reservedWin && !opts.reservedWin.closed) {
      try {
        opts.reservedWin.close();
      } catch (_) {
        /* ignore */
      }
    }
    return;
  }
  const priorIso =
    String($("ap-meeting-date")?.value || "").trim() ||
    overlayState?.session?.meeting_date ||
    "";
  if (!(await confirmOverrideIfLogged(iso))) {
    // Cancel: keep prior date selection; do not begin/clobber.
    if (priorIso) {
      syncOverlayPickers(logContext, priorIso);
      showValidateGrid(priorIso);
    }
    if (opts.reservedWin && !opts.reservedWin.closed) {
      try {
        opts.reservedWin.close();
      } catch (_) {
        /* ignore */
      }
    }
    return;
  }
  hideError("#ap-overlay-error");
  syncOverlayPickers(logContext, iso);
  const attDate = $("ap-meeting-date");
  if (attDate) attDate.value = iso;
  const fn = pendingAction;
  pendingAction = null;
  if (fn) {
    await fn(iso, opts);
    return;
  }
  if (root?.dataset.apView === "live") {
    await proceedRunLiveBegin(iso, { reservedWin: opts.reservedWin || null });
    return;
  }
  showPanel("gamify");
}

/**
 * Begin Run Live Class for a resolved meeting date, then mint the join code.
 * @param {string} iso
 * @param {{ reservedWin?: Window|null }} [opts]
 */
async function proceedRunLiveBegin(iso, opts = {}) {
  syncOverlayPickers(logContext, iso);
  const meetingInput = $("ap-meeting-date");
  if (meetingInput) meetingInput.value = iso;
  overlayState = await api(`/api/classes/${classId}/begin`, {
    method: "POST",
    body: JSON.stringify({ meeting_date: iso }),
  });
  if (overlayState.game?.status === "live") {
    await ensureLiveSessionMinted(opts);
    await markClassSetComplete();
    exitSetClassPhase();
    openLiveScoring(overlayState);
    return;
  }
  if (liveSessionId) sessionPresentIds = new Set();
  scoringLocked = false;
  trackMode = null;
  await ensureLiveSessionMinted(opts);
  await markClassSetComplete();
  exitSetClassPhase();
  renderAttendanceList();
  showPanel("att");
}

/**
 * Open Run Live Class on Set Class until date + module + slot are chosen.
 */
export async function openRunLiveClass() {
  hideError("#ap-overlay-error");
  try {
    liveSessionId = readLiveSessionId() || liveSessionId;
    await loadContext();

    if (
      overlayState?.game?.status === "live" &&
      classSetIsComplete("live") &&
      !wantsFreshSetClass()
    ) {
      exitSetClassPhase();
      openLiveScoring(overlayState);
      if (liveSessionId) {
        ensureLiveSessionOverlay(null);
        startLiveSessionPolling();
      }
      return;
    }

    const today = String(logContext.today || "").trim();
    const todayLogged = await isDateLogged(today);
    const decision = resolveLogDate(logContext, { todayLogged }, { flow: "run_live" });

    pendingAction = async (pickedIso, actionOpts = {}) => {
      const iso =
        pickedIso ||
        gridSelectedIso($("ap-day-grid")) ||
        $("ap-valid-date")?.value ||
        decision.iso;
      await proceedRunLiveBegin(iso, {
        reservedWin: actionOpts.reservedWin || null,
      });
    };
    fillValidateHint();
    showValidateGrid(decision.iso || today);
    enterSetClassPhase();
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
 * Preserves an already-chosen class date on hidden inputs unless forced.
 * @param {string} [preferred]
 * @param {{forceValue?: boolean}} [opts]
 */
function bindAllPickers(preferred, opts = {}) {
  const onInvalid = (msg) => showError("#ap-overlay-error", new Error(msg));
  for (const id of ["ap-valid-date", "ap-meeting-date"]) {
    const el = $(id);
    if (!el || el.type === "hidden") {
      if (el && el.type === "hidden") {
        const existing = String(el.value || "").trim();
        if (opts.forceValue || !existing) {
          el.value = preferred || defaultSchoolDay(logContext) || existing;
        }
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
 * Team color for a roster id when count > 1.
 * Uses assigned teams when present; otherwise a balanced preview.
 * @param {number} studentId
 * @returns {string|""}
 */
/**
 * Assigned team label when teams > 1, else empty.
 * @param {number|string} studentId
 * @returns {string}
 */
function studentTeamLabel(studentId) {
  for (const team of previewRosterTeams()) {
    if ((team.members || []).some((member) => Number(member.id) === Number(studentId))) {
      return String(team.name || "").trim();
    }
  }
  if (!teacherState.run_as_group) return "";
  const projected = lastClassList.find(
    (row) => Number(row.student_id) === Number(studentId)
  );
  if (projected?.team?.name) return String(projected.team.name);
  for (const team of assignedRosterTeams()) {
    for (const member of team.members || []) {
      if (Number(member.id) === Number(studentId)) {
        return String(team.name || "").trim();
      }
    }
  }
  return "";
}

/**
 * Compact number for ClassList Course / Game columns.
 * @param {unknown} value
 * @returns {string}
 */
function classListPts(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "0";
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

/**
 * Team color for a roster id from preview assignment or committed teams.
 * @param {number} studentId
 * @returns {string}
 */
function studentTeamColor(studentId) {
  for (const team of previewRosterTeams()) {
    if ((team.members || []).some((member) => Number(member.id) === Number(studentId))) {
      return team.color || teamColorByIndex(Number(team.sort_order) || 0);
    }
  }
  const nTeams = currentTeamCount();
  if (!teacherState.run_as_group || nTeams < 2) return "";
  const projected = lastClassList.find(
    (row) => Number(row.student_id) === Number(studentId)
  );
  if (projected?.team?.color) return String(projected.team.color);
  const assigned = assignedRosterTeams();
  if (assigned.length) {
    for (const team of assigned) {
      for (const member of team.members || []) {
        if (Number(member.id) === Number(studentId)) {
          return team.color || teamColorByIndex(Number(team.sort_order) || 0);
        }
      }
    }
  }
  const presentIds = overlayState?.present_ids || [];
  const present = new Set(presentIds.length ? presentIds : selectedPresent());
  const pool = (overlayState?.students || []).filter((row) =>
    present.size ? present.has(row.id) : true
  );
  if (present.size && !present.has(Number(studentId)) && !present.has(studentId)) return "";
  const students = sortStudents(pool, nameSort);
  const ranked = [...students].sort(
    (a, b) => (Number(b.career_total) || 0) - (Number(a.career_total) || 0)
  );
  const scores = Array.from({ length: nTeams }, () => 0);
  const sizes = Array.from({ length: nTeams }, () => 0);
  const colors = new Map();
  for (const row of ranked) {
    let best = 0;
    for (let i = 1; i < nTeams; i += 1) {
      if (scores[i] < scores[best] || (scores[i] === scores[best] && sizes[i] < sizes[best])) {
        best = i;
      }
    }
    scores[best] += Number(row.career_total) || 0;
    sizes[best] += 1;
    colors.set(Number(row.id), teamColorByIndex(best));
  }
  return colors.get(Number(studentId)) || "";
}

/**
 * Students used for a tentative Balanced / Random / Manual class-list preview.
 * Prefers students who have already joined; otherwise the visible roster.
 * @returns {any[]}
 */
function previewRosterPool() {
  const visible = classListVisibleStudents(projectedClassListStudents());
  const presentIds = new Set(selectedPresent());
  if (presentIds.size) {
    const joined = visible.filter((row) => presentIds.has(Number(row.id)));
    if (joined.length) return joined;
  }
  return visible;
}

/**
 * Preview team buckets for the selected assign mode without committing.
 * @returns {any[]}
 */
function previewRosterTeams() {
  if (teacherState.groups_configured && assignedRosterTeamsCommitted().length) {
    return [];
  }
  if (!lastAssignMode || currentTeamCount() < 2) return [];
  const nTeams = currentTeamCount();
  const students = previewRosterPool();
  const buckets = Array.from({ length: nTeams }, (_, index) => ({
    id: -(index + 1),
    name: `Team ${index + 1}`,
    color: teamColorByIndex(index),
    sort_order: index,
    members: [],
  }));
  if (lastAssignMode === "manual") {
    const steps = [...document.querySelectorAll("#ap-manual-list .team-step")];
    if (steps.length) {
      for (const el of steps) {
        const sid = Number(el.dataset.studentId);
        const idx = Math.max(0, Math.min(nTeams - 1, Number(el.dataset.teamIndex) || 0));
        const student = students.find((row) => Number(row.id) === sid);
        if (student) buckets[idx].members.push(student);
      }
      return buckets;
    }
  }
  const ordered = students.slice().sort((a, b) => {
    const score = (Number(b.career_total) || 0) - (Number(a.career_total) || 0);
    if (score) return score;
    return String(a.codename || "").localeCompare(String(b.codename || ""));
  });
  if (lastAssignMode === "random") {
    const same = lastRandomPreviewIds.length === ordered.length &&
      ordered.every((row) => lastRandomPreviewIds.includes(Number(row.id)));
    if (same) {
      ordered.sort(
        (a, b) => lastRandomPreviewIds.indexOf(Number(a.id)) - lastRandomPreviewIds.indexOf(Number(b.id))
      );
    } else {
      for (let i = ordered.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [ordered[i], ordered[j]] = [ordered[j], ordered[i]];
      }
      lastRandomPreviewIds = ordered.map((row) => Number(row.id));
    }
  } else {
    lastRandomPreviewIds = [];
  }
  ordered.forEach((student, index) => {
    const snake = Math.floor(index / nTeams) % 2 === 1;
    const slot = index % nTeams;
    const teamIndex = lastAssignMode === "balanced" && snake ? nTeams - 1 - slot : slot;
    buckets[teamIndex].members.push(student);
  });
  return buckets;
}

function assignedRosterTeamsCommitted() {
  const source = lastGroups.length ? lastGroups : overlayState?.teams || [];
  return source
    .filter((team) => team && team.name !== "Class")
    .slice()
    .sort((a, b) => (Number(a.sort_order) || 0) - (Number(b.sort_order) || 0));
}

function assignedRosterTeams() {
  const source = lastGroups.length ? lastGroups : overlayState?.teams || [];
  return source
    .filter((team) => team && team.name !== "Class")
    .slice()
    .sort((a, b) => (Number(a.sort_order) || 0) - (Number(b.sort_order) || 0));
}

/**
 * True when ClassList regroups by team with name separators.
 * Count 1 stays flat; grouping needs an assign commit (teams > 1).
 * @returns {boolean}
 */
function classListGroupsByTeam() {
  if (previewRosterTeams().length >= 1) return true;
  return Boolean(teacherState.run_as_group) && assignedRosterTeams().length >= 1;
}

/**
 * Class List rows. Hide Absent filters locally so the checkbox does not
 * wait for a full `/state` rebuild.
 * @param {any[]} students
 * @returns {any[]}
 */
function classListVisibleStudents(students) {
  const roster = Array.isArray(students) ? students : [];
  if (!teacherState.hide_absent) return roster;
  return roster.filter((row) => {
    if (typeof row.present === "boolean") return row.present;
    return sessionPresentIds.has(Number(row.id ?? row.student_id));
  });
}

/**
 * Merge a class_list snapshot into the durable full-roster cache.
 * @param {any[]} rows
 */
function adoptClassListRows(rows) {
  const incoming = Array.isArray(rows) ? rows : [];
  if (!incoming.length) return;
  const byId = new Map(
    lastClassListFull.map((row) => [Number(row.student_id ?? row.id), row])
  );
  for (const row of incoming) {
    const id = Number(row.student_id ?? row.id);
    if (!id) continue;
    byId.set(id, { ...(byId.get(id) || {}), ...row, id, student_id: id });
  }
  lastClassListFull = [...byId.values()];
  lastClassList = lastClassListFull;
}

/**
 * Normalize the server class_list projection for existing roster renderers.
 * @returns {any[]}
 */
function projectedClassListStudents() {
  const source = lastClassListFull.length ? lastClassListFull : lastClassList;
  if (!source.length) return overlayState?.students || [];
  const existing = new Map(
    (overlayState?.students || []).map((row) => [Number(row.id), row])
  );
  return source.map((row) => {
    const id = Number(row.student_id ?? row.id);
    return {
      ...(existing.get(id) || {}),
      ...row,
      id,
      codename: row.codename || existing.get(id)?.codename || "",
    };
  });
}

/**
 * Roster order for ClassList: team groups after assign, else one flat list.
 * @param {any[]} students
 * @returns {{key: string, name: string, color: string, students: any[]}[]}
 */
function classListRosterOrder(students) {
  const roster = sortStudents(students || [], nameSort);
  if (!classListGroupsByTeam()) {
    return [{ key: "flat", name: "", color: "", students: roster }];
  }
  const seen = new Set();
  const groups = [];
  for (const team of previewRosterTeams().length ? previewRosterTeams() : assignedRosterTeams()) {
    const memberIds = new Set((team.members || []).map((row) => Number(row.id)));
    const members = roster.filter((row) => memberIds.has(Number(row.id)));
    for (const row of members) seen.add(Number(row.id));
    if (!members.length) continue;
    const sortOrder = Number(team.sort_order) || 0;
    groups.push({
      key: String(team.id || sortOrder),
      name: String(team.name || `Team ${sortOrder + 1}`).trim() || `Team ${sortOrder + 1}`,
      color: team.color || teamColorByIndex(sortOrder),
      students: members,
    });
  }
  const leftover = roster.filter((row) => !seen.has(Number(row.id)));
  if (leftover.length) {
    groups.push({ key: "unassigned", name: "", color: "", students: leftover });
  }
  return groups;
}

/**
 * Append one join-only attendance row (display-only; no click toggles).
 * @param {HTMLElement} list
 * @param {any} student
 * @param {Set<any>} checked
 */
function appendAttendanceStudentRow(list, student, checked) {
  const present =
    typeof student.present === "boolean"
      ? student.present
      : checked.has(student.id);
  const late = sessionLateIds.has(student.id) || Boolean(student.late);
  const row = document.createElement("div");
  row.className = `ap-att-row${present ? " is-present" : ""}${late ? " is-late" : ""}`;
  row.dataset.studentId = String(student.id);
  row.setAttribute("aria-pressed", present ? "true" : "false");
  const mark = late ? "L" : present ? "✓" : "";
  const teamColor = studentTeamColor(student.id);
  if (teamColor) {
    row.classList.add("has-team-color");
    row.style.setProperty("--team", teamColor);
  }
  const course = classListPts(
    student.career_total ?? sessionCareerTotals[String(student.id)] ?? 0
  );
  const game = classListPts(
    student.game_points ??
      student.session_points ??
      sessionGamePoints[String(student.id)] ??
      0
  );
  const who = displayName(student);
  row.innerHTML = `<span class="ap-att-check" aria-hidden="true">${mark}</span><span class="ap-att-name">${escapeHtml(who)}</span><span class="ap-att-course" title="Course">${escapeHtml(course)}</span><span class="ap-att-game" title="Game">${escapeHtml(game)}</span><button type="button" class="ap-att-plus" data-kind="student" data-id="${Number(student.id)}" data-amount="1" data-pop-name="${escapeHtml(who)}" ${present ? "" : "disabled"} aria-label="Add one point">+1</button>`;
  list.appendChild(row);
}

/**
 * Draw join-only attendance rows (display-only; no click toggles).
 * After TEAMS assign with count > 1, rows regroup under team-name separators.
 * Beat 22b: TEAMS hides absent / not-yet-joined roster names.
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
  const grouped = classListGroupsByTeam();
  const teamsPresentOnly = Boolean(teacherState.hide_absent);
  list.dataset.grouped = grouped ? "1" : "0";
  list.dataset.presentOnly = teamsPresentOnly ? "1" : "0";
  list.dataset.showTeam = "0";
  const cols = $("ap-att-cols");
  if (cols) {
    cols.hidden = false;
    cols.dataset.showTeam = "0";
  }
  for (const group of classListRosterOrder(classListVisibleStudents(projectedClassListStudents()))) {
    if (grouped && group.name) {
      const sep = document.createElement("div");
      sep.className = "ap-att-team-sep";
      sep.setAttribute("role", "separator");
      sep.dataset.teamKey = group.key;
      if (group.color) sep.style.setProperty("--team", group.color);
      const presentTeam = group.students.some((stu) => checked.has(stu.id));
      const teamId = Number(group.key);
      sep.innerHTML = `<span class="ap-att-team-sep-name">${escapeHtml(group.name)}</span><button type="button" class="ap-att-plus" data-kind="team" data-id="${teamId}" data-amount="1" data-rule="each_member" data-pop-name="${escapeHtml(group.name)}" ${presentTeam && Number.isFinite(teamId) && teamId > 0 ? "" : "disabled"} aria-label="Add one point to ${escapeHtml(group.name)}">+1</button>`;
      list.appendChild(sep);
    }
    for (const student of group.students) {
      appendAttendanceStudentRow(list, student, checked);
    }
  }
  for (const guest of sessionGuests) {
    const row = document.createElement("div");
    row.className = "ap-att-row is-present is-guest";
    row.dataset.participantUuid = guest.participant_uuid;
    row.setAttribute("aria-pressed", "true");
    row.innerHTML = `<span class="ap-att-check" aria-hidden="true">✓</span><span class="ap-att-name">${escapeHtml(guest.codename)} <span class="ap-guest-flag">guest</span></span>`;
    list.appendChild(row);
  }
  updateAttCount();
  paintDivisionMeter();
}

/**
 * Live present count in the header and class-list pane.
 */
function updateAttCount() {
  const n = selectedPresent().length + sessionGuests.length;
  const el = $("ap-att-count");
  if (el) el.textContent = `Attendance: ${n}`;
  const summary = $("ap-att-summary");
  if (summary) {
    summary.textContent = `(${projectedClassListStudents().length + sessionGuests.length})`;
  }
  const present = $("live-header-present");
  if (present) present.textContent = `Present ${n}`;
}

/**
 * True when a supplied round, or the active live round, is a break.
 * @param {{kind?: string}|null} [round]
 * @returns {boolean}
 */
function isBreakRound(round = null) {
  if (round?.kind) return String(round.kind) === "break";
  const game = overlayState?.game || {};
  if (game.round_kind) return String(game.round_kind) === "break";
  const rounds = game.rounds || [];
  const n = Number(game.round) || 1;
  return String(rounds[n - 1]?.kind || "") === "break";
}

/**
 * True when the active live round is Open Question.
 * @returns {boolean}
 */
function isOpenQuestionRound() {
  if (isBreakRound()) return false;
  const game = overlayState?.game || {};
  if (game.round_kind) return String(game.round_kind) === "open";
  const rounds = game.rounds || [];
  const n = Number(game.round) || 1;
  const row = rounds[n - 1];
  if (row?.kind) return row.kind === "open";
  const title = String(game.round_title || "").toLowerCase();
  return title.includes("open question") || title === "";
}

/**
 * True when current round kind is Team Challenge.
 * @returns {boolean}
 */
function isChallengeRound() {
  if (isBreakRound()) return false;
  const game = overlayState?.game || {};
  if (game.round_kind) return String(game.round_kind) === "challenge";
  const rounds = game.rounds || [];
  const n = Number(game.round) || 1;
  const row = rounds[n - 1];
  if (row?.kind) return row.kind === "challenge";
  const title = String(game.round_title || "").toLowerCase();
  return title.includes("team challenge") || title.includes("challenge");
}

/**
 * True when scoring uses a profile matrix (Open Question or Team Challenge).
 * @returns {boolean}
 */
function isProfileMatrixRound() {
  return isOpenQuestionRound() || isChallengeRound();
}

/**
 * Paint locked summaries for every completed round before the active round.
 * @param {any} state
 */
function renderPastRounds(state) {
  const box = $("ap-score-past-rounds");
  if (!box) return;
  const game = state?.game || {};
  const currentRound = Number(game.round) || 1;
  const pastRounds = (game.rounds || []).slice(0, Math.max(0, currentRound - 1));
  box.hidden = pastRounds.length === 0;
  box.innerHTML = pastRounds
    .map((round, index) => {
      const kindLabel =
        ROUND_KIND_OPTIONS.find((option) => option.kind === round.kind)?.label ||
        round.kind ||
        `Round ${index + 1}`;
      const title = round.title || round.round_title || kindLabel;
      const minutes =
        Number(round.minutes) || Number(round.duration_sec) / 60;
      return `<div class="ap-past-round">
        <span>Round ${index + 1} · ${escapeHtml(title)}${minutes > 0 ? ` · ${minutes} min` : ""}</span>
        <span class="ap-past-round-lock">Locked</span>
      </div>`;
    })
    .join("");
}

/**
 * Update round labels, timer, history, and break-only scoring visibility.
 * @param {any} state
 * @returns {boolean} Whether the active round is a break.
 */
function renderScoringRoundChrome(state) {
  const game = state?.game || {};
  const n = Number(game.round) || 1;
  const currentRound = (game.rounds || [])[n - 1] || null;
  const kindLabel =
    ROUND_KIND_OPTIONS.find(
      (option) => option.kind === (game.round_kind || currentRound?.kind)
    )?.label || "";
  const title = game.round_title || currentRound?.title || kindLabel || `Round ${n}`;
  const label = $("ap-round-label");
  if (label) label.textContent = `Round ${n} · ${title}`;
  roundEndsAtMs = lockRoundDeadline(roundEndsAtMs, game.round_ends_at_ms);
  paintLiveClock();
  renderPastRounds(state);

  const breakRound = isBreakRound();
  const breakBanner = $("ap-break-banner");
  if (breakBanner) {
    breakBanner.hidden = !breakRound;
    breakBanner.textContent = breakRound
      ? `Break in progress · ${title}. Scoring is paused.`
      : "";
  }
  const scoreBody = document.querySelector("#ap-panel-score .ap-score-body");
  if (scoreBody instanceof HTMLElement) scoreBody.hidden = breakRound;
  const meta = $("ap-live-meta");
  if (meta) meta.hidden = breakRound;
  const tabs = $("ap-score-team-tabs");
  if (tabs && breakRound) {
    tabs.hidden = true;
    tabs.innerHTML = "";
  }
  window.dispatchEvent(
    new CustomEvent("lloves-score-round", {
      detail: {
        roundKind: game.round_kind || currentRound?.kind || "",
        liveSessionId: liveSessionId || Number(root?.dataset.liveSessionId || 0),
        classId,
        overlayState: state,
        breakRound,
      },
    })
  );
  return breakRound;
}

/**
 * Route to the scoring accordion step after begin/rename/start.
 * @param {any} state
 * @param {{stayOnScore?: boolean}} [opts]
 */
async function openLiveScoring(state, opts = {}) {
  overlayState = state;
  scoringLocked = true;
  await ensureOpenProfilesLoaded();
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
  const breakRound = renderScoringRoundChrome(state);
  const scoreEnd = $("ap-score-end");
  const scoreCancel = $("ap-score-cancel");
  if (breakRound) {
    pendingTeam = null;
    liveStamp = "";
    $("ap-score-list") && ($("ap-score-list").innerHTML = "");
    $("ap-live-teams") && ($("ap-live-teams").innerHTML = "");
    renderOpenProfileBar();
  } else if (isIndividual) {
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
 * Canonical class date: open session first, then picker, then default.
 * Preferring session prevents loadContext/bindAllPickers from clobbering
 * the teacher-chosen day into “today” before attendance / end-game writes.
 * @returns {string}
 */
function sessionIso() {
  const fromSession = String(overlayState?.session?.meeting_date || "").trim();
  if (fromSession) return fromSession;
  const fromAtt =
    $("ap-meeting-date")?.value || pickerValue($("ap-meeting-date"), logContext);
  if (fromAtt) return fromAtt;
  return defaultSchoolDay(logContext) || todayISO();
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
 * Set individual vs team from count (no Individual/Teams toggle).
 * @param {"individual"|"team"} mode
 */
function selectTrackMode(mode) {
  trackMode = mode;
  if (mode === "individual" && draftRound.kind !== "open") {
    draftRound = { kind: "open", minutes: 20, title: "" };
  }
  if (mode === "individual" && nextDraftRound.kind !== "open") {
    nextDraftRound = { kind: "open", minutes: 10, title: "" };
  }
  paintTeamsStripEnabled();
  syncScoreboardPreview();
  if (currentStep === "rounds") renderRoundsPanel();
  updateStepSummaries();
  const nextMode = mode === "team" ? "teams" : "individual";
  if (teacherState.groups_configured && teacherState.teams_mode !== nextMode) {
    teacherState.teams_mode = nextMode;
    patchTeacherState({ teams_mode: nextMode }, { silent: true });
  }
  renderAttendanceList();
}

/**
 * Confirm Quit, close the Zoom-share overlay, then let the caller submit.
 * @param {HTMLFormElement|null|undefined} form
 * @returns {boolean} false when the teacher cancelled the confirm
 */
function confirmQuitAndCloseOverlay(form) {
  if (!(form instanceof HTMLFormElement)) return false;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return false;
  closeLiveSessionOverlay();
  return true;
}

$("live-quit-class-form")?.addEventListener("submit", (event) => {
  const form = event.currentTarget;
  if (!(form instanceof HTMLFormElement)) return;
  if (!confirmQuitAndCloseOverlay(form)) {
    event.preventDefault();
  }
});

for (const id of ["ap-validate-cancel", "ap-score-cancel"]) {
  $(id)?.addEventListener("click", () => {
    const form = $("live-quit-class-form");
    if (form instanceof HTMLFormElement) {
      if (!confirmQuitAndCloseOverlay(form)) return;
      form.submit();
      return;
    }
    if (id === "ap-score-cancel") {
      if (!window.confirm("Quit scoring? Scores already logged stay registered.")) return;
    }
    cancelOverlay();
  });
}

document.querySelectorAll("[data-track-nav='quit']").forEach((btn) => {
  if (btn.id === "ap-score-cancel" || btn.id === "ap-validate-cancel") return;
  btn.addEventListener("click", () => {
    const form = $("live-quit-class-form");
    if (form instanceof HTMLFormElement) {
      if (!confirmQuitAndCloseOverlay(form)) return;
      form.submit();
      return;
    }
    cancelOverlay();
  });
});

$("ap-allow-guests")?.addEventListener("change", async (event) => {
  const box = event.currentTarget;
  const id = liveSessionId || readLiveSessionId();
  if (!id) return;
  const allowed = Boolean(box?.checked);
  try {
    const payload = await api(`/api/live-sessions/${id}/guests`, {
      method: "POST",
      body: JSON.stringify({ allow_unmatched_guests: allowed }),
    });
    syncAllowGuestsCheckbox(payload?.allow_unmatched_guests);
  } catch (err) {
    if (box) box.checked = !allowed;
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
      await withValidatedDate(sessionIso(), async (meeting) => {
        const ids = selectedPresent();
        if (!ids.length) {
          throw new Error(
            "No students have joined yet. Share the live session code, then continue when someone is present."
          );
        }
        // Persist present flags and advance game status to ``teams`` before assign.
        overlayState = await api(`/api/classes/${classId}/game/attendance`, {
          method: "POST",
          body: JSON.stringify({ present_ids: ids, meeting_date: meeting }),
        });
        renderTeamsPanel();
        showPanel("teams");
      });
      return;
    }
    await withValidatedDate(sessionIso(), async (meeting) => {
      const ids = selectedPresent();
      // Park on rounds (Class team) so Start Round uses the same start-rounds path.
      overlayState = await api(`/api/classes/${classId}/game/ungamified`, {
        method: "POST",
        body: JSON.stringify({
          present_ids: ids,
          meeting_date: meeting,
          go_live: false,
        }),
      });
      draftRound = { kind: "open", minutes: 20, title: "" };
      nextDraftRound = { kind: "open", minutes: 10, title: "" };
      setupRoundNumber = 1;
      pendingScoreboard = false;
      renderRoundsPanel();
      showPanel("rounds");
    });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-validate-apply")?.addEventListener("click", () => {
  const reservedWin = reserveLiveSessionOverlay();
  applyValidateDateChoice({ reservedWin }).catch((err) => showError("#ap-overlay-error", err));
});

$("ap-join-billboard-copy")?.addEventListener("click", (event) => {
  event.preventDefault();
  copyJoinBillboardCode();
});

$("ap-join-billboard-code")?.addEventListener("click", openJoinStrip);

const TEAM_COLORS = [
  "#c8102e",
  "#0b3d91",
  "#ffb81c",
  "#00843d",
  "#7b2d8e",
  "#e87722",
  "#00a3e0",
  "#5c3317",
];

/**
 * ESPN-bar color for a 0-based team index.
 * @param {number} sortOrder
 * @returns {string}
 */
function teamColorByIndex(sortOrder) {
  return TEAM_COLORS[Math.max(0, Number(sortOrder) || 0) % TEAM_COLORS.length];
}

/**
 * Current stepper value (1 = no teams).
 * @returns {number}
 */
function currentTeamCount() {
  if (teacherState.groups_configured && lastGroups.length) {
    return Math.max(2, lastGroups.length);
  }
  const raw = Number($("ap-n-teams")?.value);
  if (!Number.isFinite(raw)) return 2;
  return Math.max(2, Math.round(raw));
}

/**
 * Present-student count for the TEAMS stepper and division meter.
 * Prefers the live ClassList selection; empty overlay ``present_ids``
 * must not pin the max at a low hard-cap.
 * @returns {number}
 */
function presentCountForTeams() {
  const selected = selectedPresent().length;
  if (selected) return selected;
  const overlay = overlayState?.present_ids;
  return Array.isArray(overlay) ? overlay.length : 0;
}

/**
 * Team-count bounds from present students. Min 1 = no teams.
 * Max is the present count (not a low hard-cap).
 * @returns {{min:number, max:number}}
 */
function nTeamsBounds() {
  const present = presentCountForTeams();
  return { min: 2, max: Math.max(2, present) };
}

/**
 * Even-split division band for N present students on K teams.
 * Count 1 is individuals. Mirrors ``teams.division_strength``.
 * @param {number} presentCount
 * @param {number} teamCount
 * @returns {"individuals"|"optimal"|"okay"|"not_recommended"}
 */
function divisionStrength(presentCount, teamCount) {
  const n = Math.max(0, Math.round(Number(presentCount) || 0));
  const k = Math.round(Number(teamCount) || 0);
  if (k <= 1) return "individuals";
  if (n < 1 || k > n) return "not_recommended";
  const low = Math.floor(n / k);
  const remainder = n % k;
  if (low < 2) return "not_recommended";
  if (n >= 4 && remainder <= 1) return "optimal";
  return "okay";
}

/**
 * Paint the compact division-strength meter beside +/- .
 * Hidden at count 1 (individuals). Updates live with the stepper.
 */
function paintDivisionMeter() {
  const meter = $("ap-division-meter");
  if (!(meter instanceof HTMLElement)) return;
  const teamCount = currentTeamCount();
  const present = presentCountForTeams();
  const band = divisionStrength(present, teamCount);
  const labelEl = $("ap-division-meter-label");
  if (band === "individuals" || teacherState.groups_configured) {
    meter.hidden = true;
    meter.removeAttribute("data-band");
    meter.removeAttribute("aria-valuenow");
    meter.removeAttribute("aria-valuetext");
    meter.removeAttribute("title");
    if (labelEl) labelEl.textContent = "";
    return;
  }
  const labels = {
    optimal: { short: "Optimal", title: "Optimal — even split, teams of 2+" },
    okay: { short: "Okay", title: "Okay — usable even-split leftover" },
    not_recommended: {
      short: "Not recommended",
      title: "Not recommended — uneven or singleton teams",
    },
  };
  const copy = labels[band];
  const valueNow = band === "optimal" ? 2 : band === "okay" ? 1 : 0;
  meter.hidden = false;
  meter.dataset.band = band;
  meter.setAttribute("aria-valuenow", String(valueNow));
  meter.setAttribute("aria-valuetext", copy.short);
  meter.title = copy.title;
  if (labelEl) labelEl.textContent = copy.short;
}

/**
 * Show Assign / Track / Rename only when count > 1.
 */
function paintTeamsStripEnabled() {
  const team = currentTeamCount() > 1;
  const configured = Boolean(teacherState.groups_configured);
  const assign = $("live-teams-assign");
  if (assign) {
    assign.hidden = configured;
    assign.setAttribute("aria-disabled", !configured && team ? "false" : "true");
  }
  const opts = $("ap-track-game-opts");
  if (opts) {
    opts.hidden = false;
    opts.setAttribute("aria-disabled", team ? "false" : "true");
    opts.querySelectorAll("input").forEach((box) => {
      if (box instanceof HTMLInputElement) box.disabled = !team;
    });
  }
  const rename = $("ap-teams-rename");
  if (rename instanceof HTMLButtonElement) {
    rename.hidden = !configured;
    rename.disabled = !configured;
    rename.setAttribute("aria-disabled", configured ? "false" : "true");
  }
  if (!team || configured) closeTeamsPops({ keepRename: true });
  paintDivisionMeter();
  paintRoundStrip();
}

/**
 * Paint leftover ROUND chrome when those nodes still exist.
 * Page-4+ no longer ships the type dropdown or SET button.
 */
function paintRoundStrip() {
  const picks = $("live-round-picks");
  if (picks) {
    picks.hidden = false;
    picks.removeAttribute("hidden");
  }
  const select = $("live-round-type");
  if (select instanceof HTMLSelectElement && document.activeElement !== select) {
    select.value = readRoundTypeFromState();
  }
}

/**
 * Selected pedagogical round from persisted flags (one type).
 * @returns {"minds_on"|"action"|"consolidation"}
 */
function readRoundTypeFromState() {
  const flags = teacherState.round_flags || {};
  if (flags.consolidation) return "consolidation";
  if (flags.action) return "action";
  return "minds_on";
}

/**
 * Read the ROUND type dropdown for a SET commit.
 * @returns {{minds_on: boolean, action: boolean, consolidation: boolean}}
 */
function readRoundFlags() {
  const flags = { minds_on: false, action: false, consolidation: false };
  const select = $("live-round-type");
  const value =
    select instanceof HTMLSelectElement ? select.value : readRoundTypeFromState();
  if (value in flags) flags[value] = true;
  else flags.minds_on = true;
  return flags;
}

/**
 * Rename dialog element, or null when the live tab is not mounted.
 * @returns {HTMLDialogElement|null}
 */
function teamsRenameDialog() {
  const el = $("ap-teams-rename-dialog");
  return el instanceof HTMLDialogElement ? el : null;
}

/**
 * Portal the rename dialog onto ``document.body`` so OptionsStrip overflow cannot clip it.
 * @returns {HTMLDialogElement|null}
 */
function mountTeamsRenameDialog() {
  const dialog = teamsRenameDialog();
  if (!dialog) return null;
  if (dialog.parentElement !== document.body) {
    document.body.appendChild(dialog);
  }
  if (dialog.dataset.wired !== "1") {
    dialog.dataset.wired = "1";
    dialog.addEventListener("cancel", () => {
      // Escape: native dialog closes; discard unsaved names.
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === event.currentTarget) {
        event.currentTarget.close();
      }
    });
    dialog.addEventListener("close", () => {
      restoreTeamsRenameFocus();
    });
  }
  return dialog;
}

/**
 * Return focus to the Rename control (or the opener) after the dialog closes.
 */
function restoreTeamsRenameFocus() {
  const back = teamsRenameFocusEl;
  teamsRenameFocusEl = null;
  const target =
    back instanceof HTMLElement && document.contains(back) ? back : $("ap-teams-rename");
  if (target instanceof HTMLElement) target.focus();
}

/**
 * Open the portaled rename modal and move focus into the first name field.
 */
function openTeamsRenameModal() {
  const dialog = mountTeamsRenameDialog();
  if (!dialog || typeof dialog.showModal !== "function") return;
  closeTeamsPops({ keepRename: true });
  const active = document.activeElement;
  teamsRenameFocusEl = active instanceof HTMLElement ? active : $("ap-teams-rename");
  if (!dialog.open) dialog.showModal();
  const first = dialog.querySelector("#ap-name-list input");
  if (first instanceof HTMLElement) first.focus();
}

/**
 * Close the rename modal. Done saves first; Escape / backdrop discard.
 * @param {{save?: boolean}} [opts]
 * @returns {Promise<void>}
 */
async function closeTeamsRenameModal(opts = {}) {
  if (opts.save) {
    await saveTeamNamesFromPop();
  }
  const dialog = teamsRenameDialog();
  if (dialog?.open) dialog.close();
}

/**
 * Open the Manual assign popup. Rename uses ``openTeamsRenameModal`` instead.
 * @param {string} id
 */
function openTeamsPop(id) {
  if (id === "ap-panel-names") {
    openTeamsRenameModal();
    return;
  }
  const dialog = teamsRenameDialog();
  if (dialog?.open) dialog.close();
  const pane = $("team-assign-pane");
  const anchor = $("ap-assign-manual") || $("live-option-card");
  if (pane) {
    if (anchor) {
      const box = anchor.getBoundingClientRect();
      const width = Math.min(320, window.innerWidth - 24);
      let left = Math.round(box.right - width);
      if (left < 12) left = 12;
      if (left + width > window.innerWidth - 12) {
        left = Math.max(12, window.innerWidth - width - 12);
      }
      pane.style.top = `${Math.round(box.bottom + 8)}px`;
      pane.style.left = `${left}px`;
      pane.style.right = "auto";
      pane.style.width = `${width}px`;
    }
    pane.hidden = false;
    pane.removeAttribute("hidden");
  }
  const el = $("ap-manual-assign");
  if (el instanceof HTMLElement) {
    el.hidden = false;
    el.removeAttribute("hidden");
  }
}

/**
 * Hide the Manual popup and discard an open rename modal.
 * @param {{keepRename?: boolean}} [opts]
 */
function closeTeamsPops(opts = {}) {
  const el = $("ap-manual-assign");
  if (el) el.hidden = true;
  const pane = $("team-assign-pane");
  if (pane) pane.hidden = true;
  if (!opts.keepRename) {
    const dialog = teamsRenameDialog();
    if (dialog?.open) dialog.close();
  }
}

/**
 * Clamp team count in the overlay stepper. Count 1 = individual / no teams.
 * @param {number} value
 */
function setNTeams(value) {
  const { min, max } = nTeamsBounds();
  const n = Math.min(max, Math.max(min, Number(value) || min));
  const el = $("ap-n-teams");
  if (el) {
    el.value = String(n);
    el.min = String(min);
    el.max = String(max);
  }
  if (teacherState.groups_configured) {
    paintTeamsStripEnabled();
    paintStudentViewControls();
    renderAttendanceList();
    return;
  }
  if (n > 1) {
    selectTrackMode("team");
    if (lastAssignMode) selectAssignMode(lastAssignMode);
    else syncSetupEnabled();
  } else {
    selectTrackMode("individual");
    syncSetupEnabled();
  }
  paintTeamsStripEnabled();
  paintStudentViewControls();
  renderAttendanceList();
}

/**
 * Refresh the teams overlay from game state (scoreboard/rank live on Tracking mode).
 */
function renderTeamsPanel() {
  const box = $("ap-scoreboard-toggle");
  if (box) box.checked = Boolean(teacherState.scoreboard_visible);
  syncScoreboardPreview();
  setNTeams(Number($("ap-n-teams")?.value) || 1);
  syncSetupEnabled();
  if (lastAssignMode) {
    selectAssignMode(lastAssignMode);
  } else {
    renderAttendanceList();
  }
}

/**
 * Show the Options-strip scoreboard preview whenever the toggle is on.
 */
function syncScoreboardPreview() {
  const wrap = $("ap-scoreboard-preview-wrap");
  if (wrap) wrap.hidden = !Boolean(teacherState.scoreboard_visible);
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
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  syncSetupEnabled();
  renderAttendanceList();
}

/**
 * Set Up stays disabled until Balanced / Random / Manual is chosen.
 */
function syncSetupEnabled() {
  const btn = $("live-teams-start");
  if (!(btn instanceof HTMLButtonElement)) return;
  btn.disabled = !lastAssignMode || currentTeamCount() < 2;
}

$("ap-n-teams-down")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) - 1));
$("ap-n-teams-up")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) + 1));
$("ap-n-teams")?.addEventListener("change", () => setNTeams(Number($("ap-n-teams").value)));
$("ap-scoreboard-toggle")?.addEventListener("change", (event) => {
  const box = event.target;
  if (box instanceof HTMLInputElement) {
    patchTeacherState({ scoreboard_visible: box.checked });
    syncScoreboardPreview();
  }
});

/**
 * Assign teams, open Meet Your Team, and start the overlay meet timer.
 * @param {"random"|"balanced"|"manual"} mode
 */
async function assign(mode) {
  lastAssignMode = mode;
  const meeting = sessionIso();
  const ids = selectedPresent();
  if (!ids.length) {
    throw new Error(
      "No students have joined yet. Share the live session code, then continue when someone is present."
    );
  }
  // Ensure present flags + ``teams`` status even if Attendance Next was skipped.
  overlayState = await api(`/api/classes/${classId}/game/attendance`, {
    method: "POST",
    body: JSON.stringify({ present_ids: ids, meeting_date: meeting }),
  });
  const payload = {
    n_teams: Number($("ap-n-teams").value),
    mode,
    scoreboard_visible: Boolean($("ap-scoreboard-toggle")?.checked),
  };
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
  closeTeamsPops();
  renderAttendanceList();
  updateStepSummaries();
}

/**
 * Default session-timer minutes for the current pedagogical stage.
 * Meet stays 3; Play uses 5. Other stages keep the Meet default.
 * @param {string} [stage]
 * @returns {number}
 */
function sessionTimerDefaultMinutes(stage = teacherState.stage) {
  return stage === "play" ? 5 : 3;
}

/**
 * Read session timer minutes from the stepper.
 * @returns {number}
 */
function sessionTimerMinutes() {
  const raw = Number($("ap-meet-minutes")?.value);
  if (!Number.isFinite(raw)) return sessionTimerDefaultMinutes();
  return Math.max(1, Math.min(30, Math.round(raw)));
}

/**
 * Clamp the session timer stepper and idle clock.
 * @param {number} value
 */
function setSessionTimerMinutes(value) {
  const fallback = sessionTimerDefaultMinutes();
  const n = Math.max(1, Math.min(30, Number(value) || fallback));
  const el = $("ap-meet-minutes");
  if (el) el.value = String(n);
  const clock = $("ap-meet-live-clock");
  const btn = $("ap-meet-start");
  const state = btn?.dataset.meetState || "idle";
  if (clock && (state === "idle" || state === "paused")) {
    clock.textContent = formatCountdown(n * 60);
  }
  if (state === "paused") {
    persistPausedTimerMinutes(n);
  }
}

/**
 * Persist remaining session/Meet countdown as N integer minutes while paused.
 * @param {number} minutes
 * @returns {Promise<void>}
 */
async function persistPausedTimerMinutes(minutes) {
  const n = Math.max(1, Math.min(30, Math.round(Number(minutes) || 1)));
  try {
    overlayState = await api(`/api/classes/${classId}/game/timer/remaining`, {
      method: "POST",
      body: JSON.stringify({ minutes: n }),
    });
    applySessionTimerUi(overlayState);
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
}

let lastSessionTimerStage = "";

/**
 * Apply Meet (3) / Play (5) defaults when the idle timer changes stage.
 */
function applySessionTimerStageDefaults() {
  const stage = teacherState.stage || "join";
  if (stage === lastSessionTimerStage) return;
  lastSessionTimerStage = stage;
  const btn = $("ap-meet-start");
  if ((btn?.dataset.meetState || "idle") !== "idle") return;
  setSessionTimerMinutes(sessionTimerDefaultMinutes(stage));
}

/**
 * Start the stage-independent session countdown.
 * @returns {Promise<void>}
 */
async function startSessionTimer() {
  const minutes = sessionTimerMinutes();
  overlayState = await api(`/api/classes/${classId}/game/timer/start`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });
  applySessionTimerUi(overlayState);
}

/**
 * Paint SessionTimer Start/Pause/Resume above ClassList.
 * Updates label and clock text in place. Never hides the stepper,
 * never remounts Left|Right chrome, never swaps layout mode.
 * @param {any} [state]
 */
function applySessionTimerUi(state = overlayState) {
  lockClassListPane();
  const game = state?.game || {};
  const stepper = $("ap-meet-stepper");
  const clock = $("ap-meet-live-clock");
  const btn = $("ap-meet-start");
  const running = Boolean(game.round_ends_at_ms) && !game.timer_paused;
  const paused = Boolean(game.timer_paused);
  if (stepper) {
    stepper.hidden = false;
    stepper.removeAttribute("hidden");
    const freeze = running;
    stepper.setAttribute("aria-disabled", freeze ? "true" : "false");
    for (const el of stepper.querySelectorAll("button, input")) {
      if (el instanceof HTMLButtonElement || el instanceof HTMLInputElement) {
        el.disabled = freeze;
      }
    }
  }
  if (clock) {
    clock.hidden = false;
    clock.removeAttribute("hidden");
    if (running) {
      sessionEndsAtMs = Number(game.round_ends_at_ms) || 0;
      clock.textContent = formatCountdown(remainingUntilMs(sessionEndsAtMs));
    } else if (paused) {
      sessionEndsAtMs = 0;
      clock.textContent = formatCountdown(Number(game.round_remaining_sec) || 0);
    } else {
      sessionEndsAtMs = 0;
      clock.textContent = formatCountdown(sessionTimerMinutes() * 60);
    }
  }
  if (btn) {
    if (running) {
      btn.textContent = "Pause";
      btn.dataset.meetState = "running";
    } else if (paused) {
      btn.textContent = "Resume";
      btn.dataset.meetState = "paused";
    } else {
      btn.textContent = "Start";
      btn.dataset.meetState = "idle";
    }
  }
}

/** Session countdown deadline (epoch ms), or 0 when idle/paused. */
let sessionEndsAtMs = 0;

/**
 * Tick the session countdown when running.
 * Clock text only — never toggles hidden or remounts chrome.
 */
function paintSessionClock() {
  const clock = $("ap-meet-live-clock");
  const btn = $("ap-meet-start");
  if (!clock) return;
  if (btn?.dataset.meetState !== "running") return;
  if (!sessionEndsAtMs) return;
  clock.textContent = formatCountdown(remainingUntilMs(sessionEndsAtMs));
}

$("ap-assign-random")?.addEventListener("click", () => {
  if (currentTeamCount() < 2) return;
  selectAssignMode("random");
  closeTeamsPops();
});
$("ap-assign-balanced")?.addEventListener("click", () => {
  if (currentTeamCount() < 2) return;
  selectAssignMode("balanced");
  closeTeamsPops();
});
$("ap-assign-manual")?.addEventListener("click", () => {
  if (currentTeamCount() < 2) return;
  selectAssignMode("manual");
  const nTeams = Number($("ap-n-teams").value);
  const present = new Set(overlayState?.present_ids || selectedPresent());
  const students = sortStudents(
    (overlayState?.students || []).filter((s) => present.has(s.id)),
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
  openTeamsPop("ap-manual-assign");
});
$("ap-manual-done")?.addEventListener("click", () => closeTeamsPops());
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

/**
 * Show an assign failure inside the TEAMS OptionsStrip.
 * Never writes ``#ap-overlay-error`` or remounts Active Content.
 * @param {unknown} err
 */
function showTeamsAssignError(err) {
  hideError("#ap-overlay-error");
  showError("#live-teams-assign-error", err);
}

/**
 * Hide the TEAMS OptionsStrip assign error.
 */
function hideTeamsAssignError() {
  hideError("#live-teams-assign-error");
}

/**
 * TEAMS → MEET Next: commit Generate/assign then ``stage=meet`` in one write.
 * Count 1 skips team buckets (individual-safe). Failures stay on TEAMS.
 * @returns {Promise<void>}
 */
async function advanceTeamsToMeet() {
  hideTeamsAssignError();
  const ids = selectedPresent();
  if (!ids.length) {
    showTeamsAssignError(
      new Error(
        "No students have joined yet. Share the live session code, then continue when someone is present."
      )
    );
    return;
  }
  const nTeams = currentTeamCount();
  const body = { advance: "next" };
  if (nTeams >= 2) {
    const mode = lastAssignMode;
    if (!mode) {
      showTeamsAssignError(new Error("Choose Balanced, Random, or Manual first."));
      return;
    }
    if (mode === "manual") {
      const open = $("ap-manual-assign");
      if (!open || open.hidden) {
        showTeamsAssignError(
          new Error("Choose Assign Manually and set each student's team, then Next.")
        );
        return;
      }
      if (!manualTeamsBalanced()) {
        showTeamsAssignError(
          new Error(
            "Balance teams so sizes are equal or off by one, or pick Assign Balanced / Assign Randomly."
          )
        );
        return;
      }
    }
    body.teams_mode = "teams";
    body.assign = {
      n_teams: nTeams,
      mode,
      present_ids: ids,
    };
    if (mode === "manual") {
      body.assign.assignments = [...document.querySelectorAll("#ap-manual-list .team-step")].map(
        (el) => ({
          student_id: Number(el.dataset.studentId),
          team_index: Number(el.dataset.teamIndex),
        })
      );
    }
  } else {
    body.teams_mode = "individual";
  }
  const beforeStage = teacherState.stage;
  await patchTeacherState(body, { errorSelector: "#live-teams-assign-error" });
  if (teacherState.stage === beforeStage && beforeStage === "teams") {
    return;
  }
  hideTeamsAssignError();
  closeTeamsPops();
  renderAttendanceList();
  pendingScoreboard = false;
}

/**
 * Start team grouping without changing the current pedagogical stage.
 * @returns {Promise<void>}
 */
async function startTeamsForCurrentStage() {
  hideTeamsAssignError();
  const ids = selectedPresent();
  if (!ids.length) {
    showTeamsAssignError(
      new Error("No students have joined yet. Share the live session code first.")
    );
    return;
  }
  const nTeams = currentTeamCount();
  if (nTeams < 2) {
    await patchTeacherState({ teams_mode: "individual" });
    return;
  }
  const mode = lastAssignMode;
  if (!mode) {
    showTeamsAssignError(new Error("Choose Balanced, Random, or Manual first."));
    return;
  }
  if (mode === "manual" && !manualTeamsBalanced()) {
    showTeamsAssignError(
      new Error("Set each student's team, then start Teams again.")
    );
    return;
  }
  const scoreboardOn = Boolean($("ap-scoreboard-toggle")?.checked);
  const assign = { n_teams: nTeams, mode, present_ids: ids, scoreboard_visible: scoreboardOn };
  if (mode === "manual") {
    assign.assignments = [
      ...document.querySelectorAll("#ap-manual-list .team-step"),
    ].map((el) => ({
      student_id: Number(el.dataset.studentId),
      team_index: Number(el.dataset.teamIndex),
    }));
  }
  await patchTeacherState({
    teams_mode: "teams",
    assign,
    scoreboard_visible: scoreboardOn,
  });
  await pollLiveSessionAttendees();
  renderAttendanceList();
}

$("ap-teams-next")?.addEventListener("click", () => {
  advanceTeamsToMeet();
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
  if (hint) {
    hint.textContent = "Optional: Update team names below";
  }
  const box = $("ap-name-list");
  if (!box) return;
  box.innerHTML = "";
  for (const team of assignedRosterTeams()) {
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
    const input = wrap.querySelector("input[data-team-id]");
    if (input instanceof HTMLInputElement) {
      wireDefaultTeamNameClear(input);
    }
  }
  updateStepSummaries();
}

/**
 * Clear default ``Team N`` placeholder text when the teacher focuses to rename.
 * @param {HTMLInputElement} input
 */
function wireDefaultTeamNameClear(input) {
  input.addEventListener("focus", () => {
    if (/^Team\s+\d+$/i.test(String(input.value || "").trim())) {
      input.value = "";
    }
  });
}

/**
 * Persist rename fields and refresh team labels without remounting shell chrome.
 * @returns {Promise<void>}
 */
async function saveTeamNamesFromPop() {
  const teams = [...document.querySelectorAll("#ap-name-list input")]
    .map((el) => {
      const typed = String(el.value || "").trim();
      const fallback = String(el.defaultValue || "").trim();
      return {
        id: Number(el.dataset.teamId),
        name: typed || fallback,
      };
    })
    .filter((row) => Number.isFinite(row.id) && row.id > 0);
  if (!teams.length) return;
  if (teams.some((row) => !String(row.name || "").trim())) {
    throw new Error("Team name cannot be empty");
  }
  overlayState = await api(`/api/classes/${classId}/game/rename`, {
    method: "POST",
    body: JSON.stringify({ teams, go_live: false }),
  });
  renderAttendanceList();
  updateStepSummaries();
}

/**
 * Draft Team 1..N fields when assign has not run yet.
 */
function renderDraftNamesPanel() {
  const box = $("ap-name-list");
  if (!box) return;
  const n = currentTeamCount();
  box.innerHTML = "";
  for (let i = 0; i < n; i += 1) {
    const wrap = document.createElement("div");
    wrap.className = "team-preview";
    wrap.innerHTML = `<label class="field">Team ${i + 1}<input type="text" data-team-index="${i}" value="Team ${i + 1}"></label>`;
    box.appendChild(wrap);
    const input = wrap.querySelector("input");
    if (input instanceof HTMLInputElement) wireDefaultTeamNameClear(input);
  }
}

$("ap-teams-rename")?.addEventListener("click", async () => {
  if (!teacherState.groups_configured) return;
  try {
    const assigned = assignedRosterTeams();
    if (assigned.length >= 2) {
      renderNamesPanel();
    } else {
      renderDraftNamesPanel();
    }
    openTeamsRenameModal();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-teams-rename-done")?.addEventListener("click", () => {
  closeTeamsRenameModal({ save: true }).catch((err) => showError("#ap-teams-rename-error", err));
});

$("ap-start-game")?.addEventListener("click", async () => {
  pendingScoreboard = Boolean($("ap-scoreboard-toggle")?.checked);
  const teams = [...document.querySelectorAll("#ap-name-list input")].map((el) => ({
    id: Number(el.dataset.teamId),
    name: el.value,
  }));
  try {
    overlayState = await api(`/api/classes/${classId}/game/rename`, {
      method: "POST",
      body: JSON.stringify({ teams, go_live: false }),
    });
    draftRound = { kind: "open", minutes: 20, title: "" };
    nextDraftRound = { kind: "open", minutes: 10, title: "" };
    setupRoundNumber = 1;
    renderRoundsPanel();
    showPanel("rounds");
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-meet-minutes-down")?.addEventListener("click", () => {
  setSessionTimerMinutes(sessionTimerMinutes() - 1);
});
$("ap-meet-minutes-up")?.addEventListener("click", () => {
  setSessionTimerMinutes(sessionTimerMinutes() + 1);
});
$("ap-meet-minutes")?.addEventListener("change", () => {
  setSessionTimerMinutes(sessionTimerMinutes());
});
$("ap-meet-start")?.addEventListener("click", async () => {
  try {
    hideError("#ap-overlay-error");
    const btn = $("ap-meet-start");
    const state = btn?.dataset.meetState || "idle";
    if (state === "running") {
      overlayState = await api(`/api/classes/${classId}/game/timer/pause`, {
        method: "POST",
        body: "{}",
      });
      applySessionTimerUi(overlayState);
      return;
    }
    if (state === "paused") {
      overlayState = await api(`/api/classes/${classId}/game/timer/resume`, {
        method: "POST",
        body: "{}",
      });
      applySessionTimerUi(overlayState);
      return;
    }
    await startSessionTimer();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("live-timer-toggle")?.addEventListener("change", () => paintOptionCard());
$("live-teams-start")?.addEventListener("click", async () => {
  try {
    await startTeamsForCurrentStage();
  } catch (err) {
    showTeamsAssignError(err);
  }
});
$("live-run-as-group")?.addEventListener("change", (event) => {
  const input = event.currentTarget;
  if (!(input instanceof HTMLInputElement)) return;
  teacherState.run_as_group = input.checked;
  renderAttendanceList();
  patchTeacherState({ run_as_group: input.checked }, { silent: true });
});
$("live-hide-absent")?.addEventListener("change", (event) => {
  const input = event.currentTarget;
  if (!(input instanceof HTMLInputElement)) return;
  teacherState.hide_absent = input.checked;
  renderAttendanceList();
  patchTeacherState({ hide_absent: input.checked }, { silent: true });
});

/**
 * Round kinds available for the current tracking mode.
 * Individual tracking is Open Question only; team mode keeps all kinds.
 * @returns {typeof ROUND_KIND_OPTIONS}
 */
function availableRoundKinds() {
  if (trackMode === "individual") {
    return ROUND_KIND_OPTIONS.filter(
      (option) => option.kind === "open" || option.kind === "challenge"
    );
  }
  return ROUND_KIND_OPTIONS;
}

/**
 * Build one round editor row for the sequential setup flow.
 * @param {{kind: string, minutes: number, title: string}} round
 * @param {number} roundNumber
 * @returns {string}
 */
function roundEditorMarkup(round, roundNumber) {
  const kinds = availableRoundKinds();
  const selectedKind = kinds.some((option) => option.kind === round.kind)
    ? round.kind
    : "open";
  const options = kinds
    .map(
      (option) =>
        `<option value="${option.kind}" ${option.kind === selectedKind ? "selected" : ""}>${escapeHtml(option.label)}</option>`
    )
    .join("");
  const kindLocked = false;
  const breakTitle = isBreakRound({ ...round, kind: selectedKind })
    ? `<label class="field ap-round-title">Break title (optional)
        <input type="text" maxlength="80" value="${escapeHtml(round.title || "")}" placeholder="Break" data-round-title>
      </label>`
    : "";
  return `<div class="ap-round-row${isBreakRound({ ...round, kind: selectedKind }) ? " has-title" : ""}">
    <label class="field ap-round-type">Round ${roundNumber}
      <select data-round-kind${kindLocked ? " disabled" : ""}>${options}</select>
    </label>
    <label class="field ap-round-len">Length (min)
      <input type="number" min="1" max="180" value="${Number(round.minutes) || 10}" data-round-minutes>
    </label>
    ${breakTitle}
  </div>`;
}

/**
 * Return a validated API body for one sequential round.
 * @param {{kind: string, minutes: number, title: string}} round
 * @returns {{kind: string, minutes: number, title?: string}}
 */
function roundRequestBody(round) {
  const kind = round.kind;
  const body = {
    kind,
    minutes: Math.max(1, Math.min(180, Number(round.minutes) || 1)),
  };
  if (isBreakRound({ ...round, kind })) {
    const title = String(round.title || "").trim();
    if (title) body.title = title;
  }
  return body;
}

/**
 * Build the single Round N setup editor.
 */
function renderRoundsPanel() {
  const box = $("ap-rounds-list");
  if (!box) return;
  if (trackMode === "individual" && !["open", "challenge"].includes(draftRound.kind)) {
    draftRound = { kind: "open", minutes: draftRound.minutes || 20, title: "" };
  }
  box.innerHTML = roundEditorMarkup(draftRound, setupRoundNumber);
  const title = $("ap-rounds-title");
  if (title) title.textContent = `Scoring ${setupRoundNumber}`;
  const start = $("ap-rounds-start");
  if (start) start.textContent = "Start scoring";
  const hint = $("ap-rounds-hint");
  if (hint) {
    hint.textContent =
      trackMode === "individual"
        ? "Individual tracking uses Open Question or Team Challenge. Formative and Break stay team-only."
        : "Set up one round at a time. Open Question and Team Challenge use action chips when scoring.";
  }
  updateStepSummaries();
}

$("ap-rounds-list")?.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.matches("[data-round-kind]") && target instanceof HTMLSelectElement) {
    draftRound.kind = target.value;
    const meta = ROUND_KIND_OPTIONS.find((option) => option.kind === target.value);
    if (meta) draftRound.minutes = meta.defaultMin;
    if (!isBreakRound(draftRound)) draftRound.title = "";
    renderRoundsPanel();
    return;
  }
  if (target.matches("[data-round-minutes]") && target instanceof HTMLInputElement) {
    draftRound.minutes = Math.max(1, Math.min(180, Number(target.value) || 1));
  }
});

$("ap-rounds-list")?.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.matches("[data-round-title]")) {
    draftRound.title = target.value;
  }
});

$("ap-rounds-start")?.addEventListener("click", async () => {
  const hasLiveOverlay = Boolean(liveSessionId || readLiveSessionId());
  try {
    if (trackMode === "individual" && !["open", "challenge"].includes(draftRound.kind)) {
      draftRound = { ...draftRound, kind: "open" };
    }
    if (String(overlayState?.game?.status || "") === "live") {
      await openLiveScoring(overlayState);
      startLiveSessionPolling();
      return;
    }
    const rounds = [roundRequestBody(draftRound)];
    overlayState = await api(`/api/classes/${classId}/game/start-rounds`, {
      method: "POST",
      body: JSON.stringify({ rounds }),
    });
    if (hasLiveOverlay) ensureLiveSessionOverlay();
    pendingScoreboard = false;
    openLiveScoring(overlayState);
    startLiveSessionPolling();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

/**
 * Paint the inline editor for the next live round.
 */
function renderNextRoundPanel() {
  if (trackMode === "individual" && !["open", "challenge"].includes(nextDraftRound.kind)) {
    nextDraftRound = { kind: "open", minutes: nextDraftRound.minutes || 10, title: "" };
  }
  const fields = $("ap-next-round-fields");
  if (fields) fields.innerHTML = roundEditorMarkup(nextDraftRound, setupRoundNumber);
  const title = $("ap-next-round-title");
  if (title) title.textContent = `Scoring ${setupRoundNumber}`;
  const start = $("ap-next-round-start");
  if (start) start.textContent = "Start scoring";
}

$("ap-add-round-btn")?.addEventListener("click", () => {
  setupRoundNumber = (Number(overlayState?.game?.round) || 1) + 1;
  nextDraftRound = { kind: "open", minutes: 10, title: "" };
  renderNextRoundPanel();
  const form = $("ap-add-next-round");
  if (form) form.hidden = false;
  const add = $("ap-add-round-btn");
  if (add) add.hidden = true;
  // Bring the new round controls to the top of the viewport for editing.
  scrollActiveTrackStepIntoView(form, "start");
});

$("ap-next-round-cancel")?.addEventListener("click", () => {
  const form = $("ap-add-next-round");
  if (form) form.hidden = true;
  const add = $("ap-add-round-btn");
  if (add) add.hidden = false;
});

$("ap-next-round-fields")?.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.matches("[data-round-kind]") && target instanceof HTMLSelectElement) {
    nextDraftRound.kind = target.value;
    const meta = ROUND_KIND_OPTIONS.find((option) => option.kind === target.value);
    if (meta) nextDraftRound.minutes = meta.defaultMin;
    if (!isBreakRound(nextDraftRound)) nextDraftRound.title = "";
    renderNextRoundPanel();
    return;
  }
  if (target.matches("[data-round-minutes]") && target instanceof HTMLInputElement) {
    nextDraftRound.minutes = Math.max(
      1,
      Math.min(180, Number(target.value) || 1)
    );
  }
});

$("ap-next-round-fields")?.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.matches("[data-round-title]")) {
    nextDraftRound.title = target.value;
  }
});

$("ap-next-round-start")?.addEventListener("click", async () => {
  try {
    if (trackMode === "individual" && !["open", "challenge"].includes(nextDraftRound.kind)) {
      nextDraftRound = { ...nextDraftRound, kind: "open" };
    }
    const round = roundRequestBody(nextDraftRound);
    overlayState = await api(`/api/classes/${classId}/game/append-round`, {
      method: "POST",
      body: JSON.stringify(round),
    });
    const form = $("ap-add-next-round");
    if (form) form.hidden = true;
    const add = $("ap-add-round-btn");
    if (add) add.hidden = false;
    liveStamp = "";
    openLiveScoring(overlayState, { stayOnScore: true });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

/**
 * Split look-for labels such as noticed/generalized onto two lines.
 * @param {string} label
 * @returns {string}
 */
function stackedSlashLabel(label) {
  const parts = String(label || "")
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length < 2) return escapeHtml(label);
  return parts.map((part) => escapeHtml(part)).join("<br>");
}

/**
 * Compact student point chips, or Open Question matrix cell buttons.
 * @param {number} id
 * @returns {string}
 */
function studentButtons(id) {
  if (isProfileMatrixRound()) {
    return getActiveMatrixActions()
      .map(
        (action) =>
          `<button type="button" class="ap-action-chip" data-kind="student" data-id="${id}" data-amount="${action.amount}" data-label="${escapeHtml(action.label)}" data-lookfor="${escapeHtml(action.lookfor_key || action.id || "")}" title="+${action.amount}">${stackedSlashLabel(action.label)}</button>`
      )
      .join("");
  }
  return STUDENT_AMOUNTS.map(
    (n) =>
      `<button type="button" class="ap-score-chip" data-kind="student" data-id="${id}" data-amount="${n}">${n > 0 ? "+" : ""}${n}</button>`
  ).join("");
}

/**
 * Builtin profiles document matching historical Open Question chips.
 * @returns {{open: {active_id: string, profiles: object[]}}}
 */
function builtinOpenProfilesDoc() {
  return {
    open: {
      active_id: "default",
      profiles: [{ id: "default", name: "Default", actions: OPEN_QUESTION_ACTIONS.map((a) => ({ ...a })) }],
    },
    challenge: {
      active_id: "team_challenge",
      profiles: [{ id: "team_challenge", name: "Team Challenge", actions: TEAM_CHALLENGE_ACTIONS.map((a) => ({ ...a })) }],
    },
  };
}

/**
 * Actions for the active Open Question profile.
 * @returns {Array<{id: string, label: string, amount: number, lookfor_key?: string}>}
 */
function getActiveOpenActions() {
  const doc = openProfilesDoc || builtinOpenProfilesDoc();
  const section = doc.open || {};
  const profiles = Array.isArray(section.profiles) ? section.profiles : [];
  const activeId = String(section.active_id || "");
  const active = profiles.find((p) => p.id === activeId) || profiles[0];
  const actions = active && Array.isArray(active.actions) ? active.actions : OPEN_QUESTION_ACTIONS;
  return actions.length ? actions : OPEN_QUESTION_ACTIONS;
}

/**
 * Actions for the active Team Challenge profile.
 * @returns {Array<{id: string, label: string, amount: number, lookfor_key?: string}>}
 */
function getActiveChallengeActions() {
  const doc = openProfilesDoc || builtinOpenProfilesDoc();
  const section = doc.challenge || {};
  const profiles = Array.isArray(section.profiles) ? section.profiles : [];
  const activeId = String(section.active_id || "");
  const active = profiles.find((p) => p.id === activeId) || profiles[0];
  const actions = active && Array.isArray(active.actions) ? active.actions : TEAM_CHALLENGE_ACTIONS;
  return actions.length ? actions : TEAM_CHALLENGE_ACTIONS;
}

/**
 * Matrix actions for the current round kind.
 * @returns {Array<{id: string, label: string, amount: number, lookfor_key?: string}>}
 */
function getActiveMatrixActions() {
  return isChallengeRound() ? getActiveChallengeActions() : getActiveOpenActions();
}

/**
 * Load offering Open Question profiles (once per page; refreshable).
 * @param {{force?: boolean}} [opts]
 * @returns {Promise<void>}
 */
async function ensureOpenProfilesLoaded(opts = {}) {
  if (!opts.force && openProfilesDoc) return;
  if (!opts.force && openProfilesLoadPromise) {
    await openProfilesLoadPromise;
    return;
  }
  openProfilesLoadPromise = (async () => {
    try {
      const data = await api(`/api/classes/${classId}/ap-round-profiles`);
      if (data && data.document && data.document.open) {
        openProfilesDoc = data.document;
        if (!openProfilesDoc.challenge) {
          openProfilesDoc.challenge = builtinOpenProfilesDoc().challenge;
        }
      } else {
        openProfilesDoc = builtinOpenProfilesDoc();
      }
    } catch (_err) {
      openProfilesDoc = builtinOpenProfilesDoc();
    } finally {
      openProfilesLoadPromise = null;
    }
  })();
  await openProfilesLoadPromise;
}

/**
 * Show/hide and populate the Scoring profile dropdown for Open Question.
 */
function renderOpenProfileBar() {
  const bar = $("ap-oq-profile-bar");
  const select = $("ap-oq-profile-select");
  if (!bar || !select) return;
  const show = isProfileMatrixRound() && !isBreakRound();
  bar.hidden = !show;
  if (!show) return;
  const doc = openProfilesDoc || builtinOpenProfilesDoc();
  const section = isChallengeRound() ? doc.challenge || {} : doc.open || {};
  const profiles = Array.isArray(section.profiles) ? section.profiles : [];
  const activeId = String(section.active_id || profiles[0]?.id || "default");
  select.innerHTML = profiles
    .map(
      (profile) =>
        `<option value="${escapeHtml(profile.id)}"${profile.id === activeId ? " selected" : ""}>${escapeHtml(profile.name)}</option>`
    )
    .join("");
}

/**
 * Build a student × action scoring matrix for Open Question.
 * @param {any[]} rows
 * @returns {string}
 */
function openActionMatrixHtml(rows) {
  const actions = getActiveMatrixActions();
  if (!actions.length) {
    return `<p class="hint compact">No actions in this profile. <a href="/staff/class/${classId}?tab=profiles" target="_blank" rel="noopener">Edit Profiles</a></p>`;
  }
  const head = actions
    .map(
      (action) =>
        `<th scope="col" class="ap-oq-col" title="+${escapeHtml(String(action.amount))}">
          <span class="ap-oq-col-label">${stackedSlashLabel(action.label)}</span>
          <span class="ap-oq-col-pts">+${escapeHtml(String(action.amount))}</span>
        </th>`
    )
    .join("");
  const body = rows
    .map((row) => {
      const late = sessionLateIds.has(row.id) || Boolean(row.late);
      const cells = actions
        .map(
          (action) =>
            `<td><button type="button" class="ap-oq-cell" data-kind="student" data-id="${row.id}" data-amount="${action.amount}" data-label="${escapeHtml(action.label)}" data-lookfor="${escapeHtml(action.lookfor_key || action.id || "")}" title="${escapeHtml(action.label)} (+${action.amount})">+${escapeHtml(String(action.amount))}</button></td>`
        )
        .join("");
      return `<tr>
        <th scope="row" class="ap-oq-who">
          <span class="ap-oq-name">${nameWithMood(displayName(row), row.mood)}${late ? ' <span class="ap-late-tag">L</span>' : ""}</span>
          <span class="ap-oq-pts">${escapeHtml(formatPoints(row.session_points || 0))}</span>
        </th>
        ${cells}
      </tr>`;
    })
    .join("");
  return `<div class="ap-oq-matrix-wrap">
    <table class="ap-oq-matrix">
      <thead><tr><th scope="col" class="ap-oq-corner">Student</th>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}

/**
 * Dense individual scoring list (Class / ungamified).
 */
function renderScoreList() {
  const box = $("ap-score-list");
  if (!box) return;
  renderOpenProfileBar();
  const teams = overlayState.teams || [];
  const members = teams.flatMap((t) => t.members || []);
  const rows = sortStudents(members.length ? members : overlayState.students || [], nameSort);
  if (isProfileMatrixRound()) {
    box.innerHTML = openActionMatrixHtml(rows);
  } else {
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
  }
  updateStepSummaries();
  paintResultsStrip();
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
    meta.textContent = "Note: Team points not passed in gradebook";
  }
  renderOpenProfileBar();
  const game = state.game || {};
  const stamp = JSON.stringify({
    pending: pendingTeam,
    round: game.round,
    open: isProfileMatrixRound(),
    profile: isChallengeRound()
      ? openProfilesDoc?.challenge?.active_id || ""
      : openProfilesDoc?.open?.active_id || "",
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
  if (isBreakRound()) {
    if (tabs) {
      tabs.hidden = true;
      tabs.innerHTML = "";
    }
    rootEl.innerHTML = "";
    updateStepSummaries();
    return;
  }
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
      const playerBlocks = isProfileMatrixRound()
        ? openActionMatrixHtml(members)
        : members
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
        ${isChallengeRound() ? "" : teamControls(team.id)}
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
  paintSessionClock();
}

setInterval(paintLiveClock, 1000);

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
  if (isChallengeRound() && btn.dataset.lookfor) {
    payload.lookfor_id = btn.dataset.lookfor;
  }
  overlayState = await api(`/api/classes/${classId}/game/score`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  spawnScorePop(btn.dataset.popName || (payload.kind === "team" ? "Team" : "Student"));
  const nextPoints = { ...sessionGamePoints };
  for (const student of overlayState.students || []) {
    if (student.id == null) continue;
    const pts = Number(student.session_points ?? student.points ?? student.game_points);
    if (Number.isFinite(pts)) nextPoints[String(student.id)] = pts;
  }
  sessionGamePoints = nextPoints;
  pendingTeam = null;
  liveStamp = "";
  openLiveScoring(overlayState, { stayOnScore: true });
}

$("ap-att-list")?.addEventListener("click", async (event) => {
  const btn = event.target.closest("button.ap-att-plus[data-kind]");
  if (!btn) return;
  try {
    await postScoreFromButton(btn);
    renderAttendanceList();
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

$("ap-oq-profile-select")?.addEventListener("change", async (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement)) return;
  const nextId = select.value;
  const doc = openProfilesDoc || builtinOpenProfilesDoc();
  if (isChallengeRound()) {
    if (!doc.challenge) return;
    doc.challenge.active_id = nextId;
  } else {
    if (!doc.open) return;
    doc.open.active_id = nextId;
  }
  openProfilesDoc = doc;
  try {
    const saved = await api(`/api/classes/${classId}/ap-round-profiles`, {
      method: "PUT",
      body: JSON.stringify({ document: doc }),
    });
    if (saved?.document) openProfilesDoc = saved.document;
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
  liveStamp = "";
  if (overlayState) {
    await openLiveScoring(overlayState, { stayOnScore: true });
  }
});

async function endGame() {
  try {
    const meeting = sessionIso();
    await api(`/api/classes/${classId}/game/end`, {
      method: "POST",
      body: JSON.stringify(meeting ? { meeting_date: meeting } : {}),
    });
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
 * Fresh / reminted sessions stay on Set Class until date + module + slot are set.
 * Uses the open game's meeting_date; does not re-begin with “today” and discard
 * a picker-chosen setup column.
 * @returns {Promise<boolean>}
 */
async function resumeLiveClassIfNeeded() {
  liveSessionId = readLiveSessionId() || liveSessionId;
  if (!liveSessionId) return false;
  try {
    await loadContext();
    let state = null;
    try {
      state = await api(`/api/classes/${classId}/game`);
    } catch (_) {
      state = null;
    }
    const status = String(state?.game?.status || "");
    let teacher = teacherState;
    try {
      const payload = await api(`/api/live-sessions/${liveSessionId}/teacher-state`);
      if (payload?.teacher_state) {
        teacher = payload.teacher_state;
        adoptTeacherState(teacher);
      }
    } catch (_) {
      /* keep local defaults */
    }
    if (wantsFreshSetClass() && !classSetIsComplete(status, teacher)) return false;
    if (!classSetIsComplete(status, teacher)) return false;
    const openStatuses = new Set(["attendance", "teams", "names", "rounds", "live"]);
    overlayState = state || overlayState;
    const meeting =
      state?.session?.meeting_date || defaultSchoolDay(logContext) || todayISO();
    syncOverlayPickers(logContext, meeting);
    const meetingInput = $("ap-meeting-date");
    if (meetingInput) meetingInput.value = meeting;
    exitSetClassPhase();
    paintTeacherShell();
    if (status === "live" && state?.game) {
      openLiveScoring(overlayState);
      ensureLiveSessionOverlay();
    } else if (state?.game && openStatuses.has(status)) {
      renderAttendanceList();
      if (status === "teams" || status === "names") {
        const nAssigned = (state.teams || []).filter((team) => team.name !== "Class").length;
        if (nAssigned >= 2) setNTeams(nAssigned);
        else selectTrackMode("team");
        renderTeamsPanel();
        if (status === "names") {
          renderNamesPanel();
          openTeamsRenameModal();
        }
      }
    } else {
      renderAttendanceList();
    }
    await ensureLiveSessionMinted();
    startLiveSessionPolling();
    staffStateNeedsFull = true;
    await pollLiveSessionAttendees({ full: true, force: true });
    await loadSavedLiveLesson(
      teacherState.live_module || "M1",
      teacherState.live_slot || "C1"
    );
    paintTeacherShell();
    paintQuestionArtifact();
    await ensureC1MediaSeeded();
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

document.querySelectorAll("[data-track-nav='prev']").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.hasAttribute("disabled") || isScoringLive()) return;
    const current = btn.closest(".ap-panel")?.getAttribute("data-step");
    const back = {
      gamify: "att",
      teams: "att",
      names: "teams",
      rounds: trackMode === "team" ? "names" : "att",
    };
    const target = back[current || ""];
    if (target) showPanel(target);
  });
});

/**
 * Click an existing next-control so Advance does not add a new channel.
 * @param {string} id
 */
function clickExistingNext(id) {
  const btn = $(id);
  if (btn instanceof HTMLButtonElement && !btn.disabled) btn.click();
}

$("live-advance")?.addEventListener("click", () => {
  if (currentStep === "validate") {
    clickExistingNext("ap-validate-apply");
    return;
  }
  if (currentStep === "att" || currentStep === "gamify") {
    clickExistingNext("ap-gamify-next");
    return;
  }
  if (currentStep === "teams") {
    clickExistingNext("ap-teams-next");
    return;
  }
  if (currentStep === "names") {
    clickExistingNext("ap-start-game");
    return;
  }
  if (currentStep === "rounds") {
    clickExistingNext("ap-rounds-start");
  }
});

$("live-start")?.addEventListener("click", () => {
  if (currentStep === "names") {
    clickExistingNext("ap-start-game");
    return;
  }
  clickExistingNext("ap-rounds-start");
});

/**
 * POST a thin LiveTeacherState patch. Prev/Next only send ``advance``.
 * Stage pills are display-only and never write state.
 * @param {Record<string, unknown>} body
 * @param {{silent?: boolean}} [opts]
 */
/**
 * True when a teacher-state write changes the page, pack, or question set.
 * Hide Absent and other chrome toggles stay on the light poll path.
 * @param {Record<string, unknown>} body
 * @returns {boolean}
 */
function teacherStateNeedsQuestionRefresh(body) {
  if (!body || typeof body !== "object") return false;
  return Boolean(
    body.advance ||
      body.stage ||
      body.live_slot ||
      body.live_module ||
      body.round ||
      body.round_flags ||
      body.meet_action
  );
}

async function patchTeacherState(body, opts = {}) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) {
    adoptTeacherState(
      body.advance
        ? {
            ...teacherState,
            stage: TEACHER_STAGES[
              Math.max(
                0,
                Math.min(
                  TEACHER_STAGES.length - 1,
                  TEACHER_STAGES.indexOf(teacherState.stage) +
                    (body.advance === "next" ? 1 : -1)
                )
              )
            ],
          }
        : { ...teacherState, ...body }
    );
    return teacherState;
  }
  if (teacherStateInFlight && opts.silent) return teacherState;
  if (!body.assign) {
    adoptTeacherState(optimisticTeacherState(body));
    renderAttendanceList();
  }
  teacherStateInFlight = true;
  try {
    const res = await api(`/api/live-sessions/${sessionId}/teacher-state`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (res?.teacher_state) adoptTeacherState(res.teacher_state);
    if (res?.game) {
      overlayState = res.game;
      applySessionTimerUi(overlayState);
      renderAttendanceList();
    }
    if (teacherStateNeedsQuestionRefresh(body)) {
      staffStateNeedsFull = true;
      void pollLiveSessionAttendees({ full: true, force: true });
    }
    return teacherState;
  } catch (err) {
    if (!opts.silent) showError(opts.errorSelector || "#ap-overlay-error", err);
    return teacherState;
  } finally {
    teacherStateInFlight = false;
  }
}

$("mc-reveal-btn")?.addEventListener("click", () => {
  patchMcReveal(true);
});
$("mc-hide-reveal-btn")?.addEventListener("click", () => {
  patchMcReveal(false);
});
$("teams-spark-reveal")?.addEventListener("click", () => {
  if (lastTeamsSpark && typeof lastTeamsSpark === "object") {
    lastTeamsSpark = { ...lastTeamsSpark, reveal: true };
  }
  patchMcReveal(true);
  paintTeamsSparkCard(lastTeamsSpark);
});

$("live-stage-prev")?.addEventListener("click", () => {
  advanceLivePage(-1);
});
$("live-stage-next")?.addEventListener("click", () => {
  if (setupPhase) {
    applyValidateDateChoice();
    return;
  }
  advanceLivePage(1);
});
$("live-add-page")?.addEventListener("click", () => {
  addLiveLessonPage().catch((err) => showError("#ap-overlay-error", err));
});
$("live-delete-page")?.addEventListener("click", () => {
  deleteLiveLessonPage().catch((err) => showError("#ap-overlay-error", err));
});
document.querySelectorAll('input[name="live-add-page-kind"]').forEach((radio) => {
  radio.addEventListener("change", () => syncAddLivePageKindFields());
});
$("live-add-page-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("live-add-page-name");
  const err = $("live-add-page-error");
  const selected = document.querySelector('input[name="live-add-page-kind"]:checked');
  const kind = selected instanceof HTMLInputElement ? selected.value : "blank";
  const title = input instanceof HTMLInputElement ? input.value : "";
  submitAddLiveLessonPage(title, kind)
    .then(() => closeAddLivePageDialog())
    .catch((error) => {
      if (err instanceof HTMLElement) {
        err.hidden = false;
        err.textContent = String(error?.message || error);
      } else {
        showError("#ap-overlay-error", error);
      }
    });
});
document.querySelectorAll("[data-live-add-page-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeAddLivePageDialog());
});
$("live-add-page-dialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeAddLivePageDialog();
});
$("live-delete-page-confirm")?.addEventListener("click", () => {
  const err = $("live-delete-page-error");
  confirmDeleteLiveLessonPage()
    .then(() => closeDeleteLivePageDialog())
    .catch((error) => {
      if (err instanceof HTMLElement) {
        err.hidden = false;
        err.textContent = String(error?.message || error);
      } else {
        showError("#ap-overlay-error", error);
      }
    });
});
document.querySelectorAll("[data-live-delete-page-close]").forEach((btn) => {
  btn.addEventListener("click", () => closeDeleteLivePageDialog());
});
$("live-delete-page-dialog")?.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeDeleteLivePageDialog();
});
$("meet-chain-next")?.addEventListener("click", () => {
  patchTeacherState({ meet_action: "next" });
});

document.querySelectorAll("#live-content-tabs [data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.getAttribute("data-tab") || "media";
    const content = teacherPaneContent(tab);
    const body = {
      active_tab: tab,
      frames: { A: content },
    };
    const preset = teacherPanePreset(content);
    if (preset) body.layout_preset = preset;
    patchTeacherState(body);
    if (content === "media") ensureC1MediaSeeded();
  });
});

$("live-edit-layout")?.addEventListener("click", () => {
  const panel = $("live-layout-presets");
  const host = $("live-frames");
  const open = Boolean(panel && panel.hidden);
  if (panel) panel.hidden = !open;
  const btn = $("live-edit-layout");
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (host) host.classList.toggle("is-editing", open);
});

document.querySelectorAll("#live-preset-row [data-preset]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const preset = btn.getAttribute("data-preset") || "media_full";
    const tab =
      preset === "questions_full"
        ? "questions"
        : preset === "canvas_full"
          ? "canvas"
          : preset === "slides_full"
            ? "slides"
            : "media";
    const content = teacherPaneContent(tab);
    const body = { active_tab: tab, frames: { A: content } };
    const nextPreset = teacherPanePreset(content);
    if (nextPreset) body.layout_preset = nextPreset;
    patchTeacherState(body);
    if (content === "media") ensureC1MediaSeeded();
  });
});

/**
 * Persist the ROUND type dropdown so the choice does not snap back.
 */
function commitRoundType() {
  const flags = readRoundFlags();
  const selected = Object.keys(flags).find((key) => flags[key]) || "minds_on";
  teacherState.round = selected;
  teacherState.round_flags = flags;
  const select = $("live-round-type");
  if (select instanceof HTMLSelectElement) select.value = selected;
  patchTeacherState({ round: selected, round_flags: flags });
}

$("live-round-type")?.addEventListener("change", () => {
  commitRoundType();
});
$("live-round-set")?.addEventListener("click", () => {
  commitRoundType();
});

/**
 * Persist Module + Live class and remount the waiting-room pack.
 * @param {string} moduleId
 * @param {string} slot
 */
function applyLivePackChoice(moduleId, slot) {
  const module = String(moduleId || "M1").toUpperCase();
  const liveSlot = String(slot || "C1").toUpperCase();
  teacherState.live_module = module;
  teacherState.live_slot = liveSlot;
  textRideSlot = liveSlot === "C2" || liveSlot === "C3" ? liveSlot : "";
  textOnlyChallenge = isTextOnlyLiveSlot(liveSlot) ? liveSlot : "";
  const sessionId = liveSessionId || readLiveSessionId();
  if (teacherState.stage === "meet" && sessionId) {
    const body = { live_module: module, live_slot: liveSlot };
    if (liveSlot === "C1") body.meet_action = "reset_a";
    return patchTeacherState(body)
      .then(() => loadSavedLiveLesson(module, liveSlot))
      .then(() => paintLiveSlotPicks())
      .catch((err) => showError("#ap-overlay-error", err));
  }
  const write = sessionId
    ? patchTeacherState({ live_module: module, live_slot: liveSlot }).then(() =>
        postActiveMedia({ challenge: liveSlot })
      )
    : Promise.resolve();
  write
    .then(() => loadSavedLiveLesson(module, liveSlot))
    .then(() => {
      paintLiveLessonBadge();
      if (sessionId && liveClassSeedMedia()) return ensureC1MediaSeeded();
      if (liveClassSeedMedia()) paintActiveMediaStatus(null);
      paintLiveSlotPicks();
      return null;
    })
    .catch((err) => showError("#ap-overlay-error", err));
}

/**
 * Load a previously saved live-lesson file for this course and slot.
 * @param {string} moduleId
 * @param {string} slot
 * @returns {Promise<void>}
 */
async function loadSavedLiveLesson(moduleId, slot) {
  if (!classId) return;
  const module = String(moduleId || "M1").toUpperCase();
  const liveSlot = String(slot || "C1").toUpperCase();
  try {
    const payload = await api(
      `/api/staff/class/${classId}/live-lessons/${module}/${liveSlot}/deck?fresh=1`
    );
    if (payload?.live_metadata) {
      lastLiveMetadata = payload.live_metadata;
      paintStageRail();
      paintLiveQuestionCards();
      staffStateNeedsFull = true;
      paintSlidesMetadata();
    }
    if (liveSessionId || readLiveSessionId()) {
      await pollLiveSessionAttendees();
    }
  } catch (_) {
    /* keep current pack */
  }
}

/**
 * Persist the current live-lesson file under a new module/class code.
 * @returns {Promise<void>}
 */
async function saveLiveLessonAs() {
  const current = `${teacherState.live_module || "M1"}${teacherState.live_slot || "C1"}`;
  const raw = window.prompt("Save as (e.g. M2C3)", current);
  if (!raw) return;
  const payload = await api(`/api/classes/${classId}/live-lessons`, {
    method: "POST",
    body: JSON.stringify({
      code: raw,
      source_module: teacherState.live_module || "M1",
      source_slot: teacherState.live_slot || "C1",
    }),
  });
  if (payload?.module && payload?.live_class) {
    rememberSavedLiveLesson(payload.module, payload.live_class);
    applyLivePackChoice(payload.module, payload.live_class);
  }
}

/**
 * Keep Set Class dropdowns in sync after Save As.
 * @param {string} moduleId
 * @param {string} slot
 */
function rememberSavedLiveLesson(moduleId, slot) {
  const module = String(moduleId || "M1").toUpperCase();
  const liveSlot = String(slot || "C1").toUpperCase();
  const host = $("live-pack-strip");
  const map = slotsByModule();
  if (!Array.isArray(map[module])) map[module] = [];
  if (!map[module].includes(liveSlot)) map[module].push(liveSlot);
  if (host) host.setAttribute("data-slots-by-module", JSON.stringify(map));
  const moduleSelect = $("live-module-select");
  if (moduleSelect && ![...moduleSelect.options].some((opt) => opt.value === module)) {
    moduleSelect.append(new Option(module, module));
  }
}

document.querySelectorAll("#live-slot-picks [data-live-slot]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const slot = btn.getAttribute("data-live-slot") || "C1";
    applyLivePackChoice(teacherState.live_module || "M1", slot);
  });
});

$("live-module-select")?.addEventListener("change", () => {
  const module = $("live-module-select")?.value || "M1";
  paintLiveClassOptions(module);
  const slot = $("live-class-select")?.value || "C1";
  applyLivePackChoice(module, slot);
});

$("live-class-select")?.addEventListener("change", () => {
  applyLivePackChoice(
    $("live-module-select")?.value || teacherState.live_module || "M1",
    $("live-class-select")?.value || "C1"
  );
});

/**
 * PATCH one Student View dropdown onto teacher state.
 * @param {"media"|"canvas"|"slides"} surface
 */
function patchStudentViewFromControl(surface) {
  const el = $(`live-view-${surface}`);
  if (!(el instanceof HTMLSelectElement)) return;
  const mode = el.value === "student" || el.value === "team" ? el.value : "none";
  const next = { ...(teacherState.student_view || {}), [surface]: mode };
  teacherState.student_view = next;
  patchTeacherState({ student_view: next });
}

document.querySelectorAll("[data-surface-publish]").forEach((button) => {
  button.addEventListener("click", () => {
    publishSurface(button.getAttribute("data-surface-publish"))
      .catch((err) => showError("#ap-overlay-error", err));
  });
});
document.querySelectorAll("[data-surface-close]").forEach((button) => {
  button.addEventListener("click", () => {
    closeSurface(button.getAttribute("data-surface-close"))
      .catch((err) => showError("#ap-overlay-error", err));
  });
});

$("text-ride-freeze")?.addEventListener("click", () => {
  if (!textRideSlot) return;
  const frozen = !Boolean((teacherState.text_ride || {}).frozen);
  postActiveMedia({ frozen })
    .then(() => paintLiveSlotPicks())
    .catch((err) => showError("#ap-overlay-error", err));
});

document.querySelectorAll("#text-ride-cons [data-cons-item]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (!textRideSlot) return;
    const item = btn.getAttribute("data-cons-item") || "";
    postActiveMedia({ cons_item: `${textRideSlot}-${item}` })
      .then(() => paintLiveSlotPicks())
      .catch((err) => showError("#ap-overlay-error", err));
  });
});

$("live-question-list")?.addEventListener("click", async (event) => {
  const submission = event.target.closest("button[data-submission-value]");
  if (submission instanceof HTMLButtonElement) {
    const id = Number(submission.dataset.submissionMode) || 0;
    const value =
      submission.dataset.submissionValue === "group_submit" ? "group_submit" : "individual";
    groupSubmissionIntent.set(id, value);
    try {
      if (
        value === "group_submit" &&
        teacherState.groups_configured &&
        !teacherState.run_as_group
      ) {
        teacherState.run_as_group = true;
        teacherState.teams_mode = "teams";
        await patchTeacherState({ run_as_group: true }, { silent: true });
      }
    } catch (err) {
      showError("#ap-overlay-error", err);
    }
    paintLiveQuestionCards();
    return;
  }
});

$("live-question-list")?.addEventListener("change", async (event) => {
  const resultToggle = event.target.closest("[data-live-results-toggle]");
  if (resultToggle instanceof HTMLInputElement) {
    try {
      await setLifecycleResultsVisible(
        Number(resultToggle.dataset.liveResultsToggle) || 0,
        resultToggle.checked
      );
    } catch (err) {
      showError("#ap-overlay-error", err);
    }
    return;
  }
  const saveToggle = event.target.closest("[data-save-to-card]");
  if (saveToggle instanceof HTMLInputElement) {
    try {
      await setLifecycleSaveToCard(
        Number(saveToggle.dataset.saveToCard) || 0,
        saveToggle.checked
      );
    } catch (err) {
      showError("#ap-overlay-error", err);
    }
    return;
  }
  const publishMode = event.target.closest("select[data-publish-live-mode]");
  if (publishMode instanceof HTMLSelectElement) {
    const id = Number(publishMode.getAttribute("data-publish-live-mode")) || 0;
    const value = canonicalPublishMode(publishMode.value);
    openPublishIntent.set(
      id,
      value === "group_consensus" ? "group_consensus" : "individual"
    );
    return;
  }
  const select = event.target.closest("select[data-question-view]");
  if (!(select instanceof HTMLSelectElement)) return;
  try {
    await setQuestionStudentView(select.dataset.questionView || "", select.value);
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("live-question-list")?.addEventListener("click", async (event) => {
  const publish = event.target.closest("button[data-publish-live-item]");
  const close = event.target.closest("button[data-close-live-item]");
  const endVoting = event.target.closest("button[data-end-voting]");
  const award = event.target.closest("button[data-award-consensus]");
  const openRelocate = event.target.closest("button[data-open-relocate-dialog]");
  const button = event.target.closest("button[data-view-responses]");
  try {
    if (openRelocate instanceof HTMLButtonElement) {
      const card = openRelocate.closest(".live-question-card");
      const itemId = String(
        card?.dataset?.questionId || openRelocate.dataset.openRelocateDialog || ""
      ).trim();
      const labelEl = card?.querySelector(".live-question-card-text");
      const label = String(labelEl?.textContent || "This question")
        .replace(/^\s*\d+\s*/, "")
        .trim();
      openRelocateDialog(itemId, label || "This question");
      return;
    }
    if (publish instanceof HTMLButtonElement) {
      await publishLifecycleItem(Number(publish.dataset.publishLiveItem) || 0);
      return;
    }
    if (close instanceof HTMLButtonElement) {
      await closeLifecycleItem(Number(close.dataset.closeLiveItem) || 0);
      return;
    }
    if (endVoting instanceof HTMLButtonElement) {
      await endLifecycleVoting(Number(endVoting.dataset.endVoting) || 0);
      return;
    }
    if (award instanceof HTMLButtonElement) {
      await awardLifecycleTeam(
        Number(award.dataset.awardConsensus) || 0,
        Number(award.dataset.teamId) || 0
      );
      return;
    }
    if (!(button instanceof HTMLButtonElement)) return;
    await openQuestionResponses(
      Number(button.dataset.viewResponses) || 0,
      button.dataset.questionTitle || "Responses",
      button.dataset.questionType || "poll",
      button.dataset.hasAnswerKey === "1"
    );
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("live-responses-dialog")?.addEventListener("click", async (event) => {
  const select = event.target.closest("button[data-response-select]");
  if (select instanceof HTMLButtonElement) {
    applyQuestionResponseSelection(select.dataset.responseSelect || "");
    return;
  }
  const commit = event.target.closest("button[data-response-commit]");
  if (!(commit instanceof HTMLButtonElement) || !openResponsePromptId) return;
  const studentIds = [
    ...document.querySelectorAll(
      "#live-responses-list [data-response-student]:checked"
    ),
  ].map((input) => Number(input.dataset.responseStudent));
  const sessionId = liveSessionId || readLiveSessionId();
  try {
    const result = await api(
      `/api/live-sessions/${sessionId}/questions/${openResponsePromptId}/responses`,
      {
        method: "POST",
        body: JSON.stringify({
          mode: "manual",
          student_ids: studentIds,
          amount: 1,
          replace: true,
        }),
      }
    );
    if (result?.game) overlayState = result.game;
    paintQuestionResponses(result?.responses || []);
    renderAttendanceList();
    const dialog = $("live-responses-dialog");
    if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

document.querySelectorAll("input[name='live-team-keep']").forEach((input) => {
  input.addEventListener("change", () => {
    if (!(input instanceof HTMLInputElement) || !input.checked) return;
    const teamPane = $("team-assign-pane");
    if (!teamPane) return;
    if (input.value === "reassign") {
      teamPane.hidden = false;
      teamPane.removeAttribute("hidden");
    } else if (teacherState.stage !== "teams") {
      teamPane.hidden = true;
    }
  });
});

document.querySelectorAll("#live-content-palette [data-content-id]").forEach((chip) => {
  chip.addEventListener("dragstart", (event) => {
    const id = chip.getAttribute("data-content-id") || "";
    event.dataTransfer?.setData("text/plain", id);
    event.dataTransfer?.setData("application/x-lloves-content-id", id);
  });
});

document.querySelectorAll("#live-frames [data-drop-frame]").forEach((slot) => {
  slot.addEventListener("dragover", (event) => {
    event.preventDefault();
  });
  slot.addEventListener("drop", (event) => {
    event.preventDefault();
    const frame = slot.getAttribute("data-drop-frame") || "";
    const contentId =
      event.dataTransfer?.getData("application/x-lloves-content-id") ||
      event.dataTransfer?.getData("text/plain") ||
      "";
    if (!frame || !contentId) return;
    const frames = { ...(teacherState.frames || {}) };
    frames[frame] = contentId;
    patchTeacherState({ frames });
  });
});

/** @type {ReturnType<typeof bindWhiteboard> | null} */
let teacherBoard = null;

/**
 * True when the published whiteboard is the shared group board.
 * @returns {boolean}
 */
function whiteboardCollabOn() {
  if (String(teacherState.canvas_align || "") === "team") return true;
  const item = lifecycleItemForSurface("canvas");
  return item?.status === "active" && item?.publish_mode === "group_shared";
}

/**
 * Paint the session board. Collaborative publish replaces strokes and
 * shows named cursors; text labels always follow the session blob.
 * @param {any} view
 */
function paintTeacherCanvas(view) {
  if (!teacherBoard || !view) return;
  teacherBoard.importRemote(view, { collab: whiteboardCollabOn() });
}

/**
 * Post one canvas tick. Text commits send ``text_id``; cursors omit it.
 * @param {HTMLCanvasElement} canvas
 * @param {{x: number, y: number}} p
 * @param {{ended?: boolean, strokeId?: string, text?: {id: string, text: string}, cursorOnly?: boolean}} [extra]
 */
function postTeacherCanvas(canvas, p, extra = {}) {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId) return;
  const collab = whiteboardCollabOn();
  const align = String(teacherState.canvas_align || "student");
  const text = extra.text;
  if (!text && !collab && align === "student") return;
  if (!text && extra.cursorOnly && !collab) return;
  const body = {
    x: p.x / canvas.width,
    y: p.y / canvas.height,
  };
  if (text) {
    body.text_id = text.id;
    body.text = text.text;
  } else if (!extra.cursorOnly) {
    body.point = [body.x, body.y];
    body.stroke_id = extra.strokeId || undefined;
    body.ended = Boolean(extra.ended);
  }
  api(`/api/live-sessions/${sessionId}/canvas-presence`, {
    method: "POST",
    body: JSON.stringify(body),
  })
    .then((res) => {
      if (res?.canvas_view) paintTeacherCanvas(res.canvas_view);
    })
    .catch(() => {});
}

/**
 * Whiteboard drawing, text, and group collab (strokes + named cursors).
 */
function bindEphemeralCanvas() {
  const canvas = $("live-canvas-stub");
  if (!(canvas instanceof HTMLCanvasElement)) return;
  teacherBoard = bindWhiteboard(canvas, {
    undoBtn: $("live-canvas-undo"),
    redoBtn: $("live-canvas-redo"),
    eraseBtn: $("live-canvas-erase"),
    textBtn: $("live-canvas-text"),
    cursorLayer: $("live-canvas-cursors"),
    onCursor: (p) => postTeacherCanvas(canvas, p, { cursorOnly: true }),
    onText: (label) => postTeacherCanvas(canvas, { x: label.x * canvas.width, y: label.y * canvas.height }, { text: label }),
    onPoint: (p, ended, strokeId) => {
      postTeacherCanvas(canvas, p, { ended, strokeId });
    },
  });
}

selectTrackMode("individual");
setSessionTimerMinutes(3);

if (root?.dataset.apView === "live") {
  mountTeamsRenameDialog();
  bindActiveMediaControls();
  bindEphemeralCanvas();
  const preferResume = Boolean(readLiveSessionId() || liveSessionId) && !wantsFreshSetClass();
  if (!preferResume) enterSetClassPhase();
  paintTeacherShell();
  paintJoinBillboard(root.dataset.liveCode || "");
  (async () => {
    const resumed = await resumeLiveClassIfNeeded();
    if (!resumed) {
      await openRunLiveClass();
    }
    if (setupPhase) return;
    const sessionId = liveSessionId || readLiveSessionId();
    if (!sessionId) return;
    try {
      const res = await api(`/api/live-sessions/${sessionId}/teacher-state`);
      if (res?.teacher_state) adoptTeacherState(res.teacher_state);
    } catch (_) {
      /* first paint uses the local default */
    }
    await ensureC1MediaSeeded();
  })().catch((err) => showError("#ap-overlay-error", err));
}

/**
 * Bounce a student or team name off the live scoreboard, then fade it.
 * @param {string} name
 */
function spawnScorePop(name) {
  const layer = document.getElementById("score-pop-layer");
  if (!(layer instanceof HTMLElement)) return;
  const label = String(name || "").trim() || "Point";
  const chip = document.createElement("p");
  chip.className = "score-pop-chip";
  chip.textContent = `+1 ${label}`;
  layer.appendChild(chip);
  window.setTimeout(() => chip.remove(), 5000);
}

/**
 * Open / close the End Live Class save-options dialog.
 */
function wireEndLiveDialog() {
  const dialog = document.getElementById("end-live-dialog");
  if (!(dialog instanceof HTMLDialogElement)) return;
  document.querySelectorAll("[data-open-end-live]").forEach((btn) => {
    btn.addEventListener("click", () => dialog.showModal());
  });
  dialog.querySelector("[data-close-end-live]")?.addEventListener("click", () => {
    dialog.close();
  });
}

wireEndLiveDialog();

/**
 * Show one inline error inside the relocate dialog.
 * @param {unknown} err
 */
function showRelocateDialogError(err) {
  const el = $("live-relocate-error");
  if (!(el instanceof HTMLElement)) return;
  el.hidden = false;
  el.textContent = err instanceof Error ? err.message : String(err || "Request failed");
}

$("live-relocate-remove")?.addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  const itemId = String(pendingRelocate.itemId || "").trim();
  if (!itemId || !(btn instanceof HTMLButtonElement)) return;
  btn.disabled = true;
  try {
    await relocatePlaylistItem(itemId, { action: "remove" });
    closeRelocateDialog();
  } catch (err) {
    showRelocateDialogError(err);
  } finally {
    btn.disabled = false;
  }
});

$("live-relocate-move")?.addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  const itemId = String(pendingRelocate.itemId || "").trim();
  const select = $("live-relocate-page");
  const targetPageIndex = Number(
    select instanceof HTMLSelectElement ? select.value : 0
  );
  if (!itemId || targetPageIndex <= 0 || !(btn instanceof HTMLButtonElement)) return;
  if (targetPageIndex === currentLivePageIndex() + 1) {
    closeRelocateDialog();
    return;
  }
  btn.disabled = true;
  try {
    await relocatePlaylistItem(itemId, { targetPageIndex });
    closeRelocateDialog();
  } catch (err) {
    showRelocateDialogError(err);
  } finally {
    btn.disabled = false;
  }
});

$("live-relocate-dialog")?.addEventListener("click", (event) => {
  if (
    event.target instanceof HTMLElement &&
    event.target.matches("[data-live-relocate-close]")
  ) {
    closeRelocateDialog();
  }
});

$("live-relocate-dialog")?.addEventListener("cancel", () => {
  closeRelocateDialog();
});

$("live-import-mc-btn")?.addEventListener("click", () => openLiveMcImportPicker());

/**
 * Persist staff-edited student media stem/caption to the class overlay.
 * @param {{silent?: boolean}} [opts]
 */
async function persistActiveMediaCopy(opts = {}) {
  const stemInput = $("ap-media-stem");
  const captionInput = $("ap-media-caption");
  const status = $("ap-media-copy-status");
  const stem = stemInput instanceof HTMLInputElement ? stemInput.value.trim() : "";
  const caption = captionInput instanceof HTMLInputElement ? captionInput.value.trim() : "";
  try {
    await postActiveMedia({ stem, caption });
    if (status instanceof HTMLElement) {
      status.hidden = false;
      status.textContent = opts.silent ? "Autosaved." : "Saved for students.";
    }
  } catch (err) {
    if (status instanceof HTMLElement) {
      status.hidden = false;
      status.textContent = err instanceof Error ? err.message : String(err);
    }
    throw err;
  }
}

/**
 * Save staff-edited student media stem/caption onto the live session.
 * @param {SubmitEvent} event
 */
async function saveActiveMediaCopy(event) {
  event.preventDefault();
  window.clearTimeout(mediaCopySaveTimer);
  await persistActiveMediaCopy();
}

let mediaCopySaveTimer = 0;

/**
 * Debounce stem/caption edits onto the class overlay (next open uses this).
 */
function scheduleActiveMediaCopyAutosave() {
  window.clearTimeout(mediaCopySaveTimer);
  mediaCopySaveTimer = window.setTimeout(() => {
    persistActiveMediaCopy({ silent: true }).catch((err) =>
      showError("#ap-overlay-error", err)
    );
  }, 400);
}

$("ap-media-copy-editor")?.addEventListener("submit", (event) => {
  saveActiveMediaCopy(event).catch((err) => showError("#ap-overlay-error", err));
});
$("ap-media-stem")?.addEventListener("input", () => scheduleActiveMediaCopyAutosave());
$("ap-media-caption")?.addEventListener("input", () => scheduleActiveMediaCopyAutosave());
$("ap-media-stem")?.addEventListener("blur", () => {
  window.clearTimeout(mediaCopySaveTimer);
  persistActiveMediaCopy({ silent: true }).catch((err) =>
    showError("#ap-overlay-error", err)
  );
});
$("ap-media-caption")?.addEventListener("blur", () => {
  window.clearTimeout(mediaCopySaveTimer);
  persistActiveMediaCopy({ silent: true }).catch((err) =>
    showError("#ap-overlay-error", err)
  );
});

/**
 * Show MC / numeric fields for the Add New dialog.
 */
function syncAddQuestionTypeFields() {
  const selected = document.querySelector('input[name="live-add-q-type"]:checked');
  const kind = String(selected?.value || "mc").toLowerCase();
  const mc = $("live-add-q-mc-fields");
  const numeric = $("live-add-q-numeric-fields");
  const rank = $("live-add-q-rank-fields");
  if (mc instanceof HTMLElement) mc.hidden = kind !== "mc";
  if (numeric instanceof HTMLElement) numeric.hidden = kind !== "numeric";
  if (rank instanceof HTMLElement) rank.hidden = kind !== "rank";
  syncRankOptionRows();
}

/**
 * Show the first three rank rows and keep remove disabled at the minimum.
 */
function syncRankOptionRows() {
  const rows = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")];
  const visible = rows.filter((row) => row instanceof HTMLElement && !row.hidden);
  rows.forEach((row) => {
    const button = row.querySelector("[data-rank-remove]");
    if (button instanceof HTMLButtonElement) button.disabled = visible.length <= 3;
  });
  const add = $("live-add-q-rank-add");
  if (add instanceof HTMLButtonElement) add.disabled = visible.length >= 6;
}

const EQ_RENDER_ERROR = "Couldn't render this equation — check the TeX.";

/**
 * Strip wrapping dollar signs so stored TeX matches the KaTeX span helper.
 * @param {unknown} raw
 * @returns {string}
 */
function stripEquationLatex(raw) {
  return String(raw || "").trim().replace(/^\$+|\$+$/g, "").trim();
}

/**
 * Insert a LaTeX snippet into the Add New equation field.
 * @param {string} snippet
 */
function insertAddQuestionLatex(snippet) {
  const field = $("live-add-q-equation");
  if (!(field instanceof HTMLTextAreaElement)) return;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? field.value.length;
  const token = String(snippet || "");
  field.value = `${field.value.slice(0, start)}${token}${field.value.slice(end)}`;
  const cursor = start + token.length;
  field.focus();
  field.setSelectionRange(cursor, cursor);
  paintAddQuestionEquationPreview();
}

/**
 * Typeset the Add New equation preview with the same KaTeX helper as live cards.
 * @returns {Promise<void>}
 */
async function paintAddQuestionEquationPreview() {
  const field = $("live-add-q-equation");
  const preview = $("live-add-q-equation-preview");
  if (!(preview instanceof HTMLElement)) return;
  const latex = stripEquationLatex(
    field instanceof HTMLTextAreaElement ? field.value : ""
  );
  if (!latex) {
    preview.innerHTML = `<p class="hint compact">No equation yet.</p>`;
    return;
  }
  preview.innerHTML = liveQuestionEquationHtml({ equation_latex: latex }, {});
  await renderLiveQuestionMath(preview);
  if (preview.querySelector(".katex-error")) {
    preview.innerHTML = `<p class="error compact">${EQ_RENDER_ERROR}</p>`;
  }
}

/**
 * True when the open live lesson has a module token M1–M8.
 * @returns {boolean}
 */
function liveModuleScopeKnown() {
  return /^M[1-8]$/.test(String(teacherState.live_module || "").trim().toUpperCase());
}

/**
 * Show Bank scope only while Save to bank is checked.
 */
function syncAddQuestionBankFields() {
  const save = $("live-add-q-save-bank");
  const scope = $("live-add-q-bank-scope");
  const select = $("live-add-q-bank-scope-select");
  const hint = $("live-add-q-bank-scope-hint");
  const enabled = save instanceof HTMLInputElement && save.checked;
  if (scope instanceof HTMLElement) scope.hidden = !enabled;
  if (select instanceof HTMLSelectElement) select.disabled = !enabled;
  if (hint instanceof HTMLElement) {
    hint.hidden = !enabled || liveModuleScopeKnown();
  }
}

/**
 * Open the Add New question dialog for the current page.
 */
function openAddQuestionDialog() {
  const dialog = $("live-add-question-dialog");
  const form = $("live-add-question-form");
  const err = $("live-add-q-error");
  if (form instanceof HTMLFormElement) form.reset();
  const mc = document.querySelector('input[name="live-add-q-type"][value="mc"]');
  if (mc instanceof HTMLInputElement) mc.checked = true;
  const select = $("live-add-q-bank-scope-select");
  const currentModule = String(teacherState.live_module || "").trim().toUpperCase();
  if (select instanceof HTMLSelectElement) {
    select.value = /^M[1-8]$/.test(currentModule) ? currentModule : "M1";
  }
  if (err instanceof HTMLElement) {
    err.hidden = true;
    err.textContent = "";
  }
  syncAddQuestionTypeFields();
  syncAddQuestionBankFields();
  paintAddQuestionEquationPreview();
  if (dialog instanceof HTMLDialogElement) dialog.showModal();
}

/**
 * Close the Add New question dialog.
 */
function closeAddQuestionDialog() {
  const dialog = $("live-add-question-dialog");
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
}

/**
 * Upload an optional question image and return its public URL.
 * @param {File} file
 * @returns {Promise<string>}
 */
async function uploadLiveQuestionImage(file) {
  if (!classId || !(file instanceof File) || !file.size) return "";
  const body = new FormData();
  body.append("image", file);
  const response = await fetch(`/api/staff/class/${classId}/live-question-image`, {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    body,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return String(payload?.image_url || "").trim();
}

/**
 * POST one staff-authored question onto the current overlay page.
 * @returns {Promise<void>}
 */
async function submitAddQuestion() {
  const selected = document.querySelector('input[name="live-add-q-type"]:checked');
  const kind = String(selected?.value || "mc").toLowerCase();
  const text = String(($("live-add-q-text")?.value || "")).trim();
  if (!text) throw new Error("Question text required.");
  const equation = stripEquationLatex($("live-add-q-equation")?.value || "");
  const imageInput = $("live-add-q-image");
  const imageFile =
    imageInput instanceof HTMLInputElement && imageInput.files?.[0]
      ? imageInput.files[0]
      : null;
  const imageUrl = imageFile ? await uploadLiveQuestionImage(imageFile) : "";
  const saveBank = $("live-add-q-save-bank");
  const scope = $("live-add-q-bank-scope-select");
  const body = {
    type: kind,
    text,
    equation,
    image_url: imageUrl,
    page_number: currentLivePageNumber(),
    stage: String(teacherState.stage || "round"),
    save_to_bank: Boolean(saveBank instanceof HTMLInputElement && saveBank.checked),
    bank_scope: String(
      scope instanceof HTMLSelectElement && scope.value
        ? scope.value
        : teacherState.live_module || "M1"
    ),
  };
  if (kind === "mc") {
    body.options = [0, 1, 2, 3].map((index) =>
      String($(`live-add-q-opt-${index}`)?.value || "").trim()
    );
    const key = document.querySelector('input[name="live-add-q-key"]:checked');
    body.correct_index = Number(key?.value || 0);
  }
  if (kind === "numeric") {
    body.correct_answer = String(($("live-add-q-numeric-answer")?.value || "")).trim();
    body.tolerance = String(($("live-add-q-tolerance")?.value || "0")).trim();
    body.tolerance_kind = String(($("live-add-q-tolerance-kind")?.value || "absolute"));
  }
  if (kind === "rank") {
    body.options = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")]
      .filter((row) => row instanceof HTMLElement && !row.hidden)
      .map((row) => String(row.querySelector("input")?.value || "").trim())
      .filter(Boolean);
  }
  const module = String(teacherState.live_module || "M1").toUpperCase();
  const slot = String(teacherState.live_slot || "C1").toUpperCase();
  if (!classId) throw new Error("Class is not loaded.");
  const payload = await api(
    `/api/staff/class/${classId}/live-lessons/${module}/${slot}/add-question`,
    { method: "POST", body: JSON.stringify(body) }
  );
  applyLiveMcImportPayload(payload);
  paintLiveQuestionCards();
  paintQuestionArtifact();
  staffStateNeedsFull = true;
  const sessionId = liveSessionId || readLiveSessionId();
  if (sessionId) {
    await pollLiveSessionAttendees({ full: true, force: true });
    paintLiveQuestionCards();
  }
}

$("live-add-question-btn")?.addEventListener("click", () => openAddQuestionDialog());
document.querySelectorAll('input[name="live-add-q-type"]').forEach((input) => {
  input.addEventListener("change", () => syncAddQuestionTypeFields());
});
$("live-add-q-rank-add")?.addEventListener("click", () => {
  const hidden = document.querySelector("#live-add-q-rank-list .live-add-rank-row[hidden]");
  if (hidden instanceof HTMLElement) hidden.hidden = false;
  syncRankOptionRows();
});
document.querySelectorAll("#live-add-q-rank-list [data-rank-remove]").forEach((button) => {
  button.addEventListener("click", () => {
    const rows = [...document.querySelectorAll("#live-add-q-rank-list .live-add-rank-row")].filter(
      (row) => row instanceof HTMLElement && !row.hidden
    );
    if (rows.length <= 3) return;
    const row = button.closest(".live-add-rank-row");
    if (!(row instanceof HTMLElement)) return;
    const input = row.querySelector("input");
    if (input instanceof HTMLInputElement) input.value = "";
    row.hidden = true;
    syncRankOptionRows();
  });
});
document.querySelectorAll("[data-eq-insert]").forEach((button) => {
  button.addEventListener("click", () => {
    insertAddQuestionLatex(String(button.getAttribute("data-eq-insert") || ""));
  });
});
$("live-add-q-equation")?.addEventListener("input", () => paintAddQuestionEquationPreview());
$("live-add-q-save-bank")?.addEventListener("change", () => syncAddQuestionBankFields());
$("live-add-question-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const err = $("live-add-q-error");
  if (err instanceof HTMLElement) {
    err.hidden = true;
    err.textContent = "";
  }
  const btn = $("live-add-q-submit");
  if (btn instanceof HTMLButtonElement) btn.disabled = true;
  try {
    await submitAddQuestion();
    closeAddQuestionDialog();
  } catch (error) {
    if (err instanceof HTMLElement) {
      err.hidden = false;
      err.textContent = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (btn instanceof HTMLButtonElement) btn.disabled = false;
  }
});
$("live-add-question-dialog")?.addEventListener("click", (event) => {
  if (
    event.target instanceof HTMLElement &&
    event.target.matches("[data-live-add-question-close]")
  ) {
    closeAddQuestionDialog();
  }
});
$("live-add-question-dialog")?.addEventListener("cancel", () => closeAddQuestionDialog());
