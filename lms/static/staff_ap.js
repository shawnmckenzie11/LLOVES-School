/**
 * Run Live Class IA v2 shell: single-line header, OptionsStrip, dual body.
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
let lastAssignMode = "balanced";
let pendingTeam = null;
let roundEndsAtMs = 0;
let liveStamp = "";
let pendingScoreboard = false;
let liveSessionId = Number(root?.dataset.liveSessionId || 0) || 0;
let joinBillboardCopyTimer = null;
let sessionPollTimer = null;
let sessionPollMs = 0;
/** @type {any} */
let lastMcTally = null;
let lastMcBindKey = "";
/** C2/C3 are text-only: never stored in active_media_json. */
let textOnlyChallenge = "";
let sessionPresentIds = new Set();
/** Unmatched guests currently present in the live session. */
let sessionGuests = [];
/** @type {Set<number>} */
let sessionLateIds = new Set();
let scoringLocked = false;
let trackMode = null;
let currentStep = "att";
let draftRound = { kind: "open", minutes: 20, title: "" };
let nextDraftRound = { kind: "open", minutes: 10, title: "" };
let setupRoundNumber = 1;
let mediaPushTimer = 0;

const SEED_MEDIA_URL = "/static/live-media/m1c1-c1-real-slice.html";
const SEED_MEDIA_TITLE =
  "Consider the parabola represented by y = ax^2 + bx + c. What do you know about a, b, and c?";
const SEED_MEDIA_STEM = SEED_MEDIA_TITLE;
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

const TEACHER_STAGES = ["join", "teams", "meet", "round", "play"];
const LAYOUT_PRESETS = {
  media_full: { A: "media" },
  questions_full: { A: "questions" },
  media_questions: { A: "media", B: "questions" },
  three_up: { A: "media", B: "questions", C: "canvas_slides" },
  canvas_media: { A: "canvas_slides", B: "media" },
};

const FLAG_BY_STAGE = {
  join: "question",
  teams: "question",
  meet: "question",
  round: "question",
  play: "question",
  challenge: "question",
  freeze: "score",
};

const REACHED_STAGES = new Set(["join"]);

/** @type {{stage: string, round?: string|null, teams_mode: string, layout_preset: string, frames: Record<string, string>, active_tab: string, active_media_ref?: string|null, prompt_ref?: string|null, canvas_ephemeral: true, updated_at?: string, cue_id?: string|null, meet_chain?: any, state_seq?: number, student_frames?: Record<string, boolean>, unlocks?: Record<string, boolean>, mc_ui?: {prompt_ref: string, reveal: boolean, reveal_to_students?: boolean}}} */
let teacherState = {
  stage: "join",
  round: null,
  teams_mode: "individual",
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
  student_frames: { questions: true, media: false, canvas: false },
  unlocks: { media: false, canvas: false },
};

let teacherStateInFlight = false;
let lastTeacherMediaSrc = "";

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
  };
  if (next.mc_ui && typeof next.mc_ui === "object") {
    teacherState.mc_ui = { ...next.mc_ui };
  } else {
    delete teacherState.mc_ui;
  }
  REACHED_STAGES.add(teacherState.stage);
  if (Number(teacherState.state_seq) !== prevSeq) {
    paintTeacherShell();
    return;
  }
  paintMcResultsSlot();
}

/**
 * Mark StageRail from thin JSON stage (not the wizard step).
 */
function paintStageRail() {
  const stage = teacherState.stage || stageForStep();
  REACHED_STAGES.add(stage);
  document.querySelectorAll("#live-stage-rail [data-stage]").forEach((btn) => {
    const id = btn.getAttribute("data-stage") || "";
    const on = id === stage;
    btn.classList.toggle("is-active", on);
    if (on) btn.setAttribute("aria-current", "step");
    else btn.removeAttribute("aria-current");
    btn.classList.toggle("is-reached", REACHED_STAGES.has(id));
  });
  const index = TEACHER_STAGES.indexOf(stage);
  const prev = $("live-stage-prev");
  const next = $("live-stage-next");
  if (prev instanceof HTMLButtonElement) prev.disabled = index <= 0;
  if (next instanceof HTMLButtonElement) next.disabled = index < 0 || index >= TEACHER_STAGES.length - 1;
}

/**
 * Keep ClassListPane mounted and visible on every stage, including Prev.
 * Stage changes only swap OptionsStrip bodies and Active Content bindings.
 */
function lockClassListPane() {
  const stage = teacherState.stage || stageForStep();
  if (root) root.dataset.stage = stage;
  for (const id of ["live-shell-left", "class-list-pane", "live-shell-body", "live-shell-right"]) {
    const el = $(id);
    if (!(el instanceof HTMLElement)) continue;
    el.hidden = false;
    el.removeAttribute("hidden");
    el.classList.remove("hidden");
  }
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
    const showStrip = stage !== "join";
    card.hidden = !showStrip;
    if (showStrip) card.removeAttribute("hidden");
  }
  if (teams) teams.hidden = stage !== "teams";
  if (meet) meet.hidden = stage !== "meet";
  if (round) round.hidden = stage !== "round";
  if (play) play.hidden = stage !== "play";
  if (teamPane && stage !== "teams") {
    closeTeamsPops();
  }
  paintTeamsStripEnabled();
  if (rounds) {
    rounds.hidden = stage !== "round";
    if (stage === "round") rounds.removeAttribute("hidden");
  }
  document.querySelectorAll("#live-round-picks [data-round]").forEach((btn) => {
    const on = btn.getAttribute("data-round") === (teacherState.round || "minds_on");
    btn.classList.toggle("is-active", on);
  });
  const unlockMedia = $("live-unlock-media");
  const unlockCanvas = $("live-unlock-canvas");
  const unlocks = teacherState.unlocks || {};
  if (unlockMedia instanceof HTMLInputElement) unlockMedia.checked = Boolean(unlocks.media);
  if (unlockCanvas instanceof HTMLInputElement) unlockCanvas.checked = Boolean(unlocks.canvas);
}

/**
 * Place content ids into CSS frame slots. Never recreates the media iframe.
 */
function paintFrames() {
  const frames = teacherState.frames || LAYOUT_PRESETS[teacherState.layout_preset] || { A: "media" };
  const used = Object.keys(frames).filter((key) => frames[key]).sort().join("");
  const host = $("live-frames");
  if (host) {
    host.dataset.preset = teacherState.layout_preset || "media_full";
    host.dataset.slots = used || "A";
  }
  document.querySelectorAll(".live-content-slot").forEach((el) => {
    const id = el.getAttribute("data-content-id") || "";
    const slot = Object.keys(frames).find((key) => frames[key] === id) || "";
    el.setAttribute("data-slot", slot);
    el.classList.toggle("is-parked", !slot);
  });
  document.querySelectorAll("#live-preset-row [data-preset]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.getAttribute("data-preset") === teacherState.layout_preset);
  });
  document.querySelectorAll("#live-content-tabs [data-tab]").forEach((btn) => {
    const on = btn.getAttribute("data-tab") === teacherState.active_tab;
    btn.classList.toggle("is-active", on);
    if (on) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
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
  const stage = teacherState.stage;
  const list = $("ap-score-list");
  const hasRows = Boolean(list && list.children.length);
  const showScore =
    stage !== "join" &&
    stage !== "teams" &&
    (hasRows || (stage === "play" && isScoringLive()));
  const scorePanel = $("ap-panel-score");
  if (scorePanel) scorePanel.hidden = !showScore;
  paintMcResultsSlot();
  const mcSlot = $("mc-results-slot");
  const showMc = Boolean(mcSlot && !mcSlot.hidden);
  const show = showMc || showScore;
  results.hidden = !show;
  results.classList.toggle("is-flagged", show);
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
 * Paint LIVE / REVEAL inside the Questions ResultsStrip slot only.
 */
function paintMcResultsSlot() {
  const slot = $("mc-results-slot");
  const progress = $("mc-live-progress");
  const soft = $("mc-live-soft");
  const bars = $("mc-reveal-bars");
  const revealBtn = $("mc-reveal-btn");
  const hideBtn = $("mc-hide-reveal-btn");
  if (!slot) return;
  const tally = lastMcTally;
  const hasMc = Boolean(tally && Array.isArray(tally.choices) && tally.choices.length);
  slot.hidden = !hasMc;
  if (!hasMc) {
    lastMcBindKey = "";
    return;
  }
  const reveal = mcRevealOn(tally);
  const bind = mcBindKey(tally, reveal);
  if (bind && bind === lastMcBindKey && slot.dataset.mode === (reveal ? "reveal" : "live")) {
    return;
  }
  lastMcBindKey = bind;
  slot.dataset.mode = reveal ? "reveal" : "live";
  const responded = Number(tally.responded || 0);
  const present = Math.max(Number(tally.present || 0), responded);
  if (progress) progress.textContent = `${responded}/${present} responded`;
  const softParts = (tally.choices || []).map((row) => `${row.id} · ${row.count}`);
  if (soft) {
    soft.hidden = reveal || !softParts.length;
    soft.textContent = softParts.length ? `Soft counts · ${softParts.join(" · ")}` : "";
  }
  if (bars) {
    bars.hidden = !reveal;
    if (reveal) {
      bars.innerHTML = (tally.choices || [])
        .map((row) => {
          const pct = Math.max(0, Math.min(100, Number(row.pct) || 0));
          const label = String(row.label || "").replace(/</g, "&lt;");
          return `<div class="mc-reveal-row"><span class="mc-reveal-letter">${row.id}</span><p class="mc-reveal-label">${label}</p><span class="mc-reveal-meta">${row.count} · ${pct}%</span><span class="mc-reveal-track"><span class="mc-reveal-fill" style="width:${pct}%"></span></span></div>`;
        })
        .join("");
    }
  }
  if (revealBtn) revealBtn.hidden = reveal;
  if (hideBtn) hideBtn.hidden = !reveal;
}

/**
 * Adopt a staff /state mc_tally without remounting Active Content tabs.
 * @param {any} tally
 */
function applyMcTally(tally) {
  lastMcTally = tally && typeof tally === "object" && tally.prompt_ref ? tally : null;
  paintResultsStrip();
  syncLiveSessionPolling();
}

/**
 * Current MC prompt_ref for a reveal PATCH (source-agnostic).
 * @returns {string}
 */
function currentMcPromptRef() {
  return String(lastMcTally?.prompt_ref || teacherState.prompt_ref || "minds_on");
}

/**
 * PATCH mc_ui only. Never sends a Wonder cue_id.
 * @param {boolean} reveal
 */
function patchMcReveal(reveal) {
  patchTeacherState({
    mc_ui: {
      prompt_ref: currentMcPromptRef(),
      reveal: Boolean(reveal),
      reveal_to_students: false,
    },
  });
}

/**
 * Paint rail, OptionsStrip, and CSS slots from teacherState.
 * Prev/Next and every stage keep Left ClassListPane mounted.
 */
function paintTeacherShell() {
  lockClassListPane();
  paintStageRail();
  paintOptionCard();
  paintFrames();
  paintRightFlag();
  paintMeetChainChrome();
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
function paintQuestionArtifact(media) {
  const status = $("question-artifact-status");
  const flag = $("question-artifact-flag");
  if (!status || !flag) return;
  const row = media || {};
  const cons = String(row.cons_item || "").trim();
  const toast = String(row.toast || row.caption || "").trim();
  const chain = teacherState.meet_chain;
  const meetOn =
    teacherState.stage === "meet" ||
    String(overlayState?.game?.overlay_phase || "") === "meet_teams";
  if (meetOn && chain && Array.isArray(chain.chain) && chain.chain.length) {
    const step = String(chain.chain[chain.index] || "A");
    const labels = { A: "Today I’m the teammate who…", C: "Shared spark", B: "One thing our team might need…" };
    status.textContent = "Meet QH chain is live on the existing prompt channel. Questions tab only.";
    flag.hidden = false;
    flag.textContent = `Meet · ${step} · ${labels[step] || step}`;
    paintMeetChainChrome();
    return;
  }
  if (cons) {
    status.textContent = "CONS / QH ride uses the existing active-media + prompt channel.";
    flag.hidden = false;
    flag.textContent = toast ? `${cons} · ${toast}` : cons;
    paintMeetChainChrome();
    return;
  }
  status.textContent = "No live prompt. Meet QH chain and CONS/QH use the existing session channels.";
  flag.hidden = true;
  flag.textContent = "";
  paintMeetChainChrome();
}

/**
 * Soft A/B counts and A→C→B dots. No full ResultsStrip.
 */
function paintMeetChainChrome() {
  const chrome = $("meet-chain-chrome");
  const dots = $("meet-chain-dots");
  const countsEl = $("meet-soft-counts");
  const skip = $("meet-chain-skip-c");
  const next = $("meet-chain-next");
  const chain = teacherState.meet_chain;
  const on = teacherState.stage === "meet" && chain && Array.isArray(chain.chain);
  if (chrome) chrome.hidden = !on;
  if (!on) return;
  const letters = chain.chain;
  const index = Number(chain.index) || 0;
  const step = String(letters[index] || "A");
  if (dots) {
    dots.innerHTML = letters
      .map((letter, i) => {
        const cls = i === index ? "is-current" : i < index ? "is-done" : "";
        return `<span class="meet-dot ${cls}" data-step="${letter}">${letter}</span>`;
      })
      .join("");
  }
  if (countsEl) {
    const bag = step === "B" ? chain.b_picks || {} : chain.a_picks || {};
    const counts = {};
    Object.values(bag).forEach((choice) => {
      const key = String(choice || "").trim();
      if (!key) return;
      counts[key] = (counts[key] || 0) + 1;
    });
    const parts = Object.keys(counts).map((key) => `${key} · ${counts[key]}`);
    countsEl.textContent =
      step === "C"
        ? "Soft react only — no leaderboard."
        : parts.length
          ? `Soft counts · ${parts.join(" · ")}`
          : "Soft counts · waiting for taps";
  }
  if (skip instanceof HTMLButtonElement) {
    skip.hidden = !letters.includes("C") || step === "B";
  }
  if (next instanceof HTMLButtonElement) {
    next.disabled = index >= letters.length - 1;
  }
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
function paintJoinBillboard(code, opts = {}) {
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
      paintJoinBillboard("", { ended: true });
      stopLiveSessionPolling();
      return;
    }
    paintJoinBillboard(joinCodeFromPayload(payload));
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
    await applySessionPresentTicks(
      present.map((row) => Number(row.student_id)),
      present
    );
    const media = payload?.active_media || payload?.session?.active_media;
    paintActiveMediaStatus(media);
    paintQuestionArtifact(media);
    if (payload?.teacher_state) adoptTeacherState(payload.teacher_state);
    applyMcTally(payload?.mc_tally);
    ensureC1MediaSeeded();
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
  syncLiveSessionPolling();
}

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
    stem: row.stem || SEED_MEDIA_STEM,
    entry_chip: row.entry_chip || "",
  };
}

/**
 * Teacher preview iframe for the C1 Real-slice. Controls live in the frame.
 * @param {any} media
 */
function paintActiveMediaStatus(media) {
  const preview = $("ap-media-preview");
  if (!preview) return;
  const mediaUrl = String((media && media.url) || SEED_MEDIA_URL).trim();
  const teacherSrc = mediaUrl.includes("?")
    ? `${mediaUrl}&role=teacher`
    : `${mediaUrl}?role=teacher`;
  const currentSrc = preview.getAttribute("src") || lastTeacherMediaSrc;
  if (currentSrc !== teacherSrc && lastTeacherMediaSrc !== teacherSrc) {
    preview.src = teacherSrc;
    lastTeacherMediaSrc = teacherSrc;
  } else {
    lastTeacherMediaSrc = currentSrc || teacherSrc;
  }
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
 * Seed C1 Real-slice onto the live session when the blob is empty.
 */
async function ensureC1MediaSeeded() {
  const sessionId = liveSessionId || readLiveSessionId();
  if (!sessionId || mediaSeedInFlight || textOnlyChallenge) return;
  mediaSeedInFlight = true;
  try {
    const res = await api(`/api/live-sessions/${sessionId}/active-media`);
    if (res.active_media && res.active_media.url) {
      paintActiveMediaStatus(res.active_media);
      return;
    }
    await postActiveMedia({
      url: SEED_MEDIA_URL,
      title: SEED_MEDIA_TITLE,
      stem: SEED_MEDIA_STEM,
      caption: "",
      entry_chip: "",
      student_controls_unlocked: false,
      param_push: { a: false, b: false, c: false },
      param_frozen: { a: true, b: true, c: true },
      reveal_axes: false,
      reveal_lateral: false,
      allow_3d_limited: false,
      frozen: false,
      challenge: "C1",
      cons_item: "",
      toast: "",
      toast_key: "",
      unlock_flags: { L0: true, L1: false, L2: false, L3: false, L4: false },
      answers: [],
      params: { a: 1, b: 0, c: 0 },
      show_z_axis: false,
      surface_transparency: 0.75,
    });
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
  return res.active_media;
}

/**
 * Bind iframe → session patches. All teacher tools live in the Real-slice frame.
 */
function bindActiveMediaControls() {
  paintActiveMediaStatus(null);
  ensureC1MediaSeeded();
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data;
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
      body: "{}",
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
    openLiveScoring(overlayState);
    await ensureLiveSessionMinted(opts);
    return;
  }
  if (liveSessionId) sessionPresentIds = new Set();
  scoringLocked = false;
  trackMode = null;
  renderAttendanceList();
  showPanel("att");
  await ensureLiveSessionMinted(opts);
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
        ensureLiveSessionOverlay(null);
        startLiveSessionPolling();
      }
      return;
    }

    const today = String(logContext.today || "").trim();
    const todayLogged = await isDateLogged(today);
    const decision = resolveLogDate(logContext, { todayLogged }, { flow: "run_live" });

    if (decision.mode === "confirm_override") {
      if (!(await confirmOverrideIfLogged(decision.iso))) return;
      await proceedRunLiveBegin(decision.iso, { reservedWin: null });
      return;
    }

    if (decision.mode === "auto") {
      await proceedRunLiveBegin(decision.iso, { reservedWin: null });
      return;
    }

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
function studentTeamColor(studentId) {
  const nTeams = currentTeamCount();
  if (nTeams < 2) return "";
  const assigned = (overlayState?.teams || []).filter((team) => team.name !== "Class");
  if (assigned.length >= 2) {
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
    const teamColor = studentTeamColor(student.id);
    if (teamColor) {
      row.classList.add("has-team-color");
      row.style.setProperty("--team", teamColor);
    }
    row.innerHTML = `<span class="ap-att-check" aria-hidden="true">${mark}</span><span class="ap-att-name">${escapeHtml(displayName(student))}</span><span class="ap-att-mood" aria-hidden="true">${face}</span>`;
    list.appendChild(row);
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
}

/**
 * Live present count in the header and class-list pane.
 */
function updateAttCount() {
  const n = selectedPresent().length + sessionGuests.length;
  const el = $("ap-att-count");
  if (el) el.textContent = `Attendance: ${n}`;
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
  if (teacherState.teams_mode !== nextMode) {
    teacherState.teams_mode = nextMode;
    patchTeacherState({ teams_mode: nextMode }, { silent: true });
  }
  renderAttendanceList();
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
  const raw = Number($("ap-n-teams")?.value);
  if (!Number.isFinite(raw)) return 1;
  return Math.max(1, Math.round(raw));
}

/**
 * Team-count bounds from present students. Min 1 = no teams.
 * @returns {{min:number, max:number}}
 */
function nTeamsBounds() {
  const present = (overlayState?.present_ids || selectedPresent()).length;
  return { min: 1, max: Math.max(2, present) };
}

/**
 * Show Assign / Track / Rename only when count > 1.
 */
function paintTeamsStripEnabled() {
  const team = currentTeamCount() > 1;
  const assign = $("live-teams-assign");
  if (assign) {
    assign.hidden = false;
    assign.setAttribute("aria-disabled", team ? "false" : "true");
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
    rename.hidden = false;
    rename.disabled = !team;
    rename.setAttribute("aria-disabled", team ? "false" : "true");
  }
  if (!team) closeTeamsPops();
}

/**
 * Open one Teams popup (rename or manual). Never full-bleed under the list.
 * @param {string} id
 */
function openTeamsPop(id) {
  const pane = $("team-assign-pane");
  const anchor =
    $(id === "ap-panel-names" ? "ap-teams-rename" : "ap-assign-manual") ||
    $("live-option-card");
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
  for (const popId of ["ap-manual-assign", "ap-panel-names"]) {
    const el = $(popId);
    if (!(el instanceof HTMLElement)) continue;
    el.hidden = popId !== id;
    if (popId === id) el.removeAttribute("hidden");
  }
}

/**
 * Hide rename + manual popups and their host.
 */
function closeTeamsPops() {
  for (const popId of ["ap-manual-assign", "ap-panel-names"]) {
    const el = $(popId);
    if (el) el.hidden = true;
  }
  const pane = $("team-assign-pane");
  if (pane) pane.hidden = true;
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
  if (n > 1) {
    if (trackMode !== "team") lastAssignMode = "balanced";
    selectAssignMode(lastAssignMode || "balanced");
    selectTrackMode("team");
  } else {
    selectTrackMode("individual");
  }
  paintTeamsStripEnabled();
  renderAttendanceList();
}

/**
 * Refresh the teams overlay from game state (scoreboard/rank live on Tracking mode).
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
  setNTeams(Number($("ap-n-teams")?.value) || 1);
  if (currentTeamCount() > 1) selectAssignMode(lastAssignMode || "balanced");
}

/**
 * Show/hide the scoreboard mock preview (below checkbox container, above footer).
 */
function syncScoreboardPreview() {
  const wrap = $("ap-scoreboard-preview-wrap");
  if (wrap) wrap.hidden = true;
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
  }
}

$("ap-n-teams-down")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) - 1));
$("ap-n-teams-up")?.addEventListener("click", () => setNTeams(Number($("ap-n-teams").value) + 1));
$("ap-n-teams")?.addEventListener("change", () => setNTeams(Number($("ap-n-teams").value)));
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
  closeTeamsPops();
  renderAttendanceList();
  updateStepSummaries();
}

/**
 * Read Meet Your Team timer minutes from the stepper (default 3).
 * @returns {number}
 */
function meetMinutes() {
  const raw = Number($("ap-meet-minutes")?.value);
  if (!Number.isFinite(raw)) return 3;
  return Math.max(1, Math.min(30, Math.round(raw)));
}

/**
 * Clamp the Meet Your Team timer control.
 * @param {number} value
 */
function setMeetMinutes(value) {
  const n = Math.max(1, Math.min(30, Number(value) || 3));
  const el = $("ap-meet-minutes");
  if (el) el.value = String(n);
}

/**
 * Post meet-teams phase so the live overlay shows Meet the Teams + countdown.
 * @returns {Promise<void>}
 */
async function startMeetTeamsPhase() {
  const minutes = meetMinutes();
  overlayState = await api(`/api/classes/${classId}/game/meet-teams`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });
  applyMeetTimerUi(overlayState);
}

/**
 * Paint Meet Your Team Start/Pause control from game timer fields.
 * @param {any} [state]
 */
function applyMeetTimerUi(state = overlayState) {
  const game = state?.game || {};
  const stepper = $("ap-meet-stepper");
  const clock = $("ap-meet-live-clock");
  const btn = $("ap-meet-start");
  const phase = String(game.overlay_phase || "");
  const running =
    phase === "meet_teams" &&
    Boolean(game.round_ends_at_ms) &&
    !game.timer_paused;
  const paused = phase === "meet_teams" && Boolean(game.timer_paused);
  if (stepper) stepper.hidden = running || paused;
  if (clock) {
    clock.hidden = !(running || paused);
    if (running) {
      meetEndsAtMs = Number(game.round_ends_at_ms) || 0;
      clock.textContent = formatCountdown(remainingUntilMs(meetEndsAtMs));
    } else if (paused) {
      meetEndsAtMs = 0;
      clock.textContent = formatCountdown(Number(game.round_remaining_sec) || 0);
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
      btn.textContent = "Meet";
      btn.dataset.meetState = "idle";
    }
  }
  paintQuestionArtifact();
}

/** Meet Your Team countdown deadline (epoch ms), or 0 when idle/paused. */
let meetEndsAtMs = 0;

/**
 * Tick the Meet Your Team countdown when running.
 */
function paintMeetClock() {
  const clock = $("ap-meet-live-clock");
  const btn = $("ap-meet-start");
  if (!clock || clock.hidden) return;
  if (btn?.dataset.meetState === "paused") return;
  if (!meetEndsAtMs) return;
  clock.textContent = formatCountdown(remainingUntilMs(meetEndsAtMs));
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

$("ap-teams-next")?.addEventListener("click", () => {
  if (currentTeamCount() <= 1) {
    clickExistingNext("ap-gamify-next");
    return;
  }
  const mode = lastAssignMode || "balanced";
  if (mode === "manual") {
    const open = $("ap-manual-assign");
    if (!open || open.hidden) {
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
  if (hint) {
    hint.textContent = "Optional: Update team names below";
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
 * Persist rename fields without leaving TEAMS.
 * @returns {Promise<void>}
 */
async function saveTeamNamesFromPop() {
  const teams = [...document.querySelectorAll("#ap-name-list input")]
    .map((el) => ({
      id: Number(el.dataset.teamId),
      name: el.value,
    }))
    .filter((row) => Number.isFinite(row.id) && row.id > 0);
  if (!teams.length) return;
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
  if (currentTeamCount() < 2 || isScoringLive()) return;
  try {
    const assigned = (overlayState?.teams || []).filter((team) => team.name !== "Class");
    if (assigned.length >= 2) {
      renderNamesPanel();
    } else {
      renderDraftNamesPanel();
    }
    openTeamsPop("ap-panel-names");
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
});

$("ap-teams-rename-done")?.addEventListener("click", () => {
  saveTeamNamesFromPop()
    .then(() => closeTeamsPops())
    .catch((err) => showError("#ap-overlay-error", err));
});

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
  setMeetMinutes(meetMinutes() - 1);
});
$("ap-meet-minutes-up")?.addEventListener("click", () => {
  setMeetMinutes(meetMinutes() + 1);
});
$("ap-meet-minutes")?.addEventListener("change", () => {
  setMeetMinutes(meetMinutes());
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
      applyMeetTimerUi(overlayState);
      return;
    }
    if (state === "paused") {
      overlayState = await api(`/api/classes/${classId}/game/timer/resume`, {
        method: "POST",
        body: "{}",
      });
      applyMeetTimerUi(overlayState);
      return;
    }
    await startMeetTeamsPhase();
    await patchTeacherState({ stage: "meet" });
  } catch (err) {
    showError("#ap-overlay-error", err);
  }
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
  if (title) title.textContent = `Start Round ${setupRoundNumber}`;
  const start = $("ap-rounds-start");
  if (start) start.textContent = `Start Round ${setupRoundNumber}`;
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
  // Live-overlay is primary for Track Live Class — skip separate ESPN window.
  // Individual tracking never uses the team scoreboard overlay.
  const wantEspn = trackMode === "team" && pendingScoreboard && !hasLiveOverlay;
  const overlay = wantEspn ? reserveScoreboardOverlay() : null;
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
 * Paint the inline editor for the next live round.
 */
function renderNextRoundPanel() {
  if (trackMode === "individual" && !["open", "challenge"].includes(nextDraftRound.kind)) {
    nextDraftRound = { kind: "open", minutes: nextDraftRound.minutes || 10, title: "" };
  }
  const fields = $("ap-next-round-fields");
  if (fields) fields.innerHTML = roundEditorMarkup(nextDraftRound, setupRoundNumber);
  const title = $("ap-next-round-title");
  if (title) title.textContent = `Start Round ${setupRoundNumber}`;
  const start = $("ap-next-round-start");
  if (start) start.textContent = `Start Round ${setupRoundNumber}`;
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
  paintMeetClock();
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
    const openStatuses = new Set(["attendance", "teams", "names", "rounds", "live"]);
    if (state?.game && openStatuses.has(status)) {
      overlayState = state;
      const meeting =
        state.session?.meeting_date || defaultSchoolDay(logContext) || todayISO();
      syncOverlayPickers(logContext, meeting);
      const meetingInput = $("ap-meeting-date");
      if (meetingInput) meetingInput.value = meeting;
      if (status === "live") {
        openLiveScoring(overlayState);
        ensureLiveSessionOverlay();
        startLiveSessionPolling();
        return true;
      }
      renderAttendanceList();
      if (status === "teams" || status === "names") {
        const nAssigned = (state.teams || []).filter((team) => team.name !== "Class").length;
        if (nAssigned >= 2) setNTeams(nAssigned);
        else selectTrackMode("team");
        renderTeamsPanel();
        showPanel("teams");
        if (status === "names") {
          renderNamesPanel();
          openTeamsPop("ap-panel-names");
        }
      } else if (status === "rounds") {
        selectTrackMode("team");
        showPanel("rounds");
      } else {
        showPanel("att");
      }
      await ensureLiveSessionMinted();
      return true;
    }
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
    await ensureLiveSessionMinted();
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
  teacherStateInFlight = true;
  try {
    const res = await api(`/api/live-sessions/${sessionId}/teacher-state`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (res?.teacher_state) adoptTeacherState(res.teacher_state);
    return teacherState;
  } catch (err) {
    if (!opts.silent) showError("#ap-overlay-error", err);
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

$("live-stage-prev")?.addEventListener("click", () => {
  patchTeacherState({ advance: "prev" });
});
$("live-stage-next")?.addEventListener("click", () => {
  patchTeacherState({ advance: "next" });
});
$("meet-chain-next")?.addEventListener("click", () => {
  patchTeacherState({ meet_action: "next" });
});
$("meet-chain-skip-c")?.addEventListener("click", () => {
  patchTeacherState({ meet_action: "skip_c" });
});
$("meet-chain-end")?.addEventListener("click", () => {
  patchTeacherState({ meet_action: "clear" });
});

document.querySelectorAll("#live-content-tabs [data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.getAttribute("data-tab") || "media";
    const content =
      tab === "questions" ? "questions" : tab === "canvas_slides" ? "canvas_slides" : "media";
    const frames = { ...(teacherState.frames || {}) };
    const visible = Object.values(frames);
    const body = { active_tab: tab };
    if (!visible.includes(content)) {
      if (visible.length <= 1) {
        if (content === "questions") body.layout_preset = "questions_full";
        else if (content === "media") body.layout_preset = "media_full";
        else body.frames = { A: "canvas_slides" };
      } else {
        frames.A = content;
        body.frames = frames;
      }
    }
    patchTeacherState(body);
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
    patchTeacherState({ layout_preset: preset });
  });
});

document.querySelectorAll("#live-round-picks [data-round]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const round = btn.getAttribute("data-round") || "minds_on";
    patchTeacherState({ round, stage: "round" });
  });
});

$("live-unlock-media")?.addEventListener("change", () => {
  const box = $("live-unlock-media");
  if (!(box instanceof HTMLInputElement)) return;
  patchTeacherState({ unlocks: { media: box.checked } });
});
$("live-unlock-canvas")?.addEventListener("change", () => {
  const box = $("live-unlock-canvas");
  if (!(box instanceof HTMLInputElement)) return;
  patchTeacherState({ unlocks: { canvas: box.checked } });
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

/**
 * Ephemeral whiteboard: draw in memory only. No persist / CRDT / save.
 */
function bindEphemeralCanvas() {
  const canvas = $("live-canvas-stub");
  if (!(canvas instanceof HTMLCanvasElement)) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  let drawing = false;
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  };
  canvas.addEventListener("pointerdown", (event) => {
    drawing = true;
    const p = point(event);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing) return;
    const p = point(event);
    ctx.lineTo(p.x, p.y);
    ctx.strokeStyle = "#12202e";
    ctx.lineWidth = 2;
    ctx.stroke();
  });
  canvas.addEventListener("pointerup", () => {
    drawing = false;
  });
}

selectTrackMode("individual");
if (localStorage.getItem(scoreboardKey) === null) {
  localStorage.setItem(scoreboardKey, "1");
}
setMeetMinutes(3);

if (root?.dataset.apView === "live") {
  bindActiveMediaControls();
  bindEphemeralCanvas();
  paintTeacherShell();
  paintJoinBillboard(root.dataset.liveCode || "");
  (async () => {
    const resumed = await resumeLiveClassIfNeeded();
    if (!resumed) {
      await openRunLiveClass();
    }
    const sessionId = liveSessionId || readLiveSessionId();
    if (!sessionId) return;
    try {
      const res = await api(`/api/live-sessions/${sessionId}/teacher-state`);
      if (res?.teacher_state) adoptTeacherState(res.teacher_state);
    } catch (_) {
      /* first paint uses the local default */
    }
  })().catch((err) => showError("#ap-overlay-error", err));
}
