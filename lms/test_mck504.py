"""MCK-504: student whiteboard toolbar, line tool, feedback above the board."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent


class StudentWhiteboardToolbarTests(unittest.TestCase):
    """Template and student chrome drop erase and expose Line."""

    def test_home_toolbar_is_vertical_with_line_not_erase(self) -> None:
        """The floating pane uses a left rail with Pen, Line, Text, and Undo."""
        home = (LMS / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="student-canvas-line"', home)
        self.assertIn("live-canvas-toolbar-vertical", home)
        self.assertIn('id="student-canvas-feedback"', home)
        self.assertNotIn('id="student-canvas-erase"', home)
        self.assertNotIn('id="class-board-legend"', home)

    def test_student_css_hides_legend_and_styles_vertical_tools(self) -> None:
        """Colour chips stay off; tool buttons sit in a left column."""
        css = (LMS / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".canvas-pane .wb-formative-legend", css)
        self.assertIn("display: none !important", css)
        self.assertIn(".live-canvas-toolbar-vertical", css)

    def test_question_phone_toolbar_matches(self) -> None:
        """Docked question boards reuse the same student tool set."""
        js = (LMS / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        self.assertIn('data-tool="line"', js)
        self.assertIn("live-canvas-toolbar-vertical", js)
        self.assertNotIn('data-tool="erase"', js)


class LineStrokeAndUndoTests(unittest.TestCase):
    """Line commits two points; undo removes the segment in one press."""

    def test_line_shape_and_undo(self) -> None:
        """The line tool stores two points and one undo drops it."""
        script = r"""
import { bindWhiteboard, isLineStroke } from "./lms/static/live_whiteboard.js";

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
    blur() {},
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
  createElement(tag) {
    if (tag === "canvas") return makeEl(HTMLCanvasElement);
    if (tag === "input") return makeEl();
    return makeEl();
  },
  body: stage,
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = (fn) => { fn(); return 1; };
window.clearTimeout = () => {};
window.devicePixelRatio = 1;
window.requestAnimationFrame = (fn) => { fn(); return 1; };
globalThis.ResizeObserver = undefined;
const pen = makeEl(HTMLButtonElement);
const line = makeEl(HTMLButtonElement);
const undo = makeEl(HTMLButtonElement);
const posts = [];

const board = bindWhiteboard(canvas, {
  owner: "42",
  penBtn: pen,
  lineBtn: line,
  undoBtn: undo,
  onPoints(points, ended, strokeId) {
    posts.push({ points: points.length, ended, strokeId });
  },
});

function fail(message) { throw new Error(message); }

line.click();
canvas.dispatch("pointerdown", { clientX: 50, clientY: 60 });
canvas.dispatch("pointermove", { clientX: 210, clientY: 180 });
canvas.dispatch("pointerup", { clientX: 210, clientY: 180 });

const strokes = board.getInk().strokes;
if (strokes.length !== 1) fail("one stroke");
if (!isLineStroke(strokes[0])) fail("line shape");
if (strokes[0].points.length !== 2) fail("two points");
if (posts.length !== 1 || posts[0].points !== 2 || !posts[0].ended) fail("posted once with two points");

undo.click();
if (board.getInk().strokes.length !== 0) fail("undo removed line");

line.click();
canvas.dispatch("pointerdown", { clientX: 30, clientY: 30 });
canvas.dispatch("pointerup", { clientX: 120, clientY: 140 });
board.lockEditing();
line.click();
canvas.dispatch("pointerdown", { clientX: 10, clientY: 10 });
canvas.dispatch("pointerup", { clientX: 90, clientY: 90 });
if (board.getInk().strokes.length !== 1) fail("submit lock blocks another line");
"""
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()