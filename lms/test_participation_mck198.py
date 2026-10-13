#!/usr/bin/env python3
"""MCK-198 (MCK-52 Phase 1): individual participation level + catch-up link."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
import participation as P  # noqa: E402


class MathTests(unittest.TestCase):
    def test_percent_and_bands(self) -> None:
        cases = {(8, 10): 4, (7, 10): 3, (6, 10): 2, (5, 10): 1, (4, 10): 0, (10, 10): 4}
        for (a, o), level in cases.items():
            self.assertEqual(P.level_for(P.percent(a, o)), level, (a, o))
        self.assertIsNone(P.level_for(P.percent(0, 0)))
        self.assertEqual(P.summary(2, 3)["percent"], 67)

    def test_k_is_fewest_more_answers(self) -> None:
        self.assertEqual(P.more_to_next(6, 10), 1)   # L2 -> 70% needs 7
        self.assertEqual(P.more_to_next(0, 10), 5)   # below L1 -> 50%
        self.assertEqual(P.more_to_next(2, 3), 1)    # 67% L2 -> 3/3 L4 (>=70%)
        self.assertEqual(P.more_to_next(1, 7), 3)    # 14% -> 4/7 = 57%
        self.assertIsNone(P.more_to_next(9, 10))

    def test_lines_and_hide_until_three_offered(self) -> None:
        self.assertIsNone(P.level_line(2, 2))
        self.assertIsNone(P.level_line(0, 0))
        self.assertEqual(P.level_line(8, 10), "8/10 completed \u00b7 Level 4")
        self.assertEqual(P.level_line(6, 10), "6/10 completed \u00b7 Level 2 \u00b7 1 more")
        self.assertEqual(P.level_line(1, 3), "1/3 completed \u00b7 1 more")

    def test_pin_hash(self) -> None:
        stored = P.hash_pin("1234")
        self.assertNotIn("1234", stored)
        self.assertTrue(P.check_pin("1234", stored))
        self.assertFalse(P.check_pin("4321", stored))
        self.assertFalse(P.valid_pin("12a4"))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U")
        self.class_id = int(self.school.game.create_class(
            year="2026/27", semester="Semester 1", course_code="MCR3U", days_preset="M/W/F",
            time_label="2:00pm", codenames=["Ava", "Ben"], offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )["id"])
        g = self.school.game.conn
        self.students = [int(r[0]) for r in g.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)).fetchall()]
        self.code = str(g.execute("SELECT live_access_code FROM classes WHERE id = ?",
                                  (self.class_id,)).fetchone()[0] or "")
        if not self.code:
            self.code = "JOIN1234"
            g.execute("UPDATE classes SET live_access_code = ? WHERE id = ?", (self.code, self.class_id))
        # Sep 30 renames: codename differs from the stored first name.
        g.execute("UPDATE students SET codename = 'Avalanche', first_name = 'Ava' WHERE id = ?",
                  (self.students[0],))
        self.first = "Avalanche"

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def run_class(self, module: str, slot: str, individual: int, group: int, answers: dict[int, int]) -> str:
        """Start a run, publish items, answer some, End. Returns the run key."""
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        cols = {r[1] for r in c.execute("PRAGMA table_info(live_class_sessions)")}
        if "teacher_state_json" in cols:
            import json
            c.execute("UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
                      (json.dumps({"live_module": module, "live_slot": slot}), sid))
        now = "2026-10-13T09:00:00"
        prompts = []
        for n in range(individual + group):
            mode = "individual" if n < individual else "group"
            pid = c.execute("INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
                            "created_at, updated_at) VALUES (?, ?, 'poll', '{}', ?, ?)",
                            (sid, n, now, now)).lastrowid
            c.execute("INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
                      "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
                      "VALUES (?, ?, ?, 'round', 'poll', ?, ?, 'closed', ?, ?, ?, ?, ?)",
                      (sid, f"{module}-q{n}", f"q{n}", '{"stem": "Q%d", "options": [{"text": "A"}]}' % n,
                       pid, mode, mode, now, now, now))
            prompts.append(pid)
        for stu, count in answers.items():
            for pid in prompts[:count] + prompts[individual:]:  # also answer every group item
                c.execute("INSERT OR IGNORE INTO live_session_responses (prompt_id, student_id, response_json, "
                          "created_at, updated_at) VALUES (?, ?, '{}', ?, ?)", (pid, stu, now, now))
        run_key = str(c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0] or "")
        self.school.cleanup_live_session_response_data(sid)
        self.school.wipe_live_sessions_for_class(self.class_id)
        return run_key


class CaptureTests(Base):
    def test_group_questions_are_excluded(self) -> None:
        self.run_class("M1", "C1", individual=4, group=3, answers={self.students[0]: 3})
        # MCK-45: individual answers survive End in live_result_snapshots too.
        kept = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_result_snapshots WHERE class_id = ? AND source = 'prompt' "
            "AND student_id = ?", (self.class_id, self.students[0])).fetchone()[0]
        self.assertGreaterEqual(kept, 3)
        data = self.school.class_participation(self.class_id)
        self.assertEqual(data["modules"][0]["offered"], 4)
        cell = data["students"][0]["modules"]["M1"]
        self.assertEqual((cell["answered"], cell["offered"], cell["percent"], cell["level"]), (3, 4, 75, 3))
        self.assertEqual(data["students"][1]["modules"]["M1"]["answered"], 0)

    def test_module_complete_after_c4_or_manual_and_reversible(self) -> None:
        self.run_class("M2", "C1", 2, 0, {})
        self.assertFalse(self.school.module_complete(self.class_id, "M2"))
        self.assertTrue(self.school.set_module_complete(self.class_id, "M2", True))
        self.assertFalse(self.school.set_module_complete(self.class_id, "M2", False))
        self.run_class("M3", "C4", 1, 0, {})
        self.assertTrue(self.school.module_complete(self.class_id, "M3"))

    def test_l4_needs_group_setting_saves_and_locks_when_complete(self) -> None:
        self.run_class("M4", "C1", 3, 0, {self.students[0]: 3})
        self.assertFalse(self.school.module_l4_needs_group(self.class_id, "M4"))
        self.assertTrue(self.school.set_module_l4_needs_group(self.class_id, "M4", True))
        before = self.school.class_participation(self.class_id)
        self.assertTrue(before["modules"][0]["l4_needs_group"])
        self.assertEqual(before["students"][0]["modules"]["M4"]["level"], 4)  # saved only, no scoring
        self.school.set_module_complete(self.class_id, "M4", True)
        with self.assertRaises(PermissionError):
            self.school.set_module_l4_needs_group(self.class_id, "M4", False)

    def test_end_creates_one_link_open_by_default(self) -> None:
        self.run_class("M1", "C2", 3, 0, {})
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertGreaterEqual(len(links[0]["token"]), 24)
        self.assertEqual((links[0]["live_module"], links[0]["live_slot"], links[0]["is_open"]), ("M1", "C2", 1))


class CatchupTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", individual=4, group=2, answers={self.students[0]: 1})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.school.set_student_pin(self.class_id, self.students[0], "1234")

    def test_pin_lockout_after_five_failures_in_15_min(self) -> None:
        t = time.time()
        for _ in range(5):
            r = self.school.catchup_sign_in(self.token, self.code, self.first, "0000", now=t)
            self.assertEqual(r["reason"], "wrong")
        self.assertEqual(self.school.catchup_sign_in(self.token, self.code, self.first, "1234", now=t)["reason"], "locked")
        later = t + P.PIN_WINDOW_S + 1
        self.assertTrue(self.school.catchup_sign_in(self.token, self.code, self.first, "1234", now=later)["ok"])
        self.assertEqual(self.school.catchup_sign_in(self.token, "WRONG", self.first, "1234")["reason"], "wrong")
        # The old first name is not a sign-in name; the codename is.
        self.assertEqual(self.school.catchup_sign_in(self.token, self.code, "Ava", "1234", now=later)["reason"], "wrong")
        stored = self.school.game.conn.execute(
            "SELECT pin_hash FROM students WHERE id = ?", (self.students[0],)).fetchone()[0]
        self.assertTrue(stored and "1234" not in stored)

    def test_join_code_typed_as_pin_is_refused_without_a_failure(self) -> None:
        t = time.time()
        for _ in range(6):
            r = self.school.catchup_sign_in(self.token, self.code, self.first, self.code, now=t)
            self.assertEqual(r["reason"], "looks_like_join_code")
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        r = self.school.catchup_sign_in(self.token, self.code, self.first, live["session_code"], now=t)
        self.assertEqual(r["reason"], "looks_like_join_code")
        self.assertEqual(self.school.conn.execute(
            "SELECT COUNT(*) FROM catchup_pin_failures").fetchone()[0], 0)
        self.assertTrue(self.school.catchup_sign_in(self.token, self.code, self.first, "1234", now=t)["ok"])
        self.assertTrue(P.looks_like_join_code("abcd"))
        self.assertTrue(P.looks_like_join_code("2345", ["2345"]))
        self.assertFalse(P.looks_like_join_code("2345"))
        self.assertFalse(P.looks_like_join_code("1234"))

    def test_pin_is_its_own_screen_and_never_on_the_live_join_form(self) -> None:
        client = self.app.test_client()
        first = client.get(f"/catchup/{self.token}").get_data(as_text=True)
        self.assertNotIn('name="pin"', first)
        step2 = client.post(f"/catchup/{self.token}", data={
            "action": "who", "join_code": self.code, "codename": self.first}).get_data(as_text=True)
        self.assertIn('name="pin"', step2)
        self.assertIn('inputmode="numeric"', step2)
        self.assertIn("pin.label", step2)
        self.assertNotIn('name="join_code"', step2)
        hint = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": self.code})
        self.assertIn("pin.looks_like_join_code", hint.get_data(as_text=True))
        for path in ("/", "/join"):
            rv = client.get(path, follow_redirects=True)
            self.assertNotIn('name="pin"', rv.get_data(as_text=True), path)

    def test_closed_link(self) -> None:
        self.school.set_catchup_link_open(self.class_id, self.token, False)
        self.assertEqual(self.school.catchup_sign_in(self.token, self.code, self.first, "1234")["reason"], "closed")
        with self.assertRaises(PermissionError):
            self.school.submit_catchup_answer(self.token, self.students[0], "item:M1-q1", "A")
        rv = self.app.test_client().get(f"/catchup/{self.token}")
        self.assertEqual(rv.status_code, 403)
        self.assertIn("This catch-up is closed. Ask your teacher if you need it reopened.",
                      rv.get_data(as_text=True))

    def test_catchup_answer_source_and_count(self) -> None:
        qs = self.school.catchup_questions(self.token, self.students[0])
        self.assertEqual([q["question_key"] for q in qs], ["item:M1-q1", "item:M1-q2", "item:M1-q3"])
        self.assertTrue(self.school.submit_catchup_answer(self.token, self.students[0], "item:M1-q1", "A"))
        self.assertFalse(self.school.submit_catchup_answer(self.token, self.students[0], "item:M1-g", "A"))
        src = self.school.conn.execute(
            "SELECT source FROM participation_answers WHERE student_id = ? AND question_key = 'item:M1-q1'",
            (self.students[0],)).fetchone()[0]
        self.assertEqual(src, "catchup")
        cell = self.school.class_participation(self.class_id)["students"][0]["modules"]["M1"]
        self.assertEqual((cell["answered"], cell["catchup"]), (2, 1))

    def test_student_page_flow_shows_only_own_questions(self) -> None:
        client = self.app.test_client()
        picker = client.get(f"/catchup/{self.token}").get_data(as_text=True)
        self.assertIn(">Avalanche<", picker)
        self.assertNotIn(">Ava<", picker)
        client.post(f"/catchup/{self.token}", data={"action": "who", "join_code": self.code,
                                                    "codename": self.first})
        rv = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "9999"})
        self.assertIn("That PIN didn&#39;t match. Try again or ask your teacher to reset it.",
                      rv.get_data(as_text=True))
        rv = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "1234"})
        html = rv.get_data(as_text=True)
        self.assertIn("1/4 completed \u00b7 1 more", html)
        self.assertIn("Q1", html)
        self.assertNotIn("Q0", html)  # already answered live
        self.assertNotIn("Q4", html)  # group item
        self.assertNotIn("Ben", html)
        self.assertNotIn("team", html.lower())

    def test_staff_api_is_teacher_only(self) -> None:
        client = self.app.test_client()
        self.assertIn(client.get(f"/api/classes/{self.class_id}/catchup-links").status_code, (302, 401, 403))
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        body = client.get(f"/api/classes/{self.class_id}/catchup-links").get_json()
        self.assertTrue(body["catchup_links"][0]["url"].endswith(f"/catchup/{self.token}"))
        self.assertEqual(client.get(f"/staff/classes/{self.class_id}/participation").status_code, 200)
        self.assertEqual(client.put(f"/api/classes/{self.class_id}/students/{self.students[1]}/pin",
                                    json={"pin": "12"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
