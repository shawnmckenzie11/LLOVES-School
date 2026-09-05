/**
 * Narrow Zoom-share overlay: session code, join count, roster, optional teams.
 */
import { escapeHtml, formatPoints } from "./common.js";
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
const copyBtn = document.getElementById("live-copy-code");
const copyFeedback = document.getElementById("live-copy-feedback");
const roundEl = document.getElementById("live-round");
const countEl = document.getElementById("live-count");
const rosterBody = document.getElementById("live-roster-body");
const emptyEl = document.getElementById("live-empty");
const teamsSection = document.getElementById("live-teams");
const teamBoard = document.getElementById("live-team-board");
const endedEl = document.getElementById("live-ended");

let classId = classIdHint > 0 ? classIdHint : 0;
let tickBusy = false;
let lastRosterKey = "";
let lastTeamKey = "";
let lastRoundKey = "";
let lastPresent = [];
/** @type {any|null} */
let lastBoard = null;
let copyFeedbackTimer = 0;

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
  if (!board?.live) return false;
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
 * Paint In-class list: flat for individual, grouped by team when Team Tracking.
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
 * Show current round title for Team Tracking; hide when individual / idle.
 * @param {any} board
 */
function paintRound(board) {
  if (!roundEl) return;
  const title = String(board?.round_title || "").trim();
  const show = hasTeamScoreboard(board) && Boolean(title);
  const key = show ? title : "";
  if (key === lastRoundKey && roundEl.hidden === !show) return;
  lastRoundKey = key;
  roundEl.textContent = title;
  roundEl.hidden = !show;
  roundEl.classList.toggle("hidden", !show);
}

/**
 * Paint code, count, and roster from live-session state.
 * @param {any} state
 */
function paintSession(state) {
  const code = String(state?.code || state?.session?.session_code || "").trim() || "····";
  if (codeEl) codeEl.textContent = code;
  lastPresent = presentAttendees(state);
  if (countEl) {
    const n = Number(state?.count);
    const count = Number.isFinite(n) ? n : lastPresent.length;
    countEl.textContent = `${count} logged in`;
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
  if (!hasTeamScoreboard(board)) {
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
 * Apply scoreboard poll: round label, team-grouped roster, vertical scores.
 * @param {any} board
 */
function paintTeams(board) {
  lastBoard = board;
  paintRound(board);
  paintRoster();
  paintTeamScores(board);
}

/**
 * Copy the displayed class code to the clipboard with brief feedback.
 */
async function copyClassCode() {
  const code = String(codeEl?.textContent || "").trim();
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

copyBtn?.addEventListener("click", (event) => {
  event.preventDefault();
  copyClassCode();
});

if (!sessionId) {
  if (codeEl) codeEl.textContent = "—";
  if (countEl) countEl.textContent = "Missing session";
} else {
  tick();
  setInterval(tick, 2000);
}
