/**
 * Live whiteboard: draw, undo, redo, erase, and a Text tool.
 * Individual strokes stay in memory. Group publish and text labels
 * round-trip through the session canvas blob.
 */

/**
 * Bind pointer drawing, text entry, and remote collab paint on one canvas.
 * @param {HTMLCanvasElement} canvas
 * @param {{
 *   canDraw?: () => boolean,
 *   onPoint?: (point: {x: number, y: number}, ended: boolean, strokeId: string) => void,
 *   onCursor?: (point: {x: number, y: number}) => void,
 *   onText?: (label: {id: string, x: number, y: number, text: string}) => void,
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
  if (!ctx) {
    return {
      setEnabled: () => {},
      clear: () => {},
      setCollab: () => {},
      importRemote: () => {},
    };
  }
  /** @type {{points: {x: number, y: number}[], color: string}[]} */
  let strokes = [];
  /** @type {{points: {x: number, y: number}[], color: string}[]} */
  let redoStack = [];
  /** @type {{id: string, x: number, y: number, text: string, color: string, mine: boolean}[]} */
  let texts = [];
  /** @type {{x: number, y: number}[]} */
  let current = [];
  let drawing = false;
  let strokeId = "";
  let enabled = true;
  let collab = false;
  let tool = "draw";
  let editingId = "";
  let lastCursorAt = 0;
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

  const canDraw = () =>
    enabled && tool === "draw" && (typeof opts.canDraw !== "function" || opts.canDraw());

  /**
   * Map a pointer event into canvas pixels.
   * @param {PointerEvent} event
   */
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / Math.max(rect.width, 1)) * canvas.width,
      y: ((event.clientY - rect.top) / Math.max(rect.height, 1)) * canvas.height,
    };
  };

  /**
   * Map canvas pixels into the 0–1 session space.
   * @param {{x: number, y: number}} p
   */
  const norm = (p) => ({
    x: canvas.width ? p.x / canvas.width : 0,
    y: canvas.height ? p.y / canvas.height : 0,
  });

  const redraw = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const paintStroke = (stroke, strokeColor) => {
      const points = stroke.points || stroke;
      if (!points.length) return;
      ctx.beginPath();
      ctx.strokeStyle = strokeColor || color;
      ctx.lineWidth = lineWidth;
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i += 1) {
        ctx.lineTo(points[i].x, points[i].y);
      }
      ctx.stroke();
    };
    for (const stroke of strokes) paintStroke(stroke, stroke.color);
    if (current.length) paintStroke({ points: current }, color);
    ctx.font = "16px sans-serif";
    ctx.textBaseline = "top";
    for (const label of texts) {
      if (!label.text || label.id === editingId) continue;
      ctx.fillStyle = label.color || color;
      ctx.fillText(label.text, label.x * canvas.width, label.y * canvas.height);
    }
  };

  const syncButtons = () => {
    if (opts.undoBtn instanceof HTMLButtonElement) opts.undoBtn.disabled = !strokes.length;
    if (opts.redoBtn instanceof HTMLButtonElement) opts.redoBtn.disabled = !redoStack.length;
    if (opts.eraseBtn instanceof HTMLButtonElement) opts.eraseBtn.disabled = !strokes.length;
    if (opts.textBtn instanceof HTMLButtonElement) {
      opts.textBtn.setAttribute("aria-pressed", tool === "text" ? "true" : "false");
    }
  };

  const emit = (p, ended) => {
    if (typeof opts.onPoint === "function") opts.onPoint(p, ended, strokeId);
  };

  /**
   * @param {{x: number, y: number}} p
   */
  const emitCursor = (p) => {
    if (!collab || typeof opts.onCursor !== "function") return;
    const now = Date.now();
    if (now - lastCursorAt < 80) return;
    lastCursorAt = now;
    opts.onCursor(p);
  };

  /**
   * Paint named cursors over the board.
   * @param {any[]} cursors
   */
  const paintCursors = (cursors) => {
    const layer = opts.cursorLayer;
    if (!(layer instanceof HTMLElement)) return;
    const rows = Array.isArray(cursors) ? cursors : [];
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
    redraw();
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
   */
  const hitText = (nx, ny) => {
    const px = nx * canvas.width;
    const py = ny * canvas.height;
    for (let i = texts.length - 1; i >= 0; i -= 1) {
      const label = texts[i];
      if (!label.mine) continue;
      const lx = label.x * canvas.width;
      const ly = label.y * canvas.height;
      if (Math.abs(px - lx) < 80 && py >= ly - 4 && py <= ly + 22) return label;
    }
    return null;
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
      redraw();
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
    if (!enabled || (typeof opts.canDraw === "function" && !opts.canDraw())) {
      return;
    }
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
    strokeId = `wb-${Date.now()}`;
    const p = point(event);
    current = [p];
    canvas.setPointerCapture(event.pointerId);
    emit(p, false);
    syncButtons();
  });
  canvas.addEventListener("pointermove", (event) => {
    const p = point(event);
    emitCursor(p);
    if (!drawing || !canDraw()) return;
    current.push(p);
    redraw();
    emit(p, false);
  });
  const endStroke = (event) => {
    if (!drawing) return;
    const p = point(event);
    if (current.length) {
      strokes.push({ points: current, color });
    }
    current = [];
    emit(p, true);
    drawing = false;
    strokeId = "";
    redraw();
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
    current = [];
    redraw();
    syncButtons();
  });
  opts.textBtn?.addEventListener("click", () => {
    tool = tool === "text" ? "draw" : "text";
    if (tool !== "text") commitEditor();
    canvas.style.cursor = tool === "text" ? "text" : "crosshair";
    syncButtons();
  });

  syncButtons();
  canvas.style.cursor = "crosshair";
  return {
    setEnabled: (on) => {
      enabled = Boolean(on);
      if (!enabled) commitEditor();
    },
    clear: () => {
      strokes = [];
      redoStack = [];
      texts = [];
      current = [];
      redraw();
      syncButtons();
    },
    /**
     * Collaborative boards replace local strokes with the session view.
     * @param {boolean} on
     */
    setCollab: (on) => {
      collab = Boolean(on);
    },
    /**
     * Paint a server canvas view. Collab mode replaces strokes; text
     * always follows the session so a reload keeps labels.
     * @param {any} view
     * @param {{collab?: boolean}} [options]
     */
    importRemote: (view, options = {}) => {
      if (options.collab != null) collab = Boolean(options.collab);
      if (collab && !drawing) {
        const remote = Array.isArray(view?.strokes) ? view.strokes : [];
        strokes = remote
          .map((stroke) => {
            const points = (Array.isArray(stroke.points) ? stroke.points : [])
              .map((pt) => ({
                x: Number(pt[0]) * canvas.width,
                y: Number(pt[1]) * canvas.height,
              }))
              .filter((pt) => Number.isFinite(pt.x) && Number.isFinite(pt.y));
            return { points, color: String(stroke.color || color) };
          })
          .filter((stroke) => stroke.points.length);
        redoStack = [];
      }
      if (!editingId && Array.isArray(view?.texts)) {
        texts = view.texts
          .map((label) => ({
            id: String(label.id || ""),
            x: Number(label.x),
            y: Number(label.y),
            text: String(label.text || ""),
            color: String(label.color || color),
            mine: Boolean(label.mine),
          }))
          .filter((label) => label.id && label.text);
      }
      paintCursors(view?.cursors);
      redraw();
      syncButtons();
    },
  };
}
