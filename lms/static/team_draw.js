/**
 * MCK-242: Team Draw teacher status helpers.
 *
 * The live whiteboard has no title field and no submit control.
 */

/**
 * @param {any} item
 * @returns {boolean}
 */
export function isTeamDrawItem(item) {
  return String(item?.response_mode || "") === "team_draw";
}

/**
 * Locked steps 1–3 plus dashed step-4 tile for teacher Run Live Class.
 * @param {string[]} steps
 * @returns {string}
 */
export function teamDrawMysteryTeacherHtml(steps) {
  const locked = (Array.isArray(steps) ? steps : []).slice(0, 3);
  while (locked.length < 3) {
    locked.push("");
  }
  const tiles = locked
    .map(
      (text, index) => `<div class="team-draw-mystery-tile is-locked" aria-label="Step ${index + 1} locked">
        <span class="team-draw-mystery-num">${index + 1}</span>
        <p class="team-draw-mystery-text">${escapeTeamDraw(String(text || ""))}</p>
        <span class="team-draw-mystery-lock" aria-hidden="true">🔒</span>
      </div>`
    )
    .join("");
  const yours = `<div class="team-draw-mystery-tile is-yours" aria-label="Step 4 is yours">
      <span class="team-draw-mystery-num">4</span>
      <p class="team-draw-mystery-text"><strong>Step 4 is yours</strong></p>
    </div>`;
  return `<div class="team-draw-mystery-grid" aria-label="Mystery rule steps">${tiles}${yours}</div>`;
}

/**
 * @param {any} result
 * @returns {string}
 */
export function teamDrawStatusBoardHtml(result) {
  const board = Array.isArray(result?.status_board) ? result.status_board : [];
  const mysterySteps = Array.isArray(result?.mystery_steps) ? result.mystery_steps : [];
  const showMystery =
    Boolean(result?.mystery_rule) || (mysterySteps.length === 3 && mysterySteps.some(Boolean));
  const mysteryHtml = showMystery ? teamDrawMysteryTeacherHtml(mysterySteps) : "";
  if (!board.length) {
    return `<div class="team-draw-teacher">
      ${mysteryHtml}
      <p class="hint compact">Groups appear here after publish.</p>
    </div>`;
  }
  const rows = board
    .map((row, index) => {
      const label = String(row.status_label || (row.submitted ? "✓ submitted" : "drawing…"));
      const title = String(row.title || "").trim();
      const team = String(row.team_name || `Team ${index + 1}`);
      return `<li class="team-draw-status-row">
        <span class="team-draw-team">${escapeTeamDraw(team)}</span>
        <span class="team-draw-state">${escapeTeamDraw(label)}</span>
        ${title ? `<span class="team-draw-title">${escapeTeamDraw(title)}</span>` : ""}
      </li>`;
    })
    .join("");
  const itemId = Number(result?.item?.id) || 0;
  const actions = itemId
    ? `<div class="team-draw-teacher-actions">
        <button type="button" class="secondary live-q-btn" data-team-draw-gallery="${itemId}">Show gallery</button>
        <button type="button" class="secondary live-q-btn" data-team-draw-clear="${itemId}">End &amp; clear boards</button>
      </div>`
    : "";
  return `<div class="team-draw-teacher">
    <p class="team-draw-prompt">${escapeTeamDraw(String(result?.prompt || ""))}</p>
    ${mysteryHtml}
    <ul class="team-draw-status-board" aria-label="Team Draw status">${rows}</ul>
    ${actions}
    <div class="team-draw-gallery" data-team-draw-gallery-host="${itemId}" hidden></div>
  </div>`;
}

/**
 * @param {string} text
 * @returns {string}
 */
function escapeTeamDraw(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
