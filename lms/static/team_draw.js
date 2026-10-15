/**
 * MCK-242: Team Draw student submit + teacher status helpers.
 */

/**
 * @param {HTMLCanvasElement|null} canvas
 * @returns {string}
 */
export function canvasToPngDataUrl(canvas) {
  if (!canvas) return "";
  try {
    return canvas.toDataURL("image/png");
  } catch {
    return "";
  }
}

/**
 * @param {any} item
 * @returns {boolean}
 */
export function isTeamDrawItem(item) {
  return String(item?.response_mode || "") === "team_draw";
}

/**
 * @param {any} result
 * @returns {string}
 */
export function teamDrawStatusBoardHtml(result) {
  const board = Array.isArray(result?.status_board) ? result.status_board : [];
  if (!board.length) {
    return `<p class="hint compact">Groups appear here after publish.</p>`;
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
