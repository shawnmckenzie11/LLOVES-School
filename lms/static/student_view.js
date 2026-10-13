/**
 * MCK-199 "Student view": a teacher-only, client-side mask for screen
 * sharing. While on, every student name/codename on the teacher's live
 * class screen reads "Student N" (numbered by student id, so the same
 * student has the same number on every screen) and every points/total
 * number reads "—". Team names, questions and answer counts stay.
 *
 * It never writes to the server and never touches what students see:
 * only the teacher's own DOM text is swapped, and the originals come
 * back when it is turned off.
 */

export const STUDENT_VIEW_KEY = "lloves-student-view";
export const STUDENT_VIEW_COPY = Object.freeze({
  label: "Student view", // copy: Wonder
  title: "Hide names and points so you can share your screen", // copy: Wonder
  banner: "Student view · names and points hidden", // copy: Wonder
  exit: "Exit", // copy: Wonder
});
export const MASK = "—";
/** Elements whose whole text is a points number or total. */
export const POINTS_SELECTOR = [
  ".ap-att-course", // Class List COURSE
  ".ap-att-game", // Class List GAME
  ".live-response-points",
  ".ap-oq-pts",
  ".ap-oq-col-pts",
  ".ap-score-pts",
  ".now",
  ".prior",
  ".total",
  ".total-cell",
  ".live-sub",
  ".race-points",
  ".race-points-total",
  ".race-step-pts",
  ".feedback-score",
  ".sb-preview-score",
  "[data-points]",
].join(",");
/**
 * Elements that hold one student or guest name. Only text inside these
 * (and their title / aria-label) is ever rewritten, so question and
 * answer text that happens to contain a name stays as written.
 */
export const NAME_SELECTOR = [
  ".ap-att-name",
  ".live-response-name",
  ".ap-oq-name",
  ".ap-score-who",
  ".who",
  ".manual-row > span:first-child",
  ".preview-roster .sv-name",
  ".score-pop-chip",
  "[data-student-name]",
].join(",");
/** Children of a name element that are not part of the name. */
const NAME_KEEP = ".ap-guest-flag, .ap-late-tag";
/** Attributes on name elements (and student +1 buttons) that can carry a name. */
const NAME_ATTRS = ["title", "aria-label", "data-pop-name"];
/** Leading glyphs kept as-is: a mood face or a "+1 " score-pop prefix. */
const PREFIX_RE = /^([^\p{L}\p{N}]*(?:[+\u2212-]?\d+\s+)?)/u;

/**
 * @param {Storage | null | undefined} store
 * @returns {boolean}
 */
export function studentViewOn(store = globalThis.localStorage) {
  try {
    return store?.getItem(STUDENT_VIEW_KEY) === "1";
  } catch (_) {
    return false;
  }
}

/**
 * @param {boolean} on
 * @param {Storage | null | undefined} store
 */
export function storeStudentView(on, store = globalThis.localStorage) {
  try {
    if (on) store?.setItem(STUDENT_VIEW_KEY, "1");
    else store?.removeItem(STUDENT_VIEW_KEY);
  } catch (_) {
    /* private mode: lasts for this page only */
  }
}

/**
 * Stable numbers: the roster sorted by student id, 1-based. The same
 * roster gives the same numbers on every screen and every reload.
 * @param {any[]} rows
 * @returns {Map<number, number>}
 */
export function studentNumbers(rows) {
  const ids = [...new Set((Array.isArray(rows) ? rows : [])
    .map((row) => Number(row?.student_id ?? row?.id))
    .filter((id) => Number.isFinite(id) && id > 0))].sort((a, b) => a - b);
  return new Map(ids.map((id, index) => [id, index + 1]));
}

/**
 * Every way a student's name is shown, mapped to "Student N".
 * @param {any[]} rows
 * @returns {Map<string, string>}
 */
export function nameMasks(rows) {
  const numbers = studentNumbers(rows);
  const pairs = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const n = numbers.get(Number(row?.student_id ?? row?.id));
    if (!n) continue;
    const label = `Student ${n}`;
    const first = String(row.first_name || "").trim();
    const last = String(row.last_name || "").trim();
    const names = [
      row.codename, row.name, row.display_name, row.full_name, first, last,
      first && last ? `${first} ${last}` : "",
      first && last ? `${last}, ${first}` : "",
      first && last ? `${first} ${last[0]}.` : "",
    ];
    for (const raw of names) {
      const text = String(raw || "").trim();
      if (text && !pairs.has(text)) pairs.set(text, label);
    }
  }
  return pairs;
}

/**
 * Guest labels: "Guest N" in the order guests are first seen, kept for
 * the browser so a guest has one number on every screen and reload.
 */
export class GuestBook {
  /** @param {Storage | null | undefined} store */
  constructor(store = globalThis.localStorage) {
    this.store = store;
    /** @type {Record<string, number>} */
    this.byName = {};
    try {
      const saved = JSON.parse(store?.getItem(`${STUDENT_VIEW_KEY}-guests`) || "{}");
      if (saved && typeof saved === "object") this.byName = saved;
    } catch (_) {
      this.byName = {};
    }
  }

  /**
   * @param {string} name
   * @returns {string}
   */
  label(name) {
    const key = String(name || "").trim().toLowerCase();
    if (this.byName[key] == null) {
      this.byName[key] = Object.keys(this.byName).length + 1;
      try {
        this.store?.setItem(`${STUDENT_VIEW_KEY}-guests`, JSON.stringify(this.byName));
      } catch (_) {
        /* private mode */
      }
    }
    return `Guest ${this.byName[key]}`;
  }
}

/**
 * Masked form of one name string. Rostered names → "Student N"; any other
 * name (guests, late joiners not yet in the roster) → "Guest N", so an
 * unknown name never leaks. Labels already masked stay as they are.
 * @param {string} text
 * @param {Map<string, string>} pairs
 * @param {GuestBook} guests
 * @returns {string}
 */
export function maskName(text, pairs, guests) {
  const raw = String(text || "");
  const prefix = (raw.match(PREFIX_RE) || ["", ""])[1];
  const rest = raw.slice(prefix.length);
  const name = rest.trim();
  if (!name || !/\p{L}/u.test(name)) return raw;
  if (/^(Student|Guest) \d+$/.test(name)) return raw;
  const label = pairs.get(name) || guests.label(name);
  const tail = rest.slice(rest.indexOf(name) + name.length);
  return `${prefix}${label}${tail}`;
}

const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "NOSCRIPT"]);
/** @type {WeakMap<object, {orig: string, shown: string}>} masked nodes */
let originals = new WeakMap();
/** @type {Set<{node: any, attr?: string}>} */
let masked = new Set();

/**
 * @param {any} node
 * @param {string | undefined} attr
 * @param {string} orig
 * @param {string} shown
 */
function remember(node, attr, orig, shown) {
  const key = attr ? { node, attr } : node;
  if (attr) {
    for (const item of masked) if (item.node === node && item.attr === attr) return;
    masked.add(key);
    originals.set(key, { orig, shown });
    return;
  }
  masked.add({ node });
  originals.set(node, { orig, shown });
}

/**
 * Text nodes of a name element that belong to the name.
 * @param {Element} el
 * @returns {Text[]}
 */
function nameTextNodes(el) {
  const out = [];
  const walk = (node) => {
    for (const child of Array.from(node.childNodes || [])) {
      if (child.nodeType === 3) out.push(child);
      else if (child.nodeType === 1 && !SKIP.has(child.tagName) && !child.matches?.(NAME_KEEP)) walk(child);
    }
  };
  walk(el);
  return out;
}

/**
 * Mask names and points under ``root`` (idempotent).
 * @param {ParentNode & Node} root
 * @param {Map<string, string>} pairs
 * @param {GuestBook} guests
 */
export function applyMask(root, pairs, guests) {
  if (!root) return;
  for (const el of root.querySelectorAll(POINTS_SELECTOR)) {
    if (el.closest("[data-student-view-keep]")) continue;
    const text = el.textContent || "";
    if (!/\d/.test(text) || text === MASK) continue;
    remember(el, undefined, text, MASK);
    el.textContent = MASK;
  }
  const attrHosts = [
    ...root.querySelectorAll(NAME_SELECTOR),
    ...root.querySelectorAll('[data-kind="student"][data-pop-name]'),
  ];
  for (const el of root.querySelectorAll(NAME_SELECTOR)) {
    if (el.closest("[data-student-view-keep]")) continue;
    for (const node of nameTextNodes(el)) {
      const text = node.nodeValue || "";
      const next = maskName(text, pairs, guests);
      if (next === text) continue;
      remember(node, undefined, text, next);
      node.nodeValue = next;
    }
  }
  for (const el of attrHosts) {
    for (const attr of NAME_ATTRS) {
      if (!el.hasAttribute(attr)) continue;
      const text = el.getAttribute(attr) || "";
      // Name-only attributes are masked whole; "Add one point" stays.
      const next = attr === "data-pop-name" || pairs.has(text.trim())
        ? maskName(text, pairs, guests)
        : text;
      if (next === text) continue;
      remember(el, attr, text, next);
      el.setAttribute(attr, next);
    }
  }
}

/** Put back every masked text still on the page. */
export function restoreMask() {
  for (const item of masked) {
    const rec = originals.get(item.attr ? item : item.node);
    if (!rec) continue;
    const node = item.node;
    // A node the page rewrote since masking keeps its new text.
    if (item.attr) {
      if (node.getAttribute(item.attr) === rec.shown) node.setAttribute(item.attr, rec.orig);
    } else if (node.nodeType === 3) {
      if (node.nodeValue === rec.shown) node.nodeValue = rec.orig;
    } else if (node.textContent === rec.shown) node.textContent = rec.orig;
  }
  masked = new Set();
  originals = new WeakMap();
}

/**
 * Wire the toggle, banner and observer on the teacher live page.
 * @param {{doc?: Document, button?: HTMLElement | null, roster: () => any[], onChange?: (on: boolean) => void}} opts
 * @returns {{isOn: () => boolean, set: (on: boolean) => void, refresh: () => void}}
 */
export function mountStudentView({ doc = document, button = null, roster, onChange }) {
  // Test harnesses with a minimal fake DOM get a no-op controller.
  if (
    !doc?.body ||
    typeof doc.body.prepend !== "function" ||
    typeof doc.createTreeWalker !== "function"
  ) {
    return { isOn: () => false, set: () => {}, refresh: () => {} };
  }
  let on = studentViewOn();
  let pairs = new Map();
  const guests = new GuestBook();
  let pending = false;
  let observer = null;
  const banner = doc.createElement("div");
  banner.className = "student-view-banner";
  banner.id = "student-view-banner";
  banner.setAttribute("role", "status");
  banner.setAttribute("data-student-view-keep", "");
  banner.hidden = true;
  const words = doc.createElement("span");
  words.textContent = STUDENT_VIEW_COPY.banner;
  const exit = doc.createElement("a");
  exit.href = "#";
  exit.id = "student-view-exit";
  exit.textContent = STUDENT_VIEW_COPY.exit;
  exit.addEventListener("click", (event) => {
    event.preventDefault();
    set(false);
  });
  banner.append(words, " ", exit);
  doc.body.prepend(banner);

  const run = () => {
    pending = false;
    if (!on) return;
    observer?.disconnect();
    pairs = nameMasks(roster());
    applyMask(doc.body, pairs, guests);
    observer?.observe(doc.body, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: NAME_ATTRS,
    });
  };
  const schedule = () => {
    if (pending || !on) return;
    pending = true;
    queueMicrotask(run);
  };
  if (typeof MutationObserver === "function") observer = new MutationObserver(schedule);

  const paint = () => {
    banner.hidden = !on;
    doc.body.classList.toggle("is-student-view", on);
    if (button) {
      button.setAttribute("aria-pressed", on ? "true" : "false");
      button.classList.toggle("is-on", on);
    }
  };
  function set(want) {
    on = Boolean(want);
    storeStudentView(on);
    paint();
    if (on) run();
    else {
      observer?.disconnect();
      restoreMask();
    }
    onChange?.(on);
  }
  button?.addEventListener("click", () => set(!on));
  globalThis.addEventListener?.("storage", (event) => {
    if (event.key === STUDENT_VIEW_KEY && (event.newValue === "1") !== on) set(event.newValue === "1");
  });
  paint();
  if (on) run();
  return { isOn: () => on, set, refresh: schedule };
}
