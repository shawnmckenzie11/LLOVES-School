/**
 * Question boards on the live whiteboard (MCK-31).
 *
 * The phone, catch-up phone, and teacher gallery are the existing
 * whiteboard. This module only chooses the question run and posts Submit.
 * Drawing, colours, undo, redo, clear, and the teacher panel stay in
 * live_whiteboard.js and formative_live_board.js. After a live Submit,
 * that same board locks. Catch-up still allows a later edit.
 */

import {
  bindWhiteboard,
  boardWriteRejected,
  createBoardDeltaPoll,
  createPresenceQueue,
  normalizeBoardPoint,
} from "./live_whiteboard.js";
import { applyCanvasBoardBackground } from "./board_background.js";
import { mountTeacherGrid } from "./formative_live_board.js";
import { STUDENT_BOARD_FEEDBACK_LEAD } from "./live_question_card.js";
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
 * Student phone toolbar: Pen, Line, Text, and Undo only (MCK-504).
 * @returns {HTMLElement}
 */
function toolBar() {
  const tools = document.createElement("div");
  tools.className = "live-canvas-toolbar live-canvas-toolbar-vertical";
  tools.setAttribute("role", "toolbar");
  tools.setAttribute("aria-label", "Whiteboard tools");
  tools.innerHTML = [
    '<button type="button" data-tool="pen" class="is-on" aria-pressed="true">Pen</button>',
    '<button type="button" data-tool="line" aria-pressed="false" title="Line">Line</button>',
    '<button type="button" data-tool="text" aria-pressed="false">Text</button>',
    '<button type="button" data-tool="undo" title="Nothing to undo" disabled>Undo</button>',
  ].join("");
  return tools;
}

/**
 * Show a teacher note under the question card. The live overlay stays
 * on the old question frame; this block sits in the card flow.
 * @param {HTMLElement} slot
 * @param {string} text
 */
export function paintBoardFeedback(slot, text) {
  const note = String(text || "").trim();
  const phoneSlot = slot.querySelector(".qboard-phone-feedback");
  const card = slot.closest("article");
  const insertBefore = slot;
  let block =
    (phoneSlot instanceof HTMLElement && phoneSlot.querySelector(".qboard-feedback")) ||
    card?.querySelector(".qboard-feedback") ||
    null;
  if (!note) {
    if (block instanceof HTMLElement) block.remove();
    if (phoneSlot instanceof HTMLElement) {
      phoneSlot.hidden = true;
      phoneSlot.replaceChildren();
    }
    return;
  }
  if (!(block instanceof HTMLElement)) {
    block = document.createElement("div");
    block.className = "prompt-feedback qboard-feedback student-board-feedback-strip";
    block.setAttribute("role", "note");
    const line = document.createElement("p");
    line.className = "student-board-feedback-line";
    const lead = document.createElement("span");
    lead.className = "prompt-feedback-lead";
    lead.textContent = STUDENT_BOARD_FEEDBACK_LEAD;
    line.append(lead, ` ${note}`);
    block.append(line);
    if (phoneSlot instanceof HTMLElement) {
      phoneSlot.replaceChildren(block);
      phoneSlot.hidden = false;
    } else if (insertBefore instanceof HTMLElement) {
      insertBefore.insertAdjacentElement("beforebegin", block);
    } else if (card instanceof HTMLElement) {
      card.appendChild(block);
    }
    return;
  }
  const line = block.querySelector(".student-board-feedback-line");
  if (line instanceof HTMLElement) {
    line.innerHTML = `<span class="prompt-feedback-lead">${STUDENT_BOARD_FEEDBACK_LEAD}</span> ${escapeBoardFeedback(note)}`;
  }
}

/**
 * Escape text for a feedback line on the student phone.
 * @param {string} value
 * @returns {string}
 */
function escapeBoardFeedback(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Freeze a submitted question phone on the shared whiteboard.
 *
 * Closes an open text box, hides Submit, and stops pen, text, undo,
 * redo, and clear. Ink already on the canvas stays visible.
 *
 * @param {{
 *   board?: { lockEditing?: () => void, closeTextEntry?: () => void },
 *   submit?: HTMLButtonElement | null,
 *   typed?: HTMLTextAreaElement | null,
 *   tools?: ParentNode | null,
 * }} parts
 */
export function lockSubmittedQuestionBoard(parts) {
  const board = parts?.board;
  if (board && typeof board.closeTextEntry === "function") board.closeTextEntry();
  if (board && typeof board.lockEditing === "function") board.lockEditing();
  const submit = parts?.submit;
  if (submit) {
    submit.hidden = true;
    submit.disabled = true;
  }
  const typed = parts?.typed;
  if (typed) typed.readOnly = true;
  const buttons = parts?.tools?.querySelectorAll?.("button") || [];
  for (const btn of buttons) {
    if (btn instanceof HTMLButtonElement) btn.disabled = true;
  }
}

/** @type {Map<string, HTMLElement>} */
const phoneRoots = new Map();

/**
 * Mount one phone board on the live whiteboard sync path.
 *
 * A live-sync paint replaces the question card, which detaches this
 * phone. The same node is moved into the new card. Building another
 * canvas would blank the drawing on every poll.
 *
 * @param {HTMLElement} slot
 * @param {string} key Map key.
 * @param {{read: string, ops: string, submit: string, draft: string | null}} urls
 * @param {{team: boolean, lockOnSubmit?: boolean}} options Catch-up is never a team board.
 */
async function ensurePhoneBoard(slot, key, urls, options) {
  let root = phoneRoots.get(key);
  if (root instanceof HTMLElement && root.querySelector("canvas.student-canvas")) {
    const place = root._place;
    if (place) place.slot = slot;
    if (root.parentElement !== slot) slot.appendChild(root);
    const typed = root.querySelector(".qboard-text");
    if (typed instanceof HTMLTextAreaElement) {
      typed.hidden = slot.getAttribute("data-qboard-mode") !== "both";
    }
    const canvas = root.querySelector("canvas.student-canvas");
    if (canvas instanceof HTMLCanvasElement) {
      applyCanvasBoardBackground(canvas, slot.getAttribute("data-qboard-image") || "");
    }
    const initial = String(slot.getAttribute("data-qboard-initial-feedback") || "");
    if (initial) paintBoardFeedback(slot, initial);
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
  const feedbackSlot = document.createElement("div");
  feedbackSlot.className = "qboard-phone-feedback";
  feedbackSlot.hidden = true;
  const stage = document.createElement("div");
  stage.className = "live-canvas-stage";
  const canvas = document.createElement("canvas");
  canvas.width = 720;
  canvas.height = 360;
  canvas.className = "student-canvas";
  canvas.setAttribute("aria-label", "Whiteboard");
  applyCanvasBoardBackground(canvas, slot.getAttribute("data-qboard-image") || "");
  const tools = toolBar();
  stage.append(tools, canvas);
  const workspace = document.createElement("div");
  workspace.className = "live-canvas-workspace";
  workspace.append(stage);
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "prompt-submit";
  submit.textContent = options.team ? "Submit our board" : "Submit";
  submit.disabled = Boolean(options.team);
  const submitRow = document.createElement("div");
  submitRow.className = "qboard-tools";
  submitRow.appendChild(submit);
  root.append(text, feedbackSlot, workspace, submitRow);
  /** Current card. Live sync swaps the slot; the poll and notes follow it. */
  const place = { slot };
  root._place = place;
  phoneRoots.set(key, root);
  slot.appendChild(root);
  const initialNote = String(slot.getAttribute("data-qboard-initial-feedback") || "");
  if (initialNote) paintBoardFeedback(slot, initialNote);

  let since = 0;
  let runKey = "";
  let viewer = "";
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
    if (options.team && typeof data.submit_ready === "boolean" && !root._editingLocked) {
      submit.disabled = !data.submit_ready;
    }
    if (typeof data.feedback === "string") {
      paintBoardFeedback(place.slot, data.feedback);
      const promptId = place.slot.getAttribute("data-qboard-prompt") || "";
      if (promptId && root._feedback !== data.feedback) {
        root._feedback = data.feedback;
        window.dispatchEvent(
          new CustomEvent("lloves-question-board-note", {
            detail: { promptId, feedback: data.feedback },
          })
        );
      }
    }
    if (typeof data.image_url === "string") {
      applyCanvasBoardBackground(canvas, data.image_url);
      place.slot.setAttribute("data-qboard-image", data.image_url);
    }
    if (data.editing_locked) freezePhone();
    const locked = Boolean(data.read_only) || place.slot.getAttribute("data-qboard-readonly") === "1";
    if (locked) {
      board.setEnabled?.(false);
      submit.hidden = true;
      text.readOnly = true;
      poll.stop();
    }
    if (root._editingLocked) {
      submit.hidden = true;
      submit.disabled = true;
    }
  }

  /**
   * Hide Submit and stop every edit control on this phone.
   */
  function freezePhone() {
    root._editingLocked = true;
    lockSubmittedQuestionBoard({ board, submit, typed: text, tools });
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
    if (root._editingLocked) return;
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
    if (!root._editingLocked) submit.disabled = false;
  }

  const board = bindWhiteboard(canvas, {
    penBtn: tools.querySelector('[data-tool="pen"]'),
    lineBtn: tools.querySelector('[data-tool="line"]'),
    textBtn: tools.querySelector('[data-tool="text"]'),
    undoBtn: tools.querySelector('[data-tool="undo"]'),
    color: "#12202e",
    fixedInkColor: true,
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
      if (root._editingLocked) return;
      queue.push(questionBoardTextPost(label));
      submit.disabled = false;
    },
    onPoints: (points, ended, strokeId) => postPoints(points, ended, strokeId),
    onUndo: (stroke) => {
      if (root._editingLocked || !stroke?.id) return;
      queue.push({ op: "stroke_remove", stroke_id: stroke.id });
    },
    onClear: (gone) => {
      if (root._editingLocked) return;
      for (const stroke of gone || []) {
        if (!stroke?.id || !stroke.mine) continue;
        queue.push({ op: "stroke_remove", stroke_id: stroke.id });
      }
    },
    onRedo: (stroke) => {
      if (root._editingLocked || !stroke?.id) return;
      postPoints(stroke.points || [], true, stroke.id);
    },
  });
  board.setCollab(true);

  const poll = createBoardDeltaPoll({
    isShown: () => place.slot.isConnected && !place.slot.hidden,
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
    if (root._editingLocked) return;
    if (options.lockOnSubmit && typeof board.closeTextEntry === "function") {
      board.closeTextEntry();
    }
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
    if (options.lockOnSubmit || payload.editing_locked) freezePhone();
    const fallback = options.lockOnSubmit ? "Submitted." : "Submitted. You can still make changes.";
    showLine(root, String(payload.status_line || fallback), "status");
    since = 0;
    poll.kick(true);
    const promptId = place.slot.getAttribute("data-qboard-prompt") || "";
    if (promptId) {
      window.dispatchEvent(
        new CustomEvent("lloves-question-board-submitted", { detail: { promptId } })
      );
    }
  });
  text.addEventListener("input", () => {
    if (root._editingLocked) return;
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
    lockOnSubmit: true,
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
    void ensurePhoneBoard(slot, `cu:${itemId}`, boardUrls("catchup", itemId, token), {
      team: false,
      lockOnSubmit: false,
    });
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
    <p class="wb-live-summary" data-wb-part="summary"></p>
    <p class="wb-live-projected" data-wb-part="projected"></p>
    <div class="wb-live-body">
      <div class="wb-live-grid" data-wb-part="grid"></div>
      <aside class="wb-live-panel" data-wb-part="panel" hidden>
        <div class="wb-formative-legend" data-wb-part="legend" hidden></div>
        <div class="wb-live-open">
          <div class="wb-live-board">
            <div class="live-canvas-stage wb-live-panel-stage">
              <canvas class="wb-live-panel-canvas" data-wb-part="panel-canvas" width="720" height="360" aria-label="Whiteboard"></canvas>
            </div>
          </div>
          <div class="wb-live-feedback" data-wb-part="feedback">
            <div class="wb-live-panel-bar">
              <div class="wb-live-panel-who">
                <strong data-wb-part="panel-name">Board</strong>
                <span class="wb-live-team" data-wb-part="panel-team" hidden></span>
                <span class="wb-live-panel-status" data-wb-part="panel-status"></span>
              </div>
              <div class="wb-live-panel-nav">
                <button type="button" data-wb-part="prev" aria-label="Previous board">←</button>
                <button type="button" data-wb-part="next" aria-label="Next board">→</button>
                <button type="button" data-wb-part="collapse" class="wb-live-collapse" aria-label="Collapse feedback" aria-expanded="true">›</button>
                <button type="button" data-wb-part="close" aria-label="Close board">×</button>
              </div>
            </div>
            <div class="wb-live-panel-tools" role="toolbar" aria-label="Whiteboard tools">
              <button type="button" data-wb-part="pen" class="is-on wb-live-pen" aria-pressed="true">Pen</button>
              <button type="button" data-wb-part="undo" title="Nothing to undo" disabled>Undo</button>
              <button type="button" data-wb-part="clear">Clear my ink</button>
              <button type="button" data-wb-part="text" hidden aria-pressed="false">Text</button>
              <button type="button" data-wb-part="erase" hidden>Erase</button>
              <button type="button" data-wb-part="redo" hidden>Redo</button>
            </div>
            <p class="wb-live-answer" data-wb-part="answer" hidden></p>
            <button type="button" class="wb-live-more" data-wb-part="answer-more" hidden>more</button>
            <div class="qboard-score" data-wb-part="score" hidden></div>
            <label class="qboard-note-label">Feedback
              <textarea data-wb-part="note" rows="2" maxlength="500" aria-label="Feedback for this board"></textarea>
            </label>
            <p class="qboard-note hint" data-wb-part="note-status" hidden></p>
            <div class="qboard-waits" data-wb-part="waits"></div>
            <button type="button" data-wb-part="show" class="wb-live-show" aria-pressed="false">Show on board</button>
            <p class="qboard-show-error" data-wb-part="show-error" role="alert" hidden></p>
          </div>
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
 * Per-student participation steps beside the feedback note.
 *
 * The gallery poll refreshes the numbers. A click posts one step and
 * paints the reply so the teacher does not wait for the next poll.
 *
 * @param {HTMLElement} root
 * @param {string} sessionId
 * @param {string} promptId
 */
function wireBoardScore(root, sessionId, promptId) {
  const host = root.querySelector("[data-wb-part='score']");
  if (!(host instanceof HTMLElement)) return { open() {} };
  const url = `/api/live-sessions/${sessionId}/questions/${promptId}/participation`;

  /**
   * Rows already on the tile, in student order.
   * @param {any} tile
   * @returns {{student_id: number, earned: number, possible: number, label: string}[]}
   */
  function rowsOf(tile) {
    const ids = Array.isArray(tile?.student_ids) ? tile.student_ids : [];
    const stored = Array.isArray(tile?.participation) ? tile.participation : [];
    const byId = new Map(stored.map((row) => [Number(row?.student_id), row]));
    const names = Array.isArray(tile?.member_names) ? tile.member_names : [];
    return ids.map((raw, index) => {
      const studentId = Number(raw) || 0;
      const row = byId.get(studentId) || {};
      const label = names.length
        ? String(names[index] || "")
        : ids.length === 1
          ? String(tile?.name || "")
          : "";
      return {
        student_id: studentId,
        earned: Number(row.earned) || 0,
        possible: Number(row.possible) || 0,
        label,
      };
    }).filter((row) => row.student_id);
  }

  /**
   * @param {any} tile
   */
  function paint(tile) {
    const rows = rowsOf(tile);
    host.hidden = !rows.length;
    const existing = [...host.querySelectorAll("[data-student]")];
    const same = existing.length === rows.length
      && rows.every((row, index) => existing[index].getAttribute("data-student") === String(row.student_id));
    if (same) {
      rows.forEach((row, index) => {
        const value = existing[index].querySelector("[data-earned]");
        if (value) value.textContent = `${row.earned} of ${row.possible}`;
      });
      return;
    }
    host.replaceChildren();
    for (const row of rows) {
      const line = document.createElement("div");
      line.className = "qboard-score-row";
      line.setAttribute("data-student", String(row.student_id));
      if (row.label) {
        const who = document.createElement("span");
        who.textContent = row.label;
        line.appendChild(who);
      }
      const down = document.createElement("button");
      down.type = "button";
      down.className = "secondary";
      down.textContent = "−";
      down.setAttribute("aria-label", "Lower participation");
      const value = document.createElement("span");
      value.className = "qboard-score-value";
      value.setAttribute("data-earned", "");
      value.textContent = `${row.earned} of ${row.possible}`;
      const up = document.createElement("button");
      up.type = "button";
      up.className = "secondary";
      up.textContent = "+";
      up.setAttribute("aria-label", "Raise participation");
      down.addEventListener("click", () => { void nudge(row.student_id, -1, value, down, up); });
      up.addEventListener("click", () => { void nudge(row.student_id, 1, value, down, up); });
      line.append(down, value, up);
      host.appendChild(line);
    }
  }

  /**
   * @param {number} studentId
   * @param {number} delta
   * @param {HTMLElement} value
   * @param {HTMLButtonElement} down
   * @param {HTMLButtonElement} up
   */
  async function nudge(studentId, delta, value, down, up) {
    down.disabled = true;
    up.disabled = true;
    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ student_id: studentId, delta }),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.ok) {
        value.textContent = `${Number(payload.earned) || 0} of ${Number(payload.possible) || 0}`;
      }
    } catch (_err) {
      /* the next gallery poll repaints the stored number */
    } finally {
      down.disabled = false;
      up.disabled = false;
    }
  }

  return {
    /**
     * @param {any} tile
     */
    open(tile) {
      paint(tile || {});
    },
  };
}

/**
 * Save the side-panel note onto the open board.
 * @param {HTMLElement} root
 * @param {(key: string) => string} noteUrl
 */
function wireBoardNote(root, noteUrl) {
  /** @type {any} */
  let openTile = null;
  const field = root.querySelector("[data-wb-part='note']");
  if (!(field instanceof HTMLTextAreaElement)) return { open() {} };

  /**
   * Save the comment already typed for one board.
   *
   * There is no Send button. Blur, and leaving the board, write it.
   *
   * @param {any} tile
   * @param {string} text
   */
  async function save(tile, text) {
    const key = String(tile?.board_key || "");
    if (!key) return;
    const status = root.querySelector("[data-wb-part='note-status']");
    try {
      const response = await fetch(noteUrl(key), {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!(status instanceof HTMLElement)) return;
      status.hidden = response.ok;
      if (!response.ok) status.textContent = String(payload.error || "That didn't save.");
    } catch (_err) {
      if (status instanceof HTMLElement) {
        status.hidden = false;
        status.textContent = "That didn't save.";
      }
    }
  }

  field.addEventListener("blur", () => {
    if (!openTile) return;
    const text = field.value;
    if (text === String(openTile.feedback || "")) return;
    openTile.feedback = text;
    void save(openTile, text);
  });
  return {
    /**
     * @param {any} tile
     */
    open(tile) {
      if (openTile && document.activeElement === field) field.blur();
      openTile = tile;
      field.value = String(tile?.feedback || "");
      paintWaits(root, tile);
    },
  };
}

/**
 * Move an existing question gallery into the card this poll just built.
 *
 * Live sync replaces the question list, so the dash slot is a new node.
 * The whiteboard section is still the same element. Moving it keeps the
 * canvases, an opened board, and the tile polls. A new gallery is built
 * only when this session does not already have one.
 *
 * @param {HTMLElement} slot Fresh dash slot from this paint.
 * @param {{root?: HTMLElement, slot?: HTMLElement, sessionId?: string}|undefined} held
 * @param {string} sessionId Live session this gallery is reading.
 * @returns {boolean} True when the existing gallery was kept.
 */
function reuseTeacherGallery(slot, held, sessionId) {
  const root = held && held.root;
  if (!(root instanceof HTMLElement)) return false;
  if (String(held.sessionId || "") !== String(sessionId || "")) return false;
  if (root.dataset.wbMounted !== "1") return false;
  if (root.parentElement !== slot) slot.replaceChildren(root);
  held.slot = slot;
  return true;
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
  if (reuseTeacherGallery(slot, held, sessionId)) return;
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
  const scores = wireBoardScore(root, sessionId, promptId);
  const mounted = mountTeacherGrid(root, {
    sessionId,
    /**
     * @param {any} tile
     */
    onOpen(tile) {
      notes.open(tile);
      scores.open(tile);
    },
    /**
     * @param {any} tile
     */
    onTileSync(tile) {
      scores.open(tile);
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
    root,
    sessionId: String(sessionId),
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
  const seen = new Set();
  document.querySelectorAll("[data-qboard-dash]").forEach((slot) => {
    if (!(slot instanceof HTMLElement)) return;
    const promptId = String(slot.getAttribute("data-qboard-dash") || "");
    if (promptId) seen.add(promptId);
    paintTeacherDash(slot, id);
  });
  for (const [promptId, held] of [...galleries]) {
    if (seen.has(promptId)) continue;
    held.stop();
    galleries.delete(promptId);
  }
}
