#!/usr/bin/env python3
"""MCK-198 (MCK-52 Phase 1): individual participation level + catch-up link."""

from __future__ import annotations

import json
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


def catchup_data(html: str) -> dict:
    """The ``#catchup-data`` JSON the catch-up page hands to the live renderer."""
    start = html.index('id="catchup-data">') + len('id="catchup-data">')
    return json.loads(html[start:html.index("</script>", start)])


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
        stored = P.hash_pin("4321")
        self.assertNotIn("4321", stored)
        self.assertTrue(P.check_pin("4321", stored))
        self.assertFalse(P.check_pin("9876", stored))
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
        self.school.set_offering_schedule(
            int(offering["id"]),
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )
        self.class_id = int(self.school.game.create_class(
            year="2026/27", semester="Semester 1", course_code="MCR3U", days_preset="M/W/F",
            time_label="2:00pm", codenames=["Ava", "Ben"], offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )["id"])
        g = self.school.game.conn
        self.students = [int(r[0]) for r in g.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)).fetchall()]
        # New classes leave the old per-class join code blank (gate HIGH).
        g.execute("UPDATE classes SET live_access_code = NULL WHERE id = ?", (self.class_id,))
        # Sep 30 renames: codename differs from the stored first name.
        g.execute("UPDATE students SET codename = 'Avalanche', first_name = 'Ava' WHERE id = ?",
                  (self.students[0],))
        self.first = "Avalanche"

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def run_class(
        self,
        module: str,
        slot: str,
        individual: int,
        group: int,
        answers: dict[int, int],
        *,
        wipe: bool = True,
        ended_iso: str = "2026-10-14T14:30:00",
    ) -> str:
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
                      (sid, f"{module}-q{n}", f"q{n}",
                       '{"question_title": "c1 Q%d", "text": "Real question %d with x^2", "type": "mc", '
                       '"options": [{"text": "Option A text"}, {"text": "Option B text"}], '
                       '"correct_answer": "B", "key": "B"}' % (n, n),
                       pid, mode, mode, now, now, now))
            prompts.append(pid)
        for stu, count in answers.items():
            for pid in prompts[:count] + prompts[individual:]:  # also answer every group item
                c.execute("INSERT OR IGNORE INTO live_session_responses (prompt_id, student_id, response_json, "
                          "created_at, updated_at) VALUES (?, ?, '{}', ?, ?)", (pid, stu, now, now))
        run_key = str(c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0] or "")
        c.execute(
            "UPDATE live_class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (ended_iso, sid),
        )
        c.commit()
        self.school.cleanup_live_session_response_data(sid)
        if wipe:
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

    def test_undo_deletes_the_mark_so_c4_completes_and_locks(self) -> None:
        self.run_class("M5", "C1", 2, 0, {})
        self.school.set_module_complete(self.class_id, "M5", True)
        self.assertFalse(self.school.set_module_complete(self.class_id, "M5", False))
        self.assertIsNone(self.school.conn.execute(
            "SELECT 1 FROM participation_module_marks WHERE class_id = ? AND live_module = 'M5'",
            (self.class_id,)).fetchone())
        self.run_class("M5", "C4", 1, 0, {})
        self.assertTrue(self.school.module_complete(self.class_id, "M5"))
        with self.assertRaises(PermissionError):
            self.school.set_module_l4_needs_group(self.class_id, "M5", True)

    def test_percent_counts_distinct_slot_and_question_pairs(self) -> None:
        """The same placement key in C1 and C2 is two questions; a re-run is not."""
        s0, s1 = self.students
        self.run_class("M1", "C1", 3, 0, {s0: 2})
        self.run_class("M1", "C2", 3, 0, {s0: 1, s1: 3})
        data = self.school.class_participation(self.class_id)
        self.assertEqual(data["modules"][0]["offered"], 6)
        cells = {row["student_id"]: row["modules"]["M1"] for row in data["students"]}
        self.assertEqual((cells[s0]["answered"], cells[s0]["percent"]), (3, 50))
        self.assertEqual((cells[s1]["answered"], cells[s1]["percent"]), (3, 50))
        # Re-running C1 re-offers the same keys: still 6 offered, answers union.
        self.run_class("M1", "C1", 3, 0, {s0: 3})
        data = self.school.class_participation(self.class_id)
        self.assertEqual(data["modules"][0]["offered"], 6)
        s0_cell = next(r for r in data["students"] if r["student_id"] == s0)["modules"]["M1"]
        self.assertEqual(s0_cell["answered"], 4)
        own = self.school.student_module_participation(self.class_id, s0, "M1")
        self.assertEqual((own["answered"], own["offered"]), (4, 6))
        # A catch-up answer on the C2 link counts its own (C2, key) pair.
        c2 = next(l for l in self.school.list_catchup_links(self.class_id) if l["live_slot"] == "C2")
        self.assertTrue(self.school.submit_catchup_answer(c2["token"], s0, "item:M1-q1", "x"))
        s0_cell = next(r for r in self.school.class_participation(self.class_id)["students"]
                       if r["student_id"] == s0)["modules"]["M1"]
        self.assertEqual((s0_cell["answered"], s0_cell["catchup"]), (5, 1))

    def test_end_creates_one_link_open_by_default(self) -> None:
        self.run_class("M1", "C2", 3, 0, {})
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertGreaterEqual(len(links[0]["token"]), 24)
        self.assertEqual((links[0]["live_module"], links[0]["live_slot"], links[0]["is_open"]), ("M1", "C2", 1))


class CatchupTests(Base):

    def catchup_url(self, token: str | None = None) -> str:
        links = self.school.list_catchup_links(self.class_id)
        tok = token or self.token
        link = next((row for row in links if row["token"] == tok), links[0])
        link = {**link, "class_id": self.class_id, "token": tok}
        return self.school.catchup_canonical_path_for_link(link)

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", individual=4, group=2, answers={self.students[0]: 1})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.school.set_student_pin(self.class_id, self.students[0], "4321")

    def sign_in(self, client, name: str | None = None, pin: str = "4321",
                ip: str = "127.0.0.1", token: str | None = None, *, play: bool = True) -> str:
        """Pick a codename, then send a PIN, from one client IP. Returns the page."""
        tok = token or self.token
        post_url = f"/catchup/{tok}"
        env = {"REMOTE_ADDR": ip}
        client.post(post_url, data={"action": "who", "codename": name or self.first}, environ_base=env)
        rv = client.post(post_url, data={"action": "pin", "pin": pin}, environ_base=env)
        if play and rv.status_code in (301, 302, 303, 307, 308):
            loc = rv.headers.get("Location") or ""
            if "/student/dashboard" in loc:
                return client.get(self.catchup_url(tok) + "?play=1", environ_base=env).get_data(
                    as_text=True
                )
        return rv.get_data(as_text=True)

    def test_one_course_pin_works_on_every_link(self) -> None:
        """Shawn: one PIN per student, on the student, reused by every link."""
        self.run_class("M2", "C1", 2, 0, {})
        tokens = [link["token"] for link in self.school.list_catchup_links(self.class_id)]
        self.assertEqual(len(tokens), 2)
        for token in tokens:
            self.assertTrue(self.school.catchup_sign_in(token, self.first, "4321")["ok"], token)
            client = self.app.test_client()
            self.assertIn('id="catchup-data"', self.sign_in(client, token=token))
        stored = self.school.game.conn.execute(
            "SELECT pin_hash FROM students WHERE id = ?", (self.students[0],)).fetchone()[0]
        self.assertTrue(stored)
        # Wrong PINs no longer lock the student across links (MCK-206).
        t = time.time()
        for _ in range(5):
            self.school.catchup_sign_in(tokens[0], self.first, "0000", now=t)
        self.assertTrue(self.school.catchup_sign_in(tokens[1], self.first, "4321", now=t)["ok"])

    def test_ip_lockout_before_not_set_message(self) -> None:
        ben = self.school.class_codenames(self.class_id)[self.students[1]]
        client = self.app.test_client()
        client.post(f"/catchup/{self.token}", data={"action": "who", "codename": self.first})
        for _ in range(5):
            client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "0000"})
        client.post(f"/catchup/{self.token}", data={"action": "who", "codename": ben})
        html = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "4321"}).get_data(as_text=True)
        self.assertIn('data-key="pin.ip_locked"', html)
        self.assertNotIn('data-key="pin.not_set"', html)

    def test_pin_not_set_has_its_own_copy_and_is_not_a_failure(self) -> None:
        ben = self.school.class_codenames(self.class_id)[self.students[1]]
        for _ in range(6):
            self.assertEqual(self.school.catchup_sign_in(self.token, ben, "4321")["reason"], "not_set")
        self.assertEqual(self.school.conn.execute(
            "SELECT COUNT(*) FROM catchup_pin_failures").fetchone()[0], 0)
        html = self.sign_in(self.app.test_client(), name=ben, ip="198.51.100.4")
        self.assertIn('data-key="pin.not_set"', html)
        self.assertIn("Your teacher hasn&#39;t set your PIN yet. Ask your teacher.", html)
        self.assertNotIn('id="catchup-data"', html)
        self.assertEqual(self.school.count_join_failures(
            P.catchup_ip_limit_key("ip:198.51.100.4"), seconds=P.PIN_WINDOW_S), 0)
        # Once the teacher sets it, the same student signs in.
        self.school.set_student_pin(self.class_id, self.students[1], "5678")
        self.assertTrue(self.school.catchup_sign_in(self.token, ben, "5678")["ok"])

    def test_wrong_pins_are_rate_limited_per_link_and_ip(self) -> None:
        from unittest import mock

        ben = self.school.class_codenames(self.class_id)[self.students[1]]
        self.school.set_student_pin(self.class_id, self.students[1], "5678")
        attacker, other = "203.0.113.7", "203.0.113.99"
        with mock.patch.object(P, "PIN_MAX_ATTEMPTS", 3):
            client = self.app.test_client()
            for _ in range(3):
                html = self.sign_in(client, pin="0000", ip=attacker)
                self.assertIn('data-key="pin.wrong"', html)
            blocked = self.sign_in(client, name=ben, pin="5678", ip=attacker)
            self.assertIn('data-key="pin.ip_locked"', blocked)
            self.assertNotIn('id="catchup-data"', blocked)
            self.assertIn('id="catchup-data"',
                          self.sign_in(self.app.test_client(), name=ben, pin="5678", ip=other))
        limit_key = P.catchup_link_ip_limit_key(self.token, "ip:" + attacker)
        self.assertEqual(self.school.count_join_failures(limit_key, seconds=P.PIN_WINDOW_S), 3)
        self.assertEqual(
            self.school.count_join_failures(
                P.catchup_ip_limit_key("ip:" + attacker), seconds=P.PIN_WINDOW_S
            ),
            3,
        )

    def test_teacher_pin_reset_ends_that_students_catchup_sessions(self) -> None:
        client = self.app.test_client()
        self.assertIn('id="catchup-data"', self.sign_in(client))
        self.assertIn(
            'id="catchup-data"',
            client.get(self.catchup_url() + "?play=1").get_data(as_text=True),
        )
        ben = self.school.class_codenames(self.class_id)[self.students[1]]
        self.school.set_student_pin(self.class_id, self.students[1], "5678")
        ben_client = self.app.test_client()
        self.assertIn('id="catchup-data"', self.sign_in(ben_client, name=ben, pin="5678"))
        # Same digits still end it: a reset always makes a new hash.
        self.school.set_student_pin(self.class_id, self.students[0], "4321")
        page = client.get(self.catchup_url()).get_data(as_text=True)
        self.assertIn('name="codename"', page)
        self.assertNotIn('id="catchup-data"', page)
        client.post(f"/catchup/{self.token}", data={"question_key": "item:M1-q1", "answer": "A"})
        self.assertIsNone(self.school.conn.execute(
            "SELECT 1 FROM participation_answers WHERE student_id = ? AND source = 'catchup'",
            (self.students[0],)).fetchone())
        # Ben's session is untouched; Avalanche signs in again with the PIN.
        self.assertIn(
            'id="catchup-data"',
            ben_client.get(self.catchup_url() + "?play=1").get_data(as_text=True),
        )
        self.assertIn('id="catchup-data"', self.sign_in(client))

    def test_capture_failure_keeps_the_live_responses(self) -> None:
        from unittest import mock

        with mock.patch.object(type(self.school), "_capture_participation_unlocked",
                               side_effect=RuntimeError("boom")):
            self.run_class("M9", "C1", 2, 0, {self.students[0]: 2}, wipe=False)
        kept = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_session_responses WHERE student_id = ?",
            (self.students[0],)).fetchone()[0]
        self.assertEqual(kept, 2)

    def test_pin_lockout_after_five_failures_in_15_min(self) -> None:
        client = self.app.test_client()
        client.post(f"/catchup/{self.token}", data={"action": "who", "codename": self.first})
        for _ in range(5):
            html = client.post(
                f"/catchup/{self.token}", data={"action": "pin", "pin": "0000"}
            ).get_data(as_text=True)
            self.assertIn('data-key="pin.wrong"', html)
        locked = client.post(
            f"/catchup/{self.token}", data={"action": "pin", "pin": "4321"}
        ).get_data(as_text=True)
        self.assertIn('data-key="pin.ip_locked"', locked)
        self.assertEqual(self.school.catchup_sign_in(self.token, "Nobody", "4321")["reason"], "unknown_name")
        stored = self.school.game.conn.execute(
            "SELECT pin_hash FROM students WHERE id = ?", (self.students[0],)).fetchone()[0]
        self.assertTrue(stored and "4321" not in stored)

    def test_join_code_typed_as_pin_is_refused_without_a_failure(self) -> None:
        t = time.time()
        for _ in range(6):
            r = self.school.catchup_sign_in(self.token, self.first, "ABCD", now=t)
            self.assertEqual(r["reason"], "looks_like_join_code")
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        r = self.school.catchup_sign_in(self.token, self.first, live["session_code"], now=t)
        self.assertEqual(r["reason"], "looks_like_join_code")
        self.school.game.conn.execute(
            "UPDATE classes SET live_access_code = 'ABCD12XY' WHERE id = ?", (self.class_id,)
        )
        self.school.game.conn.commit()
        self.assertEqual(
            self.school.catchup_sign_in(self.token, self.first, "ABCD12XY", now=t)["reason"], "wrong"
        )
        self.assertTrue(self.school.catchup_sign_in(self.token, self.first, "4321", now=t)["ok"])
        self.assertTrue(P.looks_like_join_code("abcd"))
        self.assertTrue(P.looks_like_join_code("2345", ["2345"]))
        self.assertFalse(P.looks_like_join_code("2345"))
        self.assertFalse(P.looks_like_join_code("4321"))

    def test_pin_is_its_own_screen_and_never_on_the_live_join_form(self) -> None:
        client = self.app.test_client()
        first = client.get(self.catchup_url()).get_data(as_text=True)
        self.assertNotIn('name="pin"', first)
        step2 = client.post(f"/catchup/{self.token}", data={
            "action": "who", "codename": self.first}).get_data(as_text=True)
        self.assertIn('name="pin"', step2)
        self.assertIn('inputmode="numeric"', step2)
        self.assertIn("pin.label", step2)
        self.assertNotIn('name="join_code"', step2)
        self.assertIn("Your 4-digit PIN", step2)
        self.assertIn("Not the join code. Ask your teacher if you forgot it.", step2)
        self.assertIn('aria-describedby="pin_help"', step2)
        hint = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "ABCD"}).get_data(as_text=True)
        self.assertIn('data-key="pin.looks_like_join_code"', hint)
        self.assertIn("That&#39;s your join code. Enter your 4-digit PIN instead.", hint)
        self.assertNotIn(">pin.", hint)
        for path in ("/", "/join"):
            rv = client.get(path, follow_redirects=True)
            self.assertNotIn('name="pin"', rv.get_data(as_text=True), path)

    def test_lockout_and_unknown_name_have_their_own_copy(self) -> None:
        client = self.app.test_client()
        unknown = client.post(f"/catchup/{self.token}", data={"action": "who", "codename": "Nobody"})
        self.assertIn("We couldn't find that name", unknown.get_data(as_text=True).replace("&#39;", "'"))
        client.post(f"/catchup/{self.token}", data={"action": "who", "codename": self.first})
        for _ in range(5):
            client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "0000"})
        locked = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "4321"}).get_data(as_text=True)
        self.assertIn('data-key="pin.ip_locked"', locked)
        self.assertIn("Too many tries from this device", locked.replace("&#39;", "'"))

    def test_picker_shows_codenames_only(self) -> None:
        self.school.game.conn.execute(
            "UPDATE students SET codename = '', first_name = 'Zed' WHERE id = ?", (self.students[1],))
        names = list(self.school.class_codenames(self.class_id).values())
        self.assertEqual(names, ["Avalanche"])

    def test_capture_error_keeps_mck45_snapshot(self) -> None:
        from unittest import mock

        with mock.patch.object(type(self.school), "_capture_participation_unlocked",
                               side_effect=RuntimeError("boom")):
            self.run_class("M9", "C1", 2, 0, {self.students[0]: 2})
        kept = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_result_snapshots WHERE class_id = ? AND live_module = 'M9'",
            (self.class_id,)).fetchone()[0]
        self.assertGreaterEqual(kept, 2)

    def test_pin_column_exists_at_boot(self) -> None:
        cols = {r[1] for r in self.school.game.conn.execute("PRAGMA table_info(students)")}
        self.assertIn("pin_hash", cols)

    def test_closed_link(self) -> None:
        self.school.set_catchup_link_open(self.class_id, self.token, False)
        self.assertEqual(self.school.catchup_sign_in(self.token, self.first, "4321")["reason"], "closed")
        with self.assertRaises(PermissionError):
            self.school.submit_catchup_answer(self.token, self.students[0], "item:M1-q1", "A")
        rv = self.app.test_client().get(self.catchup_url())
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
        picker = client.get(self.catchup_url()).get_data(as_text=True)
        self.assertIn(">Avalanche<", picker)
        self.assertNotIn(">Ava<", picker)
        client.post(f"/catchup/{self.token}", data={"action": "who", "codename": self.first})
        rv = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "9999"})
        self.assertIn("That PIN didn&#39;t match. Try again or ask your teacher to reset it.",
                      rv.get_data(as_text=True))
        rv = client.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "4321"})
        html = client.get(self.catchup_url() + "?play=1").get_data(as_text=True)
        self.assertIn("1/4 completed \u00b7 1 more", html)
        data = catchup_data(html)
        self.assertEqual([q["question_key"] for q in data["questions"]],
                         ["item:M1-q1", "item:M1-q2", "item:M1-q3"])  # not q0 (live), not group
        first = data["questions"][0]["content"]
        # The live card paints ``title || text``: the real question, never a slot label.
        self.assertNotIn("title", first)
        self.assertEqual(first["text"], "Real question 1 with x^2")
        self.assertEqual([o["text"] for o in first["options"]], ["Option A text", "Option B text"])
        self.assertNotIn("correct_answer", first)  # teacher keys stripped, as live
        self.assertNotIn("key", first)
        self.assertIn(">Avalanche<", html)  # codename at the top
        self.assertIn("/static/student-portal.css", html)
        self.assertIn('src="/static/catchup.js"', html)
        self.assertFalse(any(q["answered"] for q in data["questions"]))
        self.assertNotIn("Ben", html)
        self.assertNotIn("team", html.lower())
        client.post(
            f"/catchup/{self.token}",
            data={"question_key": "item:M1-q1", "answer": "Option A text"},
        )
        data = catchup_data(client.get(self.catchup_url() + "?play=1").get_data(as_text=True))
        self.assertEqual(data["tag"], "Answered after class")
        self.assertEqual([q["answered"] for q in data["questions"]], [True, False, False])

    def test_catchup_reuses_the_live_question_card(self) -> None:
        """Shawn: catch-up cards come from the live renderer, not their own look."""
        card_js = (LMS_DIR / "static" / "catchup.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/live_question_card.js"', card_js)
        self.assertIn("liveQuestionHeadHtml(", card_js)
        self.assertIn("liveAnswerControlsHtml(", card_js)
        self.assertIn("renderLiveQuestionMath(", card_js)
        self.assertIn('class="student-live-card', card_js)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/live_question_card.js"', portal)
        self.assertIn("liveQuestionHeadHtml(content, item.prompt?.kind, formatQuestionHtml)", portal)
        self.assertIn("return liveAnswerControlsHtml(kind, content, action, current, prefix);", portal)

    def test_catchup_student_can_load_class_images(self) -> None:
        client = self.app.test_client()
        path = f"/api/classes/{self.class_id}/module-files/web_resources/graph.png"
        self.assertEqual(client.get(path).status_code, 403)
        self.sign_in(client)
        self.assertNotEqual(client.get(path).status_code, 403)

    def test_staff_participation_legacy_url_redirects_to_class_tab(self) -> None:
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        rv = client.get(f"/staff/classes/{self.class_id}/participation")
        self.assertEqual(rv.status_code, 302)
        self.assertIn("view=participation", rv.headers.get("Location", ""))

    def test_staff_api_is_teacher_only(self) -> None:
        client = self.app.test_client()
        self.assertIn(client.get(f"/api/classes/{self.class_id}/catchup-links").status_code, (302, 401, 403))
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        body = client.get(f"/api/classes/{self.class_id}/catchup-links").get_json()
        self.assertIn(f"/catchup/MCR3U/m1-c1/{self.token}", body["catchup_links"][0]["url"])
        self.assertEqual(
            client.get(f"/staff/class/{self.class_id}?tab=ap&view=participation").status_code,
            200,
        )
        self.assertEqual(client.put(f"/api/classes/{self.class_id}/students/{self.students[1]}/pin",
                                    json={"pin": "12ab"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
