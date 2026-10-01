/**
 * Live whiteboard: draw, undo, redo, erase, and a Text tool.
 *
 * Own ink paints on a live layer in the same frame and is never replaced
 * by a server echo. Finished ink stays on a committed layer and is extended
 * in place. Presence posts are batches, not one request per pointer move.
 */

/** How often a drawing tab flushes queued points while the pointer is down. */
export const PRESENCE_FLUSH_MS = 80;

/**
 * How often a shared-board tab with no LiveNewsWire stream asks for
 * ink since the last sequence. One request at a time; 1.5–2s.
 */
export const BOARD_DELTA_POLL_MS = 1750;

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
 * @param {HTMLElement | null | undefined} anchor
 */
export function showBoardRefreshCue(anchor) {
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
  note.textContent = "Board refreshed for the new class.";
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
 * @returns {{ stop: () => void }}
 */
export function createBoardDeltaPoll(opts) {
  let timer = 0;
  let inFlight = false;
  let stopped = false;
  let delay = 0;

  /**
   * Arm the next poll. A stopped poll does not arm again.
   */
  function schedule() {
    if (stopped) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(run, delay);
  }

  /**
   * One poll. A hidden board waits. A shown board always reads.
   */
  function run() {
    timer = 0;
    if (stopped || inFlight) return;
    if (typeof opts.isShown === "function" && !opts.isShown()) {
      delay = BOARD_DELTA_POLL_MS;
      schedule();
      return;
    }
    inFlight = true;
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
        delay = BOARD_DELTA_POLL_MS;
        if (result && typeof opts.onDelta === "function") opts.onDelta(result);
      })
      .catch(() => {
        delay = Math.min(Math.max(delay, BOARD_DELTA_POLL_MS) * 2, 8000);
      })
      .finally(() => {
        inFlight = false;
        if (!stopped) schedule();
      });
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
 *   eraseBtn?: HTMLElement | null,
 *   textBtn?: HTMLElement | null,
 *   cursorLayer?: HTMLElement | null,
 * }} [opts]
 * @returns {{
 *   setEnabled: (on: boolean) => void,
 *   clear: () => void,
 *   setCollab: (on: boolean) => void,
 *   importRemote: (view: any, options?: {collab?: boolean}) => void,
 * }}
 */
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
  /** @type {{id: string, owner: string, points: {x: number, y: number}[], color: string, mine: boolean}[]} */
  let redoStack = [];
  /** @type {{id: string, x: number, y: number, text: string, color: string, mine: boolean}[]} */
  let texts = [];
  /** @type {Set<string>} */
  const ownIds = new Set();
  /** @type {{x: number, y: number}[]} */
  let current = [];
  /** @type {{x: number, y: number}[]} */
  let pending = [];
  let drawing = false;
  let strokeId = "";
  let strokeOwner = "";
  let enabled = true;
  let collab = false;
  let tool = "draw";
  let editingId = "";
  let flushTimer = 0;
  let paintQueued = false;
  let fitting = false;
  /** @type {{nx: number, ny: number, hit: any}|null} */
  let reopen = null;
  const color = opts.color || "#12202e";
  const lineWidth = opts.lineWidth || 2;
  const stage = canvas.parentElement;
  const editor = document.createElement("input");
  editor.type = "text";
  editor.className = "canvas-text-editor";
  editor.maxLength = 240;
  editor.hidden = true;
  editor.setAttribute("aria-label", "Whiteboard text");
  if (stage) stage.appendChild(editor);

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

  /**
   * Paint session labels on the committed layer.
   */
  const drawTexts = () => {
    ctx.font = "16px sans-serif";
    ctx.textBaseline = "top";
    for (const label of texts) {
      if (!label.text || label.id === editingId) continue;
      ctx.fillStyle = label.color || color;
      ctx.fillText(label.text, label.x * logicalW, label.y * logicalH);
    }
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
  };

  /**
   * Paint the in-progress stroke. Clears only the live layer.
   */
  const paintLive = () => {
    clearLayer(liveCtx, liveCanvas);
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
      if (canvas.width === bw && canvas.height === bh && liveCanvas.width === bw) {
        applyTransform(ctx);
        applyTransform(liveCtx);
        return;
      }
      canvas.width = bw;
      canvas.height = bh;
      liveCanvas.width = bw;
      liveCanvas.height = bh;
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
    if (opts.undoBtn instanceof HTMLButtonElement) opts.undoBtn.disabled = !strokes.length;
    if (opts.redoBtn instanceof HTMLButtonElement) opts.redoBtn.disabled = !redoStack.length;
    if (opts.eraseBtn instanceof HTMLButtonElement) opts.eraseBtn.disabled = !strokes.length;
    if (opts.textBtn instanceof HTMLButtonElement) {
      opts.textBtn.setAttribute("aria-pressed", tool === "text" ? "true" : "false");
    }
  };

  /**
   * Paint named cursors over the board. Hover does not post a cursor.
   * @param {any[]} cursors
   */
  const paintCursors = (cursors) => {
    const layer = opts.cursorLayer;
    if (!(layer instanceof HTMLElement)) return;
    if (!Array.isArray(cursors)) return;
    const rows = cursors;
    layer.innerHTML = rows
      .map((row) => {
        const name = String(row.name || row.owner || "").replace(/[<>&"]/g, "");
        const tint = String(row.color || "#0b3d91").replace(/[<>"']/g, "");
        const left = Math.round(Number(row.x) * 1000) / 10;
        const top = Math.round(Number(row.y) * 1000) / 10;
        return `<span class="canvas-cursor-chip" style="left:${left}%;top:${top}%;color:${tint}">${name}</span>`;
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
    if (body) {
      texts.push({ id, x: left, y: top, text: body, color, mine: true });
    }
    replayCommitted();
    if (typeof opts.onText === "function") {
      opts.onText({ id, x: left, y: top, text: body });
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
    editor.style.left = `${nx * canvas.clientWidth}px`;
    editor.style.top = `${ny * canvas.clientHeight}px`;
    editor.focus();
    editor.select();
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
      if (Math.abs(px - lx) < 80 && py >= ly - 4 && py <= ly + 22) return label;
    }
    return null;
  };

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
          mine: false,
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
        mine: Boolean(label.mine),
      }))
      .filter((label) => label.id && label.text && Number.isFinite(label.x) && Number.isFinite(label.y));
    const prevSig = texts.map((label) => `${label.id}|${label.text}|${label.x}|${label.y}`).join("\n");
    const nextSig = next.map((label) => `${label.id}|${label.text}|${label.x}|${label.y}`).join("\n");
    if (prevSig === nextSig) return false;
    texts = next;
    return true;
  };

  editor.addEventListener("keydown", (event) => {
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

  canvas.addEventListener("pointerdown", (event) => {
    if (!enabled || (typeof opts.canDraw === "function" && !opts.canDraw())) return;
    if (tool === "text") {
      event.preventDefault();
      const p = norm(point(event));
      const hit = hitText(p.x, p.y);
      const next = { nx: hit ? hit.x : p.x, ny: hit ? hit.y : p.y, hit };
      if (!editor.hidden) {
        reopen = next;
        editor.blur();
        return;
      }
      openEditor(next.nx, next.ny, next.hit);
      return;
    }
    if (!canDraw()) return;
    drawing = true;
    redoStack = [];
    strokeOwner = ownerNow();
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
    const lifted = point(event);
    const added = pushPoint(lifted, true);
    const finished = {
      id: strokeId,
      owner: strokeOwner,
      points: current.slice(),
      color,
      mine: true,
    };
    if (finished.points.length) strokes.push(finished);
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
  canvas.addEventListener("pointerup", endStroke);
  canvas.addEventListener("pointercancel", endStroke);

  opts.undoBtn?.addEventListener("click", () => {
    if (!strokes.length) return;
    const last = strokes.pop();
    if (last) redoStack.push(last);
    if (last && last.mine && collab && typeof opts.onUndo === "function") {
      opts.onUndo(last);
    }
    replayCommitted();
    syncButtons();
  });
  opts.redoBtn?.addEventListener("click", () => {
    if (!redoStack.length) return;
    const next = redoStack.pop();
    if (next) strokes.push(next);
    if (next && next.mine && collab && typeof opts.onRedo === "function") {
      opts.onRedo(next);
    }
    replayCommitted();
    syncButtons();
  });
  opts.eraseBtn?.addEventListener("click", () => {
    if (!strokes.length) return;
    const gone = strokes.slice();
    strokes = [];
    redoStack = [];
    current = [];
    pending = [];
    replayCommitted();
    paintLive();
    syncButtons();
    if (typeof opts.onClear === "function") opts.onClear(gone);
  });
  opts.textBtn?.addEventListener("click", () => {
    tool = tool === "text" ? "draw" : "text";
    if (tool !== "text") commitEditor();
    canvas.style.cursor = tool === "text" ? "text" : "crosshair";
    syncButtons();
  });

  if (typeof ResizeObserver === "function" && stage instanceof Element) {
    const observer = new ResizeObserver(() => fitBoard());
    observer.observe(stage);
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
              mine: Boolean(op.mine) || String(op.owner || "") === ownerNow(),
            });
          }
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
          mine: false,
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
      enabled = Boolean(on);
      if (!enabled) commitEditor();
    },
    clear: () => {
      strokes = [];
      redoStack = [];
      texts = [];
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
      current = [];
      pending = [];
      drawing = false;
      ownIds.clear();
      appliedOpKeys.clear();
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
          mine: false,
        });
      }
      applyTexts({ texts: incoming.texts });
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
      paintCursors(view?.cursors);
      let structural = textChanged;
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
  };
  return boardApi;
}
