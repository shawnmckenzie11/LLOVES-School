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
  ".live-response-points",
  ".ap-oq-pts",
  ".ap-oq-col-pts",
  ".ap-score-pts",
  ".now",
  ".prior",
  ".total",
  ".live-sub",
  "[data-points]",
].join(",");

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
 * Every way a student's name is shown, mapped to "Student N", longest
 * first so "Ava Li" is replaced before "Ava".
 * @param {any[]} rows
 * @returns {[string, string][]}
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
      if (text.length >= 2 && !pairs.has(text)) pairs.set(text, label);
    }
  }
  return [...pairs.entries()].sort((a, b) => b[0].length - a[0].length);
}

/**
 * @param {string} text
 * @returns {string}
 */
function escapeRe(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Replace whole-word names in ``text``.
 * @param {string} text
 * @param {[string, string][]} pairs from {@link nameMasks}
 * @returns {string}
 */
export function maskNames(text, pairs) {
  if (!text || !pairs.length) return text;
  const lookup = new Map(pairs);
  const re = new RegExp(
    `(?<![\\p{L}\\p{N}])(${pairs.map(([name]) => escapeRe(name)).join("|")})(?![\\p{L}\\p{N}])`,
    "gu"
  );
  return text.replace(re, (match) => lookup.get(match) || match);
}

const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "NOSCRIPT"]);
/** @type {WeakMap<Node, {orig: string, shown: string}>} masked nodes */
let originals = new WeakMap();
/** @type {Set<Node>} */
let masked = new Set();

/**
 * Mask names and points under ``root`` (idempotent).
 * @param {ParentNode & Node} root
 * @param {[string, string][]} pairs
 */
export function applyMask(root, pairs) {
  if (!root) return;
  const doc = root.ownerDocument || root;
  for (const el of root.querySelectorAll(POINTS_SELECTOR)) {
    if (el.closest("[data-student-view-keep]")) continue;
    const text = el.textContent || "";
    if (!/\d/.test(text)) continue;
    if (text === MASK) continue;
    originals.set(el, { orig: text, shown: MASK });
    masked.add(el);
    el.textContent = MASK;
  }
  if (!pairs.length) return;
  const walker = doc.createTreeWalker(root, 4 /* SHOW_TEXT */);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent || SKIP.has(parent.tagName) || parent.closest("[data-student-view-keep]")) continue;
    const text = node.nodeValue || "";
    const next = maskNames(text, pairs);
    if (next === text) continue;
    originals.set(node, { orig: text, shown: next });
    masked.add(node);
    node.nodeValue = next;
  }
}

/** Put back every masked text still on the page. */
export function restoreMask() {
  for (const node of masked) {
    const rec = originals.get(node);
    if (!rec) continue;
    // A node the page rewrote since masking keeps its new text.
    if (node.nodeType === 3) {
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
  let on = studentViewOn();
  let pairs = [];
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
    applyMask(doc.body, pairs);
    observer?.observe(doc.body, { childList: true, subtree: true, characterData: true });
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
