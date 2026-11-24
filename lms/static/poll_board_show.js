/**
 * Show on Board for a whiteboard poll.
 *
 * The teacher toggle already stores the board key. This paints that
 * drawing in a full-viewport dimmed overlay, centered and read-only.
 * Turning the toggle off hides the overlay immediately. The payload is
 * strokes and text only: no student name, email, or PIN is drawn.
 *
 * MCK-514: a Show Your Work background image is drawn first, scaled to
 * fit the board and centred (the phone's rule), then the marks on top in
 * the same 2:1 board space, so they line up with the image.
 *
 * MCK-519: Show All paints that same board once per card, in a grid.
 * Each card keeps the student or team name above the drawing.
 */

import {
  drawFittedBoardBackground,
  loadBoardBackgroundImage,
  safeBoardImageUrl,
} from "./board_background.js";
import { textFontCss } from "./live_whiteboard.js";

export const POLL_BOARD_OVERLAY_ID = "poll-board-overlay";
export const POLL_BOARD_GRID_ID = "poll-board-grid";

/** Shown-board size. Ink is stored as 0–1 of this 2:1 board. */
export const SHOWN_BOARD_SIZE = Object.freeze({ w: 720, h: 360 });

const STYLE_ID = "poll-board-show-style";
const LOGICAL_W = SHOWN_BOARD_SIZE.w;
const LOGICAL_H = SHOWN_BOARD_SIZE.h;

const CSS = `
#${POLL_BOARD_OVERLAY_ID} {
  position: fixed; inset: 0; z-index: 2147482000;
  display: flex; align-items: center; justify-content: center;
  padding: 1.25rem; box-sizing: border-box;
  background: rgba(15, 23, 42, 0.78);
}
#${POLL_BOARD_OVERLAY_ID}[hidden] { display: none !important; }
#${POLL_BOARD_OVERLAY_ID} .poll-board-frame {
  max-width: min(92vw, 1100px);
  max-height: 82dvh;
  width: min(92vw, 1100px, calc(82dvh * 2));
  aspect-ratio: 2 / 1;
  background: #f7fafc;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
}
#${POLL_BOARD_OVERLAY_ID} canvas {
  display: block; width: 100%; height: 100%;
  pointer-events: none; background: #f7fafc;
}
#${POLL_BOARD_GRID_ID} {
  position: fixed; inset: 0; z-index: 2147482000;
  display: flex; align-items: center; justify-content: center;
  padding: 1.25rem; box-sizing: border-box;
  background: rgba(15, 23, 42, 0.78);
  overflow: auto;
}
#${POLL_BOARD_GRID_ID}[hidden] { display: none !important; }
#${POLL_BOARD_GRID_ID} .poll-board-grid-wrap {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 0.75rem;
  width: min(96vw, 1200px);
  max-height: 88dvh;
  overflow: auto;
}
#${POLL_BOARD_GRID_ID} .poll-board-cell {
  margin: 0;
  background: #f7fafc;
  border-radius: 12px;
  overflow: hidden;
}
#${POLL_BOARD_GRID_ID} .poll-board-name {
  display: block;
  padding: 0.4rem 0.7rem;
  font: 600 0.95rem/1.2 sans-serif;
  color: #0f172a;
  background: #e2e8f0;
}
#${POLL_BOARD_GRID_ID} .poll-board-frame {
  aspect-ratio: 2 / 1;
}
#${POLL_BOARD_GRID_ID} canvas {
  display: block; width: 100%; height: 100%;
  pointer-events: none; background: #f7fafc;
}
`;

/** @type {string | null} Last board key the teacher asked to show. Empty means off. */
let wanted = null;

/** Last Show All payload painted, so a quiet poll does not rebuild the grid. */
let gridStamp = "";

/**
 * Remember the Show on Board postcard and hide immediately when it is off.
 *
 * @param {string} boardKey Board key, or empty when the teacher turned it off.
 */
export function noteBoardShow(boardKey) {
  wanted = String(boardKey || "");
  if (!wanted) dismissPollProjection();
}

/**
 * Hide the Show All grid. A single-board Show calls this too.
 *
 * @param {boolean} on False takes the grid down. True leaves it to the payload.
 */
export function noteBoardShowAll(on) {
  if (!on) dismissShownBoards();
}

/**
 * Label drawn above one projected board.
 *
 * Member names win when the board is a team. Otherwise the tile name
 * is the student. The caption is always rendered, including when blank.
 *
 * @param {any} board One Show All card.
 * @returns {string}
 */
export function shownBoardCaption(board) {
  const members = Array.isArray(board && board.member_names)
    ? board.member_names.map((name) => String(name || "").trim()).filter(Boolean)
    : [];
  if (members.length) return members.join(", ");
  return String((board && board.name) || "").trim();
}

/**
 * Paint or clear the overlay from a student payload.
 *
 * A missing ``class_board`` key leaves the overlay as it is. ``null``
 * clears it. A gallery board (no ``poll`` flag) is not this overlay.
 * A payload that arrives after the teacher turned the toggle off does
 * not put the board back.
 *
 * @param {Document} doc
 * @param {any} data Student state or board read.
 */
export function syncPollFromPayload(doc, data) {
  if (!data || !Object.prototype.hasOwnProperty.call(data, "class_board")) return;
  const board = data.class_board;
  if (!board || board.poll !== true) {
    if (wanted === "") return;
    if (!board || board.poll !== true) dismissPollProjection(doc);
    return;
  }
  if (wanted === "") return;
  const key = String(board.board_key || "");
  if (wanted && key && wanted !== key) return;
  wanted = key || wanted;
  paint(doc, board);
}

/**
 * Hide the overlay. The student's own board is left alone.
 *
 * @param {Document} [doc]
 */
export function dismissPollProjection(doc) {
  const root = doc || (typeof document !== "undefined" ? document : null);
  if (!root) return;
  const overlay = root.getElementById(POLL_BOARD_OVERLAY_ID);
  if (overlay) overlay.hidden = true;
}

/**
 * @param {Document} doc
 * @param {any} board
 */
function paint(doc, board) {
  dismissShownBoards(doc);
  const overlay = ensureOverlay(doc);
  const canvas = overlay.querySelector("canvas");
  const imageUrl = safeBoardImageUrl(board && board.image_url);
  const view = board && board.canvas_view;
  if (canvas instanceof HTMLCanvasElement) {
    canvas.dataset.imageUrl = imageUrl;
    paintShownBoardCanvas(canvas, view, null);
    if (imageUrl) {
      void loadBoardBackgroundImage(imageUrl).then((image) => {
        // A newer board may have been shown while the image loaded.
        if (!image || canvas.dataset.imageUrl !== imageUrl) return;
        paintShownBoardCanvas(canvas, view, image);
      });
    }
  }
  overlay.hidden = false;
}

/**
 * @param {Document} doc
 * @returns {HTMLElement}
 */
function ensureStyle(doc) {
  if (!doc || doc.getElementById(STYLE_ID) || !doc.head) return;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS;
  doc.head.appendChild(style);
}

function ensureOverlay(doc) {
  let overlay = doc.getElementById(POLL_BOARD_OVERLAY_ID);
  if (overlay) return overlay;
  ensureStyle(doc);
  overlay = doc.createElement("div");
  overlay.id = POLL_BOARD_OVERLAY_ID;
  overlay.hidden = true;
  overlay.setAttribute("role", "img");
  overlay.setAttribute("aria-label", "Shown board");
  const frame = doc.createElement("div");
  frame.className = "poll-board-frame";
  const canvas = doc.createElement("canvas");
  canvas.width = LOGICAL_W;
  canvas.height = LOGICAL_H;
  canvas.setAttribute("aria-hidden", "true");
  frame.appendChild(canvas);
  overlay.appendChild(frame);
  (doc.body || doc.documentElement).appendChild(overlay);
  return overlay;
}

/**
 * @param {any} point
 * @returns {{x: number, y: number} | null}
 */
function xy(point) {
  if (Array.isArray(point)) {
    const x = Number(point[0]);
    const y = Number(point[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }
  if (!point || typeof point !== "object") return null;
  const x = Number(point.x);
  const y = Number(point.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y };
}

/**
 * Map a stored point into the 720 by 360 board. Values already in
 * 0–1 stay proportional so the drawing is not stretched.
 *
 * @param {{x: number, y: number}} point
 * @returns {{x: number, y: number}}
 */
export function place(point) {
  const normalized = Math.abs(point.x) <= 1.5 && Math.abs(point.y) <= 1.5;
  if (!normalized) return point;
  return { x: point.x * LOGICAL_W, y: point.y * LOGICAL_H };
}

/**
 * Paint the background image (when loaded), then the marks on top.
 *
 * The backing store is the 720 by 360 board. A grid cell uses the same
 * store and lets CSS scale it, so a mark stays on the image.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {any} view
 * @param {HTMLImageElement | null} image Loaded background, or null.
 */
export function paintShownBoardCanvas(canvas, view, image) {
  const context = canvas.getContext("2d");
  if (!context) return;
  canvas.width = LOGICAL_W;
  canvas.height = LOGICAL_H;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, LOGICAL_W, LOGICAL_H);
  if (image) {
    drawFittedBoardBackground(context, LOGICAL_W, LOGICAL_H, image);
  } else {
    context.fillStyle = "#f7fafc";
    context.fillRect(0, 0, LOGICAL_W, LOGICAL_H);
  }
  const strokes = Array.isArray(view && view.strokes) ? view.strokes : [];
  context.lineWidth = 3;
  context.lineCap = "round";
  context.lineJoin = "round";
  for (const stroke of strokes) {
    const points = (Array.isArray(stroke && stroke.points) ? stroke.points : [])
      .map(xy)
      .filter(Boolean)
      .map((point) => place(/** @type {{x: number, y: number}} */ (point)));
    if (!points.length) continue;
    context.beginPath();
    context.strokeStyle = String((stroke && stroke.color) || "#0f172a");
    context.moveTo(points[0].x, points[0].y);
    for (const point of points.slice(1)) context.lineTo(point.x, point.y);
    if (points.length === 1) context.lineTo(points[0].x + 0.1, points[0].y);
    context.stroke();
  }
  const texts = Array.isArray(view && view.texts) ? view.texts : [];
  context.textBaseline = "top";
  for (const label of texts) {
    const point = place(xy(label) || { x: 0, y: 0 });
    context.font = textFontCss(label && label.size, LOGICAL_H, Boolean(label && label.bold));
    context.fillStyle = String((label && label.color) || "#0f172a");
    context.fillText(String((label && label.text) || ""), point.x, point.y);
  }
}

/**
 * Hide the Show All grid. The student's own board is left alone.
 *
 * @param {Document} [doc]
 */
export function dismissShownBoards(doc) {
  gridStamp = "";
  const root = doc || (typeof document !== "undefined" ? document : null);
  if (!root) return;
  const overlay = root.getElementById(POLL_BOARD_GRID_ID);
  if (overlay) overlay.hidden = true;
}

/**
 * Paint or clear the Show All grid from a student payload.
 *
 * A missing ``class_boards`` key leaves the grid as it is. ``null``
 * clears it. An array paints one card per board, name included.
 *
 * @param {Document} doc
 * @param {any} data Student state or board read.
 * @returns {boolean} True when the grid is on screen.
 */
export function syncShownBoards(doc, data) {
  if (!data || !Object.prototype.hasOwnProperty.call(data, "class_boards")) return false;
  const boards = data.class_boards;
  if (!Array.isArray(boards)) {
    dismissShownBoards(doc);
    return false;
  }
  const stamp = JSON.stringify(
    boards.map((board) => ({
      key: board && board.board_key,
      name: shownBoardCaption(board),
      image: safeBoardImageUrl(board && board.image_url),
      view: board && board.canvas_view,
    }))
  );
  const existing = doc.getElementById(POLL_BOARD_GRID_ID);
  if (stamp === gridStamp && existing && existing.hidden === false) return true;
  gridStamp = stamp;
  paintShownGrid(doc, boards);
  return true;
}

/**
 * @param {Document} doc
 * @param {any[]} boards
 */
function paintShownGrid(doc, boards) {
  const overlay = ensureGrid(doc);
  const wrap = overlay.querySelector(".poll-board-grid-wrap");
  if (!wrap || typeof wrap.replaceChildren !== "function") return;
  wrap.replaceChildren();
  for (const board of boards) {
    const cell = doc.createElement("figure");
    cell.className = "poll-board-cell";
    const caption = doc.createElement("figcaption");
    caption.className = "poll-board-name";
    const label = shownBoardCaption(board);
    caption.textContent = label;
    if (label) cell.setAttribute("aria-label", label);
    const frame = doc.createElement("div");
    frame.className = "poll-board-frame";
    const canvas = doc.createElement("canvas");
    canvas.width = LOGICAL_W;
    canvas.height = LOGICAL_H;
    canvas.setAttribute("aria-hidden", "true");
    const imageUrl = safeBoardImageUrl(board && board.image_url);
    canvas.dataset.imageUrl = imageUrl;
    const view = board && board.canvas_view;
    paintShownBoardCanvas(canvas, view, null);
    if (imageUrl) {
      void loadBoardBackgroundImage(imageUrl).then((image) => {
        if (!image || canvas.dataset.imageUrl !== imageUrl) return;
        paintShownBoardCanvas(canvas, view, image);
      });
    }
    frame.appendChild(canvas);
    cell.appendChild(caption);
    cell.appendChild(frame);
    wrap.appendChild(cell);
  }
  dismissPollProjection(doc);
  overlay.hidden = false;
}

/**
 * @param {Document} doc
 * @returns {HTMLElement}
 */
function ensureGrid(doc) {
  let overlay = doc.getElementById(POLL_BOARD_GRID_ID);
  if (overlay) return overlay;
  ensureStyle(doc);
  overlay = doc.createElement("div");
  overlay.id = POLL_BOARD_GRID_ID;
  overlay.hidden = true;
  overlay.setAttribute("role", "img");
  overlay.setAttribute("aria-label", "All boards");
  const wrap = doc.createElement("div");
  wrap.className = "poll-board-grid-wrap";
  overlay.appendChild(wrap);
  (doc.body || doc.documentElement).appendChild(overlay);
  return overlay;
}
