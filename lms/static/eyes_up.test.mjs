/**
 * MCK-26 Eyes up overlay rules on a tiny fake DOM: on/off from the
 * student payload, inert + swallowed keys while on, nothing removed,
 * focus and inert restored on release, idempotent per poll.
 */
import { EYES_UP_COPY, eyesUpActive, eyesUpOn, paintEyesUp, setEyesUp } from "./eyes_up.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

class El {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.children = [];
    this.attrs = new Map();
    this.hidden = false;
    this.textContent = "";
    this.id = "";
    this.className = "";
    this.value = "";
    this.tabIndex = 0;
    this.parent = null;
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.type = tag === "input" ? "text" : "";
    this.selectionStart = tag === "input" || tag === "textarea" ? 0 : undefined;
    this.selectionEnd = this.selectionStart;
    const cls = new Set();
    this.classList = {
      add: (c) => cls.add(c),
      remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c),
    };
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  get parentElement() { return this.parent instanceof El ? this.parent : null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  replaceChildren(...cs) { for (const c of this.children) c.parent = null; this.children = []; this.append(...cs); }
  getElementsByTagName(tag) { const t = String(tag).toUpperCase(); return [...this.walk()].filter((n) => t === "*" || n.tagName === t); }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  append(...cs) { for (const c of cs) this.appendChild(c); }
  focus() { this.ownerDocument.activeElement = this; }
  *walk() { for (const c of this.children) { yield c; yield* c.walk(); } }
}

function makeDoc() {
  const listeners = new Map();
  const doc = {
    activeElement: null,
    defaultView: { scrollY: 0, scrollTo(_x, y) { this.scrollY = y; } },
    addEventListener(type, fn) { listeners.set(type, [...(listeners.get(type) || []), fn]); },
    removeEventListener(type, fn) { listeners.set(type, (listeners.get(type) || []).filter((f) => f !== fn)); },
    dispatch(type, init = {}) {
      let prevented = false;
      let stopped = false;
      const ev = { type, ...init, preventDefault() { prevented = true; }, stopImmediatePropagation() { stopped = true; } };
      for (const fn of listeners.get(type) || []) fn(ev);
      return { prevented, stopped };
    },
    listenerCount(type) { return (listeners.get(type) || []).length; },
    createElement(tag) { return new El(tag, doc); },
    getElementById(id) {
      for (const root of [doc.head, doc.body]) for (const n of root.walk()) if (n.id === id) return n;
      return null;
    },
    querySelectorAll(sel) {
      const attr = sel.replace(/^\[|\]$/g, "");
      return [...doc.body.walk()].filter((n) => n.hasAttribute(attr));
    },
    contains(node) { return [...doc.body.walk()].includes(node); },
    get activeElementSafe() { return doc.activeElement; },
  };
  doc.documentElement = new El("html", doc);
  doc.head = new El("head", doc);
  doc.body = new El("body", doc);
  return doc;
}

// Payload rules.
if (eyesUpOn(null) || eyesUpOn({}) || eyesUpOn({ teacher_state: {} })) fail("missing state must be off");
if (!eyesUpOn({ teacher_state: { eyes_up: true } })) fail("eyes_up true must be on");
if (eyesUpOn({ teacher_state: { eyes_up: "true" } })) fail("only a real boolean turns it on");
if (eyesUpOn({ status: "ended", teacher_state: { eyes_up: true } })) fail("ended session is never paused");
if (!eyesUpOn({ status: "waiting", waiting_room: true, teacher_state: { eyes_up: true } })) fail("waiting room still pauses");
if (eyesUpOn({ status: "waiting", redirect: "/" })) fail("no teacher state, no pause");
if (eyesUpOn({ unchanged: true })) fail("unchanged stub carries no teacher state");
// HIGH-1: End (celebrate, still "waiting") and Quit (ended) are always off.
const afterEnd = { status: "waiting", waiting_room: true, celebrate: true, teacher_state: { eyes_up: true, celebrate: true } };
if (eyesUpOn(afterEnd)) fail("End celebration payload must never pause");
if (eyesUpOn({ status: "waiting", teacher_state: { eyes_up: true, celebrate: true } })) fail("teacher_state.celebrate is off");
if (eyesUpOn({ phase: "ended", teacher_state: { eyes_up: true } })) fail("ended phase is off");
if (EYES_UP_COPY.title !== "Eyes up" || EYES_UP_COPY.line !== "Look at the board.") fail("overlay copy");
if (EYES_UP_COPY.saved !== "Your work is saved.") fail("overlay saved line copy");

const doc = makeDoc();
const main = doc.createElement("main");
const input = doc.createElement("input");
input.value = "half-typed answer";
main.appendChild(input);
const already = doc.createElement("div");
already.setAttribute("inert", "");
const script = doc.createElement("script");
doc.body.append(main, already, script);
input.focus();

// Off → off does nothing (no overlay built yet).
paintEyesUp({ teacher_state: { eyes_up: false } }, doc);
if (doc.getElementById("eyes-up-overlay")) fail("off must not build the overlay");

// On.
if (!paintEyesUp({ teacher_state: { eyes_up: true, stage: "play" } }, doc)) fail("paint on");
const overlay = doc.getElementById("eyes-up-overlay");
{
  const texts = [];
  const walk = (n) => { for (const c of n.children || []) { if (c.tagName === "P") texts.push(c.textContent); walk(c); } };
  walk(overlay);
  if (texts.join("|") !== "Eyes up|Look at the board.|Your work is saved.") fail(`overlay lines: ${texts.join("|")}`);
  if (overlay.getAttribute("aria-describedby") !== "eyes-up-line eyes-up-saved") fail("saved line is described");
}
if (!overlay || overlay.hidden) fail("overlay visible");
if (overlay.getAttribute("role") !== "alertdialog") fail("overlay role");
if (!doc.getElementById("eyes-up-style")) fail("style injected once");
if (!main.hasAttribute("inert")) fail("page content must be inert");
if (script.hasAttribute("inert")) fail("scripts untouched");
if (overlay.hasAttribute("inert")) fail("overlay itself stays live");
if (!doc.documentElement.classList.contains("eyes-up-on")) fail("scroll lock class");
if (doc.activeElement !== overlay) fail("focus moves to overlay");
const typed = doc.dispatch("keydown", { key: "a" });
if (!typed.prevented || !typed.stopped) fail("typing is swallowed while on");
const reload = doc.dispatch("keydown", { key: "r", ctrlKey: true });
if (reload.prevented) fail("browser shortcuts still work");
if (!doc.dispatch("paste").prevented) fail("paste swallowed");
if (input.value !== "half-typed answer") fail("nothing lost");
if (!main.children.includes(input)) fail("nothing removed");

// Repeated polls while on are idempotent.
const listenersOn = doc.listenerCount("keydown");
paintEyesUp({ teacher_state: { eyes_up: true } }, doc);
if (doc.listenerCount("keydown") !== listenersOn) fail("no duplicate listeners per poll");
if (doc.body.children.filter((c) => c.id === "eyes-up-overlay").length !== 1) fail("one overlay");

// Release.
if (paintEyesUp({ teacher_state: { eyes_up: false } }, doc)) fail("paint off");
if (!overlay.hidden) fail("overlay hidden after release");
if (main.hasAttribute("inert")) fail("inert removed on release");
if (!already.hasAttribute("inert")) fail("pre-existing inert is kept");
if (doc.documentElement.classList.contains("eyes-up-on")) fail("scroll lock removed");
if (doc.activeElement !== input) fail("focus returns to the input");
if (doc.dispatch("keydown", { key: "a" }).prevented) fail("typing works again");
if (input.value !== "half-typed answer") fail("value kept after release");
if (eyesUpActive()) fail("inactive after release");

// Session end while paused releases.
setEyesUp(true, doc);
paintEyesUp({ status: "ended", teacher_state: { eyes_up: true } }, doc);
if (eyesUpActive() || !overlay.hidden) fail("ended session releases the pause");

// HIGH-1 in the DOM: paused, then End arrives → released; a reload with the
// same End payload stays released. Quit (ended) also releases.
setEyesUp(true, doc);
paintEyesUp(afterEnd, doc);
if (eyesUpActive() || !overlay.hidden || main.hasAttribute("inert")) fail("End while paused releases");
paintEyesUp(afterEnd, doc);
if (eyesUpActive()) fail("End payload on reload stays released");
setEyesUp(true, doc);
paintEyesUp({ ok: true, status: "ended" }, doc);
if (eyesUpActive() || main.hasAttribute("inert")) fail("Quit while paused releases");

// MED-1: the portal re-renders the card while paused. Release puts focus,
// caret, and card scroll back on the new nodes after the repaint.
const doc2 = makeDoc();
const shell = doc2.createElement("section");
shell.id = "prompt-shell";
const card = doc2.createElement("div");
const share = doc2.createElement("textarea");
share.value = "half typed answer";
card.appendChild(share);
shell.appendChild(card);
doc2.body.appendChild(shell);
paintEyesUp({ teacher_state: { eyes_up: false } }, doc2); // starts the scroll watch
card.scrollTop = 422;
// Chrome may deliver the scroll before the (passive) input event.
doc2.dispatch("scroll", { target: card });
doc2.dispatch("touchmove", { target: card });
doc2.defaultView.scrollY = 120;
share.focus();
share.setSelectionRange(17, 17);
// A layout repaint resets the card scroll just before the pause lands
// (programmatic: no touch/wheel, so the recorded spot survives).
await new Promise((r) => setTimeout(r, 300));
card.scrollTop = 0;
doc2.dispatch("scroll", { target: card });
paintEyesUp({ teacher_state: { eyes_up: true } }, doc2);
// Re-render (as paintPrompt does on the state_seq bump): new nodes.
const card2 = doc2.createElement("div");
const share2 = doc2.createElement("textarea");
share2.value = "half typed answer";
card2.appendChild(share2);
shell.replaceChildren(card2);
doc2.defaultView.scrollY = 0;
paintEyesUp({ teacher_state: { eyes_up: false } }, doc2);
// The overlay is hidden; a browser drops focus to <body>.
doc2.activeElement = doc2.body;
// Another re-render right after release (same poll) — restore must win.
const card3 = doc2.createElement("div");
const share3 = doc2.createElement("textarea");
share3.value = "half typed answer";
card3.appendChild(share3);
shell.replaceChildren(card3);
await new Promise((r) => setTimeout(r, 350));
if (doc2.activeElement !== share3) fail("focus returns to the re-rendered textarea");
if (share3.selectionStart !== 17 || share3.selectionEnd !== 17) fail("caret restored");
if (card3.scrollTop !== 422) fail(`card scroll restored, got ${card3.scrollTop}`);
if (doc2.defaultView.scrollY !== 120) fail("page scroll restored");

// A student who taps elsewhere first keeps their new focus.
const doc3 = makeDoc();
const a = doc3.createElement("input");
a.id = "answer-a";
const b = doc3.createElement("input");
b.id = "answer-b";
doc3.body.append(a, b);
a.focus();
setEyesUp(true, doc3);
setEyesUp(false, doc3);
b.focus();
await new Promise((r) => setTimeout(r, 20));
if (doc3.activeElement !== b) fail("restore never steals focus the student moved");

console.log("ok");
