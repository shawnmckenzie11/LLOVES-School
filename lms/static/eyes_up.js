/**
 * MCK-26 "Eyes up": a calm full-screen pause on every student screen.
 *
 * Reads ``teacher_state.eyes_up`` from the /api/student/state payload the
 * portal already polls, so it adds no request. While on, the overlay
 * covers the page, the rest of <body> is ``inert``, and key/paste/input
 * events are swallowed. The overlay itself removes nothing and the
 * student stays on their page. The portal still re-renders the live card
 * on every teacher-state write (as it did before MCK-26; drafts carry
 * over), so on release this module puts focus, the caret, and scroll
 * positions back on the re-rendered nodes after that repaint.
 */

/** Placeholder copy for Wonder. */
export const EYES_UP_COPY = Object.freeze({
  title: "Eyes up", // copy: Wonder
  line: "Look at the board.", // copy: Wonder
});

const OVERLAY_ID = "eyes-up-overlay";
const STYLE_ID = "eyes-up-style";
const INERT_MARK = "data-eyes-up-inert";
const BLOCKED_EVENTS = ["keydown", "keypress", "keyup", "beforeinput", "paste", "drop"];

/**
 * True when the teacher has Eyes up on for this live session.
 * Ended or pick payloads (or a missing teacher state) are always off.
 * @param {any} payload
 * @returns {boolean}
 */
export function eyesUpOn(payload) {
  if (!payload || typeof payload !== "object") return false;
  const status = String(payload.status || "");
  // The live waiting room is "waiting" and still pauses. After End the
  // payload is also "waiting", but with celebrate: that and any ended,
  // pick, or session-less payload is always off (MCK-26 HIGH-1).
  if (status === "ended" || status === "pick") return false;
  if (String(payload.phase || "") === "ended") return false;
  if (payload.celebrate) return false;
  const ts = payload.teacher_state;
  if (!ts || typeof ts !== "object") return false;
  if (ts.celebrate) return false;
  return ts.eyes_up === true;
}

const CSS = `
#${OVERLAY_ID} {
  position: fixed; inset: 0; z-index: 2147483000;
  display: flex; align-items: center; justify-content: center;
  padding: 2rem; box-sizing: border-box;
  background: #0f172a; color: #f8fafc;
  text-align: center; cursor: default; user-select: none;
  touch-action: none; overscroll-behavior: contain;
  animation: eyes-up-in 180ms ease-out;
}
#${OVERLAY_ID}[hidden] { display: none !important; }
#${OVERLAY_ID}:focus, #${OVERLAY_ID}:focus-visible { outline: none; }
#${OVERLAY_ID} .eyes-up-card { max-width: 32rem; }
#${OVERLAY_ID} .eyes-up-icon { font-size: 3.5rem; line-height: 1; margin-bottom: 1rem; }
#${OVERLAY_ID} .eyes-up-title { margin: 0 0 0.5rem; font-size: clamp(2rem, 7vw, 3.25rem); font-weight: 700; letter-spacing: 0.01em; }
#${OVERLAY_ID} .eyes-up-line { margin: 0; font-size: clamp(1.1rem, 4vw, 1.5rem); color: #cbd5e1; }
html.eyes-up-on, html.eyes-up-on body { overflow: hidden !important; }
@keyframes eyes-up-in { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { #${OVERLAY_ID} { animation: none; } }
`;

let active = false;
/** @type {Element | null} */
let restoreFocus = null;
/** @type {{focus: any, selection: [number, number, string] | null, scrolls: {el: any, top: number, left: number}[], winY: number} | null} */
let saved = null;
let restoreTimers = [];
/** Last student scroll per element, so a repaint just before the pause cannot erase it. */
const lastScrolls = new Map();
let lastWinY = 0;
/** @type {Document | null} */
let watched = null;
let lastUserInputAt = -Infinity;
const USER_SCROLL_WINDOW_MS = 1500;
const USER_SCROLL_TO_TOP_MS = 250;

/**
 * Record student scrolls (passive, capture) while not paused.
 * @param {Document} doc
 */
function watchScrolls(doc) {
  if (watched === doc || !doc || typeof doc.addEventListener !== "function") return;
  watched = doc;
  // Chrome scrolls passive wheel/touch input on the compositor, so the
  // scroll event can arrive before the input event. Positions > 0 are
  // always recorded; a scroller at 0 only clears its entry when the
  // student's own input is the likely cause (a repaint reset must not).
  let sweepTimer = null;
  const sweep = () => {
    sweepTimer = null;
    if (active) return;
    for (const [key, entry] of Array.from(lastScrolls.entries())) {
      const el = resolve(entry.el, doc);
      if (el && !(el.scrollTop || 0) && !(el.scrollLeft || 0)) lastScrolls.delete(key);
    }
  };
  const noteInput = () => {
    lastUserInputAt = Date.now();
    if (sweepTimer === null && typeof setTimeout === "function") sweepTimer = setTimeout(sweep, 200);
  };
  for (const type of ["wheel", "touchmove", "touchstart", "pointerdown", "keydown"]) {
    doc.addEventListener(type, noteInput, { capture: true, passive: true });
  }
  doc.addEventListener(
    "scroll",
    (event) => {
      if (active) return;
      const el = /** @type {any} */ (event).target;
      if (!el || el === doc || el === doc.documentElement || el === doc.body) {
        const view = doc.defaultView;
        const y = view ? view.scrollY || 0 : 0;
        if (y > 0 || Date.now() - lastUserInputAt <= USER_SCROLL_WINDOW_MS) lastWinY = y;
        return;
      }
      const d = describe(el, doc);
      if (!d) return;
      const key = JSON.stringify(d);
      if ((el.scrollTop || 0) > 0 || (el.scrollLeft || 0) > 0) {
        lastScrolls.set(key, { el: d, top: el.scrollTop || 0, left: el.scrollLeft || 0 });
      } else if (Date.now() - lastUserInputAt <= USER_SCROLL_TO_TOP_MS) {
        // Back to the top by hand (input just now), not a repaint reset.
        lastScrolls.delete(key);
      }
    },
    { capture: true, passive: true }
  );
}

/**
 * A re-findable address for one element: its id, or the nearest id'd
 * ancestor plus the element's index among same-tag descendants.
 * @param {any} el
 * @param {Document} doc
 */
function describe(el, doc) {
  if (!el || !el.tagName) return null;
  if (el.id) return { id: el.id, tag: el.tagName, type: el.type || "" };
  let anchor = el.parentElement || null;
  while (anchor && !anchor.id) anchor = anchor.parentElement || null;
  const scope = anchor || doc.body;
  const list = Array.from(scope.getElementsByTagName(el.tagName));
  const index = list.indexOf(el);
  if (index < 0) return null;
  return { anchorId: anchor ? anchor.id : "", tag: el.tagName, index, type: el.type || "" };
}

/**
 * @param {any} d
 * @param {Document} doc
 */
function resolve(d, doc) {
  if (!d) return null;
  let el = null;
  if (d.id) {
    el = doc.getElementById(d.id);
  } else {
    const scope = d.anchorId ? doc.getElementById(d.anchorId) : doc.body;
    el = scope ? scope.getElementsByTagName(d.tag)[d.index] || null : null;
  }
  if (!el || el.tagName !== d.tag || String(el.type || "") !== String(d.type || "")) return null;
  return el;
}

/** @param {Document} doc */
function snapshot(doc) {
  const focused = doc.activeElement && doc.activeElement !== doc.body ? doc.activeElement : null;
  let selection = null;
  try {
    if (focused && typeof focused.selectionStart === "number") {
      selection = [focused.selectionStart, focused.selectionEnd, focused.selectionDirection || "none"];
    }
  } catch (_) {
    selection = null;
  }
  const byKey = new Map(lastScrolls);
  for (const el of Array.from(doc.body.getElementsByTagName("*"))) {
    if ((el.scrollTop || 0) > 0 || (el.scrollLeft || 0) > 0) {
      const d = describe(el, doc);
      if (d) byKey.set(JSON.stringify(d), { el: d, top: el.scrollTop || 0, left: el.scrollLeft || 0 });
    }
  }
  const view = doc.defaultView;
  const winY = (view ? view.scrollY || 0 : 0) || lastWinY;
  return { focus: describe(focused, doc), selection, scrolls: Array.from(byKey.values()), winY };
}

/**
 * Put focus, caret, and scroll back on the (possibly re-rendered) nodes.
 * @param {any} state
 * @param {Document} doc
 * @param {boolean} withFocus
 */
function applySnapshot(state, doc, withFocus) {
  if (!state || active) return;
  if (withFocus && state.focus) {
    const el = resolve(state.focus, doc);
    const current = doc.activeElement;
    // Do not steal focus if the student already moved on.
    if (el && (!current || current === doc.body || current === el)) {
      try {
        el.focus({ preventScroll: true });
        if (state.selection && typeof el.setSelectionRange === "function") {
          el.setSelectionRange(state.selection[0], state.selection[1], state.selection[2]);
        }
      } catch (_) {
        /* number inputs have no selection API */
      }
    }
  }
  for (const row of state.scrolls) {
    const el = resolve(row.el, doc);
    // Later passes only undo a repaint reset; a student scroll wins.
    if (el && (withFocus || !el.scrollTop)) {
      el.scrollTop = row.top;
      el.scrollLeft = row.left;
    }
  }
  const view = doc.defaultView;
  if (view && typeof view.scrollTo === "function" && state.winY && (withFocus || !view.scrollY)) {
    view.scrollTo(0, state.winY);
  }
}

/** @param {Event} event */
function swallow(event) {
  if (!active) return;
  // Browser shortcuts (reload, zoom, tab switch) still work.
  const key = /** @type {KeyboardEvent} */ (event);
  if (key.ctrlKey || key.metaKey || key.altKey || key.key === "F5") return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

/**
 * Build (once) the overlay and its style tag.
 * @param {Document} doc
 * @returns {HTMLElement}
 */
function ensureOverlay(doc) {
  let overlay = doc.getElementById(OVERLAY_ID);
  if (overlay) return overlay;
  if (!doc.getElementById(STYLE_ID)) {
    const style = doc.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    doc.head.appendChild(style);
  }
  overlay = doc.createElement("div");
  overlay.id = OVERLAY_ID;
  overlay.hidden = true;
  overlay.setAttribute("role", "alertdialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", "eyes-up-title");
  overlay.setAttribute("aria-describedby", "eyes-up-line");
  overlay.tabIndex = -1;
  const card = doc.createElement("div");
  card.className = "eyes-up-card";
  const icon = doc.createElement("div");
  icon.className = "eyes-up-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "👀";
  const title = doc.createElement("p");
  title.className = "eyes-up-title";
  title.id = "eyes-up-title";
  title.textContent = EYES_UP_COPY.title;
  const line = doc.createElement("p");
  line.className = "eyes-up-line";
  line.id = "eyes-up-line";
  line.textContent = EYES_UP_COPY.line;
  card.append(icon, title, line);
  overlay.append(card);
  doc.body.appendChild(overlay);
  return overlay;
}

/**
 * Turn the pause on or off. Idempotent; safe to call on every poll.
 * @param {boolean} on
 * @param {Document} [doc]
 * @returns {boolean} the new state
 */
export function setEyesUp(on, doc = document) {
  watchScrolls(doc);
  const want = Boolean(on);
  if (want === active) return active;
  if (!doc || !doc.body) return active;
  const overlay = ensureOverlay(doc);
  if (want) {
    for (const t of restoreTimers) clearTimeout(t);
    restoreTimers = [];
    restoreFocus = doc.activeElement && doc.activeElement !== doc.body ? doc.activeElement : null;
    saved = snapshot(doc);
    for (const child of Array.from(doc.body.children)) {
      if (child === overlay || child.tagName === "SCRIPT" || child.hasAttribute("inert")) continue;
      child.setAttribute("inert", "");
      child.setAttribute(INERT_MARK, "");
    }
    for (const type of BLOCKED_EVENTS) doc.addEventListener(type, swallow, { capture: true, passive: false });
    doc.documentElement.classList.add("eyes-up-on");
    overlay.hidden = false;
    active = true;
    try {
      overlay.focus({ preventScroll: true });
    } catch (_) {
      /* ignore */
    }
  } else {
    active = false;
    overlay.hidden = true;
    doc.documentElement.classList.remove("eyes-up-on");
    for (const type of BLOCKED_EVENTS) doc.removeEventListener(type, swallow, { capture: true });
    for (const child of Array.from(doc.querySelectorAll(`[${INERT_MARK}]`))) {
      child.removeAttribute("inert");
      child.removeAttribute(INERT_MARK);
    }
    const target = restoreFocus;
    restoreFocus = null;
    if (target && typeof (/** @type {any} */ (target).focus) === "function" && doc.contains(target)) {
      try {
        /** @type {HTMLElement} */ (target).focus({ preventScroll: true });
      } catch (_) {
        /* ignore */
      }
    }
    lastScrolls.clear();
    // The portal repaints the live card later in this same poll; restore
    // on the new nodes after it, then once more for late layout (math).
    const state = saved;
    saved = null;
    restoreTimers = [
      setTimeout(() => applySnapshot(state, doc, true), 0),
      setTimeout(() => applySnapshot(state, doc, false), 300),
      setTimeout(() => applySnapshot(state, doc, false), 1000),
    ];
  }
  return active;
}

/**
 * Paint from one student state payload.
 * @param {any} payload
 * @param {Document} [doc]
 * @returns {boolean}
 */
export function paintEyesUp(payload, doc = document) {
  return setEyesUp(eyesUpOn(payload), doc);
}

/** @returns {boolean} */
export function eyesUpActive() {
  return active;
}
