"""MCK-517: icon tools, math help, S/M/L text size, scrolling questions."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))
sys.path.insert(0, str(ROOT))

from board_ops import SqliteBoardOps, fold_one_board  # noqa: E402


class Mck517ToolbarTests(unittest.TestCase):
    """Student whiteboard tools are icon-only 44px targets."""

    def test_home_tools_are_icons_with_labels(self) -> None:
        """Pen, Line, Text, Equation, Undo, Clear, and Equation help use the named labels."""
        home = (LMS / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        css = (LMS / "static" / "student-portal.css").read_text(encoding="utf-8")
        js = (LMS / "static" / "live_whiteboard.js").read_text(encoding="utf-8")
        phone = (LMS / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        for label in ("Pen", "Line", "Text", "Equation", "Undo", "Clear board"):
            self.assertIn(f'aria-label="{label}"', home)
            self.assertIn(f'title="{label}"', home)
            self.assertIn(f'aria-label="{label}"', phone)
        for icon in ("pencil", "line", "text", "radical", "undo", "trash"):
            self.assertIn(f'data-icon="{icon}"', home)
            self.assertIn(f'data-icon="{icon}"', js)
        self.assertNotIn(">Pen</button>", home)
        self.assertNotIn(">Undo</button>", home)
        self.assertNotIn(">Clear</button>", home)
        self.assertIn("width: 44px", css)
        self.assertIn("height: 44px", css)
        self.assertIn("min-width: 44px", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn('setAttribute("aria-label", "Equation help")', js)
        self.assertIn('title = "Equation help"', js)
        self.assertIn("How to type math", js)
        self.assertIn('"Small"', js)
        self.assertIn('"Medium"', js)
        self.assertIn('"Large"', js)
        self.assertNotIn('textContent = "−"', js)
        self.assertNotIn('textContent = "+"', js)

    def test_open_questions_scroll_and_new_question_pill(self) -> None:
        """The main panel scrolls, and a busy student gets a New question pill."""
        home = (LMS / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        css = (LMS / "static" / "student-portal.css").read_text(encoding="utf-8")
        portal = (LMS / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('id="new-question-pill"', home)
        self.assertIn("New question ↓", home)
        body = css.split(".live-question-stack-body {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-y: auto", body)
        open_card = css.split(
            ".live-question-stack-body > .student-live-card.is-open-focus,", 1
        )[1].split("}", 1)[0]
        self.assertIn("overflow: visible", open_card)
        self.assertIn("studentIsComposing", portal)
        self.assertIn("applyNewQuestionScroll", portal)
        self.assertIn("is-drawing-capture", css)
        self.assertIn("touch-action: pan-y", css)


class Mck517SizeStorageTests(unittest.TestCase):
    """S/M/L lives on the existing text op and survives wipe, fold, and views."""

    def setUp(self) -> None:
        """Private sqlite file with board ops schema."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "boards.sqlite"
        import sqlite3

        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self.store = SqliteBoardOps(conn)
        self.store.ensure_schema()
        self.conn = conn

    def tearDown(self) -> None:
        """Close the connection and remove the temp directory."""
        self.conn.close()
        self.tmp.cleanup()

    def _ops(self, run_key: int, board: str) -> list[dict]:
        """Return stored op JSON objects in sequence order."""
        rows = self.conn.execute(
            "SELECT op FROM board_ops WHERE run_key = ? AND board_key = ? ORDER BY board_seq",
            (str(run_key), board),
        ).fetchall()
        return [json.loads(row["op"]) for row in rows]

    def test_letter_size_is_stored_on_the_text_op(self) -> None:
        """S, M, and L are one field on text_upsert. No new op kind is introduced."""
        for index, letter in enumerate(("S", "M", "L"), start=1):
            self.store.append_text(
                30,
                "solo:8",
                owner="8",
                name="A",
                text_id=f"t{index}",
                text=letter,
                x=0.2,
                y=0.2,
                color="#12202e",
                team_id=None,
                size=letter,
            )
        view = fold_one_board(self.store.load_keys(30, ["solo:8"]))
        self.assertEqual([row["size"] for row in view["texts"]], ["S", "M", "L"])
        stored = [op for op in self._ops(30, "solo:8") if op.get("type") == "text_upsert"]
        self.assertEqual([op["size"] for op in stored], ["S", "M", "L"])
        self.assertTrue(all(op["type"] == "text_upsert" for op in stored))

    def test_missing_size_reads_as_m_and_is_not_rewritten(self) -> None:
        """An older text op with no size field folds as M and stays without the field."""
        self.store.append_ops(
            31,
            "solo:8",
            owner="8",
            ops=[
                {
                    "type": "text_upsert",
                    "id": "old",
                    "owner": "8",
                    "text": "note",
                    "x": 0.3,
                    "y": 0.4,
                }
            ],
        )
        before = self._ops(31, "solo:8")
        self.assertNotIn("size", before[0])
        view = fold_one_board(self.store.load_keys(31, ["solo:8"]))
        self.assertEqual(view["texts"][0]["size"], "M")
        after = self._ops(31, "solo:8")
        self.assertEqual(after, before)
        self.assertNotIn("size", after[0])

    def test_wipe_unwipe_keeps_letter_size(self) -> None:
        """Clear and undo restore the same S/M/L field."""
        self.store.append_text(
            32,
            "solo:8",
            owner="8",
            name="A",
            text_id="sized",
            text="wide",
            x=0.2,
            y=0.3,
            color="#12202e",
            team_id=None,
            size="L",
        )
        self.store.append_wipe(32, "solo:8", owner="8", wipe_id="w-size")
        wiped = fold_one_board(self.store.load_keys(32, ["solo:8"]))
        self.assertEqual(wiped["texts"], [])
        self.store.append_unwipe(32, "solo:8", owner="8", unwipe_id="u-size", wipe_id="w-size")
        restored = fold_one_board(self.store.load_keys(32, ["solo:8"]))
        self.assertEqual(restored["texts"][0]["size"], "L")
        self.assertEqual(restored["texts"][0]["text"], "wide")

    def test_equation_text_is_stored_exactly_as_typed(self) -> None:
        """Plain math is not rewritten into LaTeX in the board op."""
        typed = "a/b"
        self.store.append_equation(
            33,
            "solo:8",
            owner="8",
            name="A",
            equation_id="eq",
            latex=typed,
            x=0.4,
            y=0.5,
            color="#12202e",
            team_id=None,
        )
        view = fold_one_board(self.store.load_keys(33, ["solo:8"]))
        self.assertEqual(view["equations"][0]["latex"], typed)
        stored = self._ops(33, "solo:8")[0]
        self.assertEqual(stored["latex"], typed)
        self.assertNotIn("\\frac", stored["latex"])


class Mck517ClientBehaviorTests(unittest.TestCase):
    """Translator, touch capture, text size, and the new-question pill."""

    def test_client_math_size_touch_and_scroll(self) -> None:
        """KaTeX renders the help forms, and a busy student is not scrolled."""
        script = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";
import {
  MATH_HELP_SPEC,
  boardCapturesTouch,
  equationStoredLatex,
  mathHelpLines,
  plainMathToLatex,
  textFontCss,
  textSizePx,
  textSizeToken,
} from "./lms/static/live_whiteboard.js";
import { newQuestionScrollPlan } from "./lms/static/live_question_card.js";

const sandbox = { module: { exports: {} }, exports: {}, self: {} };
vm.runInNewContext(
  readFileSync("./lms/static/vendor/katex/katex.min.js", "utf8"),
  sandbox,
  { filename: "katex.cjs" }
);
const katex = sandbox.module.exports.default || sandbox.module.exports;

function fail(message) { throw new Error(message); }

const forms = {
  "x^2": "x^{2}",
  "x^(n+1)": "x^{n+1}",
  "x_1": "x_{1}",
  "a/b": "\\frac{a}{b}",
  "(x+1)/(x-2)": "\\frac{x+1}{x-2}",
  "sqrt(x)": "\\sqrt{x}",
  pi: "\\pi",
  theta: "\\theta",
  "<=": "\\leq",
  ">=": "\\geq",
  "!=": "\\neq",
  "+-": "\\pm",
};
for (const [typed, latex] of Object.entries(forms)) {
  if (plainMathToLatex(typed) !== latex) fail(`translate ${typed} -> ${plainMathToLatex(typed)}`);
  katex.renderToString(plainMathToLatex(typed), { throwOnError: true });
}
if (plainMathToLatex("\\frac{1}{2}") !== "\\frac{1}{2}") fail("real latex changed");
if (plainMathToLatex("x^{2}") !== "x^{2}") fail("braced power changed");
katex.renderToString("\\sqrt{x}", { throwOnError: true });
if (equationStoredLatex("a/b") !== "a/b") fail("stored equation was rewritten");

const lines = mathHelpLines(katex);
const labels = lines.map((line) => line.label);
for (const spec of MATH_HELP_SPEC) {
  if (!labels.includes(spec.label)) fail(`dropped a working line: ${spec.label}`);
  const line = lines.find((row) => row.label === spec.label);
  if (line.examples.map((ex) => ex.source).join("|") !== spec.examples.join("|")) {
    fail(`examples changed for ${spec.label}`);
  }
}
if (lines.length !== MATH_HELP_SPEC.length) fail("help lines do not match the spec");

if (textSizeToken(undefined) !== "M" || textSizeToken("") !== "M") fail("missing size");
if (textSizeToken("S") !== "S" || textSizeToken("l") !== "L") fail("letter token");
const small = textSizePx("S", 360);
const medium = textSizePx("M", 360);
const large = textSizePx("L", 360);
if (!(small < medium && medium < large)) fail(`sizes ${small} ${medium} ${large}`);
if (!textFontCss("L", 360, true).startsWith("bold ")) fail("bold font");
if (textFontCss("S", 720).includes(String(textSizePx("S", 720))) === false) fail("font css");

if (boardCapturesTouch("draw") !== true) fail("pen should capture");
if (boardCapturesTouch("line") !== true) fail("line should capture");
if (boardCapturesTouch("text") !== false) fail("text should scroll");
if (boardCapturesTouch("equation") !== false) fail("equation should scroll");

const busy = newQuestionScrollPlan({
  busy: true,
  scrollTop: 180,
  previousIds: ["1"],
  nextIds: ["1", "2"],
});
if (!busy.keepScroll || !busy.showPill || busy.targetId !== "2" || busy.scrollTop !== 180) {
  fail(`busy plan ${JSON.stringify(busy)}`);
}
const free = newQuestionScrollPlan({
  busy: false,
  scrollTop: 40,
  previousIds: ["1"],
  nextIds: ["1", "2"],
});
if (free.keepScroll || free.showPill || free.targetId !== "2") fail(`free plan ${JSON.stringify(free)}`);
const same = newQuestionScrollPlan({
  busy: true,
  scrollTop: 90,
  previousIds: ["1", "2"],
  nextIds: ["1", "2"],
});
if (!same.keepScroll || same.showPill) fail(`same plan ${JSON.stringify(same)}`);

const show = readFileSync("./lms/static/poll_board_show.js", "utf8");
const tile = readFileSync("./lms/static/formative_live_board.js", "utf8");
const board = readFileSync("./lms/static/live_whiteboard.js", "utf8");
if (!show.includes("textFontCss(label && label.size")) fail("show on board drops size");
if (!tile.includes("textFontCss(label.size")) fail("projector tile drops size");
if (!board.includes("textSizePx(label?.size")) fail("board paint drops size");
if (!board.includes('canvas.setPointerCapture') ) fail("drawing capture missing");
const down = board.slice(board.indexOf('canvas.addEventListener("pointerdown"'), board.indexOf('canvas.addEventListener("pointermove"'));
const place = down.indexOf('tool === "equation" || tool === "text"');
const capture = down.indexOf("setPointerCapture");
if (place < 0 || capture < 0 || place > capture) fail("text tool captures the finger");
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_painted_and_stored_size_differs_across_s_m_l(self) -> None:
        """S, M, and L paint different fonts and stay on the label through undo."""
        script = r"""
import { bindWhiteboard, textFontCss, textSizePx } from "./lms/static/live_whiteboard.js";

function fail(message) { throw new Error(message); }
function fontPx(font) {
  const match = String(font || "").match(/(\d+(?:\.\d+)?)px/);
  return match ? Number(match[1]) : 0;
}

const tile = ["S", "M", "L"].map((size) => textSizePx(size, 160));
if (!(tile[0] < tile[1] && tile[1] < tile[2])) fail(`teacher tile sizes collapsed ${tile}`);
const tileFonts = ["S", "M", "L"].map((size) => fontPx(textFontCss(size, 160, false)));
if (!(tileFonts[0] < tileFonts[1] && tileFonts[1] < tileFonts[2])) {
  fail(`teacher tile fonts collapsed ${tileFonts}`);
}

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.Element = HTMLElement;

const painted = [];
function makeEl() {
  const listeners = {};
  const node = {
    hidden: false,
    disabled: false,
    title: "",
    value: "",
    type: "",
    textContent: "",
    className: "",
    style: {},
    dataset: {},
    children: [],
    parentElement: null,
    width: 720,
    height: 360,
    clientWidth: 320,
    clientHeight: 160,
    offsetLeft: 0,
    offsetTop: 0,
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    },
    appendChild(child) {
      node.children.push(child);
      child.parentElement = node;
      return child;
    },
    insertAdjacentElement(_where, child) { return node.appendChild(child); },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute(name, value) { node.dataset[name] = value; },
    removeAttribute() {},
    getAttribute(name) { return node.dataset[name] ?? null; },
    querySelector() { return null; },
    getBoundingClientRect() {
      return { width: 320, height: 160, left: 0, top: 0, right: 320, bottom: 160 };
    },
    setPointerCapture() {},
    focus() {},
    select() {},
    blur() { node.dispatch("blur"); },
    getContext() {
      const context = {
        _font: "10px sans-serif",
        set font(value) { this._font = value; },
        get font() { return this._font; },
        fillText() { painted.push(this._font); },
        measureText() { return { width: 24 }; },
        save() {},
        restore() {},
        setTransform() {},
        clearRect() {},
        beginPath() {},
        moveTo() {},
        lineTo() {},
        stroke() {},
        fill() {},
        arc() {},
        setLineDash() {},
      };
      return context;
    },
    dispatch(type, props) {
      const event = {
        preventDefault() {},
        stopPropagation() {},
        target: node,
        key: "",
        ...props,
      };
      for (const fn of listeners[type] || []) fn(event);
    },
    click() { node.dispatch("click"); },
  };
  return node;
}

const stage = makeEl();
const canvas = makeEl();
Object.setPrototypeOf(stage, HTMLElement.prototype);
Object.setPrototypeOf(canvas, HTMLCanvasElement.prototype);
canvas.width = 720;
canvas.height = 360;
stage.appendChild(canvas);
globalThis.document = {
  addEventListener() {},
  createElement() { return makeEl(); },
  body: stage,
  head: stage,
  querySelector() { return null; },
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = (fn) => { fn(); return 1; };
window.clearTimeout = () => {};
window.devicePixelRatio = 2;
window.requestAnimationFrame = (fn) => { fn(); return 1; };
globalThis.ResizeObserver = undefined;

const clearBtn = makeEl();
const undoBtn = makeEl();
Object.setPrototypeOf(clearBtn, HTMLButtonElement.prototype);
Object.setPrototypeOf(undoBtn, HTMLButtonElement.prototype);
const board = bindWhiteboard(canvas, { owner: "1", clearBtn, undoBtn });

function walk(node, out = []) {
  out.push(node);
  for (const child of node.children || []) walk(child, out);
  return out;
}
function editor() {
  return walk(stage).find((node) => String(node.className).includes("canvas-text-editor"));
}
function sizeButton(token) {
  return walk(stage).find((node) => node.dataset && node.dataset.size === token);
}

board.openTextAt(0.2, 0.3);
const box = editor();
if (!box) fail("text box missing");
box.value = "Hi";
const fonts = {};
const editorFonts = {};
for (const token of ["S", "M", "L"]) {
  painted.length = 0;
  const btn = sizeButton(token);
  if (!btn) fail(`missing ${token}`);
  btn.click();
  const last = painted.filter((font) => fontPx(font) > 0).pop();
  if (!last) fail(`no paint for ${token} hidden=${box.hidden} value=${JSON.stringify(box.value)} n=${painted.length}`);
  fonts[token] = fontPx(last);
  editorFonts[token] = fontPx(box.style.fontSize);
}
if (!(fonts.S < fonts.M && fonts.M < fonts.L)) fail(`painted fonts ${JSON.stringify(fonts)}`);
if (!(editorFonts.S < editorFonts.M && editorFonts.M < editorFonts.L)) {
  fail(`editor fonts ${JSON.stringify(editorFonts)}`);
}
box.dispatch("keydown", { key: "Enter" });
const stored = board.getInk().texts.map((label) => label.size);
if (stored.join(",") !== "L") fail(`stored sizes ${stored}`);

painted.length = 0;
clearBtn.click();
if (board.getInk().texts.length !== 0) fail("clear left the label");
undoBtn.click();
const restored = board.getInk().texts[0];
if (!restored || restored.size !== "L") fail(`undo size ${restored && restored.size}`);
const undone = painted.filter((font) => fontPx(font) > 0).pop();
if (fontPx(undone) !== fonts.L) fail(`undo painted ${undone} not L ${fonts.L}`);
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
