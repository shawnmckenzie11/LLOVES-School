/**
 * MCK-218 Formative-style whiteboard: member colours, one chip per item,
 * text commit-only sync, and erase limited to the caller's ink.
 */

export const FORMATIVE_PALETTE = [
  "#2dd4bf",
  "#f59e0b",
  "#a78bfa",
  "#f472b6",
  "#60a5fa",
  "#a3e635",
];

const MERGE_MS = 1500;
const MERGE_PX = 40;
const CHIP_HIDE_MS_PHONE = 3000;
const CHIP_FADE_MS = 200;
const CHIP_HOVER_GRACE_MS = 2000;
const MIN_TEXT_WIDTH = 120;

/**
 * @param {string | undefined} flag
 * @returns {boolean}
 */
export function formativeFlagEnabled(flag) {
  if (flag === false) return false;
  return true;
}

/**
 * @param {Record<string, {color?: string}>} membersById
 * @param {string} owner
 * @returns {string}
 */
export function colorForOwner(membersById, owner) {
  const row = membersById[String(owner || "")];
  if (row && row.color) return row.color;
  const slot = Number(row?.slot);
  if (Number.isFinite(slot)) return FORMATIVE_PALETTE[slot % FORMATIVE_PALETTE.length];
  return FORMATIVE_PALETTE[0];
}

/**
 * Whether collab ink shows every name.
 *
 * The projector-names toggle is hidden for the whole collab session, so
 * a saved true is ignored. Always-show names still labels the board.
 *
 * @param {boolean} alwaysShowNames
 * @param {boolean} [_projectorShowNames]
 * @returns {boolean}
 */
export function collabForcesNames(alwaysShowNames, _projectorShowNames) {
  return Boolean(alwaysShowNames);
}

/**
 * On-board name labels stay off. The colour legend names the ink.
 *
 * @returns {boolean}
 */
export function boardNameChipsEnabled() {
  return false;
}

/**
 * Names a legend would paint. Blank rows are left out.
 *
 * @param {any[] | undefined} members
 * @returns {string[]}
 */
export function legendChipLabels(members) {
  return (Array.isArray(members) ? members : [])
    .map((member) => String((member && member.name) || "").trim())
    .filter(Boolean);
}

/**
 * Legend rows for a board shown on a student screen.
 *
 * Colours stay so ink still matches. Names are omitted. The teacher
 * screen and the projector keep the original rows.
 *
 * @param {any[] | undefined} members
 * @returns {{id: string, name: string, color: string, slot?: number}[]}
 */
export function studentShownMembers(members) {
  return (Array.isArray(members) ? members : []).map((row) => ({
    id: row && row.id != null ? String(row.id) : "",
    name: "",
    color: row && row.color ? String(row.color) : "",
    slot: Number.isFinite(Number(row && row.slot)) ? Number(row.slot) : 0,
  }));
}

/**
 * How a student paints a board the teacher is showing.
 *
 * The owner's own board stays writable so an open stroke, Pen, Text,
 * and Erase survive. Any other board is view only.
 * @param {string} ownKey Board the viewer is drawing on.
 * @param {string} shownKey Board the teacher projected.
 * @returns {"show-own" | "show-class"}
 */
export function classBoardPaintMode(ownKey, shownKey) {
  const shown = String(shownKey || "");
  const own = String(ownKey || "");
  if (shown && own && shown === own) return "show-own";
  return "show-class";
}

/**
 * Legend element already in the page, or a new one above the stage.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {HTMLElement} stage
 * @returns {HTMLElement}
 */
function legendHost(canvas, stage) {
  const root = canvas.closest("#formative-live-board, #canvas-pane") || stage.parentElement;
  const found = root?.querySelector("#wb-live-legend, #class-board-legend");
  if (found instanceof HTMLElement) {
    found.classList.add("wb-formative-legend");
    return found;
  }
  const created = document.createElement("div");
  created.className = "wb-formative-legend";
  stage.parentElement?.insertBefore(created, stage);
  return created;
}

/**
 * Inline style for a cursor-name chip on the white student canvas.
 *
 * Text stays near-black on a white chip. The member colour is the
 * border, with a dark outline so the chip separates from the board.
 *
 * @param {string} tint
 * @returns {string}
 */
export function cursorChipInkStyle(tint) {
  const raw = String(tint || "");
  const safe = /^#[0-9a-fA-F]{3,8}$/.test(raw) ? raw : "#0b3d91";
  return `--cursor-tint:${safe};color:#0f172a;background:#fff;box-shadow:0 0 0 1px #0f172a;border:2px solid ${safe}`;
}

/**
 * Merge pen-down/up strokes from the same author when close in time and space.
 *
 * @param {any[]} strokes
 * @param {{owner?: string, endedAt?: number, points?: {x:number,y:number}[]}[]} meta
 * @returns {any[]}
 */
export function mergeStrokeItems(strokes, meta = []) {
  /** @type {any[]} */
  const items = [];
  const byId = new Map();
  for (const stroke of strokes) {
    if (!stroke || !Array.isArray(stroke.points) || !stroke.points.length) continue;
    const owner = String(stroke.owner || "");
    const last = items.length ? items[items.length - 1] : null;
    const endedAt = Number(stroke.endedAt || stroke.t || Date.now());
    const start = stroke.points[0];
    if (
      last &&
      last.kind === "stroke" &&
      String(last.owner) === owner &&
      endedAt - Number(last.endedAt || 0) <= MERGE_MS &&
      last.points.length &&
      Math.hypot(start.x - last.points[0].x, start.y - last.points[0].y) <= MERGE_PX
    ) {
      last.points = last.points.concat(stroke.points.slice(1));
      last.strokeIds.push(stroke.id);
      last.endedAt = endedAt;
      continue;
    }
    const item = {
      kind: "stroke",
      id: `item-${stroke.id}`,
      owner,
      points: stroke.points.slice(),
      strokeIds: [stroke.id],
      endedAt,
      source: stroke,
    };
    items.push(item);
    byId.set(stroke.id, item);
  }
  return items;
}

/**
 * @param {any[]} strokes
 * @param {any[]} texts
 * @returns {any[]}
 */
export function boardItemsFromInk(strokes, texts) {
  const strokeItems = mergeStrokeItems(strokes);
  const textItems = (Array.isArray(texts) ? texts : [])
    .filter((t) => t && t.text && t.id)
    .map((t) => ({
      kind: "text",
      id: `text-${t.id}`,
      owner: String(t.owner || ""),
      x: Number(t.x),
      y: Number(t.y),
      text: String(t.text),
      source: t,
    }));
  return strokeItems.concat(textItems);
}

/**
 * Wire formative chrome onto an existing ``bindWhiteboard`` instance.
 *
 * @param {ReturnType<import("./live_whiteboard.js").bindWhiteboard>} boardApi
 * @param {{
 *   canvas: HTMLCanvasElement,
 *   stage: HTMLElement | null,
 *   members: {id: string, name: string, color?: string, dashed?: boolean}[],
 *   ownerId: () => string,
 *   isTeacher?: boolean,
 *   isPhone?: boolean,
 *   alwaysShowNames?: boolean,
 *   projectorShowNames?: boolean,
 *   onEraseStroke?: (strokeId: string) => void,
 *   getInk?: () => {strokes: any[], texts: any[]},
 * }} opts
 */
export function wireFormativeWhiteboard(boardApi, opts) {
  const canvas = opts.canvas;
  const stage = opts.stage || canvas.parentElement;
  if (!(stage instanceof HTMLElement)) return;

  const chipLayer = document.createElement("div");
  chipLayer.className = "wb-formative-chips";
  chipLayer.setAttribute("aria-hidden", "true");
  stage.appendChild(chipLayer);

  const legend = legendHost(canvas, stage);

  const membersById = {};
  for (const m of opts.members || []) {
    membersById[String(m.id)] = m;
  }

  let alwaysShow = Boolean(opts.alwaysShowNames);
  let projectorNames = Boolean(opts.projectorShowNames);
  let hoverItemId = "";
  let hoverHideTimer = 0;
  /** @type {Map<string, number>} */
  const phoneFlashUntil = new Map();

  /**
   * @param {any[]} items
   */
  function syncChipLayerToCanvas() {
    if (!(stage instanceof HTMLElement)) return;
    const stageRect = stage.getBoundingClientRect();
    const rect = canvas.getBoundingClientRect();
    chipLayer.style.position = "absolute";
    chipLayer.style.left = `${rect.left - stageRect.left}px`;
    chipLayer.style.top = `${rect.top - stageRect.top}px`;
    chipLayer.style.width = `${rect.width}px`;
    chipLayer.style.height = `${rect.height}px`;
    chipLayer.style.right = "auto";
    chipLayer.style.bottom = "auto";
  }

  function paintChips(_items) {
    chipLayer.innerHTML = "";
    syncChipLayerToCanvas();
    const showNames = collabForcesNames(alwaysShow, projectorNames);
    if (!boardNameChipsEnabled() || !showNames) return;
  }

  function paintLegend() {
    legend.replaceChildren();
    const labels = new Set(legendChipLabels(opts.members));
    const rows = (opts.members || []).filter((member) =>
      labels.has(String(member.name || "").trim())
    );
    for (const member of rows) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wb-legend-chip";
      btn.dataset.member = String(member.id || "");
      const dot = document.createElement("span");
      dot.className = "wb-legend-dot";
      dot.style.background = String(member.color || "#94a3b8");
      btn.append(dot, document.createTextNode(String(member.name || "")));
      btn.addEventListener("click", () => {
        const id = btn.dataset.member || "";
        const items = boardItemsFromInk(
          opts.getInk?.().strokes || [],
          opts.getInk?.().texts || []
        );
        for (const item of items) {
          if (String(item.owner) === id) {
            phoneFlashUntil.set(item.id, Date.now() + CHIP_HIDE_MS_PHONE);
          }
        }
        refreshChips();
      });
      legend.appendChild(btn);
    }
    legend.hidden = rows.length === 0;
  }

  function refreshChips() {
    const ink = opts.getInk?.() || { strokes: [], texts: [] };
    paintChips(boardItemsFromInk(ink.strokes, ink.texts));
  }

  paintLegend();
  refreshChips();

  canvas.addEventListener("mousemove", (event) => {
    if (opts.isPhone) return;
    const ink = opts.getInk?.() || { strokes: [], texts: [] };
    const items = boardItemsFromInk(ink.strokes, ink.texts);
    const p = canvasPoint(canvas, event);
    const hit = hitItem(items, p, canvas);
    if (hit?.id === hoverItemId) return;
    hoverItemId = hit?.id || "";
    if (hoverHideTimer) window.clearTimeout(hoverHideTimer);
    refreshChips();
    if (hoverItemId) {
      hoverHideTimer = window.setTimeout(() => {
        hoverItemId = "";
        refreshChips();
      }, CHIP_HOVER_GRACE_MS);
    }
  });

  canvas.addEventListener(
    "click",
    (event) => {
      if (!opts.isPhone) return;
      const ink = opts.getInk?.() || { strokes: [], texts: [] };
      const items = boardItemsFromInk(ink.strokes, ink.texts);
      const hit = hitItem(items, canvasPoint(canvas, event), canvas);
      if (!hit) return;
      phoneFlashUntil.set(hit.id, Date.now() + CHIP_HIDE_MS_PHONE);
      refreshChips();
    },
    true
  );

  return {
    setAlwaysShowNames(on) {
      alwaysShow = Boolean(on);
      refreshChips();
    },
    setProjectorNames(on) {
      projectorNames = Boolean(on);
      refreshChips();
    },
    setMembers(members) {
      opts.members = members;
      for (const m of members) membersById[String(m.id)] = m;
      paintLegend();
      refreshChips();
    },
    refresh: refreshChips,
    flashNewItem(itemId) {
      phoneFlashUntil.set(itemId, Date.now() + CHIP_HIDE_MS_PHONE);
      refreshChips();
    },
  };
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {PointerEvent | MouseEvent} event
 */
function canvasPoint(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  const lw = Number(canvas.dataset.logicalWidth) || canvas.width;
  const lh = Number(canvas.dataset.logicalHeight) || canvas.height;
  return {
    x: ((event.clientX - rect.left) / Math.max(rect.width, 1)) * lw,
    y: ((event.clientY - rect.top) / Math.max(rect.height, 1)) * lh,
  };
}

/**
 * @param {any[]} items
 * @param {{x:number,y:number}} p
 * @param {HTMLCanvasElement} canvas
 */
function hitItem(items, p, canvas) {
  const lw = Number(canvas.dataset.logicalWidth) || canvas.width;
  const lh = Number(canvas.dataset.logicalHeight) || canvas.height;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item.kind === "text") {
      const lx = item.x * lw;
      const ly = item.y * lh;
      if (p.x >= lx && p.x <= lx + Math.min(MIN_TEXT_WIDTH, lw - lx - 8) && p.y >= ly && p.y <= ly + 80) {
        return item;
      }
      continue;
    }
    for (const pt of item.points || []) {
      if (Math.hypot(p.x - pt.x, p.y - pt.y) < 14) return item;
    }
  }
  return null;
}

export { MIN_TEXT_WIDTH, CHIP_HIDE_MS_PHONE, CHIP_FADE_MS, MERGE_MS, MERGE_PX };
