/**
 * Ephemeral whiteboard with undo, redo, and erase.
 * Strokes stay in memory only — nothing is persisted.
 */

/**
 * Bind pointer drawing plus undo / redo / erase on one canvas.
 * @param {HTMLCanvasElement} canvas
 * @param {{
 *   canDraw?: () => boolean,
 *   onPoint?: (point: {x: number, y: number}, ended: boolean, strokeId: string) => void,
 *   color?: string,
 *   lineWidth?: number,
 *   undoBtn?: HTMLElement | null,
 *   redoBtn?: HTMLElement | null,
 *   eraseBtn?: HTMLElement | null,
 * }} [opts]
 * @returns {{ setEnabled: (on: boolean) => void, clear: () => void }}
 */
export function bindWhiteboard(canvas, opts = {}) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return { setEnabled: () => {}, clear: () => {} };
  /** @type {{x: number, y: number}[][]} */
  let strokes = [];
  /** @type {{x: number, y: number}[][]} */
  let redoStack = [];
  /** @type {{x: number, y: number}[]} */
  let current = [];
  let drawing = false;
  let strokeId = "";
  let enabled = true;
  const color = opts.color || "#12202e";
  const lineWidth = opts.lineWidth || 2;

  const canDraw = () => enabled && (typeof opts.canDraw !== "function" || opts.canDraw());

  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  };

  const redraw = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const stroke of strokes) {
      if (!stroke.length) continue;
      ctx.beginPath();
      ctx.moveTo(stroke[0].x, stroke[0].y);
      for (let i = 1; i < stroke.length; i += 1) {
        ctx.lineTo(stroke[i].x, stroke[i].y);
      }
      ctx.stroke();
    }
  };

  const syncButtons = () => {
    if (opts.undoBtn instanceof HTMLButtonElement) opts.undoBtn.disabled = !strokes.length;
    if (opts.redoBtn instanceof HTMLButtonElement) opts.redoBtn.disabled = !redoStack.length;
    if (opts.eraseBtn instanceof HTMLButtonElement) opts.eraseBtn.disabled = !strokes.length;
  };

  const emit = (p, ended) => {
    if (typeof opts.onPoint === "function") opts.onPoint(p, ended, strokeId);
  };

  canvas.addEventListener("pointerdown", (event) => {
    if (!canDraw()) return;
    drawing = true;
    redoStack = [];
    strokeId = `wb-${Date.now()}`;
    const p = point(event);
    current = [p];
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    canvas.setPointerCapture(event.pointerId);
    emit(p, false);
    syncButtons();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing || !canDraw()) return;
    const p = point(event);
    current.push(p);
    ctx.lineTo(p.x, p.y);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();
    emit(p, false);
  });
  const endStroke = (event) => {
    if (!drawing) return;
    const p = point(event);
    if (current.length) strokes.push(current);
    current = [];
    emit(p, true);
    drawing = false;
    strokeId = "";
    syncButtons();
  };
  canvas.addEventListener("pointerup", endStroke);
  canvas.addEventListener("pointercancel", endStroke);

  opts.undoBtn?.addEventListener("click", () => {
    if (!strokes.length) return;
    const last = strokes.pop();
    if (last) redoStack.push(last);
    redraw();
    syncButtons();
  });
  opts.redoBtn?.addEventListener("click", () => {
    if (!redoStack.length) return;
    const next = redoStack.pop();
    if (next) strokes.push(next);
    redraw();
    syncButtons();
  });
  opts.eraseBtn?.addEventListener("click", () => {
    if (!strokes.length) return;
    strokes = [];
    redoStack = [];
    redraw();
    syncButtons();
  });

  syncButtons();
  return {
    setEnabled: (on) => {
      enabled = Boolean(on);
    },
    clear: () => {
      strokes = [];
      redoStack = [];
      redraw();
      syncButtons();
    },
  };
}
