/**
 * MCK-242: Draw-question submit and teacher status helpers.
 *
 * The plain live whiteboard has no title field and no submit control.
 * A team or individual draw question shows Submit and sends the drawing.
 */

/**
 * Capture the board the student is looking at, including the live ink layer.
 * @param {HTMLCanvasElement|null} canvas
 * @returns {string}
 */
export function canvasToPngDataUrl(canvas) {
  if (!(canvas instanceof HTMLCanvasElement)) return "";
  const ink = canvas.nextElementSibling;
  const layers = [canvas];
  if (ink instanceof HTMLCanvasElement && ink.classList.contains("live-canvas-ink")) {
    layers.push(ink);
  }
  const width = Math.max(...layers.map((layer) => layer.width || 0));
  const height = Math.max(...layers.map((layer) => layer.height || 0));
  if (!width || !height) {
    try {
      return canvas.toDataURL("image/png");
    } catch {
      return "";
    }
  }
  const shot = document.createElement("canvas");
  shot.width = width;
  shot.height = height;
  const ctx = shot.getContext("2d");
  if (!ctx) return "";
  for (const layer of layers) {
    ctx.drawImage(layer, 0, 0, width, height);
  }
  try {
    return shot.toDataURL("image/png");
  } catch {
    return "";
  }
}

/**
 * True for a team draw or an individual draw question.
 * @param {any} item
 * @returns {boolean}
 */
export function isDrawQuestion(item) {
  if (isTeamDrawItem(item)) return true;
  const type = String(
    item?.content?.type ||
      item?.item?.type ||
      item?.prompt?.payload?.type ||
      item?.type ||
      ""
  ).toLowerCase();
  return type === "team_draw";
}

/**
 * Whether this whiteboard should show Submit.
 *
 * A plain board stays bare. A live team or individual draw question shows
 * Submit until that drawing is sent. There is never a title box.
 * @param {any} item Active lifecycle row, or null on a plain board.
 * @param {boolean} canvasVisible
 * @returns {boolean}
 */
export function drawSubmitVisible(item, canvasVisible) {
  if (!canvasVisible || !item || typeof item !== "object") return false;
  if (String(item.status || "active") !== "active") return false;
  if (!isDrawQuestion(item)) return false;
  const team = item.group_submit;
  if (String(item.response_mode || "") === "team_draw") {
    return Boolean(team && team.can_submit && !team.submitted);
  }
  if (item.my_response) return false;
  return item.can_submit !== false;
}

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
    const waiting =
      String(result?.response_mode || "") === "individual"
        ? "Drawings appear here after students submit."
        : "Groups appear here after publish.";
    return `<div class="team-draw-teacher">
      ${mysteryHtml}
      <p class="hint compact">${waiting}</p>
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

/**
 * Catch-up controls for an individual draw question.
 * The board is the drawing. Submit stays off until there is a stroke,
 * then sends the PNG, never typed text.
 * @returns {string}
 */
export function catchupDrawControlsHtml() {
  return `<div class="student-live-answer-controls catchup-draw" data-catchup-draw="1">
    <div class="live-canvas-stage catchup-draw-stage">
      <canvas class="catchup-draw-canvas" width="720" height="360" aria-label="Drawing"></canvas>
    </div>
    <button type="button" class="prompt-submit" data-live-submit="catchup-draw" disabled>Submit</button>
  </div>`;
}

function escapeTeamDraw(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
