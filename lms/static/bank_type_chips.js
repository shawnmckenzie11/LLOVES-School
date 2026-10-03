/**
 * MCK-170: the Type chip row for bank question lists.
 *
 * One single-select row: "All" first, then each question type in the
 * current results with its count (``type_counts`` from mc-search). It
 * stays on one line; chips that do not fit move into a "More" panel
 * (a disclosure: a button plus a group of chips, no ARIA menu). "All" and
 * the selected chip always stay on the row. Shared by the Question banks
 * tab and Run Live Class Import from bank.
 * The pure helpers at the top are checked in node (bank_type_chips.test.mjs).
 */

/** Readable labels, mirroring ``lms/bank_question_types.py``. */
export const TYPE_LABELS = {
  mc: "Multiple choice",
  true_false: "True/false",
  numeric: "Numeric",
  short_answer: "Short answer",
  rank: "Rank",
  open: "Open-ended",
};

/**
 * Build the chip list: All first, then each counted type in server order.
 * The selected type stays in the list at 0 so the teacher can see and
 * clear a filter that matches nothing in the current results.
 * @param {Array<{type?: string, label?: string, count?: number}>} typeCounts
 * @param {string} selected Canonical type, or "" for All.
 * @returns {Array<{type: string, label: string, count: number, pressed: boolean}>}
 */
export function typeChipModel(typeCounts, selected) {
  const rows = Array.isArray(typeCounts) ? typeCounts : [];
  const chips = [];
  let all = 0;
  for (const row of rows) {
    const type = String(row?.type || "");
    const count = Math.max(0, Number(row?.count) || 0);
    if (!type || !count) continue;
    all += count;
    chips.push({
      type,
      label: String(row?.label || TYPE_LABELS[type] || type),
      count,
      pressed: type === selected,
    });
  }
  if (selected && !chips.some((chip) => chip.type === selected)) {
    chips.push({ type: selected, label: TYPE_LABELS[selected] || selected, count: 0, pressed: true });
  }
  return [{ type: "", label: "All", count: all, pressed: !selected }, ...chips];
}

/**
 * Pick which chips show on the row and which go into the More panel.
 * Keeps chip order. "All" (index 0) and the selected chip are pinned: they
 * are always on the row and never move into More. Other chips fill in
 * order while they fit. When even the pinned pair plus More is wider than
 * ``available`` the pinned chips still show; the CSS then lets the
 * selected chip's label shrink with an ellipsis so the row never overflows.
 * @param {{widths: number[], available: number, moreWidth: number, gap: number, selectedIndex: number}} opts
 * @returns {{visible: number[], overflow: number[]}}
 */
export function fitTypeChips({ widths, available, moreWidth, gap, selectedIndex }) {
  const all = widths.map((_, index) => index);
  const span = (indices) =>
    indices.reduce((sum, index) => sum + widths[index], 0) + Math.max(0, indices.length - 1) * gap;
  if (span(all) <= available) return { visible: all, overflow: [] };
  const room = available - moreWidth - gap;
  let visible = [...new Set([0, selectedIndex])].filter((index) => index >= 0 && index < widths.length);
  for (const index of all) {
    if (visible.includes(index)) continue;
    const next = [...visible, index].sort((a, b) => a - b);
    if (span(next) > room) break;
    visible = next;
  }
  visible.sort((a, b) => a - b);
  return { visible, overflow: all.filter((index) => !visible.includes(index)) };
}

/**
 * Read the session's type for one view; unknown values read as All.
 * @param {Storage | null | undefined} storage
 * @param {string} key
 */
export function readStoredType(storage, key) {
  try {
    const value = String(storage?.getItem(key) || "");
    return Object.prototype.hasOwnProperty.call(TYPE_LABELS, value) ? value : "";
  } catch {
    return "";
  }
}

/**
 * Remember the session's type for one view ("" clears it).
 * @param {Storage | null | undefined} storage
 * @param {string} key
 * @param {string} value
 */
export function storeType(storage, key, value) {
  try {
    if (value) storage?.setItem(key, value);
    else storage?.removeItem(key);
  } catch {
    /* private mode: the filter still works for this page */
  }
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
}

function chipInner(chip) {
  return `<span class="bank-type-chip-label">${esc(chip.label)}</span> <span class="bank-type-chip-count">${chip.count}</span>`;
}

/**
 * Where focus should land after a repaint, or null to leave focus alone.
 * Prefers the chip for ``type`` when it is on the row, then the More
 * button when it is shown, then the pressed chip.
 * @param {{type: string, visible: boolean, pressed: boolean}[]} chips Row chips in order.
 * @param {string | null} type Canonical type ("" for All), or null for "the More button".
 * @param {boolean} moreShown
 * @returns {{chip: string} | {more: true} | null}
 */
export function focusTarget(chips, type, moreShown) {
  if (type !== null) {
    const chip = chips.find((c) => c.type === type);
    if (chip?.visible) return { chip: chip.type };
  }
  if (moreShown) return { more: true };
  const pressed = chips.find((c) => c.pressed && c.visible);
  return pressed ? { chip: pressed.type } : null;
}

let mountSeq = 0;

/**
 * Mount the Type chip row into ``host`` (an empty element in a filter row).
 * @param {HTMLElement} host
 * @param {{view: string, onChange: (type: string) => void}} opts ``view`` keys
 *   the sessionStorage entry, e.g. ``import`` or ``question-banks``.
 * @returns {{value: () => string, update: (typeCounts: unknown[]) => void, destroy: () => void}}
 *   Call ``destroy`` when the host goes away (the Import picker does on close).
 */
export function mountTypeChips(host, opts) {
  const key = `lloves.bankType.${opts.view}`;
  const storage = typeof window !== "undefined" ? window.sessionStorage : null;
  const doc = host.ownerDocument;
  const menuId = `bank-type-more-${opts.view}-${++mountSeq}`;
  let selected = readStoredType(storage, key);
  let model = typeChipModel([], selected);
  let destroyed = false;
  /** @type {ResizeObserver | null} */
  let resizeObserver = null;
  host.classList.add("bank-type-chips");
  host.innerHTML = `
    <div class="bank-type-chip-row" role="group" aria-label="Question type" data-bank-type-row></div>
    <div class="bank-type-more-menu" id="${esc(menuId)}" role="group" aria-label="More question types" data-bank-type-menu hidden></div>`;
  const row = host.querySelector("[data-bank-type-row]");
  const menu = host.querySelector("[data-bank-type-menu]");
  const moreBtn = () => row.querySelector("[data-bank-type-more]");

  /** Close the panel when a click lands outside the chips (attached only while open). */
  const onDocClick = (event) => {
    const target = event.target;
    if (target && typeof target === "object" && "nodeType" in target && host.contains(target)) return;
    closeMenu();
  };
  const openMenu = () => {
    if (destroyed || !menu.hidden) return;
    menu.hidden = false;
    moreBtn()?.setAttribute("aria-expanded", "true");
    doc.addEventListener("click", onDocClick);
  };
  const closeMenu = () => {
    doc.removeEventListener("click", onDocClick);
    menu.hidden = true;
    moreBtn()?.setAttribute("aria-expanded", "false");
  };

  /** Focus a chip by type, else More, else the pressed chip (MCK-170 MED-1). */
  const restoreFocus = (type) => {
    const chips = [...row.querySelectorAll("[data-bank-type]")];
    const more = moreBtn();
    const target = focusTarget(
      chips.map((el) => ({
        type: el.getAttribute("data-bank-type") || "",
        visible: !el.hidden,
        pressed: el.getAttribute("aria-pressed") === "true",
      })),
      type,
      Boolean(more && !more.hidden)
    );
    if (!target) return;
    const el = "more" in target ? more : chips.find((c) => (c.getAttribute("data-bank-type") || "") === target.chip);
    el?.focus();
  };

  const layout = () => {
    if (destroyed) return;
    const chips = [...row.querySelectorAll("[data-bank-type]")];
    const more = moreBtn();
    if (!more || !row.clientWidth) return;
    chips.forEach((chip) => {
      chip.hidden = false;
    });
    more.hidden = false;
    // Measure natural widths: chips may shrink on the row (pinned overflow).
    row.classList.add("is-measuring");
    const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
    const widths = chips.map((chip) => chip.getBoundingClientRect().width);
    const moreWidth = more.getBoundingClientRect().width;
    row.classList.remove("is-measuring");
    const { visible, overflow } = fitTypeChips({
      widths,
      available: row.clientWidth,
      moreWidth,
      gap,
      selectedIndex: model.findIndex((chip) => chip.pressed),
    });
    chips.forEach((chip, index) => {
      chip.hidden = !visible.includes(index);
    });
    more.hidden = !overflow.length;
    menu.innerHTML = overflow
      .map((index) => {
        const chip = model[index];
        return `<button type="button" aria-pressed="${chip.pressed}" data-bank-type-pick="${esc(chip.type)}">${chipInner(chip)}</button>`;
      })
      .join("");
    if (!overflow.length) closeMenu();
  };

  /**
   * Run ``fn`` (which may rebuild or hide chips) and put focus back where
   * it was: the same chip or panel item, else More, else the pressed chip.
   * Focus outside the chips is left alone (MCK-170 MED-1).
   */
  const keepFocus = (fn) => {
    const active = doc.activeElement;
    let refocus;
    if (active instanceof Element && host.contains(active)) {
      if (active.hasAttribute("data-bank-type-more")) refocus = { type: null };
      else refocus = { type: active.getAttribute("data-bank-type") ?? active.getAttribute("data-bank-type-pick") ?? null };
    }
    fn();
    if (!refocus) return;
    if (doc.activeElement === active && active.isConnected && !active.closest("[hidden]")) return;
    // A focused item still in an open panel keeps focus there.
    const pick =
      refocus.type !== null && !menu.hidden
        ? [...menu.querySelectorAll("[data-bank-type-pick]")].find(
            (b) => b.getAttribute("data-bank-type-pick") === refocus.type
          )
        : null;
    if (pick) pick.focus();
    else restoreFocus(refocus.type);
  };

  const paint = () =>
    keepFocus(() => {
      const wasOpen = !menu.hidden;
      row.innerHTML =
        model
          .map(
            (chip) =>
              `<button type="button" class="bank-type-chip" data-bank-type="${esc(chip.type)}" aria-pressed="${chip.pressed}" title="${esc(chip.label)}">${chipInner(chip)}</button>`
          )
          .join("") +
        `<button type="button" class="bank-type-chip bank-type-more" data-bank-type-more aria-controls="${esc(menuId)}" aria-expanded="${wasOpen}">More ▾</button>`;
      layout();
    });

  const choose = (type) => {
    closeMenu();
    const changed = type !== selected;
    if (changed) {
      selected = type;
      storeType(storage, key, selected);
      model = model.map((chip) => ({ ...chip, pressed: chip.type === selected }));
      paint();
    }
    // Back to the chosen chip (or More if it is still in the panel).
    restoreFocus(selected);
    if (changed) opts.onChange(selected);
  };

  const onHostClick = (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("[data-bank-type-more]")) {
      if (menu.hidden) openMenu();
      else closeMenu();
      return;
    }
    const chip = target?.closest("[data-bank-type], [data-bank-type-pick]");
    if (chip) {
      choose(chip.getAttribute("data-bank-type") ?? chip.getAttribute("data-bank-type-pick") ?? "");
    }
  };
  const onHostKeydown = (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      event.stopPropagation();
      closeMenu();
      moreBtn()?.focus();
    }
  };
  /** Tabbing (or clicking) to anything outside the chips closes the panel. */
  const onHostFocusout = (event) => {
    const next = event.relatedTarget;
    if (!menu.hidden && next && typeof next === "object" && "nodeType" in next && !host.contains(next)) closeMenu();
  };
  host.addEventListener("click", onHostClick);
  host.addEventListener("keydown", onHostKeydown);
  host.addEventListener("focusout", onHostFocusout);
  if (typeof ResizeObserver === "function") {
    resizeObserver = new ResizeObserver(() => {
      // Safety net: a host removed without destroy() stops observing.
      if (!host.isConnected) api.destroy();
      else keepFocus(layout);
    });
    resizeObserver.observe(row);
  }
  paint();

  const api = {
    value: () => selected,
    update(typeCounts) {
      if (destroyed) return;
      model = typeChipModel(typeCounts, selected);
      paint();
    },
    /** Remove the document listener and the ResizeObserver (MCK-170 LOW-7). */
    destroy() {
      if (destroyed) return;
      destroyed = true;
      closeMenu();
      resizeObserver?.disconnect();
      resizeObserver = null;
      host.removeEventListener("click", onHostClick);
      host.removeEventListener("keydown", onHostKeydown);
      host.removeEventListener("focusout", onHostFocusout);
    },
  };
  return api;
}
