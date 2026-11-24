"""MCK-500: mood reselect, one-step undo, class-list rail, Past Modules tab."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))
sys.path.append(str(ROOT / "tools" / "math-game-show"))

from student_portal import mood_reselect_requested  # noqa: E402


class MoodReselectTests(unittest.TestCase):
    """Change mood is an explicit query, not every visit to the face screen."""

    def test_change_flag(self) -> None:
        """Only 1, true, and yes send the student back to mood."""
        self.assertTrue(mood_reselect_requested("1"))
        self.assertTrue(mood_reselect_requested(" TRUE "))
        self.assertTrue(mood_reselect_requested("yes"))
        self.assertFalse(mood_reselect_requested(None))
        self.assertFalse(mood_reselect_requested(""))
        self.assertFalse(mood_reselect_requested("0"))


class ClassListRailTests(unittest.TestCase):
    """Drawer toggle copy and the per-teacher storage key."""

    def test_labels_and_storage_key(self) -> None:
        """Class (N) when closed, Hide class list when open, key includes the teacher."""
        script = r"""
import {
  classListStorageKey,
  classListToggleLabel,
  classListToggleTitle,
  classListToggleAriaLabel,
  classListStoredOpen,
} from "./lms/static/class_list_rail.js";

const store = { rows: {}, getItem(k) { return this.rows[k] ?? null; }, setItem(k, v) { this.rows[k] = v; } };
if (classListToggleLabel(false, 3) !== "Class (3)") throw new Error("closed label");
if (classListToggleLabel(false, 0) !== "Class (0)") throw new Error("zero label");
if (classListToggleLabel(false, -2) !== "Class (0)") throw new Error("negative label");
if (classListToggleLabel(true, 3) !== "Hide class list") throw new Error("open label");
if (classListToggleTitle(false) !== "Show class list") throw new Error("closed tip");
if (classListToggleTitle(true) !== "Hide class list") throw new Error("open tip");
if (classListToggleAriaLabel(false) !== "Show class list") throw new Error("closed aria");
if (classListToggleAriaLabel(true) !== "Hide class list") throw new Error("open aria");
if (classListStorageKey(9) !== "lloves-class-list:9") throw new Error("key");
if (classListStoredOpen(store, 9)) throw new Error("missing should be closed");
store.setItem(classListStorageKey(9), "open");
if (!classListStoredOpen(store, 9)) throw new Error("open should stick");
store.setItem(classListStorageKey(9), "closed");
if (classListStoredOpen(store, 9)) throw new Error("closed should stick");
if (classListStoredOpen({ getItem() { throw new Error("blocked"); } }, 9)) throw new Error("throw stays closed");
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
        )

    def test_slide_changes_do_not_reset_the_drawer(self) -> None:
        """The open/closed write happens from the button, not from a poll."""
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(js.count("setClassListOpen("), 3)
        self.assertIn("paintClassListToggle()", js)
        self.assertIn("classListStorageKey", js)
        self.assertIn('event.key !== "Escape"', js)
        self.assertIn("class-list-scrim", html)
        self.assertIn("class-list-drawer", html)
        self.assertIn("body.staff-shell #class-list-pane .ap-att-row.has-team-color .ap-att-name", css)
        self.assertIn("class-list-drawer", css)
        self.assertNotIn("--live-left-width: 7.25rem;", css)


class WhiteboardUndoTests(unittest.TestCase):
    """Undo removes one own stroke or one text box, then the one before it."""

    def test_one_step_undo_and_shortcut(self) -> None:
        """Button and Ctrl/Cmd+Z step back one item and leave remote ink."""
        script = r"""
import { bindWhiteboard } from "./lms/static/live_whiteboard.js";

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.Element = class Element {};

function makeEl(proto) {
  const listeners = {};
  const node = {
    hidden: false,
    disabled: false,
    title: "",
    value: "",
    type: "",
    style: { cursor: "", removeProperty() {} },
    dataset: {},
    className: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [],
    parentElement: null,
    width: 720,
    height: 360,
    clientWidth: 720,
    clientHeight: 360,
    offsetLeft: 0,
    offsetTop: 0,
    appendChild(child) {
      node.children.push(child);
      child.parentElement = node;
      return child;
    },
    insertAdjacentElement(_where, child) { return node.appendChild(child); },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
    querySelector() { return null; },
    getBoundingClientRect() {
      return { width: 720, height: 360, left: 0, top: 0, right: 720, bottom: 360 };
    },
    setPointerCapture() {},
    focus() {},
    select() {},
    blur() { node.dispatch("blur"); },
    getContext() {
      return {
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fill() {}, arc() {}, save() {}, restore() {},
        setLineDash() {}, measureText() { return { width: 10 }; }, fillText() {},
      };
    },
    dispatch(type, props) {
      const event = {
        clientX: 40, clientY: 40, pointerId: 1, preventDefault() {},
        stopPropagation() {}, target: node, button: 0, ...props,
      };
      for (const fn of listeners[type] || []) fn(event);
    },
    click() { node.dispatch("click"); },
  };
  if (proto) Object.setPrototypeOf(node, proto.prototype);
  return node;
}

const docListeners = {};
const stage = makeEl(HTMLElement);
const canvas = makeEl(HTMLCanvasElement);
stage.appendChild(canvas);
globalThis.document = {
  addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  createElement() { return makeEl(); },
  body: stage,
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = (fn) => { fn(); return 1; };
window.clearTimeout = () => {};
window.devicePixelRatio = 1;
window.requestAnimationFrame = (fn) => { fn(); return 1; };
globalThis.ResizeObserver = undefined;

const calls = { undo: 0, text: [] };
const undo = makeEl(HTMLButtonElement);
const redo = makeEl(HTMLButtonElement);
const pen = makeEl(HTMLButtonElement);
const board = bindWhiteboard(canvas, {
  owner: "1",
  undoBtn: undo,
  redoBtn: redo,
  penBtn: pen,
  onUndo() { calls.undo += 1; },
  onRedo() {},
  onText(label) { calls.text.push(label.text); },
});
board.setCollab(true);

function fail(message) { throw new Error(message); }
if (!undo.disabled || undo.title !== "Nothing to undo") fail("empty undo should be disabled");

function stroke(x1, y1, x2, y2) {
  pen.click();
  canvas.dispatch("pointerdown", { clientX: x1, clientY: y1 });
  canvas.dispatch("pointermove", { clientX: x2, clientY: y2 });
  canvas.dispatch("pointerup", { clientX: x2, clientY: y2 });
}

stroke(30, 40, 180, 120);
stroke(40, 50, 200, 140);
if (board.getInk().strokes.length !== 2) fail("two strokes");
if (undo.disabled || undo.title !== "Undo last stroke or text (Ctrl/Cmd+Z)") fail("undo tip");
undo.click();
if (calls.undo !== 1 || board.getInk().strokes.length !== 1) fail("one stroke undone");
undo.click();
if (calls.undo !== 2 || board.getInk().strokes.length !== 0) fail("second stroke undone");
if (!undo.disabled || undo.title !== "Nothing to undo") fail("empty again");

stroke(30, 40, 180, 120);
board.openTextAt(0.2, 0.2);
const editor = stage.children.find((child) => child !== canvas && child.className === "canvas-text-editor");
if (!editor) fail("editor missing");
editor.value = "note";
editor.blur();
if (board.getInk().texts.length !== 1 || board.getInk().texts[0].text !== "note") fail("text stored");
if (calls.text[calls.text.length - 1] !== "note") fail("text posted");
undo.click();
if (board.getInk().texts.length !== 0) fail("text box is one step");
if (calls.text[calls.text.length - 1] !== "") fail("text delete posted");
if (board.getInk().strokes.length !== 1) fail("stroke survived the text undo");
undo.click();
if (board.getInk().strokes.length !== 0) fail("stroke after the text box");

stroke(30, 40, 180, 120);
stroke(50, 60, 220, 160);
board.importRemote({
  strokes: [{ id: "remote-1", owner: "9", color: "#111", points: [[0.1, 0.1], [0.4, 0.4]] }],
}, { collab: true });
if (board.getInk().strokes.length !== 3) fail("remote stroke missing");
undo.click();
undo.click();
const left = board.getInk().strokes.map((row) => row.id);
if (left.length !== 1 || left[0] !== "remote-1") fail("remote ink was removed");

stroke(30, 40, 180, 120);
function press(target, extra) {
  const event = {
    key: "z", ctrlKey: true, metaKey: false, shiftKey: false, altKey: false,
    repeat: false, defaultPrevented: false, target,
    preventDefault() { this.defaultPrevented = true; },
    ...extra,
  };
  for (const fn of docListeners.keydown || []) fn(event);
  return event;
}
const typed = press({ tagName: "TEXTAREA", isContentEditable: false });
if (typed.defaultPrevented || board.getInk().strokes.length !== 2) fail("shortcut hijacked a text box");
const input = press({ tagName: "INPUT", isContentEditable: false });
if (input.defaultPrevented || board.getInk().strokes.length !== 2) fail("shortcut hijacked an input");
const done = press(canvas);
if (!done.defaultPrevented || board.getInk().strokes.length !== 1) fail("ctrl+z");
stroke(30, 40, 180, 120);
const mac = press(canvas, { ctrlKey: false, metaKey: true });
if (!mac.defaultPrevented || board.getInk().strokes.length !== 1) fail("cmd+z");

stroke(30, 40, 180, 120);
board.lockEditing();
const locked = press(canvas);
undo.click();
if (locked.defaultPrevented || board.getInk().strokes.length !== 2) fail("lock still allows undo");
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
