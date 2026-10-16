/**
 * Formative live whiteboard: one board per student, teacher grid, phone tools.
 *
 * Strokes go to the server as board ops. This file does not keep a second
 * copy of Done, release, or names. Those come back on the next poll.
 */

import { bindWhiteboard, normalizeBoardPoint } from "./live_whiteboard.js";

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
 * Label from the server codename, or "Student N" when that field is blank.
 *
 * @param {{codename?: string, anon_index?: number}} tile
 * @returns {string}
 */
function tileName(tile) {
  const name = String(tile?.codename || "").trim();
  if (name) return name;
  const index = Number(tile?.anon_index) || 0;
  return index > 0 ? `Student ${index}` : "Student";
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
function foldDelta(state, delta, fallbackColor) {
  if (!delta || typeof delta !== "object") return;
  if (delta.snapshot && delta.canvas_view) {
    state.strokes = {};
    state.texts = {};
    const view = delta.canvas_view;
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
  let openId = 0;
  let session = 0;
  let tileTimer = 0;
  let panelTimer = 0;
  let panelSince = 0;
  let feedbackSince = 0;
  const openStrokes = new Set();
  const alwaysNames = root.querySelector("#wb-live-always-names");
  const projectorNames = root.querySelector("#wb-live-projector-names");
  const cols = root.querySelector("#wb-live-cols");

  const board = bindWhiteboard(canvas, {
    textBtn: root.querySelector("#wb-live-text"),
    undoBtn: root.querySelector("#wb-live-undo"),
    formative: {
      enabled: true,
      members: [],
      isTeacher: true,
      isPhone: false,
      alwaysShowNames: false,
      projectorShowNames: false,
    },
    owner: () => "teacher",
    color: TEACHER_INK,
    onPoints: (points, ended, strokeId) => {
      if (!openId) return;
      const ops = strokeOps(canvas, openStrokes, points, ended, strokeId, TEACHER_INK);
      if (ops.length) postFeedback(ops);
    },
    onText: (label) => {
      if (!openId) return;
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
      if (!openId || !stroke?.id) return;
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
   * @param {boolean} on
   */
  function showGrid(on) {
    root.hidden = !on;
    root.classList.toggle("is-on", on);
  }

  /**
   * @param {Record<string, any>} patch
   */
  function patchNames(patch) {
    const id = sessionId();
    if (!id) return;
    fetch(`/api/live-sessions/${id}/teacher-state`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).catch(() => {});
  }

  if (alwaysNames instanceof HTMLInputElement) {
    alwaysNames.addEventListener("change", () => {
      patchNames({ whiteboard_always_show_names: alwaysNames.checked });
    });
  }
  if (projectorNames instanceof HTMLInputElement) {
    projectorNames.addEventListener("change", () => {
      patchNames({ whiteboard_projector_names: projectorNames.checked });
    });
  }
  if (cols instanceof HTMLSelectElement) {
    cols.addEventListener("change", () => {
      grid.style.setProperty("--wb-live-cols", cols.value || "4");
    });
  }

  /**
   * @param {Record<string, any>[]} ops
   */
  function postFeedback(ops) {
    const id = sessionId();
    if (!id || !openId || !ops.length) return;
    fetch(`/api/live-sessions/${id}/formative-boards/${openId}/feedback`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ops, client_batch_id: batchId() }),
    }).catch(() => {});
  }

  /**
   * @param {any[]} rows
   */
  function paintGrid(rows) {
    const visible = rows.filter((tile) => filter === "all" || tile.team_board === filter);
    grid.replaceChildren();
    for (const tile of visible) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "wb-live-tile";
      if (Number(tile.student_id) === openId) button.classList.add("is-open");
      button.dataset.studentId = String(tile.student_id);
      const stage = document.createElement("canvas");
      stage.width = 280;
      stage.height = 160;
      stage.className = "wb-live-tile-canvas";
      const studentKey = String(tile.board_key || "");
      const feedbackKey = String(tile.feedback_key || "");
      const studentInk = ink[studentKey] || emptyInk();
      const feedbackInk = ink[feedbackKey] || emptyInk();
      paintTile(stage, studentInk.strokes, studentInk.texts, STUDENT_INK);
      paintTileOverlay(stage, feedbackInk);
      const meta = document.createElement("span");
      meta.className = "wb-live-tile-meta";
      const dot = document.createElement("span");
      dot.className = "wb-live-dot";
      if (tile.away) dot.classList.add("is-away");
      const name = document.createElement("span");
      name.className = "wb-live-name";
      name.textContent = tileName(tile);
      if (tile.submitted) name.append(" ✓");
      const team = document.createElement("span");
      team.className = "wb-live-team";
      team.textContent = String(tile.team_label || "");
      meta.append(dot, name, team);
      button.append(stage, meta);
      button.addEventListener("click", () => openStudent(Number(tile.student_id)));
      grid.appendChild(button);
    }
  }

  /**
   * Draw feedback ink on top of a tile that already has student ink.
   *
   * @param {HTMLCanvasElement} stage
   * @param {{strokes: Record<string, any>, texts: Record<string, any>}} feedbackInk
   */
  function paintTileOverlay(stage, feedbackInk) {
    const ctx = stage.getContext("2d");
    if (!ctx) return;
    const w = stage.width;
    const h = stage.height;
    ctx.lineWidth = 2;
    ctx.strokeStyle = TEACHER_INK;
    ctx.fillStyle = TEACHER_INK;
    for (const stroke of Object.values(feedbackInk.strokes)) {
      const points = Array.isArray(stroke.points) ? stroke.points : [];
      if (!points.length) continue;
      ctx.beginPath();
      ctx.strokeStyle = String(stroke.color || TEACHER_INK);
      points.forEach((pt, index) => {
        const x = Number(pt[0]) * w;
        const y = Number(pt[1]) * h;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
    ctx.font = "12px sans-serif";
    for (const label of Object.values(feedbackInk.texts)) {
      const body = String(label.text || "").trim();
      if (!body) continue;
      ctx.fillStyle = String(label.color || TEACHER_INK);
      ctx.fillText(body, Number(label.x) * w, Number(label.y) * h + 14);
    }
  }

  /**
   * @param {any} payload
   */
  function applyTiles(payload) {
    if (!payload || payload.ended) {
      showGrid(false);
      return;
    }
    showGrid(true);
    tiles = Array.isArray(payload.tiles) ? payload.tiles : [];
    if (context) context.textContent = String(payload.context || "");
    if (summary) summary.textContent = String(payload.summary || "");
    if (alwaysNames instanceof HTMLInputElement && document.activeElement !== alwaysNames) {
      alwaysNames.checked = Boolean(payload.always_show_names);
    }
    if (projectorNames instanceof HTMLInputElement && document.activeElement !== projectorNames) {
      projectorNames.checked = Boolean(payload.names_on_projector);
    }
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
      const studentKey = String(tile.board_key || "");
      const feedbackKey = String(tile.feedback_key || "");
      if (!ink[studentKey]) ink[studentKey] = emptyInk();
      if (!ink[feedbackKey]) ink[feedbackKey] = emptyInk();
      foldDelta(ink[studentKey], tile, STUDENT_INK);
      foldDelta(
        ink[feedbackKey],
        {
          snapshot: tile.snapshot && tile.feedback_view,
          canvas_view: tile.feedback_view,
          ops: tile.feedback_ops,
        },
        TEACHER_INK
      );
      if (tile.board_seq != null) cursors[studentKey] = Number(tile.board_seq) || 0;
      if (tile.feedback_seq != null) cursors[feedbackKey] = Number(tile.feedback_seq) || 0;
    }
    paintGrid(tiles);
    const status = root.querySelector("#wb-live-projected");
    if (status) {
      const projected = tiles.find(
        (tile) => Number(tile.student_id) === Number(payload.projected_student_id)
      );
      status.textContent = projected ? `On the board · ${tileName(projected)}` : "";
    }
  }

  /**
   * @param {number} studentId
   */
  function openStudent(studentId) {
    openId = studentId;
    panelSince = 0;
    feedbackSince = 0;
    openStrokes.clear();
    if (panel instanceof HTMLElement) panel.hidden = false;
    const tile = tiles.find((row) => Number(row.student_id) === studentId);
    const title = root.querySelector("#wb-live-panel-name");
    const age = root.querySelector("#wb-live-panel-age");
    const team = root.querySelector("#wb-live-panel-team");
    if (title) title.textContent = tile ? tileName(tile) : "Student";
    if (age) age.textContent = tile ? strokeAge(tile.last_stroke_at) : "";
    if (team) team.textContent = tile ? String(tile.team_label || "") : "";
    board.setFormative?.({
      members: [
        { id: String(studentId), name: "", color: STUDENT_INK, slot: 0 },
        { id: "teacher", name: "", color: TEACHER_INK, slot: 1 },
      ],
      alwaysShowNames: false,
    });
    paintGrid(tiles);
    refreshPanel();
    window.setTimeout(() => window.dispatchEvent(new Event("resize")), 0);
  }

  function closePanel() {
    openId = 0;
    if (panel instanceof HTMLElement) panel.hidden = true;
    paintGrid(tiles);
  }

  /**
   * @param {number} step
   */
  function stepPanel(step) {
    const visible = tiles.filter((tile) => filter === "all" || tile.team_board === filter);
    if (!visible.length) return;
    const index = visible.findIndex((tile) => Number(tile.student_id) === openId);
    const next = visible[(index + step + visible.length) % visible.length];
    if (next) openStudent(Number(next.student_id));
  }

  async function refreshPanel() {
    const id = sessionId();
    if (!id || !openId) return;
    const data = await readJson(
      `/api/live-sessions/${id}/formative-boards/${openId}?since=${panelSince}&feedback_since=${feedbackSince}`,
      { credentials: "same-origin" }
    );
    if (!data || Number(data.student_id) !== openId) return;
    const title = root.querySelector("#wb-live-panel-name");
    if (title) title.textContent = String(data.codename || "").trim() || title.textContent;
    if (data.snapshot && data.canvas_view && panelSince === 0) {
      board.resetRun?.(data.canvas_view);
    } else if (Array.isArray(data.ops) && data.ops.length) {
      board.applyDelta?.({ ops: data.ops });
    }
    if (data.feedback_view && feedbackSince === 0) {
      board.applyDelta?.({ snapshot: true, canvas_view: data.feedback_view });
    } else if (Array.isArray(data.feedback_ops) && data.feedback_ops.length) {
      board.applyDelta?.({ ops: data.feedback_ops });
    }
    panelSince = Number(data.board_seq) || panelSince;
    feedbackSince = Number(data.feedback_seq) || feedbackSince;
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
      for (const key of Object.keys(ink)) delete ink[key];
      for (const key of Object.keys(cursors)) delete cursors[key];
    }
    const query = encodeURIComponent(JSON.stringify(cursors));
    const data = await readJson(
      `/api/live-sessions/${id}/formative-boards?cursors=${query}`,
      { credentials: "same-origin" }
    );
    if (data) applyTiles(data);
  }

  root.querySelector("#wb-live-prev")?.addEventListener("click", () => stepPanel(-1));
  root.querySelector("#wb-live-next")?.addEventListener("click", () => stepPanel(1));
  root.querySelector("#wb-live-close")?.addEventListener("click", closePanel);
  root.querySelector("#wb-live-pen")?.addEventListener("click", () => {
    const textBtn = root.querySelector("#wb-live-text");
    if (textBtn instanceof HTMLButtonElement && textBtn.getAttribute("aria-pressed") === "true") {
      textBtn.click();
    }
  });
  root.querySelector("#wb-live-send")?.addEventListener("click", () => {
    const id = sessionId();
    if (!id || !openId) return;
    fetch(`/api/live-sessions/${id}/formative-boards/${openId}/release`, {
      method: "POST",
      credentials: "same-origin",
    }).catch(() => {});
  });
  root.querySelector("#wb-live-clear")?.addEventListener("click", () => {
    const id = sessionId();
    if (!id || !openId) return;
    fetch(`/api/live-sessions/${id}/formative-boards/${openId}/clear`, {
      method: "POST",
      credentials: "same-origin",
    })
      .then(() => {
        panelSince = 0;
        return refreshPanel();
      })
      .catch(() => {});
  });
  root.querySelector("#wb-live-show")?.addEventListener("click", () => {
    const id = sessionId();
    if (!id || !openId) return;
    fetch(`/api/live-sessions/${id}/formative-boards/${openId}/show`, {
      method: "POST",
      credentials: "same-origin",
    })
      .then(() => pollTiles())
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
    if (openId) refreshPanel();
  }, PANEL_POLL_MS);
  void tileTimer;
  void panelTimer;
}

/**
 * Latest teacher text on a released feedback payload.
 *
 * @param {any} feedback
 * @returns {string}
 */
function feedbackLine(feedback) {
  if (!feedback || typeof feedback !== "object") return "";
  const labels = [];
  const view = feedback.canvas_view;
  if (view && Array.isArray(view.texts)) labels.push(...view.texts);
  for (const op of feedback.ops || []) {
    if (op && op.type === "text_upsert" && String(op.text || "").trim()) labels.push(op);
  }
  const last = labels[labels.length - 1];
  const text = String(last?.text || "").trim();
  return text ? `Teacher: ${text}` : "Teacher feedback";
}

/**
 * Phone board for the signed-in student. The server decides who they are.
 *
 * @param {HTMLElement} root
 */
export function mountStudentBoard(root) {
  const canvas = root.querySelector("#wb-phone-canvas");
  if (!(canvas instanceof HTMLCanvasElement)) return;
  const openStrokes = new Set();
  let since = 0;
  let feedbackSince = 0;
  let sawFeedback = false;
  let studentOwner = "student";

  const board = bindWhiteboard(canvas, {
    undoBtn: root.querySelector("#wb-phone-undo"),
    eraseBtn: root.querySelector("#wb-phone-erase"),
    textBtn: root.querySelector("#wb-phone-text"),
    formative: {
      enabled: true,
      members: [],
      isTeacher: false,
      isPhone: true,
      alwaysShowNames: false,
      projectorShowNames: false,
    },
    owner: () => studentOwner,
    color: STUDENT_INK,
    onPoints: (points, ended, strokeId) => {
      const ops = strokeOps(canvas, openStrokes, points, ended, strokeId, STUDENT_INK);
      if (ops.length) postOps(ops);
    },
    onText: (label) => {
      const norm = normalizeBoardPoint(canvas, label);
      if (!norm) return;
      postOps([
        {
          type: "text_upsert",
          id: label.id,
          text: label.text,
          x: norm.x,
          y: norm.y,
          color: STUDENT_INK,
        },
      ]);
    },
    onUndo: (stroke) => {
      if (!stroke?.id) return;
      postOps([{ type: "stroke_remove", id: stroke.id }]);
    },
    onClear: (gone) => {
      const ops = [];
      for (const stroke of gone || []) {
        if (stroke?.id) ops.push({ type: "stroke_remove", id: stroke.id });
      }
      if (ops.length) postOps(ops);
    },
  });
  board.setCollab(true);

  /**
   * @param {RequestInit} [init]
   * @returns {RequestInit}
   */
  function withVisit(init) {
    const merge = window.studentVisitFetchInit;
    if (typeof merge === "function") return merge(init || {});
    return init || {};
  }

  /**
   * @param {Record<string, any>[]} ops
   */
  function postOps(ops) {
    fetch(
      "/api/student/formative-board/ops",
      withVisit({
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ops,
          client_batch_id: batchId(),
          since,
          feedback_since: feedbackSince,
        }),
      })
    ).catch(() => {});
  }

  /**
   * @param {any} data
   */
  function paint(data) {
    if (!data || !data.visible) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    const question = root.querySelector("#wb-phone-question");
    if (question) question.textContent = String(data.question || data.context || "Whiteboard");
    const team = root.querySelector("#wb-phone-team");
    if (team) team.textContent = String(data.team_label || "");
    const done = root.querySelector("#wb-phone-done");
    if (done instanceof HTMLButtonElement) done.classList.toggle("is-on", Boolean(data.submitted));
    const chip = root.querySelector("#wb-phone-feedback");
    if (chip instanceof HTMLElement) {
      if (data.feedback) {
        chip.hidden = false;
        chip.textContent = feedbackLine(data.feedback);
      } else {
        chip.hidden = true;
        chip.textContent = "";
      }
    }
    studentOwner = String(data.student_id || studentOwner);
    board.setFormative?.({
      members: [
        { id: studentOwner, name: "", color: STUDENT_INK, slot: 0 },
        { id: "teacher", name: "", color: TEACHER_INK, slot: 1 },
      ],
      alwaysShowNames: false,
    });
    if (data.snapshot && data.canvas_view && since === 0) {
      board.resetRun?.(data.canvas_view);
    } else if (Array.isArray(data.ops) && data.ops.length) {
      board.applyDelta?.({ ops: data.ops });
    }
    if (!sawFeedback && data.feedback?.canvas_view) {
      board.applyDelta?.({ snapshot: true, canvas_view: data.feedback.canvas_view });
      sawFeedback = true;
    } else if (Array.isArray(data.feedback?.ops) && data.feedback.ops.length) {
      board.applyDelta?.({ ops: data.feedback.ops });
      sawFeedback = true;
    }
    since = Number(data.board_seq) || since;
    if (data.feedback) feedbackSince = Number(data.feedback.board_seq) || feedbackSince;
  }

  async function poll() {
    const data = await readJson(
      `/api/student/formative-board?since=${since}&feedback_since=${feedbackSince}`,
      withVisit({ credentials: "same-origin" })
    );
    if (data) paint(data);
  }

  root.querySelector("#wb-phone-pen")?.addEventListener("click", () => {
    const textBtn = root.querySelector("#wb-phone-text");
    if (textBtn instanceof HTMLButtonElement && textBtn.getAttribute("aria-pressed") === "true") {
      textBtn.click();
    }
    root.querySelector("#wb-phone-pen")?.classList.add("is-on");
    root.querySelector("#wb-phone-text")?.classList.remove("is-on");
    root.querySelector("#wb-phone-erase")?.classList.remove("is-on");
  });
  root.querySelector("#wb-phone-question")?.addEventListener("click", () => {
    root.querySelector("#wb-phone-question")?.classList.toggle("is-collapsed");
  });
  root.querySelector("#wb-phone-done")?.addEventListener("click", () => {
    fetch(
      "/api/student/formative-board/done",
      withVisit({ method: "POST", credentials: "same-origin" })
    )
      .then(() => poll())
      .catch(() => {});
  });

  poll();
  window.setInterval(poll, TILE_POLL_MS);
}

const teacherRoot = document.getElementById("formative-live-board");
if (teacherRoot instanceof HTMLElement) mountTeacherGrid(teacherRoot);

const studentRoot = document.getElementById("student-formative-board");
if (studentRoot instanceof HTMLElement) mountStudentBoard(studentRoot);
