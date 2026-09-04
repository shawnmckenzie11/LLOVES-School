/**
 * Phone-first student live-class home: personal stats + class scoreboard.
 */
const waitEl = document.getElementById("student-wait");
const meEl = document.getElementById("me-board");
const boardEl = document.getElementById("class-board");
const body = document.body;

/**
 * Format a points value for the student boards.
 * @param {unknown} value
 * @returns {string}
 */
function pts(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  return String(Math.round(n * 10) / 10);
}

/**
 * Paint private stats (points, team, optional rank).
 * @param {any} payload
 */
function paintMe(payload) {
  if (!meEl) return;
  const me = payload.me || {};
  const rankLine =
    payload.show_rank && me.rank
      ? `<p class="stat">Rank <strong>${me.rank}</strong>${me.rank_of ? ` / ${me.rank_of}` : ""}</p>`
      : "";
  meEl.innerHTML = `
    <p class="me-name">${escapeText(me.codename || "You")}</p>
    <p class="stat">My points <strong>${escapeText(pts(me.points))}</strong></p>
    <p class="stat">Team ${escapeText(me.team_name || "—")} <strong>${escapeText(pts(me.team_points))}</strong></p>
    ${rankLine}
  `;
}

/**
 * Paint the public team scoreboard strip.
 * @param {any} payload
 */
function paintBoard(payload) {
  if (!boardEl) return;
  const sb = payload.scoreboard || {};
  const teams = sb.teams || [];
  if (!teams.length) {
    boardEl.innerHTML = `<p class="sb-idle">${sb.live ? "Scores coming…" : "Scoreboard idle"}</p>`;
    return;
  }
  boardEl.innerHTML = teams
    .map(
      (team) =>
        `<div class="sb-team" style="--team:${escapeText(team.color || "#0f766e")}">
          <span class="sb-name">${escapeText(team.name)}</span>
          <span class="sb-score">${escapeText(pts(team.score))}</span>
        </div>`
    )
    .join("");
}

/**
 * Escape text for HTML.
 * @param {unknown} value
 * @returns {string}
 */
function escapeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Apply live vs waiting layout.
 * @param {any} payload
 */
function applyLayout(payload) {
  const live = Boolean(payload.scoring);
  body.classList.toggle("is-live", live);
  if (waitEl) {
    waitEl.hidden = live;
    waitEl.textContent = live ? "" : "Waiting for your teacher to start scoring.";
  }
}

/**
 * Fetch and paint /api/student/state.
 */
async function tick() {
  try {
    const res = await fetch("/api/student/state");
    const data = await res.json();
    if (data.redirect && data.redirect !== "/student/home" && !data.me) {
      location.href = data.redirect;
      return;
    }
    applyLayout(data);
    paintMe(data);
    paintBoard(data);
  } catch (_err) {
    /* keep last paint */
  }
}

tick();
setInterval(tick, 4000);
