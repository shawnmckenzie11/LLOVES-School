/**
 * MCK-170: the Type chip row for bank question lists.
 *
 * One single-select row: "All" first, then each question type in the
 * current results with its count (``type_counts`` from mc-search). It
 * stays on one line; chips that do not fit move into a "More" menu.
 * Shared by the Question banks tab and Run Live Class Import from bank.
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
 * Pick which chips show on the row and which go into the More menu.
 * Keeps chip order, always shows the selected chip, and never lets the
 * row need more than ``available`` pixels when anything fits at all.
 * @param {{widths: number[], available: number, moreWidth: number, gap: number, selectedIndex: number}} opts
 * @returns {{visible: number[], overflow: number[]}}
 */
export function fitTypeChips({ widths, available, moreWidth, gap, selectedIndex }) {
  const all = widths.map((_, index) => index);
  const span = (indices) =>
    indices.reduce((sum, index) => sum + widths[index], 0) + Math.max(0, indices.length - 1) * gap;
  if (span(all) <= available) return { visible: all, overflow: [] };
  const room = available - moreWidth - gap;
  let visible = [];
  for (const index of all) {
    if (span([...visible, index]) > room) break;
    visible.push(index);
  }
  if (selectedIndex >= 0 && !visible.includes(selectedIndex)) {
    while (visible.length && span([...visible, selectedIndex]) > room) visible.pop();
    visible = [...visible, selectedIndex].sort((a, b) => a - b);
  }
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
 * Mount the Type chip row into ``host`` (an empty element in a filter row).
 * @param {HTMLElement} host
 * @param {{view: string, onChange: (type: string) => void}} opts ``view`` keys
 *   the sessionStorage entry, e.g. ``import`` or ``question-banks``.
 * @returns {{value: () => string, update: (typeCounts: unknown[]) => void}}
 */
export function mountTypeChips(host, opts) {
  const key = `lloves.bankType.${opts.view}`;
  const storage = typeof window !== "undefined" ? window.sessionStorage : null;
  let selected = readStoredType(storage, key);
  let model = typeChipModel([], selected);
  host.classList.add("bank-type-chips");
  host.innerHTML = `
    <div class="bank-type-chip-row" role="group" aria-label="Question type" data-bank-type-row></div>
    <div class="bank-type-more-menu" role="menu" aria-label="More question types" data-bank-type-menu hidden></div>`;
  const row = host.querySelector("[data-bank-type-row]");
  const menu = host.querySelector("[data-bank-type-menu]");

  const closeMenu = () => {
    menu.hidden = true;
    row.querySelector("[data-bank-type-more]")?.setAttribute("aria-expanded", "false");
  };

  const layout = () => {
    const chips = [...row.querySelectorAll("[data-bank-type]")];
    const more = row.querySelector("[data-bank-type-more]");
    if (!more || !row.clientWidth) return;
    chips.forEach((chip) => {
      chip.hidden = false;
    });
    more.hidden = false;
    const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
    const { visible, overflow } = fitTypeChips({
      widths: chips.map((chip) => chip.getBoundingClientRect().width),
      available: row.clientWidth,
      moreWidth: more.getBoundingClientRect().width,
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
        return `<button type="button" role="menuitemradio" aria-checked="${chip.pressed}" data-bank-type-pick="${esc(chip.type)}">${chipInner(chip)}</button>`;
      })
      .join("");
    if (!overflow.length) closeMenu();
  };

  const paint = () => {
    row.innerHTML =
      model
        .map(
          (chip) =>
            `<button type="button" class="bank-type-chip" data-bank-type="${esc(chip.type)}" aria-pressed="${chip.pressed}">${chipInner(chip)}</button>`
        )
        .join("") +
      `<button type="button" class="bank-type-chip bank-type-more" data-bank-type-more aria-haspopup="menu" aria-expanded="false">More ▾</button>`;
    layout();
  };

  const choose = (type) => {
    closeMenu();
    if (type === selected) return;
    selected = type;
    storeType(storage, key, selected);
    model = model.map((chip) => ({ ...chip, pressed: chip.type === selected }));
    paint();
    opts.onChange(selected);
  };

  host.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const more = target?.closest("[data-bank-type-more]");
    if (more) {
      menu.hidden = !menu.hidden;
      more.setAttribute("aria-expanded", String(!menu.hidden));
      return;
    }
    const chip = target?.closest("[data-bank-type], [data-bank-type-pick]");
    if (chip) {
      choose(chip.getAttribute("data-bank-type") ?? chip.getAttribute("data-bank-type-pick") ?? "");
    }
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && event.target instanceof Node && !host.contains(event.target)) closeMenu();
  });
  host.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hidden) {
      event.stopPropagation();
      closeMenu();
      row.querySelector("[data-bank-type-more]")?.focus();
    }
  });
  if (typeof ResizeObserver === "function") new ResizeObserver(() => layout()).observe(row);
  paint();

  return {
    value: () => selected,
    update(typeCounts) {
      model = typeChipModel(typeCounts, selected);
      paint();
    },
  };
}
