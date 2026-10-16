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

  const legend = document.createElement("div");
  legend.className = "wb-formative-legend";
  stage.parentElement?.insertBefore(legend, stage);

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

  function paintChips(items) {
    chipLayer.innerHTML = "";
    syncChipLayerToCanvas();
    const rect = canvas.getBoundingClientRect();
    const lw = Number(canvas.dataset.logicalWidth) || canvas.width;
    const lh = Number(canvas.dataset.logicalHeight) || canvas.height;
    const now = Date.now();
    const phone = Boolean(opts.isPhone);
    const showAll = collabForcesNames(alwaysShow, projectorNames);
    for (const item of items) {
      const member = membersById[String(item.owner)] || {
        name: String(item.owner || ""),
        color: colorForOwner(membersById, item.owner),
      };
      let visible = showAll;
      if (!visible && phone) {
        visible = (phoneFlashUntil.get(item.id) || 0) > now;
      }
      if (!visible && !phone && hoverItemId === item.id) visible = true;
      if (!visible) continue;

      const chip = document.createElement("span");
      chip.className = "name-chip";
      chip.textContent = member.name || item.owner;
      chip.style.backgroundColor = member.color || colorForOwner(membersById, item.owner);
      let ax = 0;
      let ay = 0;
      if (item.kind === "stroke" && item.points?.length) {
        ax = item.points[0].x;
        ay = item.points[0].y;
      } else if (item.kind === "text") {
        ax = item.x * lw;
        ay = item.y * lh;
      }
      const left = Math.max(8, Math.min(rect.width - 8, (ax / lw) * rect.width - 8));
      const top = Math.max(8, Math.min(rect.height - 8, (ay / lh) * rect.height - 8));
      chip.style.left = `${left}px`;
      chip.style.top = `${top}px`;
      chip.dataset.itemId = item.id;
      chipLayer.appendChild(chip);
    }
  }

  function paintLegend() {
    legend.innerHTML = (opts.members || [])
      .map((m) => {
        const dot = `<span class="wb-legend-dot" style="background:${m.color}"></span>`;
        return `<button type="button" class="wb-legend-chip" data-member="${m.id}">${dot}${m.name}</button>`;
      })
      .join("");
    legend.querySelectorAll("[data-member]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-member") || "";
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
    });
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
