/**
 * MCK-26 "Eyes up": a calm full-screen pause on every student screen.
 *
 * Reads ``teacher_state.eyes_up`` from the /api/student/state payload the
 * portal already polls, so it adds no request. While on, the overlay
 * covers the page, the rest of <body> is ``inert``, and key/paste/input
 * events are swallowed. Nothing is removed or re-rendered, so typed
 * answers, scroll, and the current page stay exactly as they were. One
 * more teacher tap releases it and focus returns where it was.
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
  // "waiting" is the live waiting room and still pauses; only a payload
  // that carries the session's teacher_state can turn it on.
  if (status === "ended" || status === "pick") return false;
  const ts = payload.teacher_state;
  return Boolean(ts && typeof ts === "object" && ts.eyes_up === true);
}

const CSS = `
#${OVERLAY_ID} {
  position: fixed; inset: 0; z-index: 2147483000;
  display: flex; align-items: center; justify-content: center;
  padding: 2rem; box-sizing: border-box;
  background: rgba(15, 23, 42, 0.97); color: #f8fafc;
  text-align: center; cursor: default; user-select: none;
  touch-action: none; overscroll-behavior: contain;
  animation: eyes-up-in 180ms ease-out;
}
#${OVERLAY_ID}[hidden] { display: none !important; }
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
  const want = Boolean(on);
  if (want === active) return active;
  if (!doc || !doc.body) return active;
  const overlay = ensureOverlay(doc);
  if (want) {
    restoreFocus = doc.activeElement && doc.activeElement !== doc.body ? doc.activeElement : null;
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
