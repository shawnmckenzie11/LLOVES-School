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
const TEACHER_INK = "#dc2626";

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
 * Whether a gallery card matches the selected filter chip.
 *
 * @param {any} tile
 * @param {string} selected
 * @returns {boolean}
 */
function tileMatches(tile, selected) {
  if (selected === "all") return true;
  if (selected === "teacher_wrote") return Boolean(tile?.teacher_wrote);
  if (tile?.status && tile.status === selected) return true;
  return tile?.team_board === selected;
}

/**
 * @param {string} iso
 * @returns {string}
 */
function strokeAge(iso) {
  const stamp = Date.parse(String(iso || ""));
  if (!Number.isFinite(stamp)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
  if (seconds < 5) return "Drawing now · last stroke just now";
  if (seconds < 90) return `Drawing now · last stroke ${seconds}s ago`;
  return "Drawing now";
}

/**
 * Empty ink bag for one board key.
 *
 * @returns {{strokes: Record<string, any>, texts: Record<string, any>}}
 */
function emptyInk() {
  return { strokes: {}, texts: {} };
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
 * Label and pressed state for Show to class.
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
    label: showing ? "Stop showing" : "Show to class",
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
  foldDelta(bag, delta, fallbackColor || STUDENT_INK);
  return bag;
}

function foldDelta(state, delta, fallbackColor) {
  if (!delta || typeof delta !== "object") return;
  if (delta.snapshot) {
    state.strokes = {};
    state.texts = {};
    const view = delta.canvas_view || {};
    for (const stroke of view.strokes || []) {
      if (!stroke?.id) continue;
      state.strokes[String(stroke.id)] = stroke;
    }
    for (const label of view.texts || []) {
      if (!label?.id) continue;
      state.texts[String(label.id)] = label;
    }
    return;
  }
  for (const op of delta.ops || []) {
    if (!op || typeof op !== "object") continue;
    const id = String(op.id || "");
    const type = String(op.type || "");
    if (!id) continue;
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
}

/**
 * Paint 0–1 strokes onto a small tile canvas.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Record<string, any>} strokes
 * @param {Record<string, any>} texts
 * @param {string} fallbackColor
 */
function paintTile(canvas, strokes, texts, fallbackColor) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
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
  ctx.font = "12px sans-serif";
  for (const label of Object.values(texts)) {
    const body = String(label.text || "").trim();
    if (!body) continue;
    ctx.fillStyle = String(label.color || fallbackColor);
    ctx.fillText(body, Number(label.x) * w, Number(label.y) * h + 12);
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
  const filters = part("filters");
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
  let filter = "all";
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

  /**
   * Size the tile row so it fills the gallery and drops columns when narrow.
   *
   * The open board is sized by CSS (full width, 2:1). Measuring that canvas
   * and writing pixel sizes from a resize observer feeds the next resize.
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
   * Reflow the tile row. The open board keeps the CSS box, so this does not
   * write the panel canvas size.
   */
  function layoutBoards() {
    if (layingOut) return;
    layingOut = true;
    try {
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
   * Sequence alone misses a point appended under the same seq. Point count
   * catches that without rebuilding the tile button.
   *
   * @param {string} key
   * @param {number} seq
   * @returns {string}
   */
  function tileInkStamp(key, seq) {
    const bag = ink[key] || emptyInk();
    let points = 0;
    for (const stroke of Object.values(bag.strokes || {})) {
      points += Array.isArray(stroke?.points) ? stroke.points.length : 0;
    }
    const texts = Object.keys(bag.texts || {}).length;
    return `${Number(seq) || 0}:${points}:${texts}`;
  }

  /**
   * Find the tile button already on the grid for this board.
   *
   * @param {string} key
   * @returns {HTMLButtonElement | null}
   */
  function tileButton(key) {
    for (const button of grid.querySelectorAll(".wb-live-tile")) {
      if (button instanceof HTMLButtonElement && button.dataset.boardKey === key) return button;
    }
    return null;
  }

  /**
   * Create one tile button and its canvas. The node stays for later polls.
   *
   * @param {string} key
   * @returns {HTMLButtonElement}
   */
  function createTileButton(key) {
    const button = document.createElement("button");
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
    button.addEventListener("click", () => openBoard(key));
    return button;
  }

  /**
   * Refresh the label and, when the ink changed, the existing canvas.
   *
   * @param {HTMLButtonElement} button
   * @param {any} tile
   */
  function updateTileButton(button, tile) {
    const key = String(tile.board_key || "");
    button.classList.toggle("is-open", Boolean(key) && key === openKey);
    const dot = button.querySelector(".wb-live-dot");
    if (dot) dot.classList.toggle("is-away", Boolean(tile.away));
    const name = button.querySelector(".wb-live-name");
    const label = tileName(tile);
    if (name && name.textContent !== label) name.textContent = label;
    const meta = button.querySelector(".wb-live-tile-meta");
    if (meta instanceof HTMLElement) {
      const wanted = [];
      if (tile.text) wanted.push(String(tile.text));
      if (tile.status) wanted.push(String(tile.status));
      const nodes = meta.querySelectorAll(".wb-live-status");
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
    if (!(stage instanceof HTMLCanvasElement)) return;
    const stamp = tileInkStamp(key, Number(tile.board_seq));
    if (stage.dataset.inkStamp === stamp) return;
    const bag = ink[key] || emptyInk();
    paintTile(stage, bag.strokes, bag.texts, STUDENT_INK);
    stage.dataset.inkStamp = stamp;
  }

  /**
   * Paint the visible tiles without replacing buttons that are already there.
   *
   * A poll used to rebuild every canvas. That flashed the row and, with the
   * open board's resize observer, fed another layout pass.
   *
   * @param {any[]} rows
   */
  function paintGrid(rows) {
    const visible = rows.filter((tile) => tileMatches(tile, filter));
    const keep = new Set();
    let previous = null;
    for (const tile of visible) {
      const key = String(tile.board_key || "");
      keep.add(key);
      let button = tileButton(key);
      if (!button) button = createTileButton(key);
      updateTileButton(button, tile);
      const at = previous ? previous.nextSibling : grid.firstChild;
      if (button !== at) grid.insertBefore(button, at);
      previous = button;
    }
    for (const button of [...grid.querySelectorAll(".wb-live-tile")]) {
      if (!keep.has(button.dataset.boardKey || "")) button.remove();
    }
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
    if (context) context.textContent = String(payload.context || "");
    if (summary) summary.textContent = String(payload.summary || "");
    if (filters instanceof HTMLElement) paintFilters(payload.filters || []);
    for (const tile of tiles) {
      const key = String(tile.board_key || "");
      if (!key) continue;
      if (!ink[key]) ink[key] = emptyInk();
      foldDelta(ink[key], tile, STUDENT_INK);
      if (tile.board_seq != null && Number.isFinite(Number(tile.board_seq))) {
        cursors[key] = Number(tile.board_seq);
      }
    }
    paintGrid(tiles);
    projectedKey = String(payload.projected_board || "");
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
   * Show to class, or Stop showing when this board is the one up.
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
   * @param {string} boardKey
   */
  function openBoard(boardKey) {
    openKey = String(boardKey || "");
    panelSince = 0;
    panelRun = runKey;
    openStrokes.clear();
    inkQueue.drop();
    board.resetRun?.({ strokes: [], texts: [] });
    if (panel instanceof HTMLElement) panel.hidden = false;
    const tile = tiles.find((row) => String(row.board_key || "") === openKey);
    const title = part("panel-name");
    const age = part("panel-age");
    const team = part("panel-team");
    if (title) title.textContent = tile ? tileName(tile) : "Board";
    if (age) age.textContent = tile ? strokeAge(tile.last_stroke_at) : "";
    if (team) team.textContent = tile && tile.team_sort_order != null ? tileName(tile) : "";
    applyPanelLegend([]);
    paintGrid(tiles);
    paintShowButton();
    if (typeof options.onOpen === "function") options.onOpen(tile || null);
    refreshPanel();
  }

  function closePanel() {
    openKey = "";
    inkQueue.drop();
    if (panel instanceof HTMLElement) panel.hidden = true;
    galleryColumns = -1;
    paintGrid(tiles);
    paintShowButton();
  }

  /**
   * Paint filter chips once per label set. Later polls only move the selection.
   *
   * @param {any[]} chips
   */
  function paintFilters(chips) {
    if (!(filters instanceof HTMLElement)) return;
    const rows = Array.isArray(chips) ? chips : [];
    const sig = rows.map((chip) => `${chip?.id ?? ""}\t${chip?.label ?? ""}`).join("\n");
    if (filters.dataset.filterSig !== sig) {
      filters.dataset.filterSig = sig;
      filters.replaceChildren();
      for (const chip of rows) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "wb-live-filter";
        button.dataset.filterId = String(chip?.id || "all");
        button.textContent = String(chip?.label || "");
        button.addEventListener("click", () => {
          filter = button.dataset.filterId || "all";
          paintFilters(rows);
          paintGrid(tiles);
        });
        filters.appendChild(button);
      }
    }
    for (const button of filters.querySelectorAll(".wb-live-filter")) {
      if (!(button instanceof HTMLButtonElement)) continue;
      button.classList.toggle("is-on", (button.dataset.filterId || "all") === filter);
    }
  }

  /**
   * @param {number} step
   */
  function stepPanel(step) {
    const visible = tiles.filter((tile) => tileMatches(tile, filter));
    if (!visible.length) return;
    const index = visible.findIndex((tile) => String(tile.board_key || "") === openKey);
    const next = visible[(index + step + visible.length) % visible.length];
    if (next) openBoard(String(next.board_key || ""));
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
   * Legend chips for the open board, plus a nameless teacher colour.
   *
   * @param {any[] | undefined} legend
   */
  function applyPanelLegend(legend) {
    const members = Array.isArray(legend) ? legend.slice() : [];
    members.push({ id: "teacher", name: "", color: TEACHER_INK, slot: 0 });
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
  part("clear")?.addEventListener("click", () => {
    const id = sessionId();
    const key = openKey;
    if (!id || !key) return;
    fetch(boardUrl(id, key, "/clear"), {
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
        if (!res.ok || !data) return;
        const fromBody = String(data.run_key || "").trim();
        if (fromBody) runKey = fromBody;
        if (!ink[key]) ink[key] = emptyInk();
        foldDelta(ink[key], data, STUDENT_INK);
        if (Number.isFinite(Number(data.board_seq))) {
          cursors[key] = Number(data.board_seq);
          if (openKey === key) panelSince = Number(data.board_seq);
        }
        paintGrid(tiles);
        if (openKey === key && data.canvas_view) {
          board.applyDelta?.({ snapshot: true, canvas_view: data.canvas_view });
        }
      })
      .catch(() => {});
  });
  part("show")?.addEventListener("click", () => {
    const id = sessionId();
    const key = openKey;
    if (!id || !key) return;
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
        if (!res.ok || !data) return;
        projectedKey = data.projected === false ? "" : key;
        paintShowButton();
        return pollTiles();
      })
      .catch(() => {});
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
