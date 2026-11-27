#!/usr/bin/env python3
"""MCK-532 / MCK-533: phone toolbar hits and a mid-stroke minimize stays still.

Seeded demo data only. Assertions do not print names, emails, codes, or ids.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402

SCRIPT = LMS_DIR / "e2e" / "board_toolbar_mck532_533.mjs"
def _evidence_dir() -> Path:
    """Write toolbar screenshots somewhere this machine can create."""
    override = os.environ.get("MCK532_EVIDENCE", "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "mck-532-533-evidence"


EVIDENCE = _evidence_dir()


def _chrome() -> str:
    """Return a Chrome binary, or empty when this machine has none."""
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/local/bin/google-chrome",
        "/usr/bin/chromium",
    ):
        if path and Path(path).is_file():
            return path
    return ""


def _node_env() -> dict[str, str]:
    """Copy the process env, adding a playwright-core path when one is given."""
    env = {**os.environ}
    node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()
    if node_path:
        env["NODE_PATH"] = node_path
    return env


def _playwright_ready() -> bool:
    """True when node can import playwright-core and Chrome is installed."""
    if not (shutil.which("node") and _chrome()):
        return False
    probe = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import { createRequire } from 'node:module';"
                "createRequire(process.cwd() + '/lms/e2e/board_toolbar_mck532_533.mjs')"
                ".resolve('playwright-core');"
            ),
        ],
        cwd=str(REPO_ROOT),
        env=_node_env(),
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


class BoardToolbarSourceTests(unittest.TestCase):
    """Markup and CSS keep a one-column phone rail plus a corner Undo/Clear row."""

    def test_toolbar_splits_drawing_tools_from_undo_and_clear(self) -> None:
        """toolBar() keeps Pen–Equation in the column and Undo/Clear in the corner."""
        js = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        block = js.split("function toolBar()")[1].split("export function paintBoardFeedback")[0]
        self.assertIn("live-canvas-toolbar-cluster", block)
        self.assertIn("live-canvas-toolbar-vertical", block)
        self.assertIn("live-canvas-toolbar-corner", block)
        column = block.split("live-canvas-toolbar-vertical")[1].split("live-canvas-toolbar-corner")[0]
        corner = block.split("live-canvas-toolbar-corner")[1]
        for tool in ("pen", "line", "text", "equation"):
            self.assertIn(f'data-tool="{tool}"', column)
            self.assertNotIn(f'data-tool="{tool}"', corner)
        self.assertIn('data-tool="undo"', corner)
        self.assertIn('data-tool="clear"', corner)
        self.assertNotIn('data-tool="undo"', column)
        self.assertNotIn('data-tool="clear"', column)

    def test_phone_css_keeps_one_column_and_a_corner_row(self) -> None:
        """Phone width does not use a two-column overlay over the board."""
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".live-canvas-toolbar-corner", css)
        self.assertIn("left: 52px", css)
        self.assertIn("min-width: 44px", css)
        self.assertIn("@media (max-width: 719.98px)", css)
        self.assertNotIn("grid-template-columns: 44px 44px", css)

    def test_stack_paint_defers_under_a_stroke_and_anchors_the_card(self) -> None:
        """A mid-stroke payload is held, then the card's top is used as the scroll anchor."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("function studentBoardStrokeIsDown()", js)
        self.assertIn("function armHeldLifecycleStackPaint()", js)
        self.assertIn('canvas.student-canvas.is-stroke-down', js)
        self.assertIn("pointerup", js)
        self.assertIn("pointercancel", js)
        self.assertIn("function lifecycleStackAnchorCard(", js)
        self.assertIn("pinLifecycleStackAnchor", js)
        self.assertIn("getBoundingClientRect().top - anchorTop", js)


@unittest.skipUnless(
    _playwright_ready(),
    "node + Chrome + playwright-core are required",
)
class BoardToolbarBrowserTests(unittest.TestCase):
    """Headless phone checks: every tool is tappable, and a mid-stroke minimize stays still."""

    def setUp(self) -> None:
        """One staff session, one synthetic student, a long earlier question, and a board."""
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("fixture-staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get(
            "/auth/google/callback?email=fixture-staff@example.com&name=T"
        )
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("fixture-staff@example.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["S1"],
            },
        ).get_json()
        self.class_id = int(created["class"]["id"])
        session = self.school.start_live_class_session(
            self.class_id, int(teacher["id"]), is_test=True
        )
        self.session_id = int(session["id"])
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_number": 2,
                "class_set": True,
            },
        )
        self.school.game.begin_game(self.class_id)
        student = self.school.game.find_student_by_codename(self.class_id, "S1")
        self.student_id = int(student["id"])
        self.earlier = self._add(
            {
                "type": "mc",
                "text": "Earlier MC " + "long stem " * 12,
                "options": ["A", "B", "C", "D"],
                "correct_index": 0,
            }
        )
        self._add({"type": "poll", "text": "Show your work on the board", "answer_with": "board"})

    def tearDown(self) -> None:
        """Close the temp database and any local server started by a test."""
        if getattr(self, "server", None):
            self.server.shutdown()
        self.school.close()
        self.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def _add(self, body: dict) -> dict:
        """Publish one question on the current slide and stage."""
        rv = self.staff.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={"page_number": 2, "stage": "round", "save_to_bank": False, **body},
        )
        self.assertLess(rv.status_code, 400, "add-question failed")
        item_id = str(rv.get_json()["placement"]["item"]["id"])
        self.school.ensure_live_session_items(self.session_id)
        row = next(
            item
            for item in self.school.list_live_session_items(self.session_id)
            if str(item.get("item_id") or "") == item_id
        )
        return self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )

    def _join_student(self):
        """Open a student client on the live session and finish the door."""
        live = self.school.get_live_session(self.session_id)
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "S1"},
        )
        self.assertIn(joined.status_code, (302, 303))
        match = re.search(r"[?&]v=([^&]+)", joined.headers.get("Location", ""))
        self.assertIsNotNone(match)
        visit = match.group(1)
        student.post("/student/mood", data={"visit_token": visit, "skip": "1"})
        page = student.get(f"/student/character?v={visit}").get_data(as_text=True)
        pick = re.search(r'name="character" value="([^"]+)"', page)
        if pick:
            student.post(
                "/student/character",
                data={"visit_token": visit, "character": pick.group(1)},
            )
        return student, visit

    def test_phone_tools_are_tappable_and_minimize_keeps_the_stroke(self) -> None:
        """Every tool is under its own centre, and a mid-stroke minimize keeps the board still."""
        from werkzeug.serving import make_server

        student, visit = self._join_student()
        earlier_id = int(self.earlier["id"])
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        cfg = {
            "base": f"http://127.0.0.1:{self.server.server_port}",
            "chrome": _chrome(),
            "cookies": {ck.key: ck.value for ck in student._cookies.values()},
            "staffCookies": {ck.key: ck.value for ck in self.staff._cookies.values()},
            "path": f"/student/home?v={visit}",
            "minimizePath": f"/api/live-sessions/{self.session_id}/items/{earlier_id}/settings",
            "out": str(EVIDENCE),
        }
        path = Path(self.tmp.name) / "e2e.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(SCRIPT), str(path)],
            cwd=str(REPO_ROOT),
            env=_node_env(),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.server.shutdown()
        self.server = None
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, proc.stderr[-1500:] if proc.stderr else "no e2e output")
        out = json.loads(lines[-1])
        safe = {
            key: out[key]
            for key in out
            if key in {
                "toolbar", "actStatus", "midStrokeDown", "midCards", "midMinimized",
                "midDelta", "topDelta", "midInk", "afterInk", "cardsBefore", "cardsAfter",
                "minimizedAfter", "minimizedBefore", "errors",
            }
        }
        (EVIDENCE / "e2e-result.json").write_text(json.dumps(safe, indent=2), encoding="utf-8")
        self.assertEqual(out.get("errors"), [], "page errors")
        for width in ("360", "390"):
            hits = out["toolbar"][width]
            self.assertEqual(len(hits), 6, width)
            for row in hits:
                self.assertTrue(row["present"], row["tool"])
                self.assertGreaterEqual(row["w"], 44, row["tool"])
                self.assertGreaterEqual(row["h"], 44, row["tool"])
                self.assertTrue(row["hit"], row["tool"])
        self.assertEqual(out["actStatus"], 200)
        self.assertTrue(out["midStrokeDown"])
        self.assertEqual(out["midCards"], out["cardsBefore"])
        self.assertEqual(out["midMinimized"], out["minimizedBefore"])
        self.assertLessEqual(out["midDelta"], 1)
        self.assertLessEqual(out["topDelta"], 1)
        self.assertGreater(out["midInk"], 0)
        self.assertGreater(out["afterInk"], 0)
        applied = (
            out["minimizedAfter"] > out["minimizedBefore"]
            or out["cardsAfter"] < out["cardsBefore"]
        )
        self.assertTrue(applied, "minimize did not reach the student stack")
        self.assertTrue((EVIDENCE / "toolbar-360.png").is_file())
        self.assertTrue((EVIDENCE / "toolbar-390.png").is_file())


if __name__ == "__main__":
    unittest.main()
