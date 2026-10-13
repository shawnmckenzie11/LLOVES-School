/**
 * MCK-199 "Student view": a teacher-only, client-side mask for screen
 * sharing. While on, every student name/codename on the teacher's live
 * class screen reads "Student N" (numbered by student id, so the same
 * student has the same number on every screen) and every points/total
 * number reads "—". Team names, questions and answer counts stay.
 *
 * It never writes to the server and never touches what students see:
 * only the teacher's own DOM text is swapped, and the originals come
 * back when it is turned off. Class List +1 chips fade while it is on
 * and stay clickable; they return to full strength when it is turned off.
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
  ".ap-live-player .who", // scoreboard player rows only; team-head .who is a team name
  ".manual-row > span:first-child",
  ".preview-roster .sv-name",
  "[data-student-name]",
].join(",");
/**
 * Name spots that may also hold a team name (the "+1 Red Foxes" pop):
 * only rostered names are masked there, never booked as guests.
 */
export const ROSTER_ONLY_SELECTOR = ".score-pop-chip";
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
  /**
   * @param {Storage | null | undefined} store
   * @param {number | string} [classId] guest numbers reset per class
   */
  constructor(store = globalThis.localStorage, classId = "") {
    this.store = store;
    this.key = `${STUDENT_VIEW_KEY}-guests-${classId || 0}`;
    /** @type {Record<string, number>} */
    this.byName = {};
    try {
      const saved = JSON.parse(store?.getItem(this.key) || "{}");
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
        this.store?.setItem(this.key, JSON.stringify(this.byName));
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
 * @param {GuestBook | null} guests ``null`` until the roster has loaded:
 *   unknown names then show as "—" (never booked as a guest too early).
 * @returns {string}
 */
export function maskName(text, pairs, guests) {
  const raw = String(text || "");
  const prefix = (raw.match(PREFIX_RE) || ["", ""])[1];
  const rest = raw.slice(prefix.length);
  const name = rest.trim();
  if (!name || !/\p{L}/u.test(name)) return raw;
  if (/^(Student|Guest) \d+$/.test(name)) return raw;
  const label = pairs.get(name) || (guests ? guests.label(name) : MASK);
  const tail = rest.slice(rest.indexOf(name) + name.length);
  return `${prefix}${label}${tail}`;
}

/** Class List +1 chips (student and team). Muted while Student view is on. */
export const PLUS_CHIP_SELECTOR = ".ap-att-plus";
/** Class token that fades a +1 chip without disabling it. */
export const PLUS_MUTED_CLASS = "is-student-view-muted";
/** Chips we faded, so turning Student view off can put them back. */
const mutedPlus = new Set();

/**
 * Class tokens on ``el``, split on whitespace.
 * @param {Element} el
 * @returns {string[]}
 */
function classNames(el) {
  return String(el.getAttribute("class") || "").split(/\s+/).filter(Boolean);
}

/**
 * Add one class token if it is not already present.
 * @param {Element} el
 * @param {string} name
 */
function addClassName(el, name) {
  const cur = classNames(el);
  if (cur.includes(name)) return;
  cur.push(name);
  el.setAttribute("class", cur.join(" "));
}

/**
 * Remove one class token and keep the rest.
 * @param {Element} el
 * @param {string} name
 */
function removeClassName(el, name) {
  const cur = classNames(el);
  const next = cur.filter((token) => token !== name);
  if (next.length === cur.length) return;
  el.setAttribute("class", next.join(" "));
}

/**
 * Fade Class List +1 chips while Student view is on.
 * The buttons stay enabled and clickable; only the muted class changes.
 * @param {ParentNode | null | undefined} root
 * @param {boolean} on
 */
export function mutePlusChips(root, on) {
  if (!on) {
    for (const el of mutedPlus) removeClassName(el, PLUS_MUTED_CLASS);
    mutedPlus.clear();
    return;
  }
  if (!root?.querySelectorAll) return;
  for (const el of root.querySelectorAll(PLUS_CHIP_SELECTOR)) {
    addClassName(el, PLUS_MUTED_CLASS);
    mutedPlus.add(el);
  }
}

const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "NOSCRIPT"]);
/**
 * Masked nodes: node → {text, attrs}. Each record is {orig, shown}.
 * @type {Map<any, {text: {orig: string, shown: string} | null, attrs: Record<string, {orig: string, shown: string}>}>}
 */
let records = new Map();

/**
 * The page's own value for a node/attribute: the stored original while
 * our mask is still showing, else whatever the page wrote since.
 * @param {any} node
 * @param {string | null} attr
 * @param {string} current
 * @returns {string}
 */
function sourceOf(node, attr, current) {
  const rec = records.get(node);
  const item = rec && (attr ? rec.attrs[attr] : rec.text);
  return item && item.shown === current ? item.orig : current;
}

/**
 * @param {any} node
 * @param {string | null} attr
 * @param {string} orig
 * @param {string} shown
 */
function remember(node, attr, orig, shown) {
  let rec = records.get(node);
  if (!rec) {
    rec = { text: null, attrs: {} };
    records.set(node, rec);
  }
  if (attr) rec.attrs[attr] = { orig, shown };
  else rec.text = { orig, shown };
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
 * Mask names and points under ``root`` (idempotent; re-masks from the
 * page's own text, so a "—" placeholder becomes a label once the roster
 * arrives).
 * @param {ParentNode & Node} root
 * @param {Map<string, string>} pairs
 * @param {GuestBook | null} guests ``null`` until the roster has loaded
 */
export function applyMask(root, pairs, guests) {
  if (!root) return;
  for (const el of root.querySelectorAll(POINTS_SELECTOR)) {
    if (el.closest("[data-student-view-keep]")) continue;
    const text = el.textContent || "";
    if (!/\d/.test(text) || text === MASK) continue;
    remember(el, null, text, MASK);
    el.textContent = MASK;
  }
  const nameEls = [
    ...Array.from(root.querySelectorAll(NAME_SELECTOR)).map((el) => [el, guests]),
    ...Array.from(root.querySelectorAll(ROSTER_ONLY_SELECTOR)).map((el) => [el, "roster"]),
  ];
  for (const [el, book] of nameEls) {
    if (el.closest("[data-student-view-keep]")) continue;
    for (const node of nameTextNodes(el)) {
      const current = node.nodeValue || "";
      const source = sourceOf(node, null, current);
      let next;
      if (book === "roster") {
        const prefix = (source.match(PREFIX_RE) || ["", ""])[1];
        next = pairs.has(source.slice(prefix.length).trim()) ? maskName(source, pairs, null) : source;
      } else {
        next = maskName(source, pairs, book);
      }
      if (next === current) continue;
      if (next !== source) remember(node, null, source, next);
      node.nodeValue = next;
    }
  }
  const attrHosts = [
    ...root.querySelectorAll(NAME_SELECTOR),
    ...root.querySelectorAll('[data-kind="student"][data-pop-name]'),
  ];
  for (const el of attrHosts) {
    for (const attr of NAME_ATTRS) {
      if (!el.hasAttribute(attr)) continue;
      const current = el.getAttribute(attr) || "";
      const source = sourceOf(el, attr, current);
      // Name-only attributes are masked whole; "Add one point" stays.
      const next = attr === "data-pop-name" || pairs.has(source.trim())
        ? maskName(source, pairs, guests)
        : source;
      if (next === current) continue;
      if (next !== source) remember(el, attr, source, next);
      el.setAttribute(attr, next);
    }
  }
  mutePlusChips(root, true);
}

/** Put back every masked text still on the page. */
export function restoreMask() {
  for (const [node, rec] of records) {
    // A node the page rewrote since masking keeps its new text.
    if (rec.text) {
      if (node.nodeType === 3) {
        if (node.nodeValue === rec.text.shown) node.nodeValue = rec.text.orig;
      } else if (node.textContent === rec.text.shown) node.textContent = rec.text.orig;
    }
    for (const [attr, item] of Object.entries(rec.attrs)) {
      if (node.getAttribute(attr) === item.shown) node.setAttribute(attr, item.orig);
    }
  }
  records = new Map();
  mutePlusChips(null, false);
}

/**
 * Wire the toggle, banner and observer on the teacher live page.
 * @param {{doc?: Document, button?: HTMLElement | null, roster: () => any[], onChange?: (on: boolean) => void}} opts
 * @returns {{isOn: () => boolean, set: (on: boolean) => void, refresh: () => void}}
 */
export function mountStudentView({ doc = document, button = null, roster, classId = "", onChange }) {
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
  const guests = new GuestBook(globalThis.localStorage, classId);
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
    const rows = roster() || [];
    pairs = nameMasks(rows);
    // No guest numbers until the class roster has loaded (names show "—").
    applyMask(doc.body, pairs, rows.length ? guests : null);
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
    mutePlusChips(doc.body, on);
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
