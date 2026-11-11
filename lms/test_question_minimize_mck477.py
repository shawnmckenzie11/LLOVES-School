#!/usr/bin/env python3
"""MCK-477: every question stays on the card, and × minimizes it.

Synthetic roster label only. Assertions do not print names, emails, or join codes.
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
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402

SCRIPT = LMS_DIR / "e2e" / "question_minimize_mck477.mjs"

# Staff-authored kinds, plus a poll answered on a board.
QUESTION_CASES: tuple[dict[str, Any], ...] = (
    {
        "label": "mc",
        "body": {
            "type": "mc",
            "text": "MC close check",
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_index": 0,
        },
        "answer": {"choice": "Red"},
    },
    {
        "label": "numeric",
        "body": {
            "type": "numeric",
            "text": "Numeric close check",
            "correct_answer": "4",
        },
        "answer": {"value": 4},
    },
    {
        "label": "poll",
        "body": {
            "type": "poll",
            "text": "Poll close check",
            "options": ["Yes", "No"],
        },
        "answer": {"choice": "Yes"},
    },
    {
        "label": "rank",
        "body": {
            "type": "rank",
            "text": "Rank close check",
            "options": ["Slope", "Intercept", "Domain"],
        },
        "answer": None,
    },
    {
        "label": "board",
        "body": {
            "type": "poll",
            "text": "Board close check",
            "answer_with": "board",
        },
        "answer": None,
    },
    {
        "label": "team_draw",
        "body": {"type": "team_draw", "text": "Draw close check"},
        "answer": None,
    },
)


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


def _playwright_ready() -> bool:
    """True when node can import playwright-core and Chrome is installed."""
    if not (shutil.which("node") and _chrome()):
        return False
    env = {**os.environ}
    node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()
    if node_path:
        env["NODE_PATH"] = node_path
    probe = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import { createRequire } from 'node:module';"
                "createRequire(process.cwd() + '/lms/e2e/question_minimize_mck477.mjs')"
                ".resolve('playwright-core');"
            ),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


class AlwaysSavedQuestionTests(unittest.TestCase):
    """Published questions stay on the student card without a persist flag."""

    def setUp(self) -> None:
        """One staff session, one synthetic student, and a live class."""
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("fixture-staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(
            "/auth/google/callback?email=fixture-staff@example.com&name=T"
        )
        code = self.school.get_user_by_email("fixture-staff@example.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["S1"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_number": 4,
                "class_set": True,
            },
        )
        self.school.game.begin_game(self.class_id)
        student = self.school.game.find_student_by_codename(self.class_id, "S1")
        self.assertIsNotNone(student)
        self.student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, self.student_id, codename="S1"
        )
        try:
            from team_draw import BUILTIN_PROMPTS
        except ImportError:
            from lms.team_draw import BUILTIN_PROMPTS

        self.draw_prompt_id = str(BUILTIN_PROMPTS[0]["id"])

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add(self, body: dict[str, Any]) -> dict[str, Any]:
        """Publish one staff question and return its lifecycle row.

        Args:
            body: Add-question JSON. Page and stage are filled in here.
        """
        payload = {"page_number": 4, "stage": "round", "save_to_bank": False, **body}
        if payload.get("type") == "team_draw":
            payload["team_draw_prompt_id"] = self.draw_prompt_id
            payload["text"] = payload.get("text") or "Draw close check"
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=payload,
        )
        self.assertLess(rv.status_code, 400, rv.get_json())
        item_id = str(rv.get_json()["placement"]["item"]["id"])
        self.school.ensure_live_session_items(self.session_id)
        row = next(
            item
            for item in self.school.list_live_session_items(self.session_id)
            if str(item.get("item_id") or "") == item_id
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        return published

    def _card(self, item_id: str) -> dict[str, Any] | None:
        """Return the student card for one catalogue id, from any pool."""
        payload = self.school.student_live_items_payload(self.session_id, self.student_id)
        pools = (
            payload.get("active_questions") or [],
            payload.get("closed_results") or [],
            payload.get("saved_cards") or [],
        )
        for pool in pools:
            for row in pool:
                if str(row.get("item_id") or "") == item_id:
                    return row
        return None

    def test_each_question_type_stays_after_close_and_leaving_the_page(self) -> None:
        """Closing a question keeps it, with the answer, after the teacher moves on."""
        published: list[tuple[str, dict[str, Any]]] = []
        for case in QUESTION_CASES:
            row = self._add(case["body"])
            published.append((case["label"], row))
            answer = case["answer"]
            if case["label"] == "rank":
                prompt = self.school._prompt_for_live_item(row)
                options = (prompt or {}).get("payload", {}).get("rank_options") or []
                answer = {"order": [opt["id"] for opt in options]}
            if answer:
                prompt = self.school._prompt_for_live_item(row)
                self.assertIsNotNone(prompt)
                self.school.submit_live_prompt_response(
                    int(prompt["id"]), self.student_id, answer
                )
            self.school.close_live_session_item(self.session_id, int(row["id"]))
        self.school.set_live_session_teacher_state(
            self.session_id, stage="summary", page_number=9
        )
        for label, row in published:
            card = self._card(str(row.get("item_id") or ""))
            self.assertIsNotNone(card, label)
            self.assertNotIn("save_to_card", card)
            self.assertEqual(card["status"], "closed")
            if label in {"mc", "numeric", "poll", "rank"}:
                self.assertIsNotNone(card.get("my_response"), label)
                self.assertIsNotNone(card.get("results"), label)

    def test_legacy_save_column_still_returns_the_card(self) -> None:
        """Old rows with the column set to 0 or 1 both stay readable."""
        kept = self._add(QUESTION_CASES[0]["body"])
        dropped = self._add({**QUESTION_CASES[1]["body"], "text": "Numeric legacy check"})
        self.school.close_live_session_item(self.session_id, int(kept["id"]))
        self.school.close_live_session_item(self.session_id, int(dropped["id"]))
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET save_to_card = 1 WHERE id = ?",
                (int(kept["id"]),),
            )
            self.school.conn.execute(
                "UPDATE live_session_items SET save_to_card = 0 WHERE id = ?",
                (int(dropped["id"]),),
            )
            self.school.conn.commit()
        self.school.set_live_session_teacher_state(
            self.session_id, stage="play", page_number=8
        )
        for item_id in (kept.get("item_id"), dropped.get("item_id")):
            card = self._card(str(item_id or ""))
            self.assertIsNotNone(card, str(item_id))
            self.assertEqual(card["status"], "closed")

    def test_no_save_to_card_control_and_settings_ignore_the_old_field(self) -> None:
        """The staff strip has no checkbox, and a lone old flag is not a setting."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertNotIn("Save to card", js)
        self.assertNotIn("data-save-to-card", js)
        self.assertNotIn("Save to card", html)
        self.assertNotIn("Save to card", student)
        self.assertIn("function minimizeLiveQuestion(", student)
        row = self._add(QUESTION_CASES[0]["body"])
        rv = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/settings",
            json={"save_to_card": True},
        )
        self.assertEqual(rv.status_code, 400)


@unittest.skipUnless(
    _playwright_ready(),
    "node + Chrome + playwright-core are required",
)
class MinimizeQuestionBrowserTests(AlwaysSavedQuestionTests):
    """Closing each question type with × parks a right-panel card."""

    def test_each_question_type_stays_after_close_and_leaving_the_page(self) -> None:
        """Server coverage lives on the non-browser class."""
        self.skipTest("covered by AlwaysSavedQuestionTests")

    def test_legacy_save_column_still_returns_the_card(self) -> None:
        """Server coverage lives on the non-browser class."""
        self.skipTest("covered by AlwaysSavedQuestionTests")

    def test_no_save_to_card_control_and_settings_ignore_the_old_field(self) -> None:
        """Server coverage lives on the non-browser class."""
        self.skipTest("covered by AlwaysSavedQuestionTests")

    def test_x_minimizes_each_question_type_and_keeps_the_card(self) -> None:
        """Each published card docks, and popping one back still shows it."""
        from werkzeug.serving import make_server

        stems = []
        for case in QUESTION_CASES:
            row = self._add(case["body"])
            stored = row.get("item") if isinstance(row.get("item"), dict) else {}
            stem = str(stored.get("text") or case["body"]["text"])
            if case["label"] == "team_draw":
                stem = "Draw your team"
            stems.append(stem[:18])
            if case["answer"]:
                prompt = self.school._prompt_for_live_item(row)
                self.school.submit_live_prompt_response(
                    int(prompt["id"]), self.student_id, case["answer"]
                )
            self.school.close_live_session_item(self.session_id, int(row["id"]))
        live = self.school.get_live_session(self.session_id)
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_attendees
                SET left_at = '2000-01-01T00:00:00'
                WHERE live_session_id = ? AND student_id = ?
                """,
                (self.session_id, self.student_id),
            )
            self.school.conn.commit()
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
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        cfg = {
            "base": f"http://127.0.0.1:{self.server.server_port}",
            "chrome": _chrome(),
            "cookies": {ck.key: ck.value for ck in student._cookies.values()},
            "path": f"/student/home?v={visit}",
            "stems": stems,
        }
        path = Path(self.tmp.name) / "e2e.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        env = {**os.environ}
        node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()
        if node_path:
            env["NODE_PATH"] = node_path
        proc = subprocess.run(
            ["node", str(SCRIPT), str(path)],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        self.server.shutdown()
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, proc.stderr[-1500:])
        out = json.loads(lines[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["saveControl"], 0)
        self.assertEqual(out["missing"], [], out.get("stems"))
        self.assertEqual(out["opened"], len(stems))
        self.assertEqual(out["chips"], len(stems))
        self.assertEqual(out["openAfter"], 0)
        self.assertEqual(str(out["badge"]).lower(), "results")
        self.assertTrue(out["keptStem"])


if __name__ == "__main__":
    unittest.main()
