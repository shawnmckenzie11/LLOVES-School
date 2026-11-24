import { wireFormativeWhiteboard, colorForOwner, cursorChipInkStyle } from "./whiteboard_formative.js";

const KATEX_LOCAL = "/static/vendor/katex";
const KATEX_CDN = "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist";

/**
 * Load KaTeX once (same pattern as ``/static/common.js``).
 * @returns {Promise<unknown>}
 */
function loadKatex() {
  if (globalThis.katex) return Promise.resolve(globalThis.katex);
  if (
    typeof document === "undefined" ||
    typeof document.querySelector !== "function" ||
    !document.head
  ) {
    return Promise.resolve(null);
  }
  if (globalThis.__llovesKatexPromise) return globalThis.__llovesKatexPromise;
  globalThis.__llovesKatexPromise = new Promise((resolve) => {
    const trySrc = (base, fallback) => {
      if (!document.querySelector('link[data-lloves-katex]')) {
        const css = document.createElement("link");
        css.rel = "stylesheet";
        css.href = `${base}/katex.min.css`;
        css.setAttribute("data-lloves-katex", "1");
        document.head.appendChild(css);
      }
      const script = document.createElement("script");
      script.src = `${base}/katex.min.js`;
      script.async = true;
      script.onload = () => resolve(globalThis.katex || null);
      script.onerror = () => {
        if (fallback) {
          document.querySelectorAll('link[data-lloves-katex]').forEach((el) => el.remove());
          trySrc(fallback, null);
          return;
        }
        resolve(null);
      };
      document.head.appendChild(script);
    };
    trySrc(KATEX_LOCAL, KATEX_CDN);
  });
  return globalThis.__llovesKatexPromise;
}

/**
 * Live whiteboard: draw, undo, redo, erase, and a Text tool.
 *
 * Own ink paints on a live layer in the same frame and is never replaced
 * by a server echo. Finished ink stays on a committed layer and is extended
 * in place. Presence posts are batches, not one request per pointer move.
 */

/** How often a drawing tab flushes queued points while the pointer is down. */
export const PRESENCE_FLUSH_MS = 80;

/** Canvas font sizes for text preset 1–4. */
const TEXT_SIZE_PX = { 1: 12, 2: 16, 3: 20, 4: 24 };

/**
 * True when the board has a finished stroke or the pen is down on one.
 * @param {number} strokeCount Finished strokes on the board.
 * @param {number} openPoints Samples in the stroke that is still open.
 * @returns {boolean}
 */
export function boardHasStroke(strokeCount, openPoints) {
  return Number(strokeCount) > 0 || Number(openPoints) > 0;
}

/**
 * Whether a committed stroke is a straight segment (two logical points).
 *
 * @param {{points?: {x: number, y: number}[]}} stroke
 * @returns {boolean}
 */
export function isLineStroke(stroke) {
  return Array.isArray(stroke?.points) && stroke.points.length === 2;
}

/**
 * How often a shared-board tab with no LiveNewsWire stream asks for
 * ink since the last sequence. One request at a time; 1.5–2s.
 */
export const BOARD_DELTA_POLL_MS = 1750;

/**
 * Wait out the server's coalesce window before one board read, so the
 * trailing tap is included and a burst does not fetch twice.
 */
export const BOARD_INK_DEBOUNCE_MS = 280;

/** Spread that one read so a group does not hit the board together. */
export const BOARD_INK_JITTER_MS = 80;

/**
 * True when a snapshot is a new head below the cursor the caller holds.
 *
 * Clear deletes the board and starts the sequence again. Merging that
 * picture would keep this tab's old ink. A snapshot that is simply a
 * wide gap (the head is still ahead of the cursor) is not a replace.
 *
 * @param {number} boardSeq Head the server just reported.
 * @param {number} requestedSince Cursor this read asked from.
 * @param {number} [heldSince] Cursor this tab has already applied.
 * @returns {boolean}
 */
export function snapshotReplacesInk(boardSeq, requestedSince, heldSince) {
  const seq = Number(boardSeq);
  if (!Number.isFinite(seq)) return false;
  const asked = Number(requestedSince);
  if (Number.isFinite(asked) && seq < asked) return true;
  const held = Number(heldSince);
  return Number.isFinite(held) && seq < held;
}

/**
 * Ops whose sequence is strictly above the caller's cursor.
 *
 * @param {any[] | null | undefined} ops
 * @param {number} cursor
 * @returns {any[]}
 */
export function opsAboveCursor(ops, cursor) {
  const floor = Number(cursor) || 0;
  const fresh = [];
  for (const op of Array.isArray(ops) ? ops : []) {
    if (!op || typeof op !== "object") continue;
    const seq = Number(op.board_seq);
    if (!Number.isFinite(seq) || seq <= floor) continue;
    fresh.push(op);
  }
  fresh.sort((a, b) => Number(a.board_seq) - Number(b.board_seq));
  return fresh;
}

/**
 * Highest sequence in ``ops``, never lower than ``cursor``.
 *
 * @param {number} cursor
 * @param {any[] | null | undefined} ops
 * @returns {number}
 */
export function cursorAfterOps(cursor, ops) {
  let high = Number(cursor) || 0;
  for (const op of Array.isArray(ops) ? ops : []) {
    const seq = Number(op && op.board_seq);
    if (Number.isFinite(seq) && seq > high) high = seq;
  }
  return high;
}

/**
 * Identity of one op. The same key applied twice is a no-op.
 *
 * A sequence is unique on a board. Ops that have no sequence fall back
 * to type plus stroke id.
 *
 * @param {any} op
 * @returns {string}
 */
export function boardOpKey(op) {
  if (!op || typeof op !== "object") return "";
  const seq = Number(op.board_seq);
  const type = String(op.type || "");
  const id = String(op.id || op.stroke_id || "");
  if (Number.isFinite(seq) && seq > 0) return `${op.board_key || ""}:${seq}`;
  return `${type}:${id}`;
}

/**
 * Remember the live run the board is drawing in.
 *
 * The first key is stored quietly. A later different key means the class
 * restarted and local ink belongs to the previous run.
 * @param {{key?: string}} state
 * @param {unknown} runKey
 * @returns {"empty" | "first" | "same" | "changed"}
 */
export function noteBoardRun(state, runKey) {
  const next = String(runKey || "").trim();
  if (!next) return "empty";
  const prev = String(state?.key || "");
  if (!prev) {
    state.key = next;
    return "first";
  }
  if (prev === next) return "same";
  state.key = next;
  if (
    boardRunBase(prev) === boardRunBase(next) &&
    !isFreshBoardRunKey(prev) &&
    !isFreshBoardRunKey(next)
  ) {
    return "same";
  }
  return "changed";
}

/**
 * Classify a board write the server refused to store.
 *
 * @param {number} status
 * @param {any} data
 * @returns {"stale" | "ended" | ""}
 */
export function boardWriteRejected(status, data) {
  if (Number(status) !== 409 || !data || typeof data !== "object") return "";
  if (data.stale_run) return "stale";
  if (data.ended) return "ended";
  return "";
}

/**
 * True when a board write came back signed out (MCK-75).
 *
 * The login gate answers 302 (or 401 for the visit-token gate). ``fetch``
 * follows the redirect by default, so a followed 302 shows up as
 * ``redirected`` with the login page's status, and ``redirect: "manual"``
 * shows up as ``opaqueredirect``. Every form means: stop posting ink.
 * @param {{status?: number, redirected?: boolean, type?: string} | null | undefined} res
 * @returns {boolean}
 */
export function boardAuthLost(res) {
  if (!res || typeof res !== "object") return false;
  const status = Number(res.status) || 0;
  return (
    status === 302 ||
    status === 401 ||
    Boolean(res.redirected) ||
    res.type === "opaqueredirect"
  );
}

/**
 * One calm line. Not an alert and not an error dialog.
 *
 * MCK-174 reuses this slot for the reopen cue, so ``text`` may replace
 * the default restart line.
 * @param {HTMLElement | null | undefined} anchor
 * @param {string} [text]
 */
export function showBoardRefreshCue(anchor, text) {
  const host =
    anchor && anchor.parentElement instanceof HTMLElement
      ? anchor.parentElement
      : document.body;
  let note = host.querySelector(".board-refresh-cue");
  if (!(note instanceof HTMLElement)) {
    note = document.createElement("p");
    note.className = "board-refresh-cue";
    note.setAttribute("role", "status");
    host.appendChild(note);
  }
  const line = String(text || "").trim();
  note.textContent = line || "Board refreshed for the new class.";
}

/**
 * True when a board run key names a Fresh board generation (MCK-174).
 *
 * The server keys generation n > 0 as ``<run_key>~g<n>``. A brand-new
 * class never has the suffix, so a run change onto a ``~g`` key is a
 * Fresh board reopen mid-class.
 * @param {unknown} runKey
 * @returns {boolean}
 */
export function isFreshBoardRunKey(runKey) {
  return /~g[0-9]+$/.test(String(runKey || "").trim());
}

/**
 * Session run key with a Fresh, question, or catch-up suffix removed.
 * @param {unknown} runKey
 * @returns {string}
 */
export function boardRunBase(runKey) {
  let key = String(runKey || "").trim();
  key = key.replace(/~cu~q[0-9]+$/, "");
  key = key.replace(/~q[0-9]+$/, "");
  key = key.replace(/~g[0-9]+$/, "");
  key = key.replace(/~cu$/, "");
  return key;
}

/**
 * True for a live question board (`<run>~q<prompt id>`), not catch-up.
 * @param {unknown} runKey
 * @returns {boolean}
 */
export function isQuestionBoardRunKey(runKey) {
  const key = String(runKey || "").trim();
  return /~q[0-9]+$/.test(key) && !/~cu~q[0-9]+$/.test(key);
}

/**
 * Remove the cue line under this board, if one is showing.
 * @param {HTMLElement | null | undefined} anchor
 */
export function clearBoardRefreshCue(anchor) {
  const host =
    anchor && anchor.parentElement instanceof HTMLElement
      ? anchor.parentElement
      : document.body;
  const note = host.querySelector(".board-refresh-cue");
  if (note instanceof HTMLElement) note.remove();
}

/**
 * Poll board ops for every tab that is showing a shared board.
 *
 * One request is in flight, whether or not the tab holds a LiveNewsWire
 * stream. Busy, 429, and 5xx back off. The timer does not send while the
 * board is hidden. ``ended`` is delivered to ``onDelta`` once, then the
 * poll stops, so an End with no restart can clear the open stroke.
 * This poll is not a wire signal.
 *
 * @param {{
 *   poll: () => Promise<any>,
 *   onDelta?: (result: any) => void,
 *   isShown?: () => boolean,
 * }} opts
 * @returns {{ stop: () => void, kick: () => void }}
 */
export function createBoardDeltaPoll(opts) {
  let timer = 0;
  let kickTimer = 0;
  let inFlight = false;
  let stopped = false;
  let delay = 0;
  let nextSlotAt = Date.now();
  let pulled = false;
  let kickAfter = false;
  let forceRead = false;

  /**
   * Arm the next poll. A stopped poll does not arm again.
   */
  function schedule() {
    if (stopped) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => run(false), delay);
  }

  /**
   * One poll. A hidden board waits. A shown board always reads.
   * @param {boolean} fromKick True when this read replaces the upcoming slot.
   */
  function run(fromKick) {
    timer = 0;
    if (stopped || inFlight) return;
    const force = forceRead;
    forceRead = false;
    if (!force && typeof opts.isShown === "function" && !opts.isShown()) {
      delay = BOARD_DELTA_POLL_MS;
      schedule();
      return;
    }
    inFlight = true;
    if (fromKick) pulled = true;
    Promise.resolve()
      .then(() => opts.poll())
      .then((result) => {
        if (stopped) return;
        if (result && result.ended) {
          stopped = true;
          if (typeof opts.onDelta === "function") opts.onDelta(result);
          return;
        }
        const status = Number(result && result.status) || 200;
        const busy = Boolean(result && (result.busy || result.retry));
        if (busy || status === 429 || status >= 500) {
          delay = Math.min(Math.max(delay, BOARD_DELTA_POLL_MS) * 2, 8000);
          return;
        }
        delay = 0;
        if (result && typeof opts.onDelta === "function") opts.onDelta(result);
      })
      .catch(() => {
        delay = Math.min(Math.max(delay, BOARD_DELTA_POLL_MS) * 2, 8000);
      })
      .finally(() => {
        inFlight = false;
        if (stopped) return;
        if (delay > BOARD_DELTA_POLL_MS) {
          schedule();
          return;
        }
        const now = Date.now();
        if (!fromKick) {
          while (nextSlotAt <= now) nextSlotAt += BOARD_DELTA_POLL_MS;
          pulled = false;
        } else if (nextSlotAt > now) {
          nextSlotAt += BOARD_DELTA_POLL_MS;
        }
        if (kickAfter && !pulled) {
          kickAfter = false;
          armKick();
          return;
        }
        kickAfter = false;
        delay = Math.max(0, nextSlotAt - Date.now());
        schedule();
      });
  }

  /**
   * Pull the next grid slot forward once. Later taps in the gap share it.
   */
  function armKick() {
    if (stopped || kickTimer) return;
    if (pulled && !forceRead) return;
    if (inFlight) {
      kickAfter = true;
      return;
    }
    if (nextSlotAt - Date.now() <= BOARD_INK_DEBOUNCE_MS + BOARD_INK_JITTER_MS) {
      return;
    }
    const wait =
      BOARD_INK_DEBOUNCE_MS +
      Math.floor(Math.random() * (BOARD_INK_JITTER_MS + 1));
    if (timer) window.clearTimeout(timer);
    timer = 0;
    kickTimer = window.setTimeout(() => {
      kickTimer = 0;
      if (stopped) return;
      if (inFlight) {
        if (!pulled) kickAfter = true;
        return;
      }
      run(true);
    }, wait);
  }

  schedule();
  return {
    /**
     * Stop polling. Used when the session ends or the tab leaves.
     */
    stop() {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      timer = 0;
      if (kickTimer) window.clearTimeout(kickTimer);
      kickTimer = 0;
    },
    /**
     * Pull the next board read forward once per poll gap.
     *
     * A burst, including the trailing tap, shares that one read.
     * ``force`` reads even when the board pane is hidden, so a clear
     * or a show reaches a phone that is not currently polling.
     * @param {boolean} [force]
     */
    kick(force) {
      if (stopped) return;
      if (force) forceRead = true;
      armKick();
    },
  };
}

/** Ignore pointer samples closer than this, in logical board pixels. */
const MIN_POINT_PX = 1.25;

/**
 * Logical board size used to normalize points. Backing-store pixels follow
 * devicePixelRatio and are not this size.
 * @param {HTMLCanvasElement | null | undefined} canvas
 * @returns {{w: number, h: number}}
 */
export function logicalBoardSize(canvas) {
  const w = Number(canvas?.dataset?.logicalWidth);
  const h = Number(canvas?.dataset?.logicalHeight);
  return {
    w: Number.isFinite(w) && w > 0 ? w : canvas?.width || 720,
    h: Number.isFinite(h) && h > 0 ? h : canvas?.height || 360,
  };
}

/**
 * Map a logical board point into the 0–1 session space.
 * @param {HTMLCanvasElement | null | undefined} canvas
 * @param {{x?: number, y?: number} | null | undefined} p
 * @returns {{x: number, y: number} | null}
 */
export function normalizeBoardPoint(canvas, p) {
  const size = logicalBoardSize(canvas);
  const x = Number(p?.x);
  const y = Number(p?.y);
  if (!Number.isFinite(x) || !Number.isFinite(y) || size.w <= 0 || size.h <= 0) {
    return null;
  }
  return {
    x: Math.min(1, Math.max(0, x / size.w)),
    y: Math.min(1, Math.max(0, y / size.h)),
  };
}

/**
 * One in-flight presence POST per tab. Later batches wait, and a reply
 * older than the newest one already applied is dropped.
 * @param {{
 *   send: (body: Record<string, any>, req: number) => Promise<any>,
 *   onReply?: (data: any, req: number) => void,
 * }} opts
 * @returns {{ push: (body: Record<string, any>) => void }}
 */
export function createPresenceQueue(opts) {
  let inFlight = false;
  /** @type {Record<string, any>[]} */
  const pending = [];
  let sent = 0;
  let applied = 0;
  let epoch = 0;

  /**
   * Send the next queued body when nothing else is on the wire.
   */
  function pump() {
    if (inFlight || pending.length === 0) return;
    const body = pending.shift();
    if (!body) return;
    inFlight = true;
    const req = ++sent;
    const stamp = epoch;
    Promise.resolve()
      .then(() => opts.send(body, req))
      .then((data) => {
        if (stamp !== epoch) return;
        if (req > applied) {
          applied = req;
          if (typeof opts.onReply === "function") opts.onReply(data, req);
        }
      })
      .catch(() => {})
      .finally(() => {
        inFlight = false;
        if (stamp === epoch) pump();
      });
  }

  return {
    /**
     * Drop queued bodies. An in-flight reply is ignored and not retried.
     */
    drop() {
      pending.length = 0;
      epoch += 1;
    },
    /**
     * Queue one presence body. Open stroke batches for the same id coalesce.
     * @param {Record<string, any>} body
     */
    push(body) {
      const next = { ...body };
      if (Array.isArray(body.points)) next.points = body.points.slice();
      const strokeId = String(next.stroke_id || "");
      const last = pending[pending.length - 1];
      const canMerge =
        last &&
        strokeId &&
        last.stroke_id === strokeId &&
        !last.ended &&
        !last.text_id &&
        !next.text_id &&
        Array.isArray(next.points);
      if (canMerge && last) {
        const prev = Array.isArray(last.points) ? last.points : [];
        last.points = prev.concat(next.points);
        last.ended = Boolean(next.ended);
        if (next.points.length) {
          const tail = next.points[next.points.length - 1];
          last.x = tail[0];
          last.y = tail[1];
        }
      } else {
        pending.push(next);
      }
      pump();
    },
  };
}

/**
 * Bind pointer drawing, text entry, and remote collab paint on one canvas.
 *
 * The canvas node passed in is the committed layer and is never replaced.
 * A second canvas, added once as the next sibling, holds the in-progress stroke.
 * @param {HTMLCanvasElement} canvas
 * @param {{
 *   canDraw?: () => boolean,
 *   onPoint?: (point: {x: number, y: number}, ended: boolean, strokeId: string) => void,
 *   onPoints?: (points: {x: number, y: number}[], ended: boolean, strokeId: string) => void,
 *   onText?: (label: {id: string, x: number, y: number, text: string}) => void,
 *   owner?: string | (() => string),
 *   color?: string,
 *   lineWidth?: number,
 *   undoBtn?: HTMLElement | null,
 *   redoBtn?: HTMLElement | null,
 *   penBtn?: HTMLElement | null,
 *   lineBtn?: HTMLElement | null,
 *   eraseBtn?: HTMLElement | null,
 *   textBtn?: HTMLElement | null,
 *   fixedInkColor?: boolean,
 *   cursorLayer?: HTMLElement | null,
 *   onInkChange?: (hasStroke: boolean) => void,
 * }} [opts]
 * @returns {{
 *   setEnabled: (on: boolean) => void,
 *   clear: () => void,
 *   setCollab: (on: boolean) => void,
 *   importRemote: (view: any, options?: {collab?: boolean}) => void,
 * }}
 */
const UNDO_TIP = "Undo last stroke or text (Ctrl/Cmd+Z)";
const NOTHING_TIP = "Nothing to undo";

/** @type {Set<{undo: () => boolean, canUndo: () => boolean, isShown: () => boolean, contains: (node: EventTarget | null) => boolean, mark: () => void}>} */
const undoBoards = new Set();
/** @type {{undo: () => boolean, canUndo: () => boolean, isShown: () => boolean, contains: (node: EventTarget | null) => boolean, mark: () => void} | null} */
let lastUndoBoard = null;
let undoShortcutInstalled = false;

/**
 * True when Ctrl/Cmd+Z should edit text instead of the board.
 * @param {EventTarget | null} node
 * @returns {boolean}
 */
function typingTarget(node) {
  if (!node || typeof node !== "object") return false;
  const tag = String(/** @type {{tagName?: string}} */ (node).tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return Boolean(/** @type {{isContentEditable?: boolean}} */ (node).isContentEditable);
}

/**
 * True when the canvas is in the document and no ancestor is hidden.
 * @param {HTMLElement | null | undefined} el
 * @returns {boolean}
 */
function elementShown(el) {
  if (!el) return false;
  if (el.hidden) return false;
  if (el.isConnected === false) return false;
  let node = el.parentElement;
  while (node) {
    if (node.hidden) return false;
    node = node.parentElement;
  }
  return true;
}

/**
 * Board that should receive Ctrl/Cmd+Z.
 * @param {EventTarget | null} target
 * @returns {{undo: () => boolean, canUndo: () => boolean, isShown: () => boolean, contains: (node: EventTarget | null) => boolean, mark: () => void} | null}
 */
function boardForUndoShortcut(target) {
  for (const board of undoBoards) {
    if (board.contains(target) && board.isShown()) return board;
  }
  if (lastUndoBoard && lastUndoBoard.isShown()) return lastUndoBoard;
  const shown = [];
  for (const board of undoBoards) {
    if (board.isShown()) shown.push(board);
  }
  return shown.length === 1 ? shown[0] : null;
}

/**
 * One desktop listener for every board on the page.
 */
function installUndoShortcut() {
  if (undoShortcutInstalled) return;
  if (typeof document === "undefined" || typeof document.addEventListener !== "function") return;
  undoShortcutInstalled = true;
  document.addEventListener("keydown", (event) => {
    if (!event || event.defaultPrevented || event.repeat) return;
    if (event.altKey || event.shiftKey) return;
    if (!(event.ctrlKey || event.metaKey)) return;
    if (String(event.key || "").toLowerCase() !== "z") return;
    if (typingTarget(event.target)) return;
    const board = boardForUndoShortcut(event.target);
    if (!board || !board.canUndo()) return;
    event.preventDefault();
    board.mark();
    board.undo();
  });
}

export function bindWhiteboard(canvas, opts = {}) {
  const ctx = canvas.getContext("2d");
  const liveCanvas = document.createElement("canvas");
  liveCanvas.className = "live-canvas-ink";
  liveCanvas.setAttribute("aria-hidden", "true");
  const liveCtx = liveCanvas.getContext("2d");
  if (!ctx || !liveCtx) {
    return {
      setEnabled: () => {},
      clear: () => {},
      setCollab: () => {},
      importRemote: () => {},
      applyDelta: () => {},
      closeTextEntry: () => {},
      lockEditing: () => {},
    };
  }
  const logicalW = canvas.width || 720;
  const logicalH = canvas.height || 360;
  canvas.dataset.logicalWidth = String(logicalW);
  canvas.dataset.logicalHeight = String(logicalH);
  canvas.dataset.boardBound = "1";
  if (canvas.parentElement) canvas.insertAdjacentElement("afterend", liveCanvas);

  /** @type {{id: string, owner: string, points: {x: number, y: number}[], color: string, mine: boolean}[]} */
  let strokes = [];
  /** @type {{id: string, x: number, y: number, latex: string, color: string, mine: boolean}[]} */
  let equations = [];
  /** @type {{kind: "stroke"|"text"|"equation"|"wipe", item?: any, id: string, snapshot?: any}[]} */
  let redoStack = [];
  /** @type {{kind: "stroke"|"text"|"equation"|"wipe", id: string, snapshot?: any}[]} */
  let undoStack = [];
  /** @type {Map<string, any>} */
  const wipeSnapshots = new Map();
  /** @type {{undo: () => boolean, canUndo: () => boolean, isShown: () => boolean, contains: (node: EventTarget | null) => boolean, mark: () => void} | null} */
  let shortcutApi = null;

  /**
   * Drop one id from the undo stack.
   * @param {"stroke"|"text"} kind
   * @param {string} id
   */
  const dropUndo = (kind, id) => {
    undoStack = undoStack.filter((row) => row.kind !== kind || row.id !== id);
    if (kind === "wipe") wipeSnapshots.delete(id);
  };

  /**
   * Make this own item the newest undo step.
   * @param {"stroke"|"text"} kind
   * @param {string} id
   */
  /**
   * @param {"stroke"|"text"|"equation"|"wipe"} kind
   * @param {string} id
   * @param {any} [snapshot]
   */
  const rememberUndo = (kind, id, snapshot) => {
    dropUndo(kind, id);
    const row = { kind, id };
    if (kind === "wipe" && snapshot) {
      row.snapshot = snapshot;
      wipeSnapshots.set(id, snapshot);
    }
    undoStack.push(row);
    redoStack = [];
  };

  /**
   * Newest own stroke or text box that is still on the board.
   * @returns {{kind: "stroke"|"text", id: string} | null}
   */
  const undoTarget = () => {
    for (let i = undoStack.length - 1; i >= 0; i -= 1) {
      const row = undoStack[i];
      if (row.kind === "stroke" && strokes.some((stroke) => stroke.id === row.id && stroke.mine)) {
        return row;
      }
      if (row.kind === "text" && texts.some((label) => label.id === row.id && label.mine)) {
        return row;
      }
      if (row.kind === "equation" && equations.some((eq) => eq.id === row.id && eq.mine)) {
        return row;
      }
      if (row.kind === "wipe" && wipeSnapshots.has(row.id)) {
        return row;
      }
    }
    return null;
  };

  /**
   * Remove the newest own stroke or text box. One press, one item.
   * @returns {boolean}
   */
  const performUndo = () => {
    if (editingLocked) return false;
    const row = undoTarget();
    if (!row) return false;
    dropUndo(row.kind, row.id);
    if (row.kind === "stroke") {
      const index = strokes.findIndex((stroke) => stroke.id === row.id && stroke.mine);
      const last = index >= 0 ? strokes.splice(index, 1)[0] : null;
      if (last) redoStack.push({ kind: "stroke", item: last });
      if (last && last.mine && collab && typeof opts.onUndo === "function") opts.onUndo(last);
    } else if (row.kind === "text") {
      const index = texts.findIndex((label) => label.id === row.id && label.mine);
      const last = index >= 0 ? texts.splice(index, 1)[0] : null;
      if (last) redoStack.push({ kind: "text", item: last });
      if (last && last.mine && collab && typeof opts.onText === "function") {
        opts.onText({ id: last.id, x: last.x, y: last.y, text: "" });
      }
    } else if (row.kind === "equation") {
      const index = equations.findIndex((eq) => eq.id === row.id && eq.mine);
      const last = index >= 0 ? equations.splice(index, 1)[0] : null;
      if (last) redoStack.push({ kind: "equation", id: last.id, item: last });
      if (last && last.mine && collab && typeof opts.onEquation === "function") {
        opts.onEquation({ id: last.id, x: last.x, y: last.y, latex: "" });
      }
    } else if (row.kind === "wipe") {
      const snapshot = wipeSnapshots.get(row.id);
      wipeSnapshots.delete(row.id);
      if (snapshot) restoreOwnerSnapshot(snapshot);
      redoStack.push({ kind: "wipe", id: row.id, snapshot });
      if (collab && typeof opts.onUnwipe === "function") {
        opts.onUnwipe({ unwipe_id: `unwipe-${row.id}`, snapshot });
      }
    }
    replayCommitted();
    syncButtons();
    return true;
  };

  /**
   * Put back the item the last undo removed.
   */
  const performRedo = () => {
    if (editingLocked || !redoStack.length) return;
    const next = redoStack.pop();
    if (!next || !next.item) return;
    if (next.kind === "text") {
      texts = texts.filter((label) => label.id !== next.item.id);
      texts.push(next.item);
      dropUndo("text", next.item.id);
      undoStack.push({ kind: "text", id: next.item.id });
      if (next.item.mine && collab && typeof opts.onText === "function") opts.onText(next.item);
    } else if (next.kind === "equation") {
      equations = equations.filter((eq) => eq.id !== next.item.id);
      equations.push(next.item);
      dropUndo("equation", next.item.id);
      undoStack.push({ kind: "equation", id: next.item.id });
      if (next.item.mine && collab && typeof opts.onEquation === "function") opts.onEquation(next.item);
    } else if (next.kind === "wipe") {
      performBoardClear(next.snapshot, next.id, false);
      dropUndo("wipe", next.id);
      undoStack.push({ kind: "wipe", id: next.id, snapshot: next.snapshot });
      if (collab && typeof opts.onWipe === "function") {
        opts.onWipe({ wipe_id: next.id, snapshot: next.snapshot });
      }
    } else {
      const stroke = next.item;
      strokes = strokes.filter((row) => row.id !== stroke.id);
      strokes.push(stroke);
      dropUndo("stroke", stroke.id);
      undoStack.push({ kind: "stroke", id: stroke.id });
      if (stroke.mine && collab && typeof opts.onRedo === "function") opts.onRedo(stroke);
    }
    replayCommitted();
    syncButtons();
  };
  /** @type {{id: string, x: number, y: number, text: string, color: string, mine: boolean, bold?: boolean, underline?: boolean, script?: string, size?: number}[]} */
  let texts = [];
  let selectedTextId = "";
  let editingEquationId = "";
  let katexReady = loadKatex();
  /** @type {Set<string>} */
  const ownIds = new Set();
  /** @type {{x: number, y: number}[]} */
  let current = [];
  /** @type {{x: number, y: number}[]} */
  let pending = [];
  let drawing = false;
  let lining = false;
  /** @type {{x: number, y: number} | null} */
  let lineStart = null;
  /** @type {{x: number, y: number} | null} */
  let linePreview = null;
  let strokeId = "";
  let strokeOwner = "";
  let enabled = true;
  /** True after a question board is submitted. Strokes, text, undo, redo, and clear stop. */
  let editingLocked = false;
  let collab = false;
  let tool = "draw";
  let editingId = "";
  let flushTimer = 0;
  let paintQueued = false;
  let fitting = false;
  /** @type {() => void} */
  let afterReplay = () => {};
  /** @type {{nx: number, ny: number, hit: any}|null} */
  let reopen = null;
  let lastEditorOpenAt = 0;
  const lineWidth = opts.lineWidth || 2;
  const hasFormativeShell = Boolean(opts.formative);
  let formativeActive = Boolean(opts.formative?.enabled);
  /** @returns {boolean} */
  const isFormative = () => hasFormativeShell && formativeActive;
  const formativeTextWidthPx = Number(opts.formative?.textWidthPx) || 160;
  const stage = canvas.parentElement;
  /** @type {HTMLInputElement | HTMLTextAreaElement} */
  const editor = hasFormativeShell
    ? document.createElement("textarea")
    : document.createElement("input");
  if (!hasFormativeShell) editor.type = "text";
  editor.className = hasFormativeShell
    ? "canvas-text-editor wb-formative-text"
    : "canvas-text-editor";
  editor.maxLength = 240;
  editor.hidden = true;
  editor.setAttribute("aria-label", "Whiteboard text");
  if (hasFormativeShell) {
    editor.style.width = `${formativeTextWidthPx}px`;
    editor.style.maxWidth = `${formativeTextWidthPx}px`;
    editor.style.resize = "none";
    editor.rows = 1;
    editor.wrap = "soft";
  }
  if (stage) stage.appendChild(editor);

  const eqLayer = document.createElement("div");
  eqLayer.className = "wb-equation-layer";
  eqLayer.setAttribute("aria-hidden", "true");
  if (stage) stage.appendChild(eqLayer);

  const eqEditor = document.createElement("div");
  eqEditor.className = "wb-equation-editor";
  eqEditor.hidden = true;
  const eqInput = document.createElement("input");
  eqInput.type = "text";
  eqInput.className = "wb-equation-input";
  eqInput.setAttribute("aria-label", "Equation");
  eqInput.maxLength = 240;
  const eqPreview = document.createElement("div");
  eqPreview.className = "wb-equation-preview";
  eqEditor.appendChild(eqInput);
  eqEditor.appendChild(eqPreview);
  if (stage) stage.appendChild(eqEditor);

  const textFormatBar = document.createElement("div");
  textFormatBar.className = "wb-text-format";
  textFormatBar.hidden = true;
  textFormatBar.setAttribute("role", "toolbar");
  textFormatBar.setAttribute("aria-label", "Text formatting");
  const fmtBold = document.createElement("button");
  fmtBold.type = "button";
  fmtBold.textContent = "B";
  fmtBold.title = "Bold";
  const fmtUnderline = document.createElement("button");
  fmtUnderline.type = "button";
  fmtUnderline.textContent = "U";
  fmtUnderline.title = "Underline";
  const fmtSuper = document.createElement("button");
  fmtSuper.type = "button";
  fmtSuper.textContent = "x²";
  fmtSuper.title = "Superscript";
  const fmtSub = document.createElement("button");
  fmtSub.type = "button";
  fmtSub.textContent = "x₂";
  fmtSub.title = "Subscript";
  const fmtSizeDown = document.createElement("button");
  fmtSizeDown.type = "button";
  fmtSizeDown.textContent = "−";
  fmtSizeDown.title = "Text size";
  const fmtSizeUp = document.createElement("button");
  fmtSizeUp.type = "button";
  fmtSizeUp.textContent = "+";
  fmtSizeUp.title = "Text size";
  for (const btn of [fmtBold, fmtUnderline, fmtSuper, fmtSub, fmtSizeDown, fmtSizeUp]) {
    textFormatBar.appendChild(btn);
  }
  if (stage) stage.appendChild(textFormatBar);

  /**
   * Current writer token for stroke ids.
   * @returns {string}
   */
  const ownerNow = () => {
    if (typeof opts.owner === "function") {
      const value = opts.owner();
      return String(value || "board");
    }
    return String(opts.owner || "board");
  };

  const memberLookup = () => {
    const rows = opts.formative?.members;
    if (!Array.isArray(rows)) return {};
    const map = {};
    for (const row of rows) map[String(row.id)] = row;
    return map;
  };
  const strokeColor = (owner) => {
    const who = String(owner || ownerNow());
    if (opts.fixedInkColor && who === ownerNow()) {
      return opts.color || "#12202e";
    }
    if (isFormative()) {
      return colorForOwner(memberLookup(), who);
    }
    return opts.color || "#12202e";
  };
  let color = strokeColor(ownerNow());

  /**
   * Stroke id unique to this owner. Not a shared clock value.
   * @param {string} owner
   * @returns {string}
   */
  const makeStrokeId = (owner) => {
    const who = String(owner || "board").replace(/[^a-zA-Z0-9_-]/g, "") || "board";
    let suffix = "";
    const cryptoObj = globalThis.crypto;
    if (cryptoObj && typeof cryptoObj.getRandomValues === "function") {
      const bytes = new Uint8Array(8);
      cryptoObj.getRandomValues(bytes);
      suffix = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    } else {
      suffix = `${Math.random().toString(16).slice(2)}${Math.random().toString(16).slice(2)}`;
    }
    return `${who}-${suffix}`;
  };

  const canDraw = () =>
    enabled && tool === "draw" && (typeof opts.canDraw !== "function" || opts.canDraw());

  /** @returns {boolean} */
  const canDrawLine = () =>
    enabled && tool === "line" && (typeof opts.canDraw !== "function" || opts.canDraw());

  /**
   * Map a pointer event into logical board pixels.
   * @param {PointerEvent} event
   * @returns {{x: number, y: number}}
   */
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / Math.max(rect.width, 1)) * logicalW,
      y: ((event.clientY - rect.top) / Math.max(rect.height, 1)) * logicalH,
    };
  };

  /**
   * Map logical pixels into the 0–1 session space.
   * @param {{x: number, y: number}} p
   * @returns {{x: number, y: number}}
   */
  const norm = (p) => ({
    x: logicalW ? p.x / logicalW : 0,
    y: logicalH ? p.y / logicalH : 0,
  });

  /**
   * Draw in logical board pixels on a device-pixel backing store.
   * @param {CanvasRenderingContext2D} context
   */
  const applyTransform = (context) => {
    const bw = canvas.width || logicalW;
    const bh = canvas.height || logicalH;
    context.setTransform(bw / logicalW, 0, 0, bh / logicalH, 0, 0);
  };

  /**
   * Clear one layer in backing-store pixels, then restore logical coordinates.
   * @param {CanvasRenderingContext2D} context
   * @param {HTMLCanvasElement} el
   */
  const clearLayer = (context, el) => {
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, el.width, el.height);
    applyTransform(context);
  };

  /**
   * Stroke one polyline, or only its new tail.
   * @param {CanvasRenderingContext2D} context
   * @param {{x: number, y: number}[]} points
   * @param {string} strokeColor
   * @param {number} from
   */
  const strokeSegment = (context, points, strokeColor, from) => {
    if (!points.length) return;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.strokeStyle = strokeColor || color;
    context.fillStyle = strokeColor || color;
    context.lineWidth = lineWidth;
    if (points.length === 1) {
      context.beginPath();
      context.arc(points[0].x, points[0].y, lineWidth / 2, 0, Math.PI * 2);
      context.fill();
      return;
    }
    const start = Math.max(0, from);
    context.beginPath();
    if (start <= 0) {
      context.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i += 1) context.lineTo(points[i].x, points[i].y);
    } else {
      context.moveTo(points[start - 1].x, points[start - 1].y);
      for (let i = start; i < points.length; i += 1) context.lineTo(points[i].x, points[i].y);
    }
    context.stroke();
  };

  const formativeTextBoxLogical = () =>
    (formativeTextWidthPx / Math.max(canvas.clientWidth, 1)) * logicalW;

  const wrapTextLines = (context, body, maxWidth) => {
    const words = String(body || "").split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const lines = [];
    let line = words[0];
    for (let i = 1; i < words.length; i += 1) {
      const probe = `${line} ${words[i]}`;
      if (context.measureText(probe).width <= maxWidth) line = probe;
      else {
        lines.push(line);
        line = words[i];
      }
    }
    lines.push(line);
    return lines;
  };

  const textPxSize = (label) => TEXT_SIZE_PX[Number(label?.size) || 2] || 16;

  const textFont = (label) => {
    const px = textPxSize(label);
    return `${label?.bold ? "bold " : ""}${px}px sans-serif`;
  };

  /**
   * Restore own strokes, labels, and equations from one wipe snapshot.
   * @param {{strokes?: any[], texts?: any[], equations?: any[]}} snapshot
   */
  const restoreOwnerSnapshot = (snapshot) => {
    const rows = Array.isArray(snapshot?.strokes) ? snapshot.strokes : [];
    for (const raw of rows) {
      const id = String(raw?.id || "");
      if (!id) continue;
      const points = remotePoints(raw);
      if (!points.length) continue;
      strokes.push({
        id,
        owner: String(raw.owner || ownerNow()),
        points,
        color: String(raw.color || color),
        mine: true,
      });
      ownIds.add(id);
    }
    const labels = Array.isArray(snapshot?.texts) ? snapshot.texts : [];
    for (const raw of labels) {
      const id = String(raw?.id || "");
      const body = String(raw?.text || "").trim();
      if (!id || !body) continue;
      texts.push({
        id,
        x: Number(raw.x),
        y: Number(raw.y),
        text: body,
        color: String(raw.color || color),
        mine: true,
        bold: Boolean(raw.bold),
        underline: Boolean(raw.underline),
        script: raw.script === "super" || raw.script === "sub" ? raw.script : "",
        size: Number(raw.size) || 2,
      });
      ownIds.add(id);
    }
    const eqs = Array.isArray(snapshot?.equations) ? snapshot.equations : [];
    for (const raw of eqs) {
      const id = String(raw?.id || "");
      const latex = String(raw?.latex || "").trim();
      if (!id || !latex) continue;
      equations.push({
        id,
        x: Number(raw.x),
        y: Number(raw.y),
        latex,
        color: String(raw.color || color),
        mine: true,
      });
      ownIds.add(id);
    }
  };

  /**
   * Serialize own ink for ``board_unwipe``.
   * @returns {{strokes: any[], texts: any[], equations: any[]}}
   */
  const ownInkSnapshot = () => {
    const strokeRows = [];
    for (const stroke of strokes) {
      if (!stroke.mine) continue;
      strokeRows.push({
        id: stroke.id,
        owner: stroke.owner || ownerNow(),
        color: stroke.color || color,
        points: stroke.points.map((p) => [
          logicalW ? p.x / logicalW : 0,
          logicalH ? p.y / logicalH : 0,
        ]),
      });
    }
    const textRows = [];
    for (const label of texts) {
      if (!label.mine || !label.text) continue;
      const row = {
        id: label.id,
        owner: ownerNow(),
        color: label.color || color,
        x: label.x,
        y: label.y,
        text: label.text,
      };
      if (label.bold) row.bold = true;
      if (label.underline) row.underline = true;
      if (label.script === "super" || label.script === "sub") row.script = label.script;
      row.size = Number(label.size) || 2;
      textRows.push(row);
    }
    const equationRows = [];
    for (const eq of equations) {
      if (!eq.mine || !eq.latex) continue;
      equationRows.push({
        id: eq.id,
        owner: ownerNow(),
        color: eq.color || color,
        x: eq.x,
        y: eq.y,
        latex: eq.latex,
      });
    }
    return { strokes: strokeRows, texts: textRows, equations: equationRows };
  };

  /**
   * Clear all own ink and optionally remember one wipe undo.
   * @param {any} snapshot
   * @param {string} wipeId
   * @param {boolean} remember
   */
  const performBoardClear = (snapshot, wipeId, remember) => {
    const mineStroke = new Set(strokes.filter((s) => s.mine).map((s) => s.id));
    strokes = strokes.filter((s) => !s.mine);
    const mineText = new Set(texts.filter((t) => t.mine).map((t) => t.id));
    texts = texts.filter((t) => !t.mine);
    const mineEq = new Set(equations.filter((e) => e.mine).map((e) => e.id));
    equations = equations.filter((e) => !e.mine);
    for (const id of mineStroke) ownIds.delete(id);
    for (const id of mineText) ownIds.delete(id);
    for (const id of mineEq) ownIds.delete(id);
    undoStack = undoStack.filter(
      (row) =>
        (row.kind === "wipe" && row.id === wipeId) ||
        (row.kind === "stroke" && !mineStroke.has(row.id)) ||
        (row.kind === "text" && !mineText.has(row.id)) ||
        (row.kind === "equation" && !mineEq.has(row.id))
    );
    current = [];
    pending = [];
    selectedTextId = "";
    hideTextFormatBar();
    if (remember && snapshot) rememberUndo("wipe", wipeId, snapshot);
    replayCommitted();
    syncButtons();
  };

  const hideTextFormatBar = () => {
    selectedTextId = "";
    textFormatBar.hidden = true;
    textFormatBar.setAttribute("hidden", "");
  };

  const showTextFormatBar = (label) => {
    if (!(label && label.mine)) {
      hideTextFormatBar();
      return;
    }
    selectedTextId = label.id;
    textFormatBar.hidden = false;
    textFormatBar.removeAttribute("hidden");
    fmtBold.classList.toggle("is-on", Boolean(label.bold));
    fmtUnderline.classList.toggle("is-on", Boolean(label.underline));
    fmtSuper.classList.toggle("is-on", label.script === "super");
    fmtSub.classList.toggle("is-on", label.script === "sub");
  };

  const selectedText = () => texts.find((label) => label.id === selectedTextId && label.mine) || null;

  const pushSelectedText = () => {
    const label = selectedText();
    if (!label || typeof opts.onText !== "function") return;
    opts.onText({
      id: label.id,
      x: label.x,
      y: label.y,
      text: label.text,
      bold: Boolean(label.bold),
      underline: Boolean(label.underline),
      script: label.script === "super" || label.script === "sub" ? label.script : "",
      size: Number(label.size) || 2,
    });
    replayCommitted();
  };

  const drawWrappedLabel = (context, body, x, y, maxWidth, fill) => {
    const lineHeight = 16 * 1.35;
    const hard = String(body || "").split("\n");
    let row = 0;
    for (const chunk of hard) {
      const lines = isFormative() ? wrapTextLines(context, chunk, maxWidth) : [chunk];
      for (const line of lines) {
        if (!line) continue;
        context.fillText(line, x, y + row * lineHeight);
        row += 1;
      }
    }
  };

  /**
   * Paint session labels on the committed layer.
   */
  const layoutEquationNodes = () => {
    eqLayer.innerHTML = "";
    const box = canvas.getBoundingClientRect();
    for (const eq of equations) {
      if (!eq.latex || eq.id === editingEquationId) continue;
      const node = document.createElement("div");
      node.className = "wb-equation-node";
      node.style.left = `${eq.x * box.width}px`;
      node.style.top = `${eq.y * box.height}px`;
      node.dataset.id = eq.id;
      eqLayer.appendChild(node);
      void katexReady.then((katex) => {
        if (!(katex && typeof katex.render === "function")) {
          node.textContent = eq.latex;
          return;
        }
        try {
          katex.render(eq.latex, node, { throwOnError: false, displayMode: false });
        } catch {
          node.textContent = eq.latex;
        }
      });
    }
    eqLayer.style.left = `${canvas.offsetLeft}px`;
    eqLayer.style.top = `${canvas.offsetTop}px`;
    eqLayer.style.width = `${box.width}px`;
    eqLayer.style.height = `${box.height}px`;
  };

  const drawTexts = () => {
    ctx.textBaseline = "top";
    for (const label of texts) {
      if (!label.text || label.id === editingId) continue;
      ctx.font = textFont(label);
      ctx.fillStyle = label.color || color;
      let lx = label.x * logicalW;
      let ly = label.y * logicalH;
      if (label.script === "super") ly -= textPxSize(label) * 0.45;
      if (label.script === "sub") ly += textPxSize(label) * 0.2;
      if (isFormative()) {
        drawWrappedLabel(ctx, label.text, lx, ly, formativeTextBoxLogical(), label.color || color);
      } else {
        ctx.fillText(label.text, lx, ly);
        if (label.underline) {
          const width = ctx.measureText(label.text).width;
          const y = ly + textPxSize(label) + 1;
          ctx.beginPath();
          ctx.moveTo(lx, y);
          ctx.lineTo(lx + width, y);
          ctx.lineWidth = 1;
          ctx.strokeStyle = label.color || color;
          ctx.stroke();
        }
      }
    }
    layoutEquationNodes();
    if (isFormative() && !editor.hidden) {
      const left = Number(editor.dataset.nx || "0") * logicalW;
      const top = Number(editor.dataset.ny || "0") * logicalH;
      const draft = String(editor.value || "");
      if (draft) {
        ctx.fillStyle = color;
        drawWrappedLabel(ctx, draft, left, top, formativeTextBoxLogical(), color);
      }
    }
  };

  const layoutFormativeEditor = () => {
    if (!isFormative() || editor.hidden) return;
    editor.style.height = "auto";
    editor.style.height = `${editor.scrollHeight}px`;
  };

  /**
   * Repaint finished ink. Pointer moves do not call this.
   */
  const replayCommitted = () => {
    clearLayer(ctx, canvas);
    for (const stroke of strokes) {
      strokeSegment(ctx, stroke.points, stroke.color, 0);
    }
    drawTexts();
    afterReplay();
  };

  /**
   * Paint the in-progress stroke. Clears only the live layer.
   */
  const paintLive = () => {
    clearLayer(liveCtx, liveCanvas);
    if (lining && lineStart && linePreview) {
      strokeSegment(liveCtx, [lineStart, linePreview], color, 0);
      return;
    }
    if (current.length) strokeSegment(liveCtx, current, color, 0);
  };

  /**
   * Coalesce live-layer paints to one per frame.
   */
  const requestPaint = () => {
    if (paintQueued) return;
    paintQueued = true;
    window.requestAnimationFrame(() => {
      paintQueued = false;
      paintLive();
    });
  };

  /**
   * Match the backing store to the CSS box and the screen density.
   */
  const fitBoard = () => {
    if (fitting) return;
    fitting = true;
    try {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 3));
      if (rect.width < 2 || rect.height < 2) {
        applyTransform(ctx);
        applyTransform(liveCtx);
        return;
      }
      const bw = Math.max(1, Math.round(rect.width * dpr));
      const bh = Math.max(1, Math.round(rect.height * dpr));
      const pinLiveLayer = () => {
        const box = canvas.getBoundingClientRect();
        liveCanvas.style.left = `${canvas.offsetLeft}px`;
        liveCanvas.style.top = `${canvas.offsetTop}px`;
        liveCanvas.style.width = `${box.width}px`;
        liveCanvas.style.height = `${box.height}px`;
        syncCursorLayerToCanvas();
      };
      if (canvas.width === bw && canvas.height === bh && liveCanvas.width === bw) {
        applyTransform(ctx);
        applyTransform(liveCtx);
        pinLiveLayer();
        return;
      }
      canvas.width = bw;
      canvas.height = bh;
      liveCanvas.width = bw;
      liveCanvas.height = bh;
      // The live layer must cover the drawing canvas, not the stage.
      // A stage that is taller than the canvas stretches this overlay
      // and the in-progress stroke sits below the pointer. Re-read the
      // box after the bitmap change, because that write can resize a
      // canvas whose CSS height is still automatic.
      pinLiveLayer();
      replayCommitted();
      paintLive();
    } finally {
      fitting = false;
    }
  };

  /**
   * Send queued samples. Stroke end always flushes whatever is still held.
   * @param {boolean} ended
   */
  const flushPending = (ended) => {
    if (flushTimer) {
      window.clearTimeout(flushTimer);
      flushTimer = 0;
    }
    if (!pending.length) return;
    const batch = pending;
    pending = [];
    const id = strokeId;
    if (!id) return;
    if (typeof opts.onPoints === "function") {
      opts.onPoints(batch, ended, id);
      return;
    }
    if (typeof opts.onPoint !== "function") return;
    batch.forEach((p, index) => {
      const last = ended && index === batch.length - 1;
      opts.onPoint(p, last, id);
    });
  };

  /**
   * Arm the next batch flush while a stroke is open.
   */
  const scheduleFlush = () => {
    if (flushTimer || !drawing) return;
    flushTimer = window.setTimeout(() => {
      flushTimer = 0;
      if (!drawing) return;
      flushPending(false);
      if (pending.length) scheduleFlush();
    }, PRESENCE_FLUSH_MS);
  };

  /**
   * Keep a sample when it moves the pen far enough to matter.
   * @param {{x: number, y: number}} p
   * @param {boolean} force
   * @returns {boolean}
   */
  const pushPoint = (p, force) => {
    const last = current[current.length - 1];
    if (!force && last) {
      const dx = p.x - last.x;
      const dy = p.y - last.y;
      if (dx * dx + dy * dy < MIN_POINT_PX * MIN_POINT_PX) return false;
    }
    if (last && last.x === p.x && last.y === p.y) return false;
    current.push(p);
    pending.push(p);
    return true;
  };

  /**
   * Pointer events between animation frames, including the event itself.
   * @param {PointerEvent} event
   * @returns {PointerEvent[]}
   */
  const samplesFrom = (event) => {
    try {
      if (typeof event.getCoalescedEvents === "function") {
        const rows = event.getCoalescedEvents();
        if (rows && rows.length) return rows;
      }
    } catch (_err) {
      /* fall through to the event */
    }
    return [event];
  };

  const syncButtons = () => {
    const frozen = editingLocked;
    const hasOwnInk =
      strokes.some((s) => s.mine) ||
      texts.some((t) => t.mine) ||
      equations.some((e) => e.mine);
    const canUndo = !frozen && Boolean(undoTarget());
    if (opts.undoBtn instanceof HTMLButtonElement) {
      opts.undoBtn.disabled = !canUndo;
      opts.undoBtn.title = canUndo ? UNDO_TIP : NOTHING_TIP;
    }
    if (opts.redoBtn instanceof HTMLButtonElement) opts.redoBtn.disabled = frozen || !redoStack.length;
    if (opts.eraseBtn instanceof HTMLButtonElement) opts.eraseBtn.disabled = frozen || !strokes.length;
    if (frozen && opts.penBtn instanceof HTMLButtonElement) opts.penBtn.disabled = true;
    if (frozen && opts.textBtn instanceof HTMLButtonElement) opts.textBtn.disabled = true;
    if (frozen && opts.equationBtn instanceof HTMLButtonElement) opts.equationBtn.disabled = true;
    if (frozen && opts.clearBtn instanceof HTMLButtonElement) opts.clearBtn.disabled = true;
    if (typeof opts.onInkChange === "function") {
      opts.onInkChange(boardHasStroke(strokes.length, current.length));
    }
    if (opts.penBtn instanceof HTMLButtonElement) {
      opts.penBtn.setAttribute("aria-pressed", tool === "draw" ? "true" : "false");
      opts.penBtn.classList.toggle("is-on", tool === "draw");
    }
    if (opts.textBtn instanceof HTMLButtonElement) {
      opts.textBtn.setAttribute("aria-pressed", tool === "text" ? "true" : "false");
      opts.textBtn.classList.toggle("is-on", tool === "text");
    }
    if (opts.lineBtn instanceof HTMLButtonElement) {
      opts.lineBtn.setAttribute("aria-pressed", tool === "line" ? "true" : "false");
      opts.lineBtn.classList.toggle("is-on", tool === "line");
      opts.lineBtn.disabled = frozen;
    }
    if (opts.equationBtn instanceof HTMLButtonElement) {
      opts.equationBtn.setAttribute("aria-pressed", tool === "equation" ? "true" : "false");
      opts.equationBtn.classList.toggle("is-on", tool === "equation");
      opts.equationBtn.disabled = frozen;
    }
    if (opts.clearBtn instanceof HTMLButtonElement) {
      opts.clearBtn.disabled = frozen || !hasOwnInk;
    }
  };

  /**
   * Paint named cursors over the board. Hover does not post a cursor.
   * @param {any[]} cursors
   */
  const syncCursorLayerToCanvas = () => {
    const layer = opts.cursorLayer;
    if (!(layer instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement)) return;
    const rect = canvas.getBoundingClientRect();
    layer.style.left = `${canvas.offsetLeft}px`;
    layer.style.top = `${canvas.offsetTop}px`;
    layer.style.width = `${rect.width}px`;
    layer.style.height = `${rect.height}px`;
    layer.style.right = "auto";
    layer.style.bottom = "auto";
  };

  const paintCursors = (cursors) => {
    const layer = opts.cursorLayer;
    if (!(layer instanceof HTMLElement)) return;
    if (!Array.isArray(cursors)) return;
    syncCursorLayerToCanvas();
    const rows = cursors;
    layer.innerHTML = rows
      .map((row) => {
        const name = String(row.name || "").replace(/[<>&"]/g, "").trim();
        if (!name) return "";
        const tint = String(row.color || "#0b3d91").replace(/[<>"']/g, "");
        const left = Math.round(Number(row.x) * 1000) / 10;
        const top = Math.round(Number(row.y) * 1000) / 10;
        return `<span class="canvas-cursor-chip" style="left:${left}%;top:${top}%;${cursorChipInkStyle(tint)}">${name}</span>`;
      })
      .join("");
  };

  const hideEditor = () => {
    editor.hidden = true;
    editor.value = "";
    editingId = "";
  };

  /**
   * Commit the open text editor into the session label list.
   */
  const commitEditor = () => {
    if (editor.hidden) return;
    const body = editor.value.trim();
    const id = editingId;
    const left = Number(editor.dataset.nx || "0");
    const top = Number(editor.dataset.ny || "0");
    hideEditor();
    if (!id) return;
    texts = texts.filter((label) => label.id !== id);
    dropUndo("text", id);
    const existing = texts.find((row) => row.id === id);
    const style = {
      bold: Boolean(existing?.bold),
      underline: Boolean(existing?.underline),
      script: existing?.script === "super" || existing?.script === "sub" ? existing.script : "",
      size: Number(existing?.size) || 2,
    };
    if (body) {
      texts.push({
        id,
        x: left,
        y: top,
        text: body,
        color: strokeColor(ownerNow()),
        mine: true,
        ...style,
      });
      ownIds.add(id);
      rememberUndo("text", id);
    }
    replayCommitted();
    if (typeof opts.onText === "function") {
      opts.onText({ id, x: left, y: top, text: body, ...style });
    }
  };

  /**
   * Open the text editor at a normalized point.
   * @param {number} nx
   * @param {number} ny
   * @param {{id?: string, text?: string}|null} existing
   */
  const openEditor = (nx, ny, existing) => {
    if (!(stage instanceof HTMLElement)) return;
    editingId = existing?.id || `tx-${Date.now()}`;
    editor.value = existing?.text || "";
    editor.dataset.nx = String(nx);
    editor.dataset.ny = String(ny);
    editor.hidden = false;
    editor.removeAttribute("hidden");
    editor.style.left = `${nx * canvas.clientWidth}px`;
    editor.style.top = `${ny * canvas.clientHeight}px`;
    if (isFormative()) layoutFormativeEditor();
    lastEditorOpenAt = performance.now();
    window.setTimeout(() => {
      if (editor.hidden) return;
      editor.focus();
      if (!isFormative()) editor.select();
    }, 0);
  };

  /**
   * @param {number} nx
   * @param {number} ny
   * @returns {{id: string, x: number, y: number, text: string} | null}
   */
  const hitText = (nx, ny) => {
    const px = nx * logicalW;
    const py = ny * logicalH;
    for (let i = texts.length - 1; i >= 0; i -= 1) {
      const label = texts[i];
      if (!label.mine) continue;
      const lx = label.x * logicalW;
      const ly = label.y * logicalH;
      const tall = textPxSize(label) + 8;
      if (Math.abs(px - lx) < 120 && py >= ly - 4 && py <= ly + tall) return label;
    }
    return null;
  };

  const hideEqEditor = () => {
    eqEditor.hidden = true;
    eqEditor.setAttribute("hidden", "");
    eqInput.value = "";
    eqPreview.textContent = "";
    editingEquationId = "";
  };

  const commitEqEditor = () => {
    if (eqEditor.hidden) return;
    const body = eqInput.value.trim();
    const id = editingEquationId || `eq-${Date.now()}`;
    const left = Number(eqEditor.dataset.nx || "0");
    const top = Number(eqEditor.dataset.ny || "0");
    hideEqEditor();
    if (!body) {
      equations = equations.filter((eq) => eq.id !== id);
      if (typeof opts.onEquation === "function") opts.onEquation({ id, x: left, y: top, latex: "" });
      replayCommitted();
      return;
    }
    equations = equations.filter((eq) => eq.id !== id);
    const row = {
      id,
      x: left,
      y: top,
      latex: body,
      color: strokeColor(ownerNow()),
      mine: true,
    };
    equations.push(row);
    ownIds.add(id);
    rememberUndo("equation", id);
    replayCommitted();
    if (typeof opts.onEquation === "function") opts.onEquation(row);
  };

  const openEqEditor = (nx, ny, existing) => {
    if (!(stage instanceof HTMLElement)) return;
    editingEquationId = existing?.id || `eq-${Date.now()}`;
    eqInput.value = existing?.latex || "";
    eqEditor.dataset.nx = String(nx);
    eqEditor.dataset.ny = String(ny);
    eqEditor.hidden = false;
    eqEditor.removeAttribute("hidden");
    eqEditor.style.left = `${nx * canvas.clientWidth}px`;
    eqEditor.style.top = `${ny * canvas.clientHeight}px`;
    void katexReady.then((katex) => {
      const latex = eqInput.value.trim();
      if (!latex) {
        eqPreview.textContent = "";
        return;
      }
      if (katex && typeof katex.render === "function") {
        try {
          katex.render(latex, eqPreview, { throwOnError: false, displayMode: false });
          return;
        } catch {
          /* plain text */
        }
      }
      eqPreview.textContent = latex;
    });
    window.setTimeout(() => eqInput.focus(), 0);
  };

  eqInput.addEventListener("input", () => {
    const latex = eqInput.value.trim();
    void katexReady.then((katex) => {
      if (!latex) {
        eqPreview.textContent = "";
        return;
      }
      if (katex && typeof katex.render === "function") {
        try {
          katex.render(latex, eqPreview, { throwOnError: false, displayMode: false });
          return;
        } catch {
          /* plain */
        }
      }
      eqPreview.textContent = latex;
    });
  });
  eqInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitEqEditor();
    } else if (event.key === "Escape") {
      event.preventDefault();
      hideEqEditor();
    }
  });
  eqInput.addEventListener("blur", () => {
    window.setTimeout(() => commitEqEditor(), 0);
  });

  const updateSelectedText = (patch) => {
    const label = selectedText();
    if (!label) return;
    Object.assign(label, patch);
    showTextFormatBar(label);
    pushSelectedText();
  };
  fmtBold.addEventListener("click", () => {
    const label = selectedText();
    if (!label) return;
    updateSelectedText({ bold: !label.bold });
  });
  fmtUnderline.addEventListener("click", () => {
    const label = selectedText();
    if (!label) return;
    updateSelectedText({ underline: !label.underline });
  });
  fmtSuper.addEventListener("click", () => {
    const label = selectedText();
    if (!label) return;
    updateSelectedText({ script: label.script === "super" ? "" : "super" });
  });
  fmtSub.addEventListener("click", () => {
    const label = selectedText();
    if (!label) return;
    updateSelectedText({ script: label.script === "sub" ? "" : "sub" });
  });
  fmtSizeDown.addEventListener("click", () => {
    const label = selectedText();
    if (!label) return;
    const next = Math.max(1, (Number(label.size) || 2) - 1);
    updateSelectedText({ size: next });
  });
  fmtSizeUp.addEventListener("click", () => {
    const label = selectedText();
    if (!label) return;
    const next = Math.min(4, (Number(label.size) || 2) + 1);
    updateSelectedText({ size: next });
  });

  /**
   * True when two logical points are the same ink sample.
   * @param {{x: number, y: number}} a
   * @param {{x: number, y: number}} b
   * @returns {boolean}
   */
  const samePoint = (a, b) => Math.abs(a.x - b.x) < 0.6 && Math.abs(a.y - b.y) < 0.6;

  /**
   * True when ``next`` starts with ``prev`` and may add a tail.
   * @param {{x: number, y: number}[]} prev
   * @param {{x: number, y: number}[]} next
   * @returns {boolean}
   */
  const isExtension = (prev, next) => {
    if (next.length < prev.length) return false;
    for (let i = 0; i < prev.length; i += 1) {
      if (!samePoint(prev[i], next[i])) return false;
    }
    return true;
  };

  /**
   * Logical points from one server stroke.
   * @param {any} stroke
   * @returns {{x: number, y: number}[]}
   */
  const remotePoints = (stroke) =>
    (Array.isArray(stroke?.points) ? stroke.points : [])
      .map((pt) => ({
        x: Number(pt[0]) * logicalW,
        y: Number(pt[1]) * logicalH,
      }))
      .filter((pt) => Number.isFinite(pt.x) && Number.isFinite(pt.y));

  /**
   * Merge remote strokes by id. Own finished and in-progress strokes stay.
   * @param {any[]} remote
   * @returns {boolean} True when the committed layer must be replayed.
   */
  const mergeRemoteStrokes = (remote) => {
    let structural = false;
    /** @type {{stroke: {points: {x: number, y: number}[], color: string}, from: number}[]} */
    const jobs = [];
    /** @type {Set<string>} */
    const remoteIds = new Set();
    for (const raw of remote) {
      const id = String(raw?.id || "");
      if (!id) continue;
      remoteIds.add(id);
      if (ownIds.has(id)) continue;
      const points = remotePoints(raw);
      if (!points.length) continue;
      const strokeColor = String(raw.color || color);
      const existing = strokes.find((stroke) => stroke.id === id);
      if (!existing) {
        const stroke = {
          id,
          owner: String(raw.owner || ""),
          points,
          color: strokeColor,
          mine: Boolean(raw.mine) || String(raw.owner || "") === ownerNow(),
        };
        strokes.push(stroke);
        jobs.push({ stroke, from: 0 });
        continue;
      }
      if (isExtension(existing.points, points) && existing.color === strokeColor) {
        const from = existing.points.length;
        if (from === points.length) continue;
        existing.points = points;
        jobs.push({ stroke: existing, from });
        continue;
      }
      existing.points = points;
      existing.color = strokeColor;
      structural = true;
    }
    const kept = [];
    for (const stroke of strokes) {
      if (ownIds.has(stroke.id) || remoteIds.has(stroke.id)) kept.push(stroke);
      else structural = true;
    }
    strokes = kept;
    if (structural) return true;
    for (const job of jobs) strokeSegment(ctx, job.stroke.points, job.stroke.color, job.from);
    return false;
  };

  /**
   * Replace session labels when the editor is closed.
   * @param {any} view
   * @returns {boolean}
   */
  const applyTexts = (view) => {
    if (editingId || !Array.isArray(view?.texts)) return false;
    const next = view.texts
      .map((label) => ({
        id: String(label.id || ""),
        x: Number(label.x),
        y: Number(label.y),
        text: String(label.text || ""),
        color: String(label.color || color),
        bold: Boolean(label.bold),
        underline: Boolean(label.underline),
        script: label.script === "super" || label.script === "sub" ? label.script : "",
        size: Number(label.size) || 2,
        mine:
          Boolean(label.mine) ||
          String(label.owner || "") === ownerNow() ||
          ownIds.has(String(label.id || "")),
      }))
      .filter((label) => label.id && label.text && Number.isFinite(label.x) && Number.isFinite(label.y));
    const liveTextIds = new Set(next.map((label) => label.id));
    undoStack = undoStack.filter((row) => row.kind !== "text" || liveTextIds.has(row.id));
    const prevSig = texts.map((label) => `${label.id}|${label.text}|${label.x}|${label.y}`).join("\n");
    const nextSig = next.map((label) => `${label.id}|${label.text}|${label.x}|${label.y}`).join("\n");
    if (prevSig === nextSig) return false;
    texts = next;
    return true;
  };

  /**
   * @param {any} view
   * @returns {boolean}
   */
  const applyEquations = (view) => {
    if (editingEquationId || !Array.isArray(view?.equations)) return false;
    const next = view.equations
      .map((eq) => ({
        id: String(eq.id || ""),
        x: Number(eq.x),
        y: Number(eq.y),
        latex: String(eq.latex || ""),
        color: String(eq.color || color),
        mine:
          Boolean(eq.mine) ||
          String(eq.owner || "") === ownerNow() ||
          ownIds.has(String(eq.id || "")),
      }))
      .filter((eq) => eq.id && eq.latex && Number.isFinite(eq.x) && Number.isFinite(eq.y));
    const prevSig = equations.map((eq) => `${eq.id}|${eq.latex}|${eq.x}|${eq.y}`).join("\n");
    const nextSig = next.map((eq) => `${eq.id}|${eq.latex}|${eq.x}|${eq.y}`).join("\n");
    if (prevSig === nextSig) return false;
    equations = next;
    return true;
  };

  editor.addEventListener("input", () => {
    if (!isFormative()) return;
    layoutFormativeEditor();
    replayCommitted();
  });
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && isFormative() && event.shiftKey) {
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      editor.blur();
    } else if (event.key === "Escape") {
      event.preventDefault();
      reopen = null;
      hideEditor();
      editor.blur();
      replayCommitted();
    }
  });
  editor.addEventListener("blur", () => {
    window.setTimeout(() => {
commitEditor();
      if (!reopen) return;
      const next = reopen;
      reopen = null;
      openEditor(next.nx, next.ny, next.hit);
    }, 0);
  });

  /**
   * Commit one straight segment as a normal two-point stroke.
   *
   * @param {{x: number, y: number}} start
   * @param {{x: number, y: number}} end
   */
  const commitLine = (start, end) => {
    if (editingLocked) return;
    strokeOwner = ownerNow();
    color = strokeColor(strokeOwner);
    strokeId = makeStrokeId(strokeOwner);
    ownIds.add(strokeId);
    const points = [
      { x: start.x, y: start.y },
      { x: end.x, y: end.y },
    ];
    const finished = {
      id: strokeId,
      owner: strokeOwner,
      points,
      color: strokeColor(strokeOwner),
      mine: true,
    };
    strokes.push(finished);
    rememberUndo("stroke", finished.id);
    pending = points.slice();
    flushPending(true);
    strokeSegment(ctx, finished.points, finished.color, 0);
    paintLive();
    syncButtons();
    strokeId = "";
  };

  canvas.addEventListener("pointerdown", (event) => {
    shortcutApi?.mark();
    if (editingLocked || !enabled || (typeof opts.canDraw === "function" && !opts.canDraw())) return;
    if (tool === "equation") {
      event.preventDefault();
      const p = norm(point(event));
      hideTextFormatBar();
      if (!eqEditor.hidden) commitEqEditor();
      openEqEditor(p.x, p.y, null);
      return;
    }
    if (tool === "text") {
      event.preventDefault();
      const p = norm(point(event));
      const hit = hitText(p.x, p.y);
      if (hit) showTextFormatBar(hit);
      else hideTextFormatBar();
      const next = { nx: hit ? hit.x : p.x, ny: hit ? hit.y : p.y, hit };
      if (!editor.hidden) {
        if (performance.now() - lastEditorOpenAt < 200) return;
        reopen = next;
        editor.blur();
        return;
      }
      openEditor(next.nx, next.ny, next.hit);
      return;
    }
    if (canDrawLine()) {
      event.preventDefault();
      redoStack = [];
      lining = true;
      lineStart = point(event);
      linePreview = lineStart;
      strokeOwner = ownerNow();
      color = strokeColor(strokeOwner);
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (_err) {
        /* ignore */
      }
      requestPaint();
      syncButtons();
      return;
    }
    if (!canDraw()) return;
    drawing = true;
    redoStack = [];
    strokeOwner = ownerNow();
    color = strokeColor(strokeOwner);
    strokeId = makeStrokeId(strokeOwner);
    ownIds.add(strokeId);
    current = [];
    pending = [];
    pushPoint(point(event), true);
    try {
      canvas.setPointerCapture(event.pointerId);
    } catch (_err) {
      /* Synthetic events and some browsers cannot capture. Drawing still proceeds. */
    }
    requestPaint();
    scheduleFlush();
    syncButtons();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (lining && canDrawLine()) {
      linePreview = point(event);
      requestPaint();
      return;
    }
    if (!drawing || !canDraw()) return;
    let moved = false;
    for (const sample of samplesFrom(event)) {
      if (pushPoint(point(sample), false)) moved = true;
    }
    if (!moved) return;
    requestPaint();
    scheduleFlush();
  });
  /**
   * Finish the open stroke, stamp it on the committed layer, and flush.
   * @param {PointerEvent} event
   */
  const endStroke = (event) => {
    if (!drawing) return;
    if (editingLocked) {
      drawing = false;
      current = [];
      pending = [];
      strokeId = "";
      return;
    }
    const lifted = point(event);
    const added = pushPoint(lifted, true);
    const finished = {
      id: strokeId,
      owner: strokeOwner,
      points: current.slice(),
      color: strokeColor(strokeOwner),
      mine: true,
    };
    if (finished.points.length) {
      strokes.push(finished);
      rememberUndo("stroke", finished.id);
    }
    const id = strokeId;
    drawing = false;
    // Pen-up often repeats the last sample, and pushPoint drops that
    // duplicate. The lift still has to post so the server stores stroke_end.
    if (!added && !pending.length && id) {
      pending.push(current[current.length - 1] || lifted);
    }
    flushPending(true);
    current = [];
    strokeId = "";
    if (finished.points.length) strokeSegment(ctx, finished.points, finished.color, 0);
    paintLive();
    syncButtons();
  };
  /**
   * Finish a line drag preview.
   * @param {PointerEvent} event
   */
  const endLine = (event) => {
    if (!lining) return;
    const start = lineStart;
    const end = linePreview || point(event);
    lining = false;
    lineStart = null;
    linePreview = null;
    if (start) commitLine(start, end);
    else paintLive();
    syncButtons();
  };
  canvas.addEventListener("pointerup", (event) => {
    if (lining) {
      endLine(event);
      return;
    }
    endStroke(event);
  });
  canvas.addEventListener("pointercancel", (event) => {
    if (lining) {
      lining = false;
      lineStart = null;
      linePreview = null;
      paintLive();
      syncButtons();
      return;
    }
    endStroke(event);
  });

  opts.undoBtn?.addEventListener("click", () => {
    shortcutApi?.mark();
    performUndo();
  });
  opts.redoBtn?.addEventListener("click", () => {
    shortcutApi?.mark();
    performRedo();
  });
  opts.penBtn?.addEventListener("click", () => {
    if (editingLocked) return;
    tool = "draw";
    if (!editor.hidden) commitEditor();
    hideEqEditor();
    hideTextFormatBar();
    canvas.style.cursor = "crosshair";
    syncButtons();
  });
  opts.lineBtn?.addEventListener("click", () => {
    if (editingLocked) return;
    tool = "line";
    if (!editor.hidden) commitEditor();
    hideEqEditor();
    hideTextFormatBar();
    canvas.style.cursor = "crosshair";
    syncButtons();
  });
  opts.eraseBtn?.addEventListener("click", () => {
    if (editingLocked || !strokes.length) return;
    const gone = isFormative()
      ? strokes.filter((stroke) => stroke.mine)
      : strokes.slice();
    if (!gone.length) return;
    strokes = isFormative()
      ? strokes.filter((stroke) => !stroke.mine)
      : [];
    const goneIds = new Set(gone.map((stroke) => stroke.id));
    undoStack = undoStack.filter((row) => row.kind !== "stroke" || !goneIds.has(row.id));
    redoStack = [];
    current = [];
    pending = [];
    replayCommitted();
    paintLive();
    syncButtons();
    if (typeof opts.onClear === "function") opts.onClear(gone);
  });
  opts.textBtn?.addEventListener("click", () => {
    if (editingLocked) return;
    tool = tool === "text" ? "draw" : "text";
    if (tool !== "text") {
      commitEditor();
      hideTextFormatBar();
    }
    hideEqEditor();
    canvas.style.cursor = tool === "text" ? "text" : "crosshair";
    syncButtons();
  });
  opts.equationBtn?.addEventListener("click", () => {
    if (editingLocked) return;
    tool = tool === "equation" ? "draw" : "equation";
    if (tool !== "equation") hideEqEditor();
    commitEditor();
    hideTextFormatBar();
    canvas.style.cursor = tool === "equation" ? "crosshair" : "crosshair";
    syncButtons();
  });
  opts.clearBtn?.addEventListener("click", () => {
    if (editingLocked) return;
    const snapshot = ownInkSnapshot();
  const hasInk =
      snapshot.strokes.length || snapshot.texts.length || snapshot.equations.length;
    if (!hasInk) return;
    const wipeId = makeStrokeId(ownerNow());
    performBoardClear(snapshot, wipeId, true);
    if (typeof opts.onWipe === "function") {
      opts.onWipe({ wipe_id: wipeId, snapshot });
    }
  });

  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(() => fitBoard());
    if (stage instanceof Element) observer.observe(stage);
    observer.observe(canvas);
  }
  window.addEventListener("resize", () => fitBoard());
  fitBoard();
  /**
   * Apply a since-seq delta or a snapshot without remounting the canvas.
   *
   * Own strokes are not replaced by server points. ``stroke_remove`` drops
   * that id, including this tab's own undo echo. Errors never clear the
   * board; callers simply do not call this.
   * @param {any} delta
   */
  /** Op keys already folded into this board. A second apply is a no-op. */
  const appliedOpKeys = new Set();

  const applyDelta = (delta) => {
    if (!delta || typeof delta !== "object") return;
    if (delta.snapshot && delta.canvas_view) {
      importRemoteHolder(delta.canvas_view);
      return;
    }
    const ops = []
      .concat(Array.isArray(delta.ops) ? delta.ops : [])
      .concat(Array.isArray(delta.ops_since) ? delta.ops_since : [])
      .concat(Array.isArray(delta.teacher_ops) ? delta.teacher_ops : []);
    if (!ops.length) return;
    let structural = false;
    /** @type {any[]} */
    const cursors = [];
    for (const op of ops) {
      if (!op || typeof op !== "object") continue;
      const type = String(op.type || "");
      const id = String(op.id || op.stroke_id || "");
      const opKey = boardOpKey(op);
      if (opKey && appliedOpKeys.has(opKey)) continue;
      if (opKey) appliedOpKeys.add(opKey);
      if (op.x != null && op.y != null && op.owner) cursors.push(op);
      if (type === "stroke_remove" && id) {
        dropUndo("stroke", id);
        const before = strokes.length;
        strokes = strokes.filter((stroke) => stroke.id !== id);
        if (strokes.length !== before) structural = true;
        continue;
      }
      if (type === "text_upsert") {
        if (!editingId) {
          const body = String(op.text || "").trim();
          texts = texts.filter((label) => label.id !== id);
          if (body && id) {
            texts.push({
              id,
              x: Number(op.x),
              y: Number(op.y),
              text: body,
              color: String(op.color || color),
              bold: Boolean(op.bold),
              underline: Boolean(op.underline),
              script: op.script === "super" || op.script === "sub" ? op.script : "",
              size: Number(op.size) || 2,
              mine: Boolean(op.mine) || String(op.owner || "") === ownerNow(),
            });
          }
          structural = true;
        }
        continue;
      }
      if (type === "equation_upsert") {
        if (!editingEquationId) {
          const latex = String(op.latex || "").trim();
          equations = equations.filter((eq) => eq.id !== id);
          if (latex && id) {
            equations.push({
              id,
              x: Number(op.x),
              y: Number(op.y),
              latex,
              color: String(op.color || color),
              mine: Boolean(op.mine) || String(op.owner || "") === ownerNow(),
            });
          }
          structural = true;
        }
        continue;
      }
      if (type === "board_wipe") {
        const who = String(op.owner || "");
        if (!who || who === ownerNow()) {
          performBoardClear(null, String(op.id || ""), false);
          structural = true;
        }
        continue;
      }
      if (type === "board_unwipe") {
        const who = String(op.owner || "");
        if (!who || who === ownerNow()) {
          restoreOwnerSnapshot(op);
          structural = true;
        }
        continue;
      }
      if (type !== "stroke_add" && type !== "pts_append") continue;
      if (!id || ownIds.has(id)) continue;
      const points = remotePoints({ points: op.points });
      if (!points.length) continue;
      const existing = strokes.find((stroke) => stroke.id === id);
      if (!existing) {
        strokes.push({
          id,
          owner: String(op.owner || ""),
          points,
          color: String(op.color || color),
          mine: Boolean(op.mine) || String(op.owner || "") === ownerNow(),
        });
        structural = true;
        continue;
      }
      // stroke_add does not append points onto an existing stroke
      if (type === "stroke_add") continue;
      const merged = existing.points.concat(points);
      if (isExtension(existing.points, merged)) {
        const from = existing.points.length;
        existing.points = merged;
        strokeSegment(ctx, existing.points, existing.color, from);
      } else {
        existing.points = merged;
        structural = true;
      }
    }
    if (cursors.length) paintCursors(cursors);
    if (structural) replayCommitted();
    syncButtons();
  };

  /**
   * Snapshot paint used by applyDelta. Declared late so importRemote exists.
   * @param {any} view
   */
  function importRemoteHolder(view) {
    boardApi.importRemote(view, { collab: true });
  }

  syncButtons();
  canvas.style.cursor = "crosshair";
  const boardApi = {
    setEnabled: (on) => {
      if (editingLocked) {
        enabled = false;
        hideEditor();
        return;
      }
      enabled = Boolean(on);
      if (!enabled) commitEditor();
    },
    /**
     * Hide the open text box without storing a new label.
     *
     * Submit uses this so an in-progress label does not post after the
     * board is already sent.
     */
    closeTextEntry() {
      reopen = null;
      hideEditor();
    },
    /**
     * Stop strokes, text, undo, redo, and clear on this canvas.
     *
     * The ink already on the board stays. A later ``setEnabled(true)``
     * does not unlock it. Question boards call this after Submit.
     */
    lockEditing() {
      editingLocked = true;
      enabled = false;
      drawing = false;
      lining = false;
      lineStart = null;
      linePreview = null;
      current = [];
      pending = [];
      strokeId = "";
      tool = "draw";
      boardApi.closeTextEntry();
      hideEqEditor();
      hideTextFormatBar();
      canvas.style.cursor = "default";
      syncButtons();
    },
    clear: () => {
      if (editingLocked) return;
      strokes = strokes.filter((s) => !s.mine);
      texts = texts.filter((t) => !t.mine);
      equations = equations.filter((e) => !e.mine);
      redoStack = [];
      current = [];
      pending = [];
      replayCommitted();
      paintLive();
      syncButtons();
    },
    /**
     * Replace local ink with one server view for a new live run.
     *
     * Own-stroke protection and the op-id cache belong to the previous
     * run, so both are cleared. Pending points are not replayed.
     * @param {any} view
     */
    resetRun(view) {
      strokes = [];
      redoStack = [];
      texts = [];
      equations = [];
      current = [];
      pending = [];
      drawing = false;
      ownIds.clear();
      appliedOpKeys.clear();
      wipeSnapshots.clear();
      const incoming = view && typeof view === "object" ? view : {};
      const remote = Array.isArray(incoming.strokes) ? incoming.strokes : [];
      for (const stroke of remote) {
        if (!stroke || typeof stroke !== "object") continue;
        const id = String(stroke.id || "");
        if (!id) continue;
        const points = remotePoints(stroke);
        if (!points.length) continue;
        strokes.push({
          id,
          owner: String(stroke.owner || ""),
          points,
          color: String(stroke.color || color),
          mine: Boolean(stroke.mine) || String(stroke.owner || "") === ownerNow(),
        });
      }
      applyTexts({ texts: incoming.texts });
      applyEquations({ equations: incoming.equations });
      undoStack = [];
      redoStack = [];
      for (const stroke of strokes) {
        if (!stroke.mine) continue;
        ownIds.add(stroke.id);
        undoStack.push({ kind: "stroke", id: stroke.id });
      }
      for (const label of texts) {
        if (!label.mine) continue;
        ownIds.add(label.id);
        undoStack.push({ kind: "text", id: label.id });
      }
      for (const eq of equations) {
        if (!eq.mine) continue;
        ownIds.add(eq.id);
        undoStack.push({ kind: "equation", id: eq.id });
      }
      paintCursors(incoming.cursors || []);
      replayCommitted();
      paintLive();
      syncButtons();
    },
    /**
     * Collaborative boards merge remote strokes instead of replacing them.
     * @param {boolean} on
     */
    setCollab: (on) => {
      collab = Boolean(on);
    },
    /**
     * Paint a server canvas view.
     *
     * Collab merges by stroke id and leaves this tab's own strokes on the
     * pixels that were drawn locally. The canvas element is not replaced.
     * @param {any} view
     * @param {{collab?: boolean}} [options]
     */
    importRemote: (view, options = {}) => {
      if (options.collab != null) collab = Boolean(options.collab);
      const textChanged = applyTexts(view);
      const eqChanged = applyEquations(view);
      paintCursors(view?.cursors);
      let structural = textChanged || eqChanged;
      if (collab && Array.isArray(view?.strokes)) {
        structural = mergeRemoteStrokes(view.strokes) || structural;
      }
      if (structural) replayCommitted();
      syncButtons();
    },
    /**
     * Apply ops since seq, or a snapshot view. Own ink stays put.
     * @param {any} delta
     */
    applyDelta,
    getInk: () => ({
      strokes: strokes.map((s) => ({ ...s, points: s.points.slice() })),
      texts: texts.map((t) => ({ ...t })),
      equations: equations.map((e) => ({ ...e })),
    }),
    openTextAt: (nx, ny, existing = null) => {
      tool = "text";
      syncButtons();
      openEditor(Number(nx), Number(ny), existing);
    },
  };
  /** @type {ReturnType<typeof wireFormativeWhiteboard> | null} */
  let formativeWire = null;
  const ensureFormativeWire = () => {
    if (formativeWire || !hasFormativeShell) return formativeWire;
    formativeWire = wireFormativeWhiteboard(boardApi, {
      canvas,
      stage,
      members: opts.formative.members || [],
      ownerId: ownerNow,
      isTeacher: Boolean(opts.formative.isTeacher),
      isPhone: Boolean(opts.formative.isPhone),
      alwaysShowNames: Boolean(opts.formative.alwaysShowNames),
      projectorShowNames: Boolean(opts.formative.projectorShowNames),
      getInk: () => boardApi.getInk(),
      onEraseStroke: opts.formative.onEraseStroke,
    });
    afterReplay = () => formativeWire?.refresh();
    return formativeWire;
  };
  if (hasFormativeShell) {
    boardApi.setFormative = (patch) => {
      if (!patch || typeof patch !== "object") return;
      if (Array.isArray(patch.members)) {
        opts.formative.members = patch.members;
        formativeActive = patch.members.length > 0;
      }
      if ("alwaysShowNames" in patch) {
        opts.formative.alwaysShowNames = Boolean(patch.alwaysShowNames);
      }
      if ("projectorShowNames" in patch) {
        opts.formative.projectorShowNames = Boolean(patch.projectorShowNames);
      }
      if (!formativeActive) return;
      const wire = ensureFormativeWire();
      if (!wire) return;
      if ("alwaysShowNames" in patch) wire.setAlwaysShowNames(patch.alwaysShowNames);
      if ("projectorShowNames" in patch) wire.setProjectorNames(patch.projectorShowNames);
      if (Array.isArray(patch.members)) wire.setMembers(patch.members);
      wire.refresh();
    };
    boardApi.refreshFormative = () => formativeWire?.refresh();
    if (formativeActive) ensureFormativeWire();
  }
  shortcutApi = {
    undo: () => performUndo(),
    canUndo: () => !editingLocked && Boolean(undoTarget()),
    isShown: () => elementShown(canvas),
    contains: (node) => {
      if (!node) return false;
      if (node === canvas || node === stage || node === opts.undoBtn || node === editor) return true;
      let current = /** @type {{parentElement?: Element | null} | null} */ (node);
      while (current) {
        if (current === stage || current === canvas) return true;
        current = current.parentElement || null;
      }
      return false;
    },
    mark: () => {
      lastUndoBoard = shortcutApi;
    },
  };
  undoBoards.add(shortcutApi);
  installUndoShortcut();
  return boardApi;
}
