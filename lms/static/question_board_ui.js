/**
 * Question boards on the live whiteboard (MCK-31).
 *
 * The phone, catch-up phone, and teacher gallery are the existing
 * whiteboard. This module only chooses the question run and posts Submit.
 * Drawing, colours, undo, redo, clear, and the teacher panel stay in
 * live_whiteboard.js and formative_live_board.js.
 */

import {
  bindWhiteboard,
  boardWriteRejected,
  createBoardDeltaPoll,
  createPresenceQueue,
  normalizeBoardPoint,
} from "./live_whiteboard.js";
import { mountTeacherGrid } from "./formative_live_board.js";
import { studentShownMembers } from "./whiteboard_formative.js";

const SIZE_LINE = "Your board is too full to submit. Erase a little and try again.";
const DOWN_LINE = "That didn't go through. Your drawing is safe, so press Submit again.";

/**
 * Presence body for one text label.
 *
 * ``commitEditor`` already passes ``x`` and ``y`` in the 0–1 board
 * (``dataset.nx`` / ``dataset.ny``). Dividing by the canvas size again
 * pins a centered label in the top-left corner.
 *
 * @param {{id?: string, text?: string, x?: number, y?: number}} label
 * @returns {{text_id: string, text: string, x: number, y: number}}
 */
export function questionBoardTextPost(label) {
  return {
    text_id: String(label?.id || ""),
    text: String(label?.text || ""),
    x: Number(label?.x),
    y: Number(label?.y),
  };
}

/** @type {Set<{kick: (force?: boolean) => void}>} */
const polls = new Set();

/**
 * Pull every open question-board poll forward.
 *
 * The live wire already kicks the deck board. Question boards share that tap.
 * @param {boolean} [force] Read even when the card is hidden.
 */
export function kickQuestionBoardPolls(force) {
  for (const poll of polls) {
    if (poll && typeof poll.kick === "function") poll.kick(Boolean(force));
  }
}

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

/**
 * @returns {string}
 */
function batchId() {
  return `b-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 8)}`;
}

/**
 * Show a line under the board.
 * @param {HTMLElement} slot
 * @param {string} line
 * @param {"error" | "status"} [tone]
 */
function showLine(slot, line, tone) {
  let note = slot.querySelector(".qboard-note");
  if (!(note instanceof HTMLElement)) {
    note = document.createElement("p");
    note.className = "qboard-note hint";
    slot.appendChild(note);
  }
  note.textContent = line;
  note.hidden = !line;
  note.classList.toggle("error", tone === "error");
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
 * Toolbar the live student board uses, plus Redo and Clear.
 * @returns {HTMLElement}
 */
function toolBar() {
  const tools = document.createElement("div");
  tools.className = "live-canvas-toolbar";
  tools.setAttribute("role", "toolbar");
  tools.setAttribute("aria-label", "Whiteboard tools");
  tools.innerHTML = [
    '<button type="button" data-tool="pen" class="is-on" aria-pressed="true">Pen</button>',
    '<button type="button" data-tool="text" aria-pressed="false">Text</button>',
    '<button type="button" data-tool="erase">Erase</button>',
    '<button type="button" data-tool="undo">Undo</button>',
    '<button type="button" data-tool="redo">Redo</button>',
    '<button type="button" data-tool="clear">Clear</button>',
  ].join("");
  return tools;
}

/**
 * Show a teacher note under the question card. The live overlay stays
 * on the old question frame; this block sits in the card flow.
 * @param {HTMLElement} slot
 * @param {string} text
 */
function paintBoardFeedback(slot, text) {
  const card = slot.closest("article");
  if (!(card instanceof HTMLElement)) return;
  let block = card.querySelector(".qboard-feedback");
  const note = String(text || "").trim();
  if (!note) {
    if (block instanceof HTMLElement) block.remove();
    return;
  }
  if (!(block instanceof HTMLElement)) {
    block = document.createElement("div");
    block.className = "prompt-feedback qboard-feedback";
    block.setAttribute("role", "note");
    const lead = document.createElement("p");
    lead.className = "prompt-feedback-lead";
    lead.textContent = "Feedback";
    const why = document.createElement("p");
    why.className = "prompt-feedback-why";
    block.append(lead, why);
    card.appendChild(block);
  }
  const why = block.querySelector(".prompt-feedback-why");
  if (why) why.textContent = note;
}

/** @type {Map<string, HTMLElement>} */
const phoneRoots = new Map();

/**
 * Mount one phone board on the live whiteboard sync path.
 * @param {HTMLElement} slot
 * @param {string} key Map key.
 * @param {{read: string, ops: string, submit: string, draft: string | null}} urls
 * @param {{team: boolean}} options Catch-up is never a team board.
 */
async function ensurePhoneBoard(slot, key, urls, options) {
  let root = phoneRoots.get(key);
  if (root && root.isConnected) {
    if (root.parentElement !== slot) slot.appendChild(root);
    return;
  }
  if (root && root._poll) {
    root._poll.stop();
    polls.delete(root._poll);
  }
  root = document.createElement("div");
  root.className = "qboard-phone";
  const text = document.createElement("textarea");
  text.className = "qboard-text";
  text.maxLength = 500;
  text.rows = 2;
  text.hidden = slot.getAttribute("data-qboard-mode") !== "both";
  text.setAttribute("aria-label", "Typed answer");
  const stage = document.createElement("div");
  stage.className = "live-canvas-stage";
  const canvas = document.createElement("canvas");
  canvas.width = 720;
  canvas.height = 360;
  canvas.className = "student-canvas";
  canvas.setAttribute("aria-label", "Whiteboard");
  const image = slot.getAttribute("data-qboard-image") || "";
  if (image) canvas.style.backgroundImage = `url("${image.replace(/"/g, "")}")`;
  canvas.style.backgroundSize = "contain";
  canvas.style.backgroundRepeat = "no-repeat";
  canvas.style.backgroundPosition = "center";
  stage.appendChild(canvas);
  const tools = toolBar();
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "prompt-submit";
  submit.textContent = options.team ? "Submit our board" : "Submit";
  submit.disabled = Boolean(options.team);
  const submitRow = document.createElement("div");
  submitRow.className = "qboard-tools";
  submitRow.appendChild(submit);
  root.append(text, tools, stage, submitRow);
  phoneRoots.set(key, root);
  slot.appendChild(root);

  let since = 0;
  let runKey = "";
  let viewer = "";
  const clearBtn = tools.querySelector('[data-tool="clear"]');
  const eraseBtn = tools.querySelector('[data-tool="erase"]');
  if (clearBtn instanceof HTMLButtonElement && eraseBtn instanceof HTMLButtonElement) {
    clearBtn.addEventListener("click", () => eraseBtn.click());
  }

  /**
   * Paint a presence reply or a since-seq poll.
   * @param {any} data
   */
  function applyIncoming(data) {
    if (!data || typeof data !== "object") return;
    if (data.run_key) runKey = String(data.run_key);
    if (data.viewer_id != null && data.viewer_id !== "") viewer = String(data.viewer_id);
    const members = studentShownMembers(data.formative?.members);
    if (members.length) {
      board.setFormative?.({
        members,
        alwaysShowNames: false,
        projectorShowNames: false,
      });
    }
    if (typeof data.board_seq === "number") since = data.board_seq;
    board.applyDelta?.(data);
    if (data.status_line) showLine(root, String(data.status_line), "status");
    if (data.text && !text.value) text.value = String(data.text);
    if (options.team && typeof data.submit_ready === "boolean") {
      submit.disabled = !data.submit_ready;
    }
    if (typeof data.feedback === "string") {
      paintBoardFeedback(slot, data.feedback);
      const promptId = slot.getAttribute("data-qboard-prompt") || "";
      if (promptId && root._feedback !== data.feedback) {
        root._feedback = data.feedback;
        window.dispatchEvent(
          new CustomEvent("lloves-question-board-note", {
            detail: { promptId, feedback: data.feedback },
          })
        );
      }
    }
    const locked = Boolean(data.read_only) || slot.getAttribute("data-qboard-readonly") === "1";
    if (locked) {
      board.setEnabled?.(false);
      submit.hidden = true;
      text.readOnly = true;
      poll.stop();
    }
  }

  const queue = createPresenceQueue({
    /**
     * @param {Record<string, any>} body
     * @returns {Promise<any>}
     */
    send(body) {
      return fetch(
        urls.ops,
        studentInit({
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            ...body,
            since,
            run_key: runKey,
            client_batch_id: batchId(),
          }),
        })
      ).then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (boardWriteRejected(res.status, data) || res.status === 409) {
          since = 0;
          poll.kick(true);
          return null;
        }
        if (!res.ok) return null;
        return data;
      });
    },
    /**
     * @param {any} data
     */
    onReply(data) {
      applyIncoming(data);
    },
  });

  /**
   * Queue a stroke batch the way the live student board does.
   * @param {{x: number, y: number}[]} points
   * @param {boolean} ended
   * @param {string} strokeId
   */
  function postPoints(points, ended, strokeId) {
    const samples = [];
    for (const point of points || []) {
      const norm = normalizeBoardPoint(canvas, point);
      if (norm) samples.push([norm.x, norm.y]);
    }
    if (!samples.length && !ended) return;
    const tail = samples[samples.length - 1] || [0, 0];
    queue.push({
      points: samples,
      x: tail[0],
      y: tail[1],
      stroke_id: strokeId || undefined,
      ended: Boolean(ended),
    });
    submit.disabled = false;
  }

  const board = bindWhiteboard(canvas, {
    penBtn: tools.querySelector('[data-tool="pen"]'),
    textBtn: tools.querySelector('[data-tool="text"]'),
    eraseBtn,
    undoBtn: tools.querySelector('[data-tool="undo"]'),
    redoBtn: tools.querySelector('[data-tool="redo"]'),
    formative: {
      enabled: true,
      members: [],
      isTeacher: false,
      isPhone: true,
      alwaysShowNames: false,
      projectorShowNames: false,
    },
    owner: () => viewer || "student",
    onText: (label) => {
      queue.push(questionBoardTextPost(label));
      submit.disabled = false;
    },
    onPoints: (points, ended, strokeId) => postPoints(points, ended, strokeId),
    onUndo: (stroke) => {
      if (!stroke?.id) return;
      queue.push({ op: "stroke_remove", stroke_id: stroke.id });
    },
    onClear: (gone) => {
      for (const stroke of gone || []) {
        if (!stroke?.id || !stroke.mine) continue;
        queue.push({ op: "stroke_remove", stroke_id: stroke.id });
      }
    },
    onRedo: (stroke) => {
      if (!stroke?.id) return;
      postPoints(stroke.points || [], true, stroke.id);
    },
  });
  board.setCollab(true);

  const poll = createBoardDeltaPoll({
    isShown: () => slot.isConnected && !slot.hidden,
    poll() {
      const query = since ? `?since=${since}` : "";
      return fetch(urls.read + query, studentInit({ credentials: "same-origin", headers: { Accept: "application/json" } })).then(
        async (res) => {
          if (res.status === 429 || res.status === 503 || res.status >= 500) {
            return { status: res.status, busy: true };
          }
          const data = await res.json().catch(() => null);
          if (!res.ok) return { status: res.status, busy: res.status >= 500 };
          return { ...(data || {}), status: res.status };
        }
      );
    },
    onDelta: applyIncoming,
  });
  polls.add(poll);
  root._poll = poll;

  submit.addEventListener("click", async () => {
    const response = await fetch(
      urls.submit,
      studentInit({
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text: text.value }),
      })
    );
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
    since = 0;
    poll.kick(true);
    const promptId = slot.getAttribute("data-qboard-prompt") || "";
    if (promptId) {
      window.dispatchEvent(
        new CustomEvent("lloves-question-board-submitted", { detail: { promptId } })
      );
    }
  });
  text.addEventListener("input", () => {
    const ink = board.getInk?.().strokes || [];
    submit.disabled = !text.value.trim() && !ink.length;
    if (options.team && urls.draft) {
      window.clearTimeout(root._draftTimer);
      root._draftTimer = window.setTimeout(() => {
        fetch(
          urls.draft,
          studentInit({
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text.value }),
          })
        ).catch(() => {});
      }, 2000);
    }
  });
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

/**
 * The live teacher gallery, scoped to one question so ids are not repeated.
 * @returns {string}
 */
function teacherGalleryHtml() {
  return `<header class="wb-live-head">
      <div>
        <h2 class="wb-live-title">Live whiteboard</h2>
        <p class="wb-live-context" data-wb-part="context"></p>
      </div>
      <div class="wb-live-controls">
        <label>Tiles/row
          <select data-wb-part="cols" aria-label="Tiles per row">
            <option value="3">3</option>
            <option value="4" selected>4</option>
            <option value="5">5</option>
          </select>
        </label>
        <span class="wb-live-pill">Live</span>
      </div>
    </header>
    <div class="wb-live-filters" data-wb-part="filters" role="tablist"></div>
    <p class="wb-live-summary" data-wb-part="summary"></p>
    <p class="wb-live-projected" data-wb-part="projected"></p>
    <div class="wb-live-body">
      <div class="wb-live-grid" data-wb-part="grid"></div>
      <aside class="wb-live-panel" data-wb-part="panel" hidden>
        <div class="wb-live-panel-bar">
          <button type="button" data-wb-part="prev" aria-label="Previous board">←</button>
          <button type="button" data-wb-part="next" aria-label="Next board">→</button>
          <strong data-wb-part="panel-name">Board</strong>
          <span class="wb-live-team" data-wb-part="panel-team"></span>
          <button type="button" data-wb-part="close" aria-label="Close board">×</button>
        </div>
        <p class="wb-live-age" data-wb-part="panel-age"></p>
        <div class="qboard-waits" data-wb-part="waits"></div>
        <label class="qboard-note-label">Feedback
          <textarea data-wb-part="note" rows="3" maxlength="500" aria-label="Feedback for this board"></textarea>
        </label>
        <button type="button" data-wb-part="save-note">Save feedback</button>
        <p class="qboard-note hint" data-wb-part="note-status" hidden></p>
        <div class="wb-live-panel-tools" role="toolbar" aria-label="Whiteboard tools">
          <button type="button" data-wb-part="pen" class="is-on" aria-pressed="true">Pen</button>
          <button type="button" data-wb-part="text" aria-pressed="false">Text</button>
          <button type="button" data-wb-part="erase">Erase</button>
          <button type="button" data-wb-part="undo">Undo</button>
          <button type="button" data-wb-part="redo">Redo</button>
          <button type="button" data-wb-part="show" class="wb-live-show">Show to class</button>
          <button type="button" data-wb-part="clear">Clear board</button>
        </div>
        <div class="wb-formative-legend" data-wb-part="legend" hidden></div>
        <div class="live-canvas-stage wb-live-panel-stage">
          <canvas class="wb-live-panel-canvas" data-wb-part="panel-canvas" width="720" height="360" aria-label="Whiteboard"></canvas>
        </div>
      </aside>
    </div>`;
}

/** @type {Map<string, {slot: HTMLElement, stop: () => void}>} */
const galleries = new Map();

/**
 * Waiting students use the same Skip button the other group questions use.
 * @param {HTMLElement} root
 * @param {any} tile
 */
function paintWaits(root, tile) {
  const host = root.querySelector("[data-wb-part='waits']");
  if (!(host instanceof HTMLElement)) return;
  host.replaceChildren();
  const waits = Array.isArray(tile?.question_waits) ? tile.question_waits : [];
  if (!waits.length) return;
  const list = document.createElement("ul");
  list.className = "question-skip-list";
  const itemId = Number(tile?.live_item_id) || 0;
  for (const row of waits) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "question-skip-name";
    name.textContent = String(row?.name || "");
    li.appendChild(name);
    if (row?.skipped) {
      const status = document.createElement("span");
      status.className = "question-skip-status";
      status.textContent = " Skipped — catch-up required.";
      li.appendChild(status);
    } else if (itemId) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary live-q-btn";
      button.dataset.questionSkip = String(itemId);
      button.dataset.studentId = String(Number(row?.student_id) || 0);
      button.dataset.teamId = String(Number(tile?.team_id) || 0);
      const who = name.textContent || "this student";
      button.textContent = "Skip for this question";
      button.setAttribute("aria-label", `Skip ${who} for this question`);
      li.appendChild(document.createTextNode(" "));
      li.appendChild(button);
    }
    list.appendChild(li);
  }
  host.appendChild(list);
}

/**
 * Save the side-panel note onto the open board.
 * @param {HTMLElement} root
 * @param {(key: string) => string} noteUrl
 */
function wireBoardNote(root, noteUrl) {
  /** @type {any} */
  let openTile = null;
  const save = root.querySelector("[data-wb-part='save-note']");
  if (!(save instanceof HTMLButtonElement)) return { open() {} };
  save.addEventListener("click", async () => {
    const key = String(openTile?.copy_run_key ? openTile.board_key : openTile?.board_key || "");
    if (!key) return;
    const field = root.querySelector("[data-wb-part='note']");
    const text = field instanceof HTMLTextAreaElement ? field.value : "";
    const status = root.querySelector("[data-wb-part='note-status']");
    save.disabled = true;
    try {
      const response = await fetch(noteUrl(key), {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text }),
      });
      const payload = await response.json().catch(() => ({}));
      if (status instanceof HTMLElement) {
        status.hidden = false;
        status.textContent = response.ok ? "Feedback saved." : String(payload.error || "That didn't save.");
      }
    } finally {
      save.disabled = false;
    }
  });
  return {
    /**
     * @param {any} tile
     */
    open(tile) {
      openTile = tile;
      const field = root.querySelector("[data-wb-part='note']");
      if (field instanceof HTMLTextAreaElement) field.value = String(tile?.feedback || "");
      paintWaits(root, tile);
    },
  };
}

/**
 * Paint the existing teacher whiteboard for one question.
 * @param {HTMLElement} slot
 * @param {string} sessionId
 */
function paintTeacherDash(slot, sessionId) {
  const promptId = String(slot.getAttribute("data-qboard-dash") || "");
  if (!promptId || !sessionId) return;
  const held = galleries.get(promptId);
  if (held && held.slot === slot && slot.querySelector(".wb-live")) return;
  if (held) held.stop();
  const root = document.createElement("section");
  root.className = "wb-live";
  root.innerHTML = teacherGalleryHtml();
  slot.replaceChildren(root);
  const notes = wireBoardNote(
    root,
    (key) =>
      `/api/live-sessions/${sessionId}/questions/${promptId}/boards/${encodeURIComponent(key)}/note`
  );
  const mounted = mountTeacherGrid(root, {
    sessionId,
    /**
     * @param {any} tile
     */
    onOpen(tile) {
      notes.open(tile);
    },
    /**
     * @param {string} id
     * @param {string} cursors
     * @param {string} tap
     * @returns {string}
     */
    galleryUrl(id, cursors, tap) {
      return `/api/live-sessions/${id}/questions/${promptId}/boards?cursors=${cursors}${tap}`;
    },
    /**
     * @param {string} id
     * @param {string} key
     * @param {string} [suffix]
     * @returns {string}
     */
    boardUrl(id, key, suffix) {
      const base = `/api/live-sessions/${id}/questions/${promptId}/boards/${encodeURIComponent(key)}`;
      if (suffix === "/feedback") return `${base}/ink`;
      return `${base}${suffix || ""}`;
    },
  });
  galleries.set(promptId, {
    slot,
    stop() {
      mounted?.stop?.();
    },
  });
}

/**
 * Lesson Slides submissions. Same gallery and side panel, drawing left as stored.
 * @param {HTMLElement} slot
 * @param {{classId: string, runKey: string, questionKey: string}} options
 * @returns {{stop: () => void}}
 */
export function mountSubmissionDashboard(slot, options) {
  const classId = String(options?.classId || "");
  const runKey = String(options?.runKey || "");
  const questionKey = String(options?.questionKey || "");
  const root = document.createElement("section");
  root.className = "wb-live";
  root.innerHTML = teacherGalleryHtml();
  slot.replaceChildren(root);
  const notes = wireBoardNote(
    root,
    (key) => `/api/classes/${classId}/lesson-slides/boards/${encodeURIComponent(key)}`
  );
  const mounted = mountTeacherGrid(root, {
    sessionId: classId,
    readOnly: true,
    /**
     * @param {any} tile
     */
    onOpen(tile) {
      notes.open(tile);
    },
    /**
     * @returns {string}
     */
    galleryUrl() {
      const params = new URLSearchParams({ run_key: runKey, question_key: questionKey });
      return `/api/classes/${classId}/lesson-slides/boards?${params.toString()}`;
    },
    /**
     * @param {string} _id
     * @param {string} key
     * @returns {string}
     */
    boardUrl(_id, key) {
      return `/api/classes/${classId}/lesson-slides/boards/${encodeURIComponent(key)}`;
    },
  });
  return {
    stop() {
      mounted?.stop?.();
    },
  };
}

/**
 * Attach dashboards after the teacher question list is painted.
 * @param {string | number} sessionId
 */
export function mountTeacherQuestionBoards(sessionId) {
  const id = String(sessionId || "");
  document.querySelectorAll("[data-qboard-dash]").forEach((slot) => {
    if (slot instanceof HTMLElement) paintTeacherDash(slot, id);
  });
}
