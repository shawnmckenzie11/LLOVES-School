/**
 * Question-board phone and teacher tiles (MCK-31).
 *
 * The drawing surface is the existing whiteboard. This module only mounts
 * it on a poll and posts Submit.
 */

import { bindWhiteboard, normalizeBoardPoint } from "./live_whiteboard.js";

const SIZE_LINE = "Your board is too full to submit. Erase a little and try again.";
const DOWN_LINE = "That didn't go through. Your drawing is safe, so press Submit again.";

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
 * @param {HTMLElement} slot
 * @param {string} line
 */
function showLine(slot, line) {
  let note = slot.querySelector(".qboard-line");
  if (!(note instanceof HTMLElement)) {
    note = document.createElement("p");
    note.className = "qboard-line";
    slot.appendChild(note);
  }
  note.textContent = line;
  note.hidden = !line;
}

/**
 * Mount or reuse the student board inside a question card.
 * @param {HTMLElement} slot
 */
async function ensureStudentBoard(slot) {
  const promptId = String(slot.getAttribute("data-qboard-prompt") || "");
  if (!promptId) return;
  let held = studentBoards.get(promptId);
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
    submit.textContent = slot.getAttribute("data-qboard-team") === "1" ? "Submit our board" : "Submit";
    tools.appendChild(submit);
    const line = document.createElement("p");
    line.className = "qboard-line";
    line.hidden = true;
    root.append(text, canvas, line, tools);
    const image = slot.getAttribute("data-qboard-image") || "";
    if (image) canvas.style.backgroundImage = `url("${image.replace(/"/g, "")}")`;
    const started = new Set();
    const board = bindWhiteboard(canvas, {
      owner: () => "student",
      onPoints: (points, ended, strokeId) => {
        const normalized = (Array.isArray(points) ? points : [])
          .map((point) => normalizeBoardPoint(canvas, point))
          .filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y))
          .map((point) => [point.x, point.y]);
        const ops = [];
        if (normalized.length) {
          const type = started.has(strokeId) ? "pts_append" : "stroke_add";
          started.add(strokeId);
          ops.push({ type, id: strokeId, points: normalized });
        }
        if (ended) {
          ops.push({ type: "stroke_end", id: strokeId });
          started.delete(strokeId);
        }
        if (!ops.length) return;
        const body = { ops };
        fetch(`/api/student/question-board/${promptId}/ops`, studentInit({
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(body),
        })).catch(() => {});
        submit.disabled = false;
      },
    });
    submit.addEventListener("click", async () => {
      const response = await fetch(`/api/student/question-board/${promptId}/submit`, studentInit({
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text: text.value }),
      }));
      const payload = await response.json().catch(() => ({}));
      if (response.status === 413) {
        showLine(root, SIZE_LINE);
        return;
      }
      if (response.status === 503) {
        showLine(root, DOWN_LINE);
        return;
      }
      if (!response.ok) {
        showLine(root, String(payload.error || "That didn't go through. Your drawing is safe, so press Submit again."));
        return;
      }
      showLine(root, "");
      const fresh = await fetch(`/api/student/question-board/${promptId}`, studentInit({
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }));
      const data = await fresh.json().catch(() => ({}));
      if (data.status_line) showLine(root, String(data.status_line));
    });
    text.addEventListener("input", () => {
      submit.disabled = !text.value.trim();
      if (slot.getAttribute("data-qboard-team") === "1") {
        window.clearTimeout(root._draftTimer);
        root._draftTimer = window.setTimeout(() => {
          fetch(`/api/student/question-board/${promptId}/draft`, studentInit({
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text.value }),
          })).catch(() => {});
        }, 2000);
      }
    });
    held = { root, board };
    studentBoards.set(promptId, held);
    const res = await fetch(`/api/student/question-board/${promptId}`, studentInit({
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }));
    const data = await res.json().catch(() => ({}));
    if (data.canvas_view && typeof board.resetRun === "function") board.resetRun(data.canvas_view);
    if (data.text) text.value = String(data.text);
    if (data.status_line) showLine(root, String(data.status_line));
    if (data.read_only) {
      board.setEnabled?.(false);
      submit.hidden = true;
      text.readOnly = true;
    }
    submit.disabled = !(data.text || (data.canvas_view && (data.canvas_view.strokes || []).length));
  }
  if (held.root.parentElement !== slot) slot.appendChild(held.root);
}

/**
 * Attach boards after the student question stack is painted.
 */
export function mountStudentQuestionBoards() {
  document.querySelectorAll("[data-qboard-prompt]").forEach((slot) => {
    if (slot instanceof HTMLElement) void ensureStudentBoard(slot);
  });
}

/** @type {Map<string, HTMLElement>} */
const teacherRoots = new Map();

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
    teacherRoots.set(promptId, root);
  }
  const label = data.label === "Typing and whiteboard" ? "poll · Typing and whiteboard" : "poll · Whiteboard";
  const filters = (data.filters || [])
    .map(
      (chip) =>
        `<button type="button" class="qboard-filter" data-qboard-filter="${chip.id}">${chip.label} ${chip.count}</button>`
    )
    .join("");
  const tiles = (data.tiles || [])
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
  root.innerHTML = `<header class="qboard-head"><span class="qboard-chip">${escapeHtml(label)}</span><span class="qboard-stem">${escapeHtml(
    data.stem || ""
  )}</span><span class="qboard-summary">${escapeHtml(data.summary || "")}</span></header>
    <div class="qboard-filters">${filters}</div>
    <div class="qboard-layout"><div class="qboard-tiles">${tiles}</div><aside class="qboard-panel" hidden></aside></div>`;
  if (root.parentElement !== slot) slot.appendChild(root);
  root.querySelectorAll("[data-qboard-thumb]").forEach((canvas) => {
    const key = canvas.getAttribute("data-qboard-thumb");
    const tile = (data.tiles || []).find((row) => row.board_key === key);
    if (canvas instanceof HTMLCanvasElement && tile) paintBoardThumb(canvas, tile.canvas_view || {});
  });
  const panel = root.querySelector(".qboard-panel");
  root.querySelectorAll("[data-qboard-open]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.getAttribute("data-qboard-open") || "";
      const tile = (data.tiles || []).find((row) => row.board_key === key);
      if (!(panel instanceof HTMLElement) || !tile) return;
      panel.hidden = false;
      const keys = (data.tiles || []).map((row) => String(row.board_key));
      const index = keys.indexOf(key);
      panel.innerHTML = `<p class="qboard-panel-name">${escapeHtml(tile.name || "")}</p>
        ${tile.text ? `<p class="qboard-panel-text">${escapeHtml(tile.text)}</p>` : ""}
        <canvas class="qboard-panel-canvas" width="280" height="180" aria-label="Board"></canvas>
        <div class="qboard-panel-tools">
          <button type="button" data-qboard-pen="${escapeHtml(key)}">Pen</button>
          <button type="button" data-qboard-undo="${escapeHtml(key)}">Undo</button>
          <button type="button" data-qboard-show="${escapeHtml(key)}">Show on board</button>
          <button type="button" data-qboard-step="-1" ${index <= 0 ? "disabled" : ""}>‹ Prev</button>
          <button type="button" data-qboard-step="1" ${index >= keys.length - 1 ? "disabled" : ""}>Next ›</button>
        </div>`;
      const canvas = panel.querySelector("canvas");
      if (canvas instanceof HTMLCanvasElement) paintBoardThumb(canvas, tile.canvas_view || {});
      panel.querySelector("[data-qboard-show]")?.addEventListener("click", () => {
        fetch(
          `/api/live-sessions/${sessionId}/questions/${promptId}/boards/${encodeURIComponent(key)}/show`,
          { method: "POST", credentials: "same-origin", headers: { Accept: "application/json" } }
        ).catch(() => {});
      });
      panel.querySelector("[data-qboard-pen]")?.addEventListener("click", async () => {
        await fetch(
          `/api/live-sessions/${sessionId}/questions/${promptId}/boards/${encodeURIComponent(key)}/ink`,
          {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              ops: [
                {
                  type: "stroke_add",
                  id: `mark-${key}`,
                  points: [[0.2, 0.7], [0.8, 0.3]],
                  color: "#16a34a",
                },
                { type: "stroke_end", id: `mark-${key}` },
              ],
            }),
          }
        );
        paintTeacherDash(slot, sessionId);
      });
      panel.querySelectorAll("[data-qboard-step]").forEach((button) => {
        button.addEventListener("click", () => {
          const next = keys[index + Number(button.getAttribute("data-qboard-step"))];
          const opener = root.querySelector(`[data-qboard-open="${CSS.escape(next || "")}"]`);
          if (opener instanceof HTMLElement) opener.click();
        });
      });
    });
  });
}

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
 * Attach dashboards after the teacher question list is painted.
 * @param {string | number} sessionId
 */
export function mountTeacherQuestionBoards(sessionId) {
  const id = String(sessionId || "");
  document.querySelectorAll("[data-qboard-dash]").forEach((slot) => {
    if (slot instanceof HTMLElement) void paintTeacherDash(slot, id);
  });
}
