/**
 * Narrow Zoom-share overlay: session code, join count, roster, optional teams.
 */
import { escapeHtml, formatPoints, formatCountdown, remainingUntilMs } from "./common.js";
import { moodGlyph } from "/static/mood_faces.js";

const params = new URLSearchParams(location.search);
if (params.get("overlay") === "1") {
  document.body.classList.add("overlay");
}

const sessionId = Number(
  (location.pathname.match(/\/live-overlay\/(\d+)/) || [])[1] || params.get("session") || 0
);
const classIdHint = Number(params.get("class_id") || 0);

const codeEl = document.getElementById("live-code");
const copyFeedback = document.getElementById("live-copy-feedback");
const roundEl = document.getElementById("live-round");
const roundClockEl = document.getElementById("live-round-clock");
const countEl = document.getElementById("live-count");
const rosterBody = document.getElementById("live-roster-body");
const emptyEl = document.getElementById("live-empty");
const teamsSection = document.getElementById("live-teams");
const teamBoard = document.getElementById("live-team-board");
const endedEl = document.getElementById("live-ended");
const anticipationEl = document.getElementById("live-anticipation");
const anticipationClockEl = document.getElementById("live-anticipation-clock");
const anticipationTitleEl = document.getElementById("live-anticipation-title");
const celebrateEl = document.getElementById("live-celebrate");
const celebrateTeamEl = document.getElementById("live-celebrate-team");
const celebrateMembersEl = document.getElementById("live-celebrate-members");
const confettiEl = document.getElementById("live-confetti");

let classId = classIdHint > 0 ? classIdHint : 0;
let tickBusy = false;
let lastRosterKey = "";
let lastTeamKey = "";
let lastRoundKey = "";
let roundEndsAtMs = 0;
let lastPresent = [];
/** @type {any|null} */
let lastBoard = null;
let copyFeedbackTimer = 0;
let lastPhase = "";
let celebrationDone = false;
/** @type {AudioContext|null} */
let audioCtx = null;
let anticipationBeepTimer = 0;
let anticipationIntensity = 0;

/**
 * Present attendees (still in the session) from a state payload.
 * @param {any} state
 * @returns {Array<{student_id:number,codename:string,mood:string|null}>}
 */
function presentAttendees(state) {
  const rows = Array.isArray(state?.attendees) ? state.attendees : [];
  return rows
    .filter((row) => !row?.left_at)
    .map((row) => ({
      student_id: Number(row.student_id),
      codename: String(row.codename || "").trim() || `Student ${row.student_id}`,
      mood: row.mood || null,
    }))
    .sort((a, b) => a.codename.localeCompare(b.codename, undefined, { sensitivity: "base" }));
}

/**
 * True when scoreboard payload has real team competition (not Class bucket).
 * @param {any} board
 * @returns {boolean}
 */
function hasTeamScoreboard(board) {
  if (!board) return false;
  const phase = String(board.overlay_phase || "");
  const active =
    Boolean(board.live) ||
    Boolean(board.final) ||
    phase === "meet_teams" ||
    phase === "anticipation";
  if (!active) return false;
  const teams = Array.isArray(board.teams) ? board.teams : [];
  if (teams.length < 2) return false;
  if (teams.length === 1 && teams[0]?.name === "Class") return false;
  return true;
}

/**
 * Map student_id → team metadata from a live scoreboard payload.
 * @param {any} board
 * @returns {Map<number, {id:number,name:string,color:string,sort:number}>}
 */
function teamByStudentId(board) {
  const map = new Map();
  const teams = Array.isArray(board?.teams) ? board.teams : [];
  teams.forEach((team, index) => {
    const meta = {
      id: Number(team.id) || index,
      name: String(team.name || `Team ${index + 1}`),
      color: String(team.color || "#888"),
      sort: index,
    };
    for (const player of team.players || []) {
      const sid = Number(player.student_id);
      if (Number.isFinite(sid) && sid > 0) map.set(sid, meta);
    }
  });
  return map;
}

/**
 * One roster list item HTML (mood glyph + escaped codename).
 * @param {{codename:string,mood:string|null}} row
 * @returns {string}
 */
function rosterItemHtml(row) {
  const face = moodGlyph(row.mood);
  const label = face ? `${face} ${escapeHtml(row.codename)}` : escapeHtml(row.codename);
  return `<li>${label}</li>`;
}

/**
 * Paint Participants list: flat for individual, grouped by team when Team Tracking.
 */
function paintRoster() {
  if (!rosterBody) return;
  const present = lastPresent;
  const teamMode = hasTeamScoreboard(lastBoard);
  const key = [
    teamMode ? "team" : "flat",
    present.map((row) => `${row.student_id}:${row.codename}:${row.mood || ""}`).join("|"),
    teamMode
      ? (lastBoard?.teams || [])
          .map((t) => `${t.id}:${(t.players || []).map((p) => p.student_id).join(",")}`)
          .join(";")
      : "",
  ].join("::");
  if (key === lastRosterKey) {
    if (emptyEl) emptyEl.hidden = present.length > 0;
    return;
  }
  lastRosterKey = key;

  if (!teamMode) {
    rosterBody.innerHTML = `<ul class="live-overlay-list" id="live-roster"></ul>`;
    const list = document.getElementById("live-roster");
    if (list) list.innerHTML = present.map(rosterItemHtml).join("");
  } else {
    const byTeam = teamByStudentId(lastBoard);
    /** @type {Map<number|string, {name:string,color:string,sort:number,members:typeof present}>} */
    const groups = new Map();
    (lastBoard?.teams || []).forEach((team, index) => {
      const tid = Number(team.id) || index;
      groups.set(tid, {
        name: String(team.name || "Team"),
        color: String(team.color || "#888"),
        sort: index,
        members: [],
      });
    });
    const unassigned = [];
    for (const row of present) {
      const meta = byTeam.get(row.student_id);
      if (!meta) {
        unassigned.push(row);
        continue;
      }
      const group = groups.get(meta.id);
      if (group) group.members.push(row);
      else unassigned.push(row);
    }
    const blocks = [...groups.values()]
      .sort((a, b) => a.sort - b.sort)
      .map((group) => {
        const items = group.members.map(rosterItemHtml).join("");
        return `<div class="live-overlay-team-group" style="--team:${escapeHtml(group.color)}">
          <h3 class="live-overlay-team-heading">${escapeHtml(group.name)}</h3>
          <ul class="live-overlay-list">${items || `<li class="live-overlay-muted">—</li>`}</ul>
        </div>`;
      });
    if (unassigned.length) {
      blocks.push(`<div class="live-overlay-team-group live-overlay-team-group--unassigned">
        <h3 class="live-overlay-team-heading">Joining…</h3>
        <ul class="live-overlay-list">${unassigned.map(rosterItemHtml).join("")}</ul>
      </div>`);
    }
    rosterBody.innerHTML = blocks.join("");
  }
  if (emptyEl) emptyEl.hidden = present.length > 0;
}

/**
 * Show current round title and countdown for Team Tracking; hide when individual / idle.
 * @param {any} board
 */
function paintRound(board) {
  const phase = String(board?.overlay_phase || "");
  if (phase === "anticipation") {
    if (roundEl) {
      roundEl.hidden = true;
      roundEl.classList.add("hidden");
    }
    if (roundClockEl) {
      roundClockEl.hidden = true;
      roundClockEl.classList.add("hidden");
    }
    return;
  }
  const title = String(board?.round_title || "").trim();
  const show = hasTeamScoreboard(board) && Boolean(title);
  const key = show ? `${title}:${board?.round_ends_at_ms || ""}` : "";
  if (roundEl) {
    if (key === lastRoundKey && roundEl.hidden === !show) {
      paintRoundClock(board);
      return;
    }
    lastRoundKey = key;
    roundEl.textContent = show ? (phase === "meet_teams" ? title : `Round: ${title}`) : "";
    roundEl.hidden = !show;
    roundEl.classList.toggle("hidden", !show);
  }
  roundEndsAtMs = Number(board?.round_ends_at_ms) || 0;
  paintRoundClock(board);
}

/**
 * Paint the round countdown under the round label.
 * @param {any} board
 */
function paintRoundClock(board) {
  if (!roundClockEl) return;
  const phase = String(board?.overlay_phase || "");
  if (phase === "anticipation") {
    roundClockEl.hidden = true;
    roundClockEl.classList.add("hidden");
    return;
  }
  const show = hasTeamScoreboard(board) && Boolean(board?.round_ends_at_ms);
  roundClockEl.textContent = show ? formatCountdown(remainingUntilMs(roundEndsAtMs)) : "";
  roundClockEl.hidden = !show;
  roundClockEl.classList.toggle("hidden", !show);
}

/**
 * Display join code without spaces (raw uppercase).
 * @param {string} code
 * @returns {string}
 */
function formatDisplayCode(code) {
  return copyableCode(code) || "····";
}

/**
 * Raw code text suitable for clipboard copy.
 * @param {string} code
 * @returns {string}
 */
function copyableCode(code) {
  return String(code || "").trim().toUpperCase().replace(/\s+/g, "");
}

/**
 * Paint code, count, and roster from live-session state.
 * @param {any} state
 */
function paintSession(state) {
  const code = String(state?.code || state?.session?.session_code || "").trim() || "····";
  if (codeEl) codeEl.textContent = formatDisplayCode(code);
  lastPresent = presentAttendees(state);
  if (countEl) {
    const n = Number(state?.count);
    const count = Number.isFinite(n) ? n : lastPresent.length;
    countEl.textContent = `(${count})`;
  }
  paintRoster();
  const ended = state?.phase === "ended" || state?.session?.status === "ended";
  if (endedEl) {
    endedEl.hidden = !ended;
    endedEl.classList.toggle("hidden", !ended);
  }
  if (ended) {
    document.body.classList.add("is-ended");
  }
  const sessionClass = Number(state?.session?.class_id || 0);
  if (sessionClass > 0) classId = sessionClass;
}

/**
 * Stack ESPN-style vertical team score boxes under the roster (no Leaders ticker).
 * @param {any} board
 */
function paintTeamScores(board) {
  if (!teamsSection || !teamBoard) return;
  const phase = String(board?.overlay_phase || "");
  if (phase === "anticipation" || !hasTeamScoreboard(board)) {
    teamsSection.hidden = true;
    teamsSection.classList.add("hidden");
    teamBoard.innerHTML = "";
    lastTeamKey = "";
    return;
  }
  const teams = board.teams || [];
  const key = teams.map((t) => `${t.id}:${t.score}:${t.name}`).join("|");
  teamsSection.hidden = false;
  teamsSection.classList.remove("hidden");
  if (key === lastTeamKey) return;
  lastTeamKey = key;
  teamBoard.innerHTML = teams
    .map(
      (team) => `<div class="live-overlay-espn" style="--team:${escapeHtml(team.color || "#888")}">
        <span class="live-overlay-espn-swatch" aria-hidden="true"></span>
        <div class="live-overlay-espn-meta">
          <span class="live-overlay-espn-name">${escapeHtml(team.name)}</span>
          <span class="live-overlay-espn-score">${escapeHtml(formatPoints(team.score))}</span>
        </div>
      </div>`
    )
    .join("");
}

/**
 * Ensure a shared AudioContext for original anticipation beeps.
 * @returns {AudioContext|null}
 */
function getAudioContext() {
  if (audioCtx) return audioCtx;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  audioCtx = new Ctx();
  return audioCtx;
}

/**
 * Play one short original beep (not copyrighted theme audio).
 * @param {number} intensity 0–1
 */
function playAnticipationBeep(intensity) {
  const ctx = getAudioContext();
  if (!ctx) return;
  const now = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const freq = 220 + intensity * 440;
  osc.type = "triangle";
  osc.frequency.setValueAtTime(freq, now);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.045 + intensity * 0.06, now + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(now);
  osc.stop(now + 0.2);
}

/**
 * Start CSS + Web Audio anticipation sequence (~2 minutes).
 * @param {any} board
 */
function startAnticipation(board) {
  if (!anticipationEl) return;
  anticipationEl.hidden = false;
  anticipationEl.classList.remove("hidden");
  document.body.classList.add("is-anticipation");
  anticipationIntensity = 0;
  if (anticipationTitleEl) anticipationTitleEl.textContent = "Rounds incoming";
  paintAnticipationClock(board);
  if (anticipationBeepTimer) window.clearInterval(anticipationBeepTimer);
  anticipationBeepTimer = window.setInterval(() => {
    anticipationIntensity = Math.min(1, anticipationIntensity + 0.03);
    document.body.style.setProperty("--anticipation-intensity", String(anticipationIntensity));
    if (anticipationTitleEl) {
      const stings = ["Rounds incoming", "Teams ready", "Hold…", "Almost there"];
      const idx = Math.min(stings.length - 1, Math.floor(anticipationIntensity * stings.length));
      anticipationTitleEl.textContent = stings[idx];
    }
    playAnticipationBeep(anticipationIntensity);
  }, 3500);
}

/**
 * Stop anticipation visuals and audio loop.
 */
function stopAnticipation() {
  if (anticipationBeepTimer) {
    window.clearInterval(anticipationBeepTimer);
    anticipationBeepTimer = 0;
  }
  if (anticipationEl) {
    anticipationEl.hidden = true;
    anticipationEl.classList.add("hidden");
  }
  document.body.classList.remove("is-anticipation");
  document.body.style.removeProperty("--anticipation-intensity");
}

/**
 * Paint anticipation countdown from board timer.
 * @param {any} board
 */
function paintAnticipationClock(board) {
  if (!anticipationClockEl) return;
  const ends = Number(board?.round_ends_at_ms) || 0;
  anticipationClockEl.textContent = ends ? formatCountdown(remainingUntilMs(ends)) : "";
}

/**
 * Rank teams by score descending.
 * @param {any} board
 * @returns {Array<any>}
 */
function rankedTeams(board) {
  const teams = Array.isArray(board?.teams) ? [...board.teams] : [];
  return teams.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
}

/**
 * Run winner celebration once when the final board arrives.
 * @param {any} board
 */
function maybeCelebrateWinner(board) {
  const isFinal =
    Boolean(board?.final) || board?.status === "ended" || String(board?.overlay_phase || "") === "";
  if (!board || !hasTeamScoreboard(board)) return;
  if (!(Boolean(board.final) || board.status === "ended")) return;
  if (celebrationDone) return;
  celebrationDone = true;
  stopAnticipation();
  const winners = rankedTeams(board);
  const top = winners[0];
  if (!top || !celebrateEl) return;
  document.body.classList.add("is-celebrating");
  celebrateEl.hidden = false;
  celebrateEl.classList.remove("hidden");
  if (celebrateTeamEl) celebrateTeamEl.textContent = String(top.name || "Winner");
  const members = Array.isArray(top.players) ? top.players : [];
  if (celebrateMembersEl) {
    celebrateMembersEl.innerHTML = members
      .map((p) => {
        const name = String(p.codename || p.first_name || p.name || "").trim() || "Player";
        return `<li>${escapeHtml(name)}</li>`;
      })
      .join("");
  }
  spawnConfetti();
  window.setTimeout(() => {
    document.body.classList.remove("is-celebrating");
    if (celebrateEl) {
      celebrateEl.classList.add("is-settled");
    }
  }, 8000);
  void isFinal;
}

/**
 * Create lightweight CSS confetti particles in the celebration layer.
 */
function spawnConfetti() {
  if (!confettiEl) return;
  confettiEl.innerHTML = "";
  const colors = ["#f5c518", "#3d7eff", "#9dffb0", "#ff8f8f", "#d7deef"];
  for (let i = 0; i < 36; i += 1) {
    const bit = document.createElement("span");
    bit.className = "live-overlay-confetti-bit";
    bit.style.setProperty("--i", String(i));
    bit.style.setProperty("--c", colors[i % colors.length]);
    bit.style.setProperty("--x", `${(Math.random() * 100).toFixed(1)}%`);
    bit.style.setProperty("--d", `${(0.8 + Math.random() * 1.4).toFixed(2)}s`);
    confettiEl.appendChild(bit);
  }
}

/**
 * Apply scoreboard poll: phase, round label, team-grouped roster, vertical scores.
 * @param {any} board
 */
function paintTeams(board) {
  lastBoard = board;
  const phase = String(board?.overlay_phase || "");
  if (phase === "anticipation") {
    if (lastPhase !== "anticipation") startAnticipation(board);
    else paintAnticipationClock(board);
  } else if (lastPhase === "anticipation") {
    stopAnticipation();
  }
  lastPhase = phase;
  paintRound(board);
  paintRoster();
  paintTeamScores(board);
  maybeCelebrateWinner(board);
}

/**
 * Copy the displayed class code to the clipboard with brief feedback.
 */
async function copyClassCode() {
  const code = copyableCode(codeEl?.textContent || "");
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
  if (!copyFeedback) return;
  copyFeedback.hidden = false;
  if (copyFeedbackTimer) window.clearTimeout(copyFeedbackTimer);
  copyFeedbackTimer = window.setTimeout(() => {
    copyFeedback.hidden = true;
  }, 1600);
}

/**
 * Fetch JSON from an API path; return null on auth/network failure.
 * @param {string} url
 * @returns {Promise<any|null>}
 */
async function fetchJson(url) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) return null;
  return response.json();
}

/**
 * Poll session roster and optional class scoreboard once.
 */
async function tick() {
  if (!sessionId || tickBusy) return;
  tickBusy = true;
  try {
    const payload = await fetchJson(`/api/live-sessions/${sessionId}/state`);
    if (payload?.ok === false) return;
    if (payload) paintSession(payload);
    if (classId > 0) {
      const board = await fetchJson(`/api/classes/${classId}/scoreboard`);
      if (board) paintTeams(board);
    }
  } catch {
    /* keep last paint; retry next interval */
  } finally {
    tickBusy = false;
  }
}

codeEl?.addEventListener("click", (event) => {
  event.preventDefault();
  copyClassCode();
});
codeEl?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    copyClassCode();
  }
});

if (!sessionId) {
  if (codeEl) codeEl.textContent = "—";
  if (countEl) countEl.textContent = "Missing session";
} else {
  tick();
  setInterval(tick, 2000);
  setInterval(() => {
    if (!lastBoard) return;
    const phase = String(lastBoard.overlay_phase || "");
    if (phase === "anticipation") paintAnticipationClock(lastBoard);
    else paintRoundClock(lastBoard);
  }, 1000);
}
