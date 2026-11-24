"""MCK-504: student whiteboard toolbar, line tool, feedback above the board."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
from app import create_app  # noqa: E402


def _stroke(stroke_id: str = "ink") -> list[dict]:
    """One finished stroke for board submit tests."""
    return [
        {"type": "stroke_add", "id": stroke_id, "points": [[0.2, 0.2], [0.5, 0.5]]},
        {"type": "stroke_end", "id": stroke_id},
    ]


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


class StudentBoardFeedbackReadTests(unittest.TestCase):
    """read_board always returns feedback so the client can hide stale notes."""

    def setUp(self) -> None:
        """Staff fixture, three students, one live class."""
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("fixture-staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=fixture-staff@example.com&name=T")
        code = self.school.get_user_by_email("fixture-staff@example.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["S1", "S2", "S3"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_number": 1},
        )
        self.school.game.begin_game(self.class_id)
        self.ids = []
        for codename in ("S1", "S2", "S3"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(self.session_id, int(row["id"]), codename=codename)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _publish_board_poll(self, text: str) -> int:
        """Publish an individual board question and return prompt id."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={
                "type": "poll",
                "text": text,
                "page_number": 1,
                "stage": "round",
                "answer_with": "board",
                "save_to_bank": False,
            },
        )
        self.assertLess(rv.status_code, 400, rv.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = None
        for row in self.school.list_live_session_items(self.session_id):
            stored = row.get("item") if isinstance(row.get("item"), dict) else {}
            if str(stored.get("text") or "") == text:
                item = row
                break
        self.assertIsNotNone(item)
        published = self.school.publish_live_session_item(
            self.session_id,
            int(item["id"]),
            publish_mode="individual",
        )
        return int(published["prompt_id"])

    def _student_read(self, student_id: int) -> dict:
        """Student board poll body for this student's solo board."""
        key = f"solo:{student_id}"
        return self.school.read_board(
            self.session_id, key, 0, student_id=student_id
        )

    def test_feedback_field_clears_after_teacher_clears_note(self) -> None:
        """A cleared teacher note returns feedback=\"\" on the next read."""
        prompt_id = self._publish_board_poll("Feedback clear")
        sid = self.ids[0]
        self.school.append_question_board_ops(
            self.session_id, prompt_id, sid, _stroke()
        )
        self.school.submit_question_board(self.session_id, prompt_id, sid, "")
        key = f"solo:{sid}"
        saved = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/note",
            json={"text": "Nice slope work."},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with_note = self._student_read(sid)
        self.assertEqual(with_note.get("feedback"), "Nice slope work.")
        cleared = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/note",
            json={"text": ""},
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        after = self._student_read(sid)
        self.assertIn("feedback", after)
        self.assertEqual(after.get("feedback"), "")

    def test_feedback_field_empty_after_question_closes(self) -> None:
        """After Close, the next read_board carries feedback=\"\" so the slot hides."""
        prompt_id = self._publish_board_poll("Close clears")
        sid = self.ids[0]
        self.school.append_question_board_ops(
            self.session_id, prompt_id, sid, _stroke()
        )
        self.school.submit_question_board(self.session_id, prompt_id, sid, "")
        key = f"solo:{sid}"
        self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/note",
            json={"text": "While the question is open."},
        )
        self.assertEqual(self._student_read(sid).get("feedback"), "While the question is open.")
        board_item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if int(row.get("prompt_id") or 0) == prompt_id
        )
        self.school.close_live_session_item(self.session_id, int(board_item["id"]))
        body = self._student_read(sid)
        self.assertIn("feedback", body)
        self.assertEqual(body.get("feedback"), "")

    def test_feedback_note_is_not_shared_across_students(self) -> None:
        """A teacher note on solo:A never appears on solo:B reads."""
        prompt_id = self._publish_board_poll("Solo notes")
        for sid in (self.ids[0], self.ids[2]):
            self.school.append_question_board_ops(
                self.session_id, prompt_id, sid, _stroke(f"s-{sid}")
            )
            self.school.submit_question_board(self.session_id, prompt_id, sid, "")
        key = f"solo:{self.ids[0]}"
        self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/note",
            json={"text": "Only for S1."},
        )
        self.assertEqual(self._student_read(self.ids[0]).get("feedback"), "Only for S1.")
        peer = self._student_read(self.ids[2])
        self.assertIn("feedback", peer)
        self.assertEqual(peer.get("feedback"), "")


if __name__ == "__main__":
    unittest.main()