/**
 * Question-board phone, catch-up phone, and teacher tiles (MCK-31).
 *
 * The drawing surface is the existing whiteboard. This module mounts it
 * on a poll and posts Submit. Teacher ink uses the same red pen and
 * stroke batches as the live gallery.
 */

import { bindWhiteboard, normalizeBoardPoint } from "./live_whiteboard.js";

const SIZE_LINE = "Your board is too full to submit. Erase a little and try again.";
const DOWN_LINE = "That didn't go through. Your drawing is safe, so press Submit again.";
const TEACHER_INK = "#dc2626";

/**
 * Attach the student visit token when this tab is not using a staff cookie.
 * @param {RequestInit} [init]
 * @returns {RequestInit}
 */
function studentInit(init) {
  if (typeof window.studentVisitFetchInit === "function") {
    return window.studentVisitFetchInit(init || {});
  }
  return init || { credentials: "same-origin", headers: { Accept: "application/json" } };
}

/** @type {Map<string, {root: HTMLElement, board: ReturnType<typeof bindWhiteboard>}>} */
const studentBoards = new Map();

/**
 * Paint compact strokes onto a thumbnail canvas.
 * @param {HTMLCanvasElement} canvas
 * @param {{strokes?: {points?: number[][], color?: string}[], texts?: {x?: number, y?: number, text?: string, color?: string}[]}} view
 */
export function paintBoardThumb(canvas, view) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(0, 0, w, h);
  for (const stroke of view?.strokes || []) {
    const points = Array.isArray(stroke.points) ? stroke.points : [];
    if (!points.length) continue;
    ctx.strokeStyle = String(stroke.color || "#2563eb");
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.beginPath();
    points.forEach((pt, index) => {
      const x = Number(pt[0]) * w;
      const y = Number(pt[1]) * h;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  ctx.font = "12px sans-serif";
  for (const label of view?.texts || []) {
    ctx.fillStyle = String(label.color || "#0f172a");
    ctx.fillText(String(label.text || ""), Number(label.x || 0) * w, Number(label.y || 0) * h);
  }
}

/**
 * Show a line under the board. Failures stay on the error class.
 * @param {HTMLElement} slot
 * @param {string} line
 * @param {"error" | "status"} [tone]
 */
function showLine(slot, line, tone) {
  let note = slot.querySelector(".qboard-note");
  if (!(note instanceof HTMLElement)) {
    note = document.createElement("p");
    note.className = "qboard-note";
    const tools = slot.querySelector(".qboard-tools");
    if (tools) slot.insertBefore(note, tools);
    else slot.appendChild(note);
  }
  note.className = tone === "error" ? "qboard-note qboard-line" : "qboard-note qboard-status";
  note.textContent = line;
  note.hidden = !line;
}

/**
 * Turn pointer samples into stroke ops. The first batch of an id is stroke_add.
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
 * Routes the phone uses for one board.
 * @param {"live" | "catchup"} kind
 * @param {string} id Prompt id or participation item id.
 * @param {string} [token] Catch-up link token. Not logged.
 * @returns {{read: string, ops: string, submit: string, draft: string | null}}
 */
function boardUrls(kind, id, token) {
  if (kind === "catchup") {
    const base = `/api/catchup/${encodeURIComponent(token || "")}/questions/${id}/board`;
    return { read: base, ops: `${base}/ops`, submit: `${base}/submit`, draft: null };
  }
  const base = `/api/student/question-board/${id}`;
  return { read: base, ops: `${base}/ops`, submit: `${base}/submit`, draft: `${base}/draft` };
}

/**
 * Mount one phone board and keep its ink in sync with the server.
 * @param {HTMLElement} slot
 * @param {string} key Map key.
 * @param {{read: string, ops: string, submit: string, draft: string | null}} urls
 * @param {{team: boolean}} options Catch-up is never a team board.
 */
async function ensurePhoneBoard(slot, key, urls, options) {
  let held = studentBoards.get(key);
  if (!held) {
    const root = document.createElement("div");
    root.className = "qboard-phone";
    const text = document.createElement("textarea");
    text.className = "qboard-text";
    text.maxLength = 500;
    text.rows = 2;
    text.hidden = slot.getAttribute("data-qboard-mode") !== "both";
    text.setAttribute("aria-label", "Typed answer");
    const canvas = document.createElement("canvas");
    canvas.width = 720;
    canvas.height = 360;
    canvas.className = "qboard-canvas";
    canvas.setAttribute("aria-label", "Whiteboard");
    const tools = document.createElement("div");
    tools.className = "qboard-tools";
    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "prompt-submit";
    submit.textContent = options.team ? "Submit our board" : "Submit";
    tools.appendChild(submit);
    const line = document.createElement("p");
    line.className = "qboard-note qboard-status";
    line.hidden = true;
    root.append(text, canvas, line, tools);
    const image = slot.getAttribute("data-qboard-image") || "";
    if (image) canvas.style.backgroundImage = `url("${image.replace(/"/g, "")}")`;
    const started = new Set();
    const board = bindWhiteboard(canvas, {
      owner: () => "student",
      onPoints: (points, ended, strokeId) => {
        const ops = strokeOps(canvas, started, points, ended, strokeId, "");
        if (!ops.length) return;
        fetch(urls.ops, studentInit({
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ ops }),
        })).catch(() => {});
        submit.disabled = false;
      },
    });
    board.setCollab(true);
    submit.addEventListener("click", async () => {
      const response = await fetch(urls.submit, studentInit({
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text: text.value }),
      }));
      const payload = await response.json().catch(() => ({}));
      if (response.status === 413) {
        showLine(root, SIZE_LINE, "error");
        return;
      }
      if (!response.ok) {
        showLine(root, String(payload.error || DOWN_LINE), "error");
        return;
      }
      showLine(root, String(payload.status_line || "Submitted. You can still make changes."), "status");
      const fresh = await fetch(urls.read, studentInit({
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }));
      const data = await fresh.json().catch(() => ({}));
      if (data.canvas_view) board.importRemote(data.canvas_view, { collab: true });
      if (data.status_line) showLine(root, String(data.status_line), "status");
      root._seq = data.board_seq;
    });
    text.addEventListener("input", () => {
      submit.disabled = !text.value.trim() && !(board.getInk?.().strokes || []).length;
      if (options.team && urls.draft) {
        window.clearTimeout(root._draftTimer);
        root._draftTimer = window.setTimeout(() => {
          fetch(urls.draft, studentInit({
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text.value }),
          })).catch(() => {});
        }, 2000);
      }
    });
    held = { root, board };
    studentBoards.set(key, held);
    const res = await fetch(urls.read, studentInit({
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }));
    const data = await res.json().catch(() => ({}));
    if (data.canvas_view && typeof board.resetRun === "function") board.resetRun(data.canvas_view);
    board.setCollab(true);
    if (data.text) text.value = String(data.text);
    if (data.status_line) showLine(root, String(data.status_line), "status");
    root._seq = data.board_seq;
    const locked = data.read_only || slot.getAttribute("data-qboard-readonly") === "1";
    if (locked) {
      board.setEnabled?.(false);
      submit.hidden = true;
      text.readOnly = true;
    }
    submit.disabled = !(data.text || (data.canvas_view && (data.canvas_view.strokes || []).length));
    if (!locked) {
      root._poll = window.setInterval(async () => {
        const fresh = await fetch(urls.read, studentInit({
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        }));
        if (!fresh.ok) return;
        const next = await fresh.json().catch(() => null);
        if (!next || next.board_seq === root._seq) return;
        root._seq = next.board_seq;
        if (next.canvas_view) board.importRemote(next.canvas_view, { collab: true });
      }, 2000);
    }
  }
  if (held.root.parentElement !== slot) slot.appendChild(held.root);
}

/**
 * Mount or reuse the student board inside a question card.
 * @param {HTMLElement} slot
 */
async function ensureStudentBoard(slot) {
  const promptId = String(slot.getAttribute("data-qboard-prompt") || "");
  if (!promptId) return;
  await ensurePhoneBoard(slot, `live:${promptId}`, boardUrls("live", promptId), {
    team: slot.getAttribute("data-qboard-team") === "1",
  });
}

/**
 * Attach boards after the student question stack is painted.
 */
export function mountStudentQuestionBoards() {
  document.querySelectorAll("[data-qboard-prompt]").forEach((slot) => {
    if (slot instanceof HTMLElement) void ensureStudentBoard(slot);
  });
}

/**
 * Mount catch-up boards. Wording stays solo even when the live question was a team board.
 * @param {string} token Catch-up link token from the page path. Not logged.
 */
export function mountCatchupQuestionBoards(token) {
  document.querySelectorAll("[data-qboard-catchup]").forEach((slot) => {
    if (!(slot instanceof HTMLElement)) return;
    const itemId = String(slot.getAttribute("data-qboard-catchup") || "");
    if (!itemId || !token) return;
    void ensurePhoneBoard(slot, `cu:${itemId}`, boardUrls("catchup", itemId, token), { team: false });
  });
}

/** @type {Map<string, HTMLElement>} */
const teacherRoots = new Map();

/**
 * @param {string} value
 * @returns {string}
 */
function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Colour chips with roster names. The student projection drops the names.
 * @param {{id?: string, name?: string, color?: string}[]} rows
 * @returns {string}
 */
function legendHtml(rows) {
  return (rows || [])
    .map((row) => {
      const name = String(row.name || "").trim();
      if (!name) return "";
      const color = String(row.color || "#64748b");
      return `<span class="qboard-legend-chip"><span class="qboard-legend-swatch" style="background:${escapeHtml(color)}"></span>${escapeHtml(name)}</span>`;
    })
    .join("");
}

/**
 * Paint the teacher tile dashboard into one question card.
 * @param {HTMLElement} slot
 * @param {string} sessionId
 */
async function paintTeacherDash(slot, sessionId) {
  const promptId = String(slot.getAttribute("data-qboard-dash") || "");
  if (!promptId || !sessionId) return;
  const res = await fetch(
    `/api/live-sessions/${sessionId}/questions/${promptId}/boards`,
    { credentials: "same-origin", headers: { Accept: "application/json" } }
  );
  if (!res.ok) return;
  const data = await res.json().catch(() => null);
  if (!data || !data.ok) return;
  let root = teacherRoots.get(promptId);
  if (!root) {
    root = document.createElement("div");
    root.className = "qboard-dash-grid";
    root._filter = "all";
    root._openKey = "";
    root._openStrokes = new Set();
    teacherRoots.set(promptId, root);
  }
  const label = data.label === "Typing and whiteboard" ? "poll · Typing and whiteboard" : "poll · Whiteboard";
  const filter = String(root._filter || "all");
  const filters = (data.filters || [])
    .map((chip) => {
      const on = chip.id === filter ? " is-on" : "";
      return `<button type="button" class="qboard-filter${on}" data-qboard-filter="${escapeHtml(chip.id)}">${escapeHtml(chip.label)} ${chip.count}</button>`;
    })
    .join("");
  const visible = (data.tiles || []).filter((tile) => filter === "all" || tile.status === filter);
  const tiles = visible
    .map((tile) => {
      const text = tile.text ? `<p class="qboard-tile-text">${escapeHtml(tile.text)}</p>` : "";
      const members = (tile.member_names || []).filter(Boolean).join(", ");
      const who = members ? `<p class="qboard-tile-members">${escapeHtml(members)}</p>` : "";
      return `<button type="button" class="qboard-tile" data-qboard-open="${escapeHtml(tile.board_key)}">
        <canvas class="qboard-thumb" width="160" height="90" data-qboard-thumb="${escapeHtml(tile.board_key)}"></canvas>
        ${text}
        <span class="qboard-tile-name">${escapeHtml(tile.name || "")}</span>
        ${who}
        <span class="qboard-tile-status">${escapeHtml(tile.status || "")}</span>
      </button>`;
    })
    .join("");
  const panelWasOpen = root._openKey;
  let panel = root.querySelector(".qboard-panel");
  const keepPen = panel instanceof HTMLElement && panel.querySelector("canvas") && panelWasOpen;
  if (!root.querySelector(".qboard-layout")) {
    root.innerHTML = `<header class="qboard-head"></header><div class="qboard-filters"></div><div class="qboard-layout"><div class="qboard-tiles"></div><aside class="qboard-panel" hidden></aside></div>`;
    panel = root.querySelector(".qboard-panel");
  }
  const head = root.querySelector(".qboard-head");
  if (head) {
    head.innerHTML = `<span class="qboard-chip">${escapeHtml(label)}</span><span class="qboard-stem">${escapeHtml(
      data.stem || ""
    )}</span><span class="qboard-summary">${escapeHtml(data.summary || "")}</span>`;
  }
  const filterHost = root.querySelector(".qboard-filters");
  if (filterHost) filterHost.innerHTML = filters;
  const tileHost = root.querySelector(".qboard-tiles");
  if (tileHost) tileHost.innerHTML = tiles;
  if (root.parentElement !== slot) slot.appendChild(root);
  root.querySelectorAll("[data-qboard-thumb]").forEach((canvas) => {
    const key = canvas.getAttribute("data-qboard-thumb");
    const tile = visible.find((row) => row.board_key === key);
    if (canvas instanceof HTMLCanvasElement && tile) paintBoardThumb(canvas, tile.canvas_view || {});
  });
  root.querySelectorAll("[data-qboard-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      root._filter = button.getAttribute("data-qboard-filter") || "all";
      root._openKey = "";
      if (panel instanceof HTMLElement) {
        panel.hidden = true;
        panel.innerHTML = "";
      }
      void paintTeacherDash(slot, sessionId);
    });
  });
  /**
   * Open one tile in the side panel with a real pen.
   * @param {string} key
   */
  function openTile(key) {
    const tile = (data.tiles || []).find((row) => row.board_key === key);
    if (!(panel instanceof HTMLElement) || !tile) return;
    root._openKey = key;
    panel.hidden = false;
    const keys = visible.map((row) => String(row.board_key));
    const index = keys.indexOf(key);
    panel.innerHTML = `<p class="qboard-panel-name">${escapeHtml(tile.name || "")}</p>
      <div class="qboard-legend">${legendHtml(tile.legend || [])}</div>
      ${tile.text ? `<p class="qboard-panel-text">${escapeHtml(tile.text)}</p>` : ""}
      <canvas class="qboard-panel-canvas" width="560" height="280" aria-label="Board"></canvas>
      <div class="qboard-panel-tools">
        <button type="button" data-qboard-pen>Pen</button>
        <button type="button" data-qboard-undo>Undo</button>
        <button type="button" data-qboard-show>Show on board</button>
        <button type="button" data-qboard-step="-1" ${index <= 0 ? "disabled" : ""}>‹ Prev</button>
        <button type="button" data-qboard-step="1" ${index >= keys.length - 1 ? "disabled" : ""}>Next ›</button>
      </div>`;
    const canvas = panel.querySelector("canvas");
    if (!(canvas instanceof HTMLCanvasElement)) return;
    root._openStrokes = new Set();
    const board = bindWhiteboard(canvas, {
      penBtn: panel.querySelector("[data-qboard-pen]"),
      undoBtn: panel.querySelector("[data-qboard-undo]"),
      owner: () => "teacher",
      color: TEACHER_INK,
      onPoints: (points, ended, strokeId) => {
        const ops = strokeOps(canvas, root._openStrokes, points, ended, strokeId, TEACHER_INK);
        if (ops.length) postInk(key, ops);
      },
      onUndo: (stroke) => {
        if (!stroke?.id) return;
        postInk(key, [{ type: "stroke_remove", id: stroke.id }]);
      },
    });
    board.setCollab(true);
    if (typeof board.resetRun === "function") board.resetRun(tile.canvas_view || {});
    board.setCollab(true);
    panel.querySelector("[data-qboard-show]")?.addEventListener("click", () => {
      fetch(
        `/api/live-sessions/${sessionId}/questions/${promptId}/boards/${encodeURIComponent(key)}/show`,
        { method: "POST", credentials: "same-origin", headers: { Accept: "application/json" } }
      ).catch(() => {});
    });
    panel.querySelectorAll("[data-qboard-step]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = keys[index + Number(button.getAttribute("data-qboard-step"))];
        if (next) openTile(next);
      });
    });
  }
  /**
   * Post teacher ink. The pen already painted it locally.
   * @param {string} key
   * @param {Record<string, any>[]} ops
   */
  function postInk(key, ops) {
    fetch(
      `/api/live-sessions/${sessionId}/questions/${promptId}/boards/${encodeURIComponent(key)}/ink`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ ops }),
      }
    ).catch(() => {});
  }
  root.querySelectorAll("[data-qboard-open]").forEach((button) => {
    button.addEventListener("click", () => {
      openTile(button.getAttribute("data-qboard-open") || "");
    });
  });
  const stillVisible = Boolean(panelWasOpen) && visible.some((tile) => tile.board_key === panelWasOpen);
  if (stillVisible && !keepPen) openTile(panelWasOpen);
  if (!stillVisible && panel instanceof HTMLElement) {
    panel.hidden = true;
  }
}

/**
 * Attach dashboards after the teacher question list is painted.
 * @param {string | number} sessionId
 */
export function mountTeacherQuestionBoards(sessionId) {
  const id = String(sessionId || "");
  document.querySelectorAll("[data-qboard-dash]").forEach((slot) => {
    if (slot instanceof HTMLElement) void paintTeacherDash(slot, id);
  });
}
