/**
 * Teacher gallery for the one live whiteboard.
 *
 * Cards stay hidden until a whiteboard question is published. Labels come
 * from the server. Strokes stay in board ops.
 */

import {
  bindWhiteboard,
  createPresenceQueue,
  normalizeBoardPoint,
} from "./live_whiteboard.js";
import {
  applyCanvasBoardBackground,
  drawFittedBoardBackground,
  loadBoardBackgroundImage,
} from "./board_background.js";
import { featureOn } from "./teacher_features.js";

/** Teacher tile poll. About every 2 seconds, one since-seq request. */
const TILE_POLL_MS = 2000;
/** Open side panel refreshes faster than the grid. */
const PANEL_POLL_MS = 500;
/** Submit-only overviews wait at least this long between polls. */
const SUBMIT_REFRESH_MS = 30000;
/** Set by the Refresh button so the next gallery read is a tap. */
let overviewTap = false;

/**
 * Poll gap for this page. Submit-only whiteboards use the server minimum.
 * @returns {number}
 */
function overviewPollMs(root) {
  const node = root || document.getElementById("formative-live-board");
  const asked = Number(node && node.dataset ? node.dataset.refreshMs : TILE_POLL_MS);
  if (Number.isFinite(asked) && asked >= SUBMIT_REFRESH_MS) return asked;
  return TILE_POLL_MS;
}
const STUDENT_INK = "#2563eb";
/** Teacher pen. Green so it stays distinct from student blue. */
const TEACHER_INK = "#16a34a";
/** Feedback column width, in CSS pixels. */
export const FEEDBACK_PANEL_PX = 320;
/** Gap between the opened board and the feedback column. */
export const FEEDBACK_GAP_PX = 16;
/** Board width where the column would start covering the drawing. */
export const BOARD_MIN_PX = 640;

/**
 * @param {string} url
 * @param {RequestInit} [init]
 * @returns {Promise<any | null>}
 */
async function readJson(url, init) {
  const res = await fetch(url, init);
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch (_err) {
    return null;
  }
}

/**
 * @returns {string}
 */
function batchId() {
  return `b-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 8)}`;
}

/**
 * Post ops, and on a stale-run 409 fetch the current run key and post once more.
 *
 * The second post uses the key from the refetch. The key that was just
 * refused is not sent again. Any other status, including a second 409,
 * is returned as-is.
 *
 * @param {{
 *   runKey: string,
 *   post: (runKey: string) => Promise<{status: number, data: any}>,
 *   refetch: () => Promise<string>,
 * }} args
 * @returns {Promise<{status: number, data: any, sent: string[]}>}
 */
export async function postOpsWithFreshRun({ runKey, post, refetch }) {
  const first = String(runKey || "");
  const sent = [first];
  let result = await post(first);
  const stale =
    Number(result?.status) === 409 &&
    Boolean(result?.data && result.data.stale_run);
  if (!stale) return { status: result.status, data: result.data, sent };
  const fresh = String((await refetch()) || "").trim();
  if (!fresh || fresh === first) return { status: result.status, data: result.data, sent };
  sent.push(fresh);
  result = await post(fresh);
  return { status: result.status, data: result.data, sent };
}

/**
 * Label from the server. A blank name stays blank.
 *
 * @param {{name?: string}} tile
 * @returns {string}
 */
function tileName(tile) {
  return String(tile?.name || "").trim();
}

/**
 * Empty ink bag for one board key.
 *
 * @returns {{strokes: Record<string, any>, texts: Record<string, any>, equations: Record<string, any>, pendingSnapshot: boolean}}
 */
function emptyInk() {
  return { strokes: {}, texts: {}, equations: {}, pendingSnapshot: false };
}

/**
 * Fold a since-seq delta into a tile's ink.
 *
 * @param {{strokes: Record<string, any>, texts: Record<string, any>}} state
 * @param {any} delta
 * @param {string} fallbackColor
 */
/**
 * True when a gallery poll moved onto another board generation.
 *
 * An empty previous key is the first paint, not a fresh reopen.
 *
 * @param {string} previous Run key already painted.
 * @param {string} next Run key on this payload.
 * @returns {boolean}
 */
export function galleryRunChanged(previous, next) {
  const prior = String(previous || "").trim();
  const current = String(next || "").trim();
  return Boolean(prior && current && prior !== current);
}

/**
 * Label and pressed state for Show on board.
 *
 * The button shows this board, and the same press stops it. Another
 * open board stays a show action until it is the one projected.
 *
 * @param {string} openKey Board open in the side panel.
 * @param {string} projectedKey Board the class is being shown.
 * @returns {{showing: boolean, label: string, pressed: boolean}}
 */
export function showControlState(openKey, projectedKey) {
  const open = String(openKey || "");
  const projected = String(projectedKey || "");
  const showing = Boolean(open) && open === projected;
  return {
    showing,
    label: showing ? "Stop showing" : "Show on board",
    pressed: showing,
  };
}

/** Logical board the panel canvas is created at. Tiles and the open board share it. */
export const BOARD_LOGICAL = { width: 720, height: 360 };

/** Narrowest tile before the grid drops a column, in CSS pixels. */
export const GALLERY_MIN_TILE = 200;

/**
 * How many columns the teacher grid uses so the row fills `containerWidth`.
 *
 * `maxColumns` is a cap, not a fixed track count. A narrow pane drops
 * columns once a tile would be thinner than `minTile`. Fewer boards than
 * the cap still span the row, so unused tracks do not sit empty at the side.
 *
 * @param {number} containerWidth
 * @param {number} tileCount
 * @param {number} [maxColumns]
 * @param {number} [minTile]
 * @param {number} [gap]
 * @returns {number}
 */
export function galleryColumnCount(
  containerWidth,
  tileCount,
  maxColumns = 4,
  minTile = GALLERY_MIN_TILE,
  gap = 10
) {
  const width = Math.max(0, Number(containerWidth) || 0);
  const tiles = Math.max(0, Math.floor(Number(tileCount) || 0));
  let cap = Math.max(1, Math.floor(Number(maxColumns) || 4));
  const floor = Math.max(1, Number(minTile) || GALLERY_MIN_TILE);
  const gutter = Math.max(0, Number(gap) || 0);
  if (width <= 0) return 1;
  while (cap > 1) {
    const tile = (width - (cap - 1) * gutter) / cap;
    if (tile >= floor) break;
    cap -= 1;
  }
  if (!tiles) return cap;
  return Math.max(1, Math.min(cap, tiles));
}

/**
 * Display box for an opened board: the full available width, logical aspect.
 *
 * One scale is used on both axes so strokes and text are not stretched.
 *
 * @param {number} availableWidth
 * @param {{width?: number, height?: number}} [logical]
 * @returns {{width: number, height: number, scale: number}}
 */
export function expandedBoardBox(availableWidth, logical) {
  const lw = Number(logical?.width) > 0 ? Number(logical.width) : BOARD_LOGICAL.width;
  const lh = Number(logical?.height) > 0 ? Number(logical.height) : BOARD_LOGICAL.height;
  const width = Math.max(0, Math.floor(Number(availableWidth) || 0));
  const height = lw > 0 ? (width * lh) / lw : 0;
  const scale = lw > 0 ? width / lw : 1;
  return { width, height, scale };
}

/**
 * Width for an opened board, and whether feedback sits beside it.
 *
 * The column is 320px with a 16px gap. Side by side only while the
 * board would still be at least 640px. Below that the board keeps the
 * full container width and the column overlays it.
 *
 * @param {number} containerWidth
 * @returns {{width: number, beside: boolean}}
 */
export function openedBoardSlot(containerWidth) {
  const width = Math.max(0, Math.floor(Number(containerWidth) || 0));
  const besideWidth = width - (FEEDBACK_PANEL_PX + FEEDBACK_GAP_PX);
  const beside = besideWidth >= BOARD_MIN_PX;
  return { width: beside ? besideWidth : width, beside };
}

/**
 * Height the opened board may use.
 *
 * A flex pane shorter than the viewport is the limit. An auto-height
 * card reports its content, which already includes the board, so that
 * measurement loses to the viewport remainder.
 *
 * @param {number} paneHeight
 * @param {number} viewportRemainder
 * @returns {number}
 */
export function openedBoardHeight(paneHeight, viewportRemainder) {
  const pane = Number(paneHeight) || 0;
  const view = Math.max(0, Number(viewportRemainder) || 0);
  if (pane > 80 && pane < view - 1) return pane;
  return view;
}

/**
 * Largest board that fits a width and a height without cropping.
 *
 * The width is the narrower column from `openedBoardSlot` when the
 * panel sits beside the board. `expandedBoardBox` keeps the board's
 * own aspect. When that box is taller than `availableHeight`, the same
 * function is called again with the width that matches the height.
 *
 * @param {number} availableWidth
 * @param {number} availableHeight
 * @param {{width?: number, height?: number}} [logical]
 * @returns {{width: number, height: number, scale: number}}
 */
export function fitOpenedBoard(availableWidth, availableHeight, logical) {
  const lw = Number(logical?.width) > 0 ? Number(logical.width) : BOARD_LOGICAL.width;
  const lh = Number(logical?.height) > 0 ? Number(logical.height) : BOARD_LOGICAL.height;
  const logicalBox = { width: lw, height: lh };
  const byWidth = expandedBoardBox(availableWidth, logicalBox);
  const limit = Number(availableHeight);
  if (!(limit > 0) || byWidth.height <= limit) return byWidth;
  const narrower = lh > 0 ? Math.floor((limit * lw) / lh) : 0;
  return expandedBoardBox(Math.max(0, narrower), logicalBox);
}

/**
 * Header status for one opened board.
 *
 * Question boards already say Drawing, Submitted, or Teacher wrote.
 * A live whiteboard without that label says Drawing while the card is up.
 *
 * @param {any} tile
 * @returns {string}
 */
export function panelStatusLabel(tile) {
  const named = String(tile?.status || "").trim();
  if (
    named === "Drawing" ||
    named === "Submitted" ||
    named === "Teacher wrote" ||
    named === "Not started"
  ) {
    return named;
  }
  if (tile?.teacher_wrote) return "Teacher wrote";
  if (tile?.drawing) return "Drawing";
  return "";
}

/**
 * Name line for the feedback header.
 *
 * A team shows its name plus members. A solo board is the student name.
 *
 * @param {any} tile
 * @returns {{title: string, members: string, status: string}}
 */
export function panelHeading(tile) {
  const title = String(tile?.name || "").trim() || "Board";
  const names = Array.isArray(tile?.member_names)
    ? tile.member_names.map((name) => String(name || "").trim()).filter(Boolean)
    : [];
  const team = tile?.team_sort_order != null;
  const members = team ? names.filter((name) => name !== title).join(", ") : "";
  return { title, members, status: panelStatusLabel(tile) };
}

/**
 * Typed work shows only for a Typing and whiteboard question that has text.
 *
 * @param {string} answerWith
 * @param {string} text
 * @returns {boolean}
 */
export function typedAnswerVisible(answerWith, text) {
  return String(answerWith || "") === "both" && String(text || "").trim().length > 0;
}

/**
 * Bitmap size for a gallery tile. Same aspect as the live board.
 *
 * @param {number} [width]
 * @returns {{width: number, height: number}}
 */
export function tileBitmapSize(width = 320) {
  const w = Math.max(2, Math.round(Number(width) || 320));
  const h = Math.max(1, Math.round((w * BOARD_LOGICAL.height) / BOARD_LOGICAL.width));
  return { width: w, height: h };
}

/**
 * Fold one gallery delta into a tile's ink bag.
 *
 * A snapshot replaces the bag, including an empty fresh board.
 *
 * @param {{strokes?: Record<string, any>, texts?: Record<string, any>}|null|undefined} state
 * @param {any} delta
 * @param {string} [fallbackColor]
 * @returns {{strokes: Record<string, any>, texts: Record<string, any>}}
 */
export function foldGalleryInk(state, delta, fallbackColor) {
  const bag = state && typeof state === "object" ? state : emptyInk();
  if (!bag.strokes || typeof bag.strokes !== "object") bag.strokes = {};
  if (!bag.texts || typeof bag.texts !== "object") bag.texts = {};
  if (!bag.equations || typeof bag.equations !== "object") bag.equations = {};
  foldDelta(bag, delta, fallbackColor || STUDENT_INK);
  return bag;
}

/**
 * Replace one bag with a folded canvas view.
 *
 * A missing equations list clears equations, the same way an empty
 * stroke list clears ink.
 *
 * @param {{strokes: Record<string, any>, texts: Record<string, any>, equations: Record<string, any>, pendingSnapshot?: boolean}} state
 * @param {any} view
 */
function loadCanvasView(state, view) {
  const picture = view && typeof view === "object" ? view : {};
  state.strokes = {};
  state.texts = {};
  state.equations = {};
  state.pendingSnapshot = false;
  for (const stroke of picture.strokes || []) {
    if (!stroke?.id) continue;
    state.strokes[String(stroke.id)] = stroke;
  }
  for (const label of picture.texts || []) {
    if (!label?.id) continue;
    state.texts[String(label.id)] = label;
  }
  for (const eq of picture.equations || []) {
    if (!eq?.id || !String(eq.latex || "").trim()) continue;
    state.equations[String(eq.id)] = eq;
  }
}

/**
 * Drop one writer's strokes, labels, and equations.
 *
 * @param {{strokes: Record<string, any>, texts: Record<string, any>, equations: Record<string, any>}} state
 * @param {unknown} owner
 */
function dropOwnerInk(state, owner) {
  const who = String(owner || "").trim();
  if (!who) return;
  for (const [id, stroke] of Object.entries(state.strokes || {})) {
    if (String(stroke?.owner || "") === who) delete state.strokes[id];
  }
  for (const [id, label] of Object.entries(state.texts || {})) {
    if (String(label?.owner || "") === who) delete state.texts[id];
  }
  for (const [id, eq] of Object.entries(state.equations || {})) {
    if (String(eq?.owner || "") === who) delete state.equations[id];
  }
}

/**
 * Insert, replace, or delete one equation. Empty latex deletes it.
 *
 * @param {{equations: Record<string, any>}} state
 * @param {any} op
 * @param {string} fallbackColor
 */
function applyEquation(state, op, fallbackColor) {
  const id = String(op?.id || "");
  if (!id) return;
  const latex = String(op.latex || "").trim();
  if (!latex) {
    delete state.equations[id];
    return;
  }
  state.equations[id] = {
    id,
    owner: op.owner,
    x: Number(op.x),
    y: Number(op.y),
    latex,
    color: String(op.color || fallbackColor),
  };
}

/**
 * Fold one gallery delta into a tile bag.
 *
 * A snapshot replaces strokes, texts, and equations. ``board_wipe``
 * drops that owner. ``equation_upsert`` with empty latex deletes the
 * equation. ``board_unwipe`` has no archive on the public op: a
 * ``canvas_view`` on the same delta is applied, otherwise
 * ``pendingSnapshot`` asks the next poll for a full fold.
 *
 * @param {{strokes: Record<string, any>, texts: Record<string, any>, equations: Record<string, any>, pendingSnapshot?: boolean}} state
 * @param {any} delta
 * @param {string} fallbackColor
 */
function foldDelta(state, delta, fallbackColor) {
  if (!state.equations || typeof state.equations !== "object") state.equations = {};
  state.pendingSnapshot = false;
  if (!delta || typeof delta !== "object") return;
  if (delta.snapshot) {
    loadCanvasView(state, delta.canvas_view);
    return;
  }
  let unwipe = false;
  for (const op of delta.ops || []) {
    if (!op || typeof op !== "object") continue;
    const id = String(op.id || "");
    const type = String(op.type || "");
    if (!id) continue;
    if (type === "board_wipe") {
      dropOwnerInk(state, op.owner);
      continue;
    }
    if (type === "board_unwipe") {
      unwipe = true;
      continue;
    }
    if (type === "equation_upsert") {
      applyEquation(state, op, fallbackColor);
      continue;
    }
    if (type === "stroke_remove") {
      delete state.strokes[id];
      continue;
    }
    if (type === "text_upsert") {
      if (!String(op.text || "").trim()) delete state.texts[id];
      else state.texts[id] = op;
      continue;
    }
    if (type !== "stroke_add" && type !== "pts_append") continue;
    const points = Array.isArray(op.points) ? op.points : [];
    const existing = state.strokes[id];
    if (!existing || type === "stroke_add") {
      state.strokes[id] = {
        id,
        owner: op.owner,
        color: op.color || fallbackColor,
        points: points.slice(),
      };
      continue;
    }
    existing.points = (existing.points || []).concat(points);
  }
  if (!unwipe) return;
  if (delta.canvas_view) {
    loadCanvasView(state, delta.canvas_view);
    return;
  }
  state.pendingSnapshot = true;
}

/**
 * Stable stamp for one board's ink.
 *
 * A gallery poll often repeats the same strokes under a new sequence
 * number. Sequence is not part of the stamp, so that poll does not
 * clear the thumbnail.
 *
 * @param {Record<string, any>|null|undefined} strokes
 * @param {Record<string, any>|null|undefined} texts
 * @param {Record<string, any>|null|undefined} [equations]
 * @returns {string}
 */
export function inkContentStamp(strokes, texts, equations) {
  let hash = 2166136261;
  /**
   * @param {unknown} value
   */
  const mix = (value) => {
    const text = String(value ?? "");
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    hash ^= 124;
    hash = Math.imul(hash, 16777619);
  };
  const strokeRows = Object.values(strokes || {});
  strokeRows.sort((left, right) => String(left?.id || "").localeCompare(String(right?.id || "")));
  for (const stroke of strokeRows) {
    mix(stroke?.id);
    mix(stroke?.color);
    const points = Array.isArray(stroke?.points) ? stroke.points : [];
    mix(points.length);
    for (const point of points) {
      if (Array.isArray(point)) {
        mix(point[0]);
        mix(point[1]);
      } else {
        mix(point?.x);
        mix(point?.y);
      }
    }
  }
  const labels = Object.values(texts || {});
  labels.sort((left, right) => String(left?.id || "").localeCompare(String(right?.id || "")));
  for (const label of labels) {
    mix(label?.id);
    mix(label?.text);
    mix(label?.x);
    mix(label?.y);
    mix(label?.color);
    mix(label?.size);
    mix(label?.bold ? 1 : 0);
    mix(label?.underline ? 1 : 0);
    mix(label?.script);
  }
  const eqs = Object.values(equations || {});
  eqs.sort((left, right) => String(left?.id || "").localeCompare(String(right?.id || "")));
  for (const eq of eqs) {
    mix(eq?.id);
    mix(eq?.latex);
    mix(eq?.x);
    mix(eq?.y);
    mix(eq?.color);
  }
  return (hash >>> 0).toString(16);
}

/**
 * Keep one button and one canvas per board.
 *
 * A canvas is cleared only when its ink stamp changed. An unchanged
 * thumbnail stays the same node and is not redrawn.
 *
 * @param {HTMLElement} grid
 * @param {Array<{
 *   key: string,
 *   name?: string,
 *   away?: boolean,
 *   status?: string,
 *   text?: string,
 *   open?: boolean,
 *   stamp: string,
 *   strokes?: Record<string, any>,
 *   texts?: Record<string, any>,
 *   equations?: Record<string, any>,
 * }>} tiles
 * @param {{
 *   paint: (canvas: HTMLCanvasElement, strokes: Record<string, any>, texts: Record<string, any>, equations: Record<string, any>) => void,
 *   onOpen?: (key: string) => void,
 * }} hooks
 * @returns {{painted: string[], kept: string[]}}
 */
export function syncWhiteboardGrid(grid, tiles, hooks) {
  const visible = Array.isArray(tiles) ? tiles : [];
  const keep = new Set();
  /** @type {string[]} */
  const painted = [];
  /** @type {string[]} */
  const kept = [];
  let previous = null;
  for (const tile of visible) {
    const key = String(tile?.key || "");
    keep.add(key);
    let button = null;
    for (const candidate of grid.querySelectorAll(".wb-live-tile")) {
      if (candidate instanceof HTMLButtonElement && candidate.dataset.boardKey === key) {
        button = candidate;
        break;
      }
    }
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "wb-live-tile";
      button.dataset.boardKey = key;
      const stage = document.createElement("canvas");
      const bitmap = tileBitmapSize(320);
      stage.width = bitmap.width;
      stage.height = bitmap.height;
      stage.className = "wb-live-tile-canvas";
      const meta = document.createElement("span");
      meta.className = "wb-live-tile-meta";
      const dot = document.createElement("span");
      dot.className = "wb-live-dot";
      const name = document.createElement("span");
      name.className = "wb-live-name";
      const team = document.createElement("span");
      team.className = "wb-live-team";
      meta.append(dot, name, team);
      button.append(stage, meta);
      button.addEventListener("click", () => {
        if (typeof hooks.onOpen === "function") hooks.onOpen(key);
      });
    }
    button.classList.toggle("is-open", Boolean(tile.open));
    const dot = button.querySelector(".wb-live-dot");
    if (dot) dot.classList.toggle("is-away", Boolean(tile.away));
    const name = button.querySelector(".wb-live-name");
    const label = String(tile.name || "");
    if (name && name.textContent !== label) name.textContent = label;
    const meta = button.querySelector(".wb-live-tile-meta");
    if (meta instanceof HTMLElement) {
      const wanted = [];
      if (tile.text) wanted.push(String(tile.text));
      if (tile.status) wanted.push(String(tile.status));
      const nodes = [...meta.querySelectorAll(".wb-live-status")];
      const same =
        nodes.length === wanted.length &&
        wanted.every((text, index) => nodes[index].textContent === text);
      if (!same) {
        for (const node of nodes) node.remove();
        for (const text of wanted) {
          const span = document.createElement("span");
          span.className = "wb-live-status";
          span.textContent = text;
          meta.appendChild(span);
        }
      }
    }
    const stage = button.querySelector("canvas");
    if (stage instanceof HTMLCanvasElement) {
      const stamp = String(tile.stamp || "");
      if (stage.dataset.inkStamp === stamp) kept.push(key);
      else {
        hooks.paint(stage, tile.strokes || {}, tile.texts || {}, tile.equations || {});
        stage.dataset.inkStamp = stamp;
        painted.push(key);
      }
    }
    const at = previous ? previous.nextSibling : grid.firstChild;
    if (button !== at) grid.insertBefore(button, at);
    previous = button;
  }
  for (const button of [...grid.querySelectorAll(".wb-live-tile")]) {
    if (!keep.has(button.dataset.boardKey || "")) button.remove();
  }
  return { painted, kept };
}

/**
 * Paint 0–1 strokes onto a small tile canvas.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Record<string, any>} strokes
 * @param {Record<string, any>} texts
 * @param {string} fallbackColor
 */
/**
 * Paint one thumbnail, optionally with the question image under the ink.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Record<string, any>} strokes
 * @param {Record<string, any>} texts
 * @param {string} fallbackColor
 * @param {HTMLImageElement | null} [background]
 * @param {Record<string, any>} [equations]
 */
function paintTile(canvas, strokes, texts, fallbackColor, background = null, equations = {}) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  drawFittedBoardBackground(ctx, w, h, background);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = 2;
  for (const stroke of Object.values(strokes)) {
    const points = Array.isArray(stroke.points) ? stroke.points : [];
    if (points.length < 1) continue;
    ctx.beginPath();
    ctx.strokeStyle = String(stroke.color || fallbackColor);
    points.forEach((pt, index) => {
      const x = Number(pt[0]) * w;
      const y = Number(pt[1]) * h;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  for (const label of Object.values(texts)) {
    const body = String(label.text || "").trim();
    if (!body) continue;
    const px = Math.max(10, Math.round(8 + (Number(label.size) || 2) * 2));
    ctx.font = `${label.bold ? "700" : "400"} ${px}px sans-serif`;
    ctx.fillStyle = String(label.color || fallbackColor);
    let y = Number(label.y) * h + 12;
    if (label.script === "super") y -= px * 0.45;
    if (label.script === "sub") y += px * 0.2;
    ctx.fillText(body, Number(label.x) * w, y);
    if (label.underline) {
      const width = ctx.measureText ? ctx.measureText(body).width : body.length * 6;
      ctx.fillRect(Number(label.x) * w, y + 2, width, 1);
    }
  }
  ctx.font = "12px sans-serif";
  for (const eq of Object.values(equations || {})) {
    const latex = String(eq?.latex || "").trim();
    if (!latex) continue;
    ctx.fillStyle = String(eq.color || fallbackColor);
    ctx.fillText(latex, Number(eq.x) * w, Number(eq.y) * h + 12);
  }
}

/**
 * Turn pointer samples into stroke ops. The first batch of an id is stroke_add.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Set<string>} openIds
 * @param {{x: number, y: number}[]} points
 * @param {boolean} ended
 * @param {string} strokeId
 * @param {string} color
 * @returns {Record<string, any>[]}
 */
function strokeOps(canvas, openIds, points, ended, strokeId, color) {
  const id = String(strokeId || "");
  if (!id) return [];
  const samples = [];
  for (const point of points || []) {
    const norm = normalizeBoardPoint(canvas, point);
    if (norm) samples.push([norm.x, norm.y]);
  }
  /** @type {Record<string, any>[]} */
  const ops = [];
  if (!openIds.has(id)) {
    if (!samples.length) return [];
    ops.push({ type: "stroke_add", id, points: samples, color });
    openIds.add(id);
  } else if (samples.length) {
    ops.push({ type: "pts_append", id, points: samples, color });
  }
  if (ended) {
    ops.push({ type: "stroke_end", id, color });
    openIds.delete(id);
  }
  return ops;
}

/**
 * @param {HTMLElement} root
 */
export function mountTeacherGrid(root, options = {}) {
  /**
   * A control in this gallery, by the live-board part name.
   *
   * The deck gallery uses ids. A question gallery uses data-wb-part so
   * two boards on one page do not share an id.
   * @param {string} name
   * @returns {Element | null}
   */
  function part(name) {
    return (
      root.querySelector(`[data-wb-part="${name}"]`) ||
      root.querySelector(`#wb-live-${name}`)
    );
  }
  if (root.dataset.wbMounted === "1") {
    return { stop() {} };
  }
  root.dataset.wbMounted = "1";
  const grid = part("grid");
  const panel = part("panel");
  const summary = part("summary");
  const context = part("context");
  const canvas = part("panel-canvas");
  if (!(grid instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement)) return;

  /** @type {Record<string, {strokes: Record<string, any>, texts: Record<string, any>}>} */
  const ink = {};
  /** @type {Record<string, number>} */
  const cursors = {};
  /** @type {any[]} */
  let tiles = [];
  let answerWith = "";
  /** Question image locked under ink for this poll, when set. */
  let boardBackgroundUrl = "";
  let boardBackgroundImage = null;
  let openKey = "";
  let session = 0;
  let tileTimer = 0;
  let panelTimer = 0;
  let panelSince = 0;
  let panelRun = "";
  let runKey = "";
  let projectedKey = "";
  const openStrokes = new Set();
  const cols = part("cols");

  const readOnly = Boolean(options.readOnly);
  if (readOnly) {
    for (const name of ["pen", "text", "erase", "undo", "redo", "clear", "show"]) {
      const node = part(name);
      if (node instanceof HTMLElement) {
        node.hidden = true;
        node.style.display = "none";
      }
    }
  }

  const board = bindWhiteboard(canvas, {
    penBtn: part("pen"),
    textBtn: part("text"),
    eraseBtn: part("erase"),
    undoBtn: part("undo"),
    redoBtn: part("redo"),
    formative: {
      enabled: true,
      members: [],
      isTeacher: true,
      isPhone: false,
      alwaysShowNames: true,
      projectorShowNames: false,
    },
    owner: () => "teacher",
    color: TEACHER_INK,
    onPoints: (points, ended, strokeId) => {
      if (!openKey) return;
      const ops = strokeOps(canvas, openStrokes, points, ended, strokeId, TEACHER_INK);
      if (ops.length) postFeedback(ops);
    },
    onText: (label) => {
      if (!openKey) return;
      const norm = normalizeBoardPoint(canvas, label);
      if (!norm) return;
      postFeedback([
        {
          type: "text_upsert",
          id: label.id,
          text: label.text,
          x: norm.x,
          y: norm.y,
          color: TEACHER_INK,
        },
      ]);
    },
    onUndo: (stroke) => {
      if (!openKey || !stroke?.id) return;
      postFeedback([{ type: "stroke_remove", id: stroke.id }]);
    },
    onClear: (gone) => {
      const ops = [];
      for (const stroke of gone || []) {
        if (!stroke?.id || !stroke.mine) continue;
        ops.push({ type: "stroke_remove", id: stroke.id });
      }
      if (ops.length) postFeedback(ops);
    },
    onRedo: (stroke) => {
      if (!openKey || !stroke?.id) return;
      const samples = [];
      for (const point of stroke.points || []) {
        const norm = normalizeBoardPoint(canvas, point);
        if (norm) samples.push([norm.x, norm.y]);
      }
      if (!samples.length) return;
      postFeedback([{ type: "stroke_add", id: stroke.id, points: samples, color: TEACHER_INK }]);
    },
  });
  board.setCollab(true);

  /**
   * @returns {string}
   */
  function sessionId() {
    if (typeof options.sessionId === "function") return String(options.sessionId() || "").trim();
    if (options.sessionId) return String(options.sessionId).trim();
    const ap = document.getElementById("ap-root");
    return String(ap?.dataset.liveSessionId || "").trim();
  }

  /**
   * @param {string} id
   * @param {string} key
   * @param {string} [suffix]
   * @returns {string}
   */
  function boardUrl(id, key, suffix) {
    if (typeof options.boardUrl === "function") return options.boardUrl(id, key, suffix);
    return `/api/live-sessions/${id}/whiteboard-gallery/${encodeURIComponent(key)}${suffix || ""}`;
  }

  /**
   * @param {boolean} on
   */
  function showGrid(on) {
    root.hidden = !on;
    root.classList.toggle("is-on", on);
  }

  let layoutObserver = null;
  /** Column count last written onto the grid. Avoids a style write that retriggers resize. */
  let galleryColumns = -1;
  /** Cached tile gap. Reading it inside a resize callback forces layout and can loop. */
  let galleryGap = null;
  /** True while a layout pass is running, so a nested callback cannot re-enter. */
  let layingOut = false;
  /** Last legend signature painted on the open board. */
  let legendSig = "";
  /** Last stage width written for the open board. Skips a resize loop. */
  let openBoxWidth = -1;
  /** Last feedback max-height written. Same guard as the stage width. */
  let openBoxCap = "";

  /**
   * Size the tile row so it fills the gallery and drops columns when narrow.
   *
   * While a board is open this returns without writing. The open canvas
   * keeps a CSS aspect ratio; writing its pixel size from a resize
   * observer feeds the next resize.
   */
  function layoutGallery() {
    if (!(grid instanceof HTMLElement)) return;
    if (panel instanceof HTMLElement && !panel.hidden) return;
    const width = grid.clientWidth;
    if (width < 2) return;
    if (galleryGap == null) {
      const gap = Number.parseFloat(getComputedStyle(grid).columnGap);
      galleryGap = Number.isFinite(gap) ? gap : 10;
    }
    const cap = cols instanceof HTMLSelectElement ? Number(cols.value) : 4;
    const columns = galleryColumnCount(
      width,
      grid.childElementCount,
      cap,
      GALLERY_MIN_TILE,
      galleryGap
    );
    if (columns === galleryColumns) return;
    galleryColumns = columns;
    grid.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  }

  /**
   * Fit the opened board into the pane, leaving 336px when the column fits.
   *
   * The stage width is the only inline size. The canvas stays `width: 100%`
   * at its own aspect, which is what keeps polls from resizing it.
   */
  function layoutOpenBoard() {
    if (!(panel instanceof HTMLElement) || panel.hidden) return;
    const body = panel.parentElement;
    if (!(body instanceof HTMLElement)) return;
    const containerWidth = body.clientWidth;
    if (containerWidth < 2) return;
    const top = Math.max(0, body.getBoundingClientRect().top);
    const limit = openedBoardHeight(body.clientHeight, window.innerHeight - top);
    const slot = openedBoardSlot(containerWidth);
    const box = fitOpenedBoard(slot.width, limit);
    const width = Math.max(0, Math.round(box.width));
    root.classList.toggle("is-overlay", !slot.beside);
    const stage = canvas.parentElement;
    if (stage instanceof HTMLElement && openBoxWidth !== width) {
      openBoxWidth = width;
      stage.style.width = `${width}px`;
    }
    const feedback = part("feedback");
    if (feedback instanceof HTMLElement) {
      const cap = `${Math.max(0, Math.round(box.height))}px`;
      if (openBoxCap !== cap) {
        openBoxCap = cap;
        feedback.style.maxHeight = cap;
      }
    }
  }

  /**
   * Reflow the tile row, and the opened board when one is up.
   *
   * The canvas element is not given an inline width. A resize observer
   * that wrote that width is what made the opened board vanish.
   */
  function layoutBoards() {
    if (layingOut) return;
    layingOut = true;
    try {
      layoutOpenBoard();
      layoutGallery();
    } finally {
      layingOut = false;
    }
  }

  if (cols instanceof HTMLSelectElement) {
    cols.addEventListener("change", () => {
      galleryColumns = -1;
      layoutBoards();
    });
  }

  /**
   * One teacher ink post at a time, same as the student presence queue.
   *
   * Local ink is already on the live layer. This only ships the ops.
   * Batches stay in order so a stroke_add is stored before its tail.
   */
  const inkQueue = createPresenceQueue({
    /**
     * @param {Record<string, any>} body
     * @returns {Promise<any>}
     */
    send(body) {
      const id = sessionId();
      const key = String(body.board_key || "");
      const ops = Array.isArray(body.ops) ? body.ops : [];
      if (!id || !key || !ops.length) return Promise.resolve(null);
      const claimed = runKey;
      return postOpsWithFreshRun({
        runKey: claimed,
        post: async (run) => {
          const res = await fetch(boardUrl(id, key, "/feedback"), {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ops,
              client_batch_id: batchId(),
              run_key: run,
            }),
          });
          let data = null;
          try {
            data = await res.json();
          } catch (_err) {
            data = null;
          }
          return { status: res.status, data };
        },
        refetch: async () => {
          const data = await readJson(
            `/api/live-sessions/${id}/whiteboard-gallery?cursors=${encodeURIComponent(JSON.stringify(cursors))}`,
            { credentials: "same-origin" }
          );
          return String(data?.run_key || "");
        },
      }).then((result) => {
        const fromBody = String(result?.data?.run_key || "").trim();
        if (fromBody) runKey = fromBody;
        else if (result.sent.length > 1) {
          const fresh = String(result.sent[result.sent.length - 1] || "").trim();
          if (fresh) runKey = fresh;
        }
        return result.data;
      });
    },
  });

  /**
   * @param {Record<string, any>[]} ops
   */
  function postFeedback(ops) {
    if (readOnly) return;
    const key = openKey;
    if (!sessionId() || !key || !ops.length) return;
    inkQueue.push({ board_key: key, ops });
  }

  /**
   * Stamp for one tile's ink so a poll can skip repainting an unchanged canvas.
   *
   * The stamp is the ink itself. A new sequence with the same strokes
   * does not clear the thumbnail.
   *
   * @param {string} key
   * @returns {string}
   */
  function tileInkStamp(key) {
    const bag = ink[key] || emptyInk();
    return inkContentStamp(bag.strokes, bag.texts, bag.equations);
  }

  /**
   * Paint the visible tiles without replacing buttons that are already there.
   *
   * A poll used to rebuild every canvas. That flashed the row and, with the
   * open board's resize observer, fed another layout pass. Only a board
   * whose ink changed is redrawn.
   *
   * @param {any[]} rows
   */
  function paintGrid(rows) {
    const visible = Array.isArray(rows) ? rows : [];
    syncWhiteboardGrid(
      grid,
      visible.map((tile) => {
        const key = String(tile.board_key || "");
        const bag = ink[key] || emptyInk();
        return {
          key,
          name: tileName(tile),
          away: Boolean(tile.away),
          status: tile.status ? String(tile.status) : "",
          text: tile.text ? String(tile.text) : "",
          open: Boolean(key) && key === openKey,
          stamp: tileInkStamp(key),
          strokes: bag.strokes,
          texts: bag.texts,
          equations: bag.equations,
        };
      }),
      {
        /**
         * @param {HTMLCanvasElement} stage
         * @param {Record<string, any>} strokes
         * @param {Record<string, any>} texts
         * @param {Record<string, any>} equations
         */
        paint(stage, strokes, texts, equations) {
          paintTile(stage, strokes, texts, STUDENT_INK, boardBackgroundImage, equations);
        },
        /**
         * @param {string} key
         */
        onOpen(key) {
          openBoard(key);
        },
      }
    );
    layoutGallery();
  }

  /**
   * @param {any} payload
   */
  function applyTiles(payload) {
    const visible = Boolean(payload && payload.visible && !payload.ended);
    showGrid(visible);
    if (!visible) {
      tiles = [];
      return;
    }
    tiles = Array.isArray(payload.tiles) ? payload.tiles : [];
    if (typeof payload.answer_with === "string") answerWith = payload.answer_with;
    const nextBg = String(payload.image_url || "").trim();
    let backgroundCleared = false;
    if (nextBg !== boardBackgroundUrl) {
      const hadBackground = Boolean(boardBackgroundUrl);
      boardBackgroundUrl = nextBg;
      boardBackgroundImage = null;
      if (boardBackgroundUrl) {
        void loadBoardBackgroundImage(boardBackgroundUrl).then((image) => {
          boardBackgroundImage = image;
          applyCanvasBoardBackground(canvas, boardBackgroundUrl);
          for (const button of grid.querySelectorAll(".wb-live-tile")) {
            if (!(button instanceof HTMLButtonElement)) continue;
            const tileCanvas = button.querySelector("canvas");
            if (!(tileCanvas instanceof HTMLCanvasElement)) continue;
            delete tileCanvas.dataset.inkStamp;
          }
          paintGrid(tiles);
          if (openKey) {
            const openTile = tiles.find((row) => String(row.board_key || "") === openKey);
            if (openTile) paintPanelCopy(openTile);
          }
        });
      } else if (hadBackground) {
        backgroundCleared = true;
        applyCanvasBoardBackground(canvas, "");
      }
    }
    if (context) context.textContent = String(payload.context || "");
    if (summary) summary.textContent = String(payload.summary || "");
    for (const tile of tiles) {
      const key = String(tile.board_key || "");
      if (!key) continue;
      if (!ink[key]) ink[key] = emptyInk();
      foldDelta(ink[key], tile, STUDENT_INK);
      if (ink[key].pendingSnapshot) {
        // The public unwipe carries no archive. A cursor past the head
        // is the existing snapshot signal, so the next poll sends the
        // folded canvas instead of another empty undo.
        ink[key].pendingSnapshot = false;
        cursors[key] = Number.MAX_SAFE_INTEGER;
      } else if (tile.board_seq != null && Number.isFinite(Number(tile.board_seq))) {
        cursors[key] = Number(tile.board_seq);
      }
    }
    if (backgroundCleared) {
      for (const button of grid.querySelectorAll(".wb-live-tile")) {
        if (!(button instanceof HTMLButtonElement)) continue;
        const tileCanvas = button.querySelector("canvas");
        if (tileCanvas instanceof HTMLCanvasElement) delete tileCanvas.dataset.inkStamp;
      }
    }
    paintGrid(tiles);
    if (openKey) {
      const openTile = tiles.find((row) => String(row.board_key || "") === openKey);
      if (openTile) {
        paintPanelCopy(openTile);
        if (typeof options.onTileSync === "function") options.onTileSync(openTile);
      }
    }
    const showButton = part("show");
    if (!(showButton instanceof HTMLButtonElement) || showButton.dataset.pending !== "1") {
      projectedKey = String(payload.projected_board || "");
    }
    const status = part("projected");
    if (status) {
      const projected = tiles.find(
        (tile) => String(tile.board_key || "") === projectedKey
      );
      status.textContent = projected ? `On the board · ${tileName(projected)}` : "";
    }
    paintShowButton();
  }

  /**
   * Show on board, or Stop showing when this board is the one up.
   */
  function paintShowButton() {
    const button = part("show");
    if (!(button instanceof HTMLButtonElement)) return;
    const state = showControlState(openKey, projectedKey);
    button.textContent = state.label;
    button.setAttribute("aria-pressed", state.pressed ? "true" : "false");
    button.classList.toggle("is-on", state.showing);
  }

  /**
   * Header, status, and typed answer for the open board.
   *
   * Text is written only when it changed, so a poll does not rebuild the column.
   *
   * @param {any} tile
   */
  function paintPanelCopy(tile) {
    const heading = panelHeading(tile);
    const title = part("panel-name");
    const team = part("panel-team");
    const status = part("panel-status");
    if (title && title.textContent !== heading.title) title.textContent = heading.title;
    if (team instanceof HTMLElement) {
      team.textContent = heading.members;
      team.hidden = !heading.members;
    }
    if (status instanceof HTMLElement) {
      status.textContent = heading.status;
      status.hidden = !heading.status;
    }
    const answer = part("answer");
    const more = part("answer-more");
    const text = String(tile?.text || "");
    const show = typedAnswerVisible(answerWith, text);
    if (answer instanceof HTMLElement) {
      answer.hidden = !show;
      if (answer.dataset.raw !== text) {
        answer.dataset.raw = text;
        answer.textContent = text;
        answer.classList.remove("is-open");
        if (more instanceof HTMLButtonElement) more.hidden = true;
        if (show) {
          window.requestAnimationFrame(() => {
            if (answer.dataset.raw !== text) return;
            const overflow = answer.scrollHeight > answer.clientHeight + 1;
            if (more instanceof HTMLButtonElement) more.hidden = !overflow;
          });
        }
      }
    }
  }

  /**
   * Open one board and its feedback column.
   *
   * Prev/Next passes `keepCollapse` so the column stays up instead of
   * returning to the grid. A fresh open from a tile expands it.
   *
   * @param {string} boardKey
   * @param {{keepCollapse?: boolean}} [options]
   */
  function openBoard(boardKey, boardOptions = {}) {
    const keepCollapse = Boolean(boardOptions.keepCollapse);
    openKey = String(boardKey || "");
    panelSince = 0;
    panelRun = runKey;
    openStrokes.clear();
    inkQueue.drop();
    board.resetRun?.({ strokes: [], texts: [] });
    if (panel instanceof HTMLElement) panel.hidden = false;
    if (!keepCollapse) root.classList.remove("is-feedback-collapsed");
    const tile = tiles.find((row) => String(row.board_key || "") === openKey);
    paintPanelCopy(tile);
    applyCanvasBoardBackground(canvas, boardBackgroundUrl);
    applyPanelLegend([]);
    paintGrid(tiles);
    paintShowButton();
    layoutOpenBoard();
    if (typeof options.onOpen === "function") options.onOpen(tile || null);
    refreshPanel();
  }

  function closePanel() {
    openKey = "";
    openBoxWidth = -1;
    openBoxCap = "";
    inkQueue.drop();
    if (panel instanceof HTMLElement) panel.hidden = true;
    root.classList.remove("is-feedback-collapsed");
    galleryColumns = -1;
    paintGrid(tiles);
    paintShowButton();
  }

  /**
   * @param {number} step
   */
  function stepPanel(step) {
    const visible = tiles;
    if (!visible.length) return;
    const index = visible.findIndex((tile) => String(tile.board_key || "") === openKey);
    const next = visible[(index + step + visible.length) % visible.length];
    if (next) openBoard(String(next.board_key || ""), { keepCollapse: true });
  }

  async function refreshPanel() {
    const id = sessionId();
    if (!id || !openKey) return;
    const data = await readJson(
      `${boardUrl(id, openKey)}?since=${panelSince}`,
      { credentials: "same-origin" }
    );
    if (!data || String(data.board_key || "") !== openKey) return;
    applyPanelLegend(data.legend);
    const title = part("panel-name");
    if (title) title.textContent = String(data.name || "").trim() || title.textContent;
    const nextRun = String(data.run_key || "").trim();
    const seq = Number(data.board_seq);
    if (galleryRunChanged(panelRun, nextRun)) {
      panelRun = nextRun;
      openStrokes.clear();
      inkQueue.drop();
      if (Number.isFinite(seq)) panelSince = seq;
      board.resetRun?.(data.canvas_view || { strokes: [], texts: [] });
      return;
    }
    if (nextRun) panelRun = nextRun;
    if (data.snapshot && data.canvas_view) {
      board.applyDelta?.({ snapshot: true, canvas_view: data.canvas_view });
    } else if (Array.isArray(data.ops) && data.ops.length) {
      board.applyDelta?.({ ops: data.ops });
    }
    if (Number.isFinite(seq)) panelSince = seq;
  }

  async function pollTiles() {
    const id = sessionId();
    if (!id) {
      showGrid(false);
      session = 0;
      return;
    }
    if (Number(id) !== session) {
      session = Number(id);
      tiles = [];
      runKey = "";
      for (const key of Object.keys(ink)) delete ink[key];
      for (const key of Object.keys(cursors)) delete cursors[key];
    }
    const query = encodeURIComponent(JSON.stringify(cursors));
    const tap = overviewTap ? "&tap=1" : "";
    overviewTap = false;
    const galleryUrl =
      typeof options.galleryUrl === "function"
        ? options.galleryUrl(id, query, tap)
        : `/api/live-sessions/${id}/whiteboard-gallery?cursors=${query}${tap}`;
    const data = await readJson(galleryUrl, { credentials: "same-origin" });
    if (!data) return;
    const next = String(data.run_key || "").trim();
    const changed = galleryRunChanged(runKey, next);
    if (changed) {
      for (const key of Object.keys(ink)) delete ink[key];
      for (const key of Object.keys(cursors)) delete cursors[key];
      panelSince = 0;
      panelRun = "";
      openStrokes.clear();
      inkQueue.drop();
      if (openKey) board.resetRun?.({ strokes: [], texts: [] });
    }
    if (next) runKey = next;
    applyTiles(data);
    if (changed && openKey) {
      panelRun = next;
      refreshPanel();
    }
  }

  /**
   * Legend chips for the open board, including the teacher ink layer.
   *
   * Every chip has a name. The teacher row is labelled Teacher.
   *
   * @param {any[] | undefined} legend
   */
  function applyPanelLegend(legend) {
    const members = Array.isArray(legend) ? legend.slice() : [];
    members.push({ id: "teacher", name: "Teacher", color: TEACHER_INK, slot: 0 });
    const sig = members
      .map((row) => `${row?.id ?? ""}|${row?.name ?? ""}|${row?.color ?? ""}|${row?.slot ?? ""}`)
      .join("\n");
    if (sig === legendSig) return;
    legendSig = sig;
    board.setFormative?.({
      members,
      alwaysShowNames: true,
      projectorShowNames: false,
    });
  }

  part("prev")?.addEventListener("click", () => stepPanel(-1));
  part("next")?.addEventListener("click", () => stepPanel(1));
  part("close")?.addEventListener("click", closePanel);
  part("answer-more")?.addEventListener("click", () => {
    const answer = part("answer");
    const more = part("answer-more");
    if (answer instanceof HTMLElement) answer.classList.add("is-open");
    if (more instanceof HTMLElement) more.hidden = true;
  });
  part("collapse")?.addEventListener("click", () => {
    const collapsed = root.classList.toggle("is-feedback-collapsed");
    const button = part("collapse");
    if (button instanceof HTMLButtonElement) {
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      button.setAttribute("aria-label", collapsed ? "Expand feedback" : "Collapse feedback");
      button.textContent = collapsed ? "‹" : "›";
    }
  });
  part("clear")?.addEventListener("click", () => {
    const erase = part("erase");
    if (erase instanceof HTMLElement) erase.click();
  });
  part("show")?.addEventListener("click", () => {
    const button = part("show");
    const id = sessionId();
    const key = openKey;
    if (!id || !key || !(button instanceof HTMLButtonElement)) return;
    if (button.dataset.pending === "1") return;
    const previous = projectedKey;
    projectedKey = previous === key ? "" : key;
    paintShowButton();
    button.dataset.pending = "1";
    const error = part("show-error");
    if (error instanceof HTMLElement) {
      error.hidden = true;
      error.textContent = "";
    }
    const showFailure = () => {
      projectedKey = previous;
      paintShowButton();
      if (error instanceof HTMLElement) {
        error.hidden = false;
        error.textContent = "Couldn't update the board.";
      }
    };
    fetch(boardUrl(id, key, "/show"), {
      method: "POST",
      credentials: "same-origin",
    })
      .then(async (res) => {
        let data = null;
        try {
          data = await res.json();
        } catch (_err) {
          data = null;
        }
        if (!res.ok || !data) {
          showFailure();
          return;
        }
        projectedKey = data.projected === false ? "" : key;
        paintShowButton();
        return pollTiles();
      })
      .catch(() => {
        showFailure();
      })
      .finally(() => {
        button.dataset.pending = "";
      });
  });

  const ap = document.getElementById("ap-root");
  if (ap) {
    const observer = new MutationObserver(() => {
      pollTiles();
    });
    observer.observe(ap, { attributes: true, attributeFilter: ["data-live-session-id"] });
  }
  const refreshButton = part("refresh");
  if (refreshButton) {
    refreshButton.addEventListener("click", () => {
      overviewTap = true;
      pollTiles();
    });
  }
  if (typeof ResizeObserver === "function") {
    layoutObserver = new ResizeObserver(() => layoutBoards());
    layoutObserver.observe(grid);
  }
  /**
   * Arrow keys move the open board. Escape and a click outside close it.
   *
   * Pointerdown, not click: the tile click that opens a board would
   * otherwise bubble and close it in the same turn.
   *
   * @param {KeyboardEvent} event
   */
  function onPanelKey(event) {
    if (!openKey) return;
    const target = event.target;
    const tag = target instanceof HTMLElement ? target.tagName : "";
    const typing =
      tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT" ||
      (target instanceof HTMLElement && target.isContentEditable);
    if (event.key === "Escape") {
      event.preventDefault();
      closePanel();
      return;
    }
    if (typing) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      stepPanel(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      stepPanel(1);
    }
  }

  /**
   * @param {Event} event
   */
  function onOutside(event) {
    if (!openKey || !(panel instanceof HTMLElement)) return;
    const target = event.target;
    if (!(target instanceof Node) || panel.contains(target)) return;
    closePanel();
  }

  document.addEventListener("keydown", onPanelKey);
  document.addEventListener("pointerdown", onOutside);
  window.addEventListener("resize", layoutBoards);
  const pollMs = overviewPollMs(root);
  const panelMs = pollMs >= SUBMIT_REFRESH_MS ? pollMs : PANEL_POLL_MS;
  pollTiles();
  tileTimer = window.setInterval(pollTiles, pollMs);
  panelTimer = window.setInterval(() => {
    if (openKey) refreshPanel();
  }, panelMs);
  void tileTimer;
  void panelTimer;
  return {
    /**
     * Stop this gallery's polls. Used when a question card is repainted.
     */
    stop() {
      window.clearInterval(tileTimer);
      window.clearInterval(panelTimer);
      layoutObserver?.disconnect();
      layoutObserver = null;
      document.removeEventListener("keydown", onPanelKey);
      document.removeEventListener("pointerdown", onOutside);
      window.removeEventListener("resize", layoutBoards);
      root.dataset.wbMounted = "";
    },
  };
}

if (typeof document !== "undefined") {
  const teacherRoot = document.getElementById("formative-live-board");
  const boardOn = featureOn("whiteboard");
  if (teacherRoot instanceof HTMLElement && boardOn) mountTeacherGrid(teacherRoot);
}
