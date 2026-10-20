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

/** Teacher tile poll. About every 2 seconds, one since-seq request. */
const TILE_POLL_MS = 2000;
/** Open side panel refreshes faster than the grid. */
const PANEL_POLL_MS = 500;
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
export function mountTeacherGrid(root) {
  const grid = root.querySelector("#wb-live-grid");
  const panel = root.querySelector("#wb-live-panel");
  const filters = root.querySelector("#wb-live-filters");
  const summary = root.querySelector("#wb-live-summary");
  const context = root.querySelector("#wb-live-context");
  const canvas = root.querySelector("#wb-live-panel-canvas");
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
  const cols = root.querySelector("#wb-live-cols");

  const board = bindWhiteboard(canvas, {
    penBtn: root.querySelector("#wb-live-pen"),
    textBtn: root.querySelector("#wb-live-text"),
    eraseBtn: root.querySelector("#wb-live-erase"),
    undoBtn: root.querySelector("#wb-live-undo"),
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
  });
  board.setCollab(true);

  /**
   * @returns {string}
   */
  function sessionId() {
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
    return `/api/live-sessions/${id}/whiteboard-gallery/${encodeURIComponent(key)}${suffix || ""}`;
  }

  /**
   * @param {boolean} on
   */
  function showGrid(on) {
    root.hidden = !on;
    root.classList.toggle("is-on", on);
  }

  if (cols instanceof HTMLSelectElement) {
    cols.addEventListener("change", () => {
      grid.style.setProperty("--wb-live-cols", cols.value || "4");
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
    const key = openKey;
    if (!sessionId() || !key || !ops.length) return;
    inkQueue.push({ board_key: key, ops });
  }

  /**
   * @param {any[]} rows
   */
  function paintGrid(rows) {
    const visible = rows.filter((tile) => tileMatches(tile, filter));
    grid.replaceChildren();
    for (const tile of visible) {
      const key = String(tile.board_key || "");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "wb-live-tile";
      if (key && key === openKey) button.classList.add("is-open");
      button.dataset.boardKey = key;
      const stage = document.createElement("canvas");
      stage.width = 280;
      stage.height = 160;
      stage.className = "wb-live-tile-canvas";
      const bag = ink[key] || emptyInk();
      paintTile(stage, bag.strokes, bag.texts, STUDENT_INK);
      const meta = document.createElement("span");
      meta.className = "wb-live-tile-meta";
      const dot = document.createElement("span");
      dot.className = "wb-live-dot";
      if (tile.away) dot.classList.add("is-away");
      const name = document.createElement("span");
      name.className = "wb-live-name";
      name.textContent = tileName(tile);
      const team = document.createElement("span");
      team.className = "wb-live-team";
      team.textContent = tile.team_sort_order == null ? "" : "";
      meta.append(dot, name, team);
      if (tile.status) {
        const wrote = document.createElement("span");
        wrote.className = "wb-live-status";
        wrote.textContent = String(tile.status);
        meta.append(wrote);
      }
      button.append(stage, meta);
      button.addEventListener("click", () => openBoard(key));
      grid.appendChild(button);
    }
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
    if (filters instanceof HTMLElement) {
      filters.replaceChildren();
      for (const chip of payload.filters || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "wb-live-filter";
        if (chip.id === filter) button.classList.add("is-on");
        button.textContent = String(chip.label || "");
        button.addEventListener("click", () => {
          filter = String(chip.id || "all");
          for (const node of filters.querySelectorAll(".wb-live-filter")) {
            node.classList.toggle("is-on", node === button);
          }
          paintGrid(tiles);
        });
        filters.appendChild(button);
      }
    }
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
    const status = root.querySelector("#wb-live-projected");
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
    const button = root.querySelector("#wb-live-show");
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
    const title = root.querySelector("#wb-live-panel-name");
    const age = root.querySelector("#wb-live-panel-age");
    const team = root.querySelector("#wb-live-panel-team");
    if (title) title.textContent = tile ? tileName(tile) : "Board";
    if (age) age.textContent = tile ? strokeAge(tile.last_stroke_at) : "";
    if (team) team.textContent = tile && tile.team_sort_order != null ? tileName(tile) : "";
    applyPanelLegend([]);
    paintGrid(tiles);
    paintShowButton();
    refreshPanel();
    window.setTimeout(() => window.dispatchEvent(new Event("resize")), 0);
  }

  function closePanel() {
    openKey = "";
    inkQueue.drop();
    if (panel instanceof HTMLElement) panel.hidden = true;
    paintGrid(tiles);
    paintShowButton();
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
    const title = root.querySelector("#wb-live-panel-name");
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
    const data = await readJson(
      `/api/live-sessions/${id}/whiteboard-gallery?cursors=${query}`,
      { credentials: "same-origin" }
    );
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
    board.setFormative?.({
      members,
      alwaysShowNames: true,
      projectorShowNames: false,
    });
  }

  root.querySelector("#wb-live-prev")?.addEventListener("click", () => stepPanel(-1));
  root.querySelector("#wb-live-next")?.addEventListener("click", () => stepPanel(1));
  root.querySelector("#wb-live-close")?.addEventListener("click", closePanel);
  root.querySelector("#wb-live-clear")?.addEventListener("click", () => {
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
  root.querySelector("#wb-live-show")?.addEventListener("click", () => {
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
  pollTiles();
  tileTimer = window.setInterval(pollTiles, TILE_POLL_MS);
  panelTimer = window.setInterval(() => {
    if (openKey) refreshPanel();
  }, PANEL_POLL_MS);
  void tileTimer;
  void panelTimer;
}

if (typeof document !== "undefined") {
  const teacherRoot = document.getElementById("formative-live-board");
  if (teacherRoot instanceof HTMLElement) mountTeacherGrid(teacherRoot);
}
