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
const countEl = document.getElementById("live-count");
const rosterEl = document.getElementById("live-roster");
const emptyEl = document.getElementById("live-empty");
const teamsSection = document.getElementById("live-teams");
const teamBoard = document.getElementById("live-team-board");
const endedEl = document.getElementById("live-ended");

let classId = classIdHint > 0 ? classIdHint : 0;
let tickBusy = false;
let lastRosterKey = "";
let lastTeamKey = "";

/**
 * Present attendees (still in the session) from a state payload.
 * @param {any} state
 * @returns {Array<{student_id:number,codename:string}>}
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
 * Paint code, count, and roster from live-session state.
 * @param {any} state
 */
function paintSession(state) {
  const code = String(state?.code || state?.session?.session_code || "").trim() || "····";
  if (codeEl) codeEl.textContent = code;
  const present = presentAttendees(state);
  if (countEl) {
    const n = Number(state?.count);
    const count = Number.isFinite(n) ? n : present.length;
    countEl.textContent = `${count} logged in`;
  }
  const key = present.map((row) => `${row.student_id}:${row.codename}:${row.mood || ""}`).join("|");
  if (key !== lastRosterKey && rosterEl) {
    lastRosterKey = key;
    rosterEl.innerHTML = present
      .map((row) => {
        const face = moodGlyph(row.mood);
        const label = face ? `${face} ${escapeHtml(row.codename)}` : escapeHtml(row.codename);
        return `<li>${label}</li>`;
      })
      .join("");
  }
  if (emptyEl) emptyEl.hidden = present.length > 0;
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
 * Stack ESPN-style team scores under the roster when team scoring is live.
 * @param {any} board
 */
function paintTeams(board) {
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
      (team) => `<div class="live-overlay-team" style="--team:${escapeHtml(team.color || "#888")}">
        <span class="live-overlay-team-swatch" aria-hidden="true"></span>
        <span class="live-overlay-team-name">${escapeHtml(team.name)}</span>
        <span class="live-overlay-team-score">${escapeHtml(formatPoints(team.score))}</span>
      </div>`
    )
    .join("");
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

if (!sessionId) {
  if (codeEl) codeEl.textContent = "—";
  if (countEl) countEl.textContent = "Missing session";
} else {
  tick();
  setInterval(tick, 2000);
}
