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
 */

import { drawFittedBoardBackground, loadBoardBackgroundImage } from "./board_background.js";

export const POLL_BOARD_OVERLAY_ID = "poll-board-overlay";

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
`;

/** @type {string | null} Last board key the teacher asked to show. Empty means off. */
let wanted = null;

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
  const overlay = ensureOverlay(doc);
  const canvas = overlay.querySelector("canvas");
  const imageUrl = String((board && board.image_url) || "").trim();
  const view = board && board.canvas_view;
  if (canvas instanceof HTMLCanvasElement) {
    canvas.dataset.imageUrl = imageUrl;
    drawBoard(canvas, view, null);
    if (imageUrl) {
      void loadBoardBackgroundImage(imageUrl).then((image) => {
        // A newer board may have been shown while the image loaded.
        if (!image || canvas.dataset.imageUrl !== imageUrl) return;
        drawBoard(canvas, view, image);
      });
    }
  }
  overlay.hidden = false;
}

/**
 * @param {Document} doc
 * @returns {HTMLElement}
 */
function ensureOverlay(doc) {
  let overlay = doc.getElementById(POLL_BOARD_OVERLAY_ID);
  if (overlay) return overlay;
  if (!doc.getElementById(STYLE_ID) && doc.head) {
    const style = doc.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    doc.head.appendChild(style);
  }
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
 * @param {HTMLCanvasElement} canvas
 * @param {any} view
 * @param {HTMLImageElement | null} image Loaded background, or null.
 */
function drawBoard(canvas, view, image) {
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
  context.font = "28px sans-serif";
  context.textBaseline = "top";
  for (const label of texts) {
    const point = place(xy(label) || { x: 0, y: 0 });
    context.fillStyle = String((label && label.color) || "#0f172a");
    context.fillText(String((label && label.text) || ""), point.x, point.y);
  }
}
