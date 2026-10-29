"""MCK-339: Begin new module wraps one section and keeps every saved row."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from begin_module import (  # noqa: E402
    dialog_copy,
    disabled_reason,
    tally_attendance,
    toast_for_reload,
    wrapped_up_label,
)
from test_participation_mck198 import Base  # noqa: E402

LMS_DIR = Path(__file__).resolve().parent
E2E = LMS_DIR / "e2e" / "begin_module_mck339.mjs"
KEPT = (
    "participation_items",
    "participation_answers",
    "participation_runs",
    "live_result_snapshots",
    "sessions",
    "session_scores",
    "catchup_links",
    "live_class_sessions",
)


def _counts(conn, class_id: int) -> dict[str, int]:
    """Row counts that Begin new module must not shrink.

    Args:
        conn: School sqlite connection.
        class_id: Section id. Tables without ``class_id`` are counted whole.
    """
    out: dict[str, int] = {}
    for table in KEPT:
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if "class_id" in columns:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE class_id = ?",
                (int(class_id),),
            ).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        out[table] = int(row["n"])
    marks = conn.execute(
        "SELECT COUNT(*) AS n FROM participation_module_marks WHERE class_id = ?",
        (int(class_id),),
    ).fetchone()
    out["participation_module_marks"] = int(marks["n"])
    return out


def _seed_held_class(school, class_id: int, student_ids: list[int], *, module: str = "M1", slot: str = "C1") -> str:
    """Save one real class slot, one live answer, and one present mark.

    Args:
        school: Open ``SchoolDB``.
        class_id: Section id.
        student_ids: Roster ids. The first is present and has the answer.
        module: Module token.
        slot: Class slot token.

    Returns:
        Run key. Not a join code.
    """
    run_key = f"held-{uuid.uuid4().hex}"
    now = "2026-10-14T15:00:00"
    conn = school.conn
    conn.execute(
        """
        INSERT INTO participation_runs (
            class_id, run_key, is_real, auto_is_real, discarded, ended_at
        ) VALUES (?, ?, 1, 1, 0, ?)
        """,
        (int(class_id), run_key, now),
    )
    conn.execute(
        """
        INSERT INTO participation_items (
            class_id, run_key, live_module, live_slot, question_key, item_json,
            prompt_kind, offered_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'mc', ?)
        """,
        (
            int(class_id),
            run_key,
            module,
            slot,
            "item:q1",
            json.dumps({"type": "mc", "prompt_kind": "mc"}),
            now,
        ),
    )
    if student_ids:
        conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id, source,
                answer_json, answered_at
            ) VALUES (?, ?, ?, ?, ?, 'live', '{}', ?)
            """,
            (int(class_id), run_key, module, "item:q1", int(student_ids[0]), now),
        )
    cur = conn.execute(
        """
        INSERT INTO sessions (class_id, starts_at, header_label, status, source)
        VALUES (?, ?, ?, 'ended', 'game')
        """,
        (int(class_id), now, f"{module} {slot}"),
    )
    session_id = int(cur.lastrowid)
    for index, student_id in enumerate(student_ids):
        conn.execute(
            """
            INSERT INTO session_scores (session_id, student_id, present, late, points)
            VALUES (?, ?, ?, 0, 0)
            """,
            (session_id, int(student_id), 1 if index == 0 else 0),
        )
        conn.execute(
            """
            INSERT INTO live_participation_credits (
                run_key, student_id, session_id, points, points_r1, points_r2, points_r3
            ) VALUES (?, ?, ?, 0, 0, 0, 0)
            """,
            (run_key, int(student_id), session_id),
        )
    conn.commit()
    return run_key


def _mark_present(school, class_id: int, run_key: str, student_ids: list[int]) -> None:
    """Link a saved run to present marks on ``session_scores``.

    Args:
        school: Open ``SchoolDB``.
        class_id: Section id.
        run_key: Participation run that already exists.
        student_ids: Roster ids. The first is present.
    """
    now = "2026-10-14T15:00:00"
    conn = school.conn
    cur = conn.execute(
        """
        INSERT INTO sessions (class_id, starts_at, header_label, status, source)
        VALUES (?, ?, 'class', 'ended', 'game')
        """,
        (int(class_id), now),
    )
    session_id = int(cur.lastrowid)
    conn.execute("DELETE FROM live_participation_credits WHERE run_key = ?", (run_key,))
    for index, student_id in enumerate(student_ids):
        conn.execute(
            """
            INSERT INTO session_scores (session_id, student_id, present, late, points)
            VALUES (?, ?, ?, 0, 0)
            """,
            (session_id, int(student_id), 1 if index == 0 else 0),
        )
        conn.execute(
            """
            INSERT INTO live_participation_credits (
                run_key, student_id, session_id, points, points_r1, points_r2, points_r3
            ) VALUES (?, ?, ?, 0, 0, 0, 0)
            """,
            (run_key, int(student_id), session_id),
        )
    conn.commit()


def _sign_in(client, school, email: str) -> None:
    """Staff session for a user that is already registered."""
    client.get(f"/auth/google/callback?email={email}&name=T")
    user = school.get_user_by_email(email)
    client.post("/verify-email", data={"code": user["verification_code"]})


class CopyTests(unittest.TestCase):
    """Dialog sentences and attendance tally, with no database."""

    def test_dialog_names_the_section_and_the_next_module(self) -> None:
        copy = dialog_copy(3, 4, 2, 4)
        self.assertEqual(copy["title"], "Start Module 4?")
        self.assertEqual(copy["confirm"], "Start Module 4")
        self.assertEqual(copy["cancel"], "Cancel")
        self.assertEqual(copy["button"], "Begin new module")
        self.assertEqual(
            copy["toast"],
            "Module 3 wrapped up. Module 4 has started.",
        )
        self.assertIn("Module 3 for this section (2 of 4 classes held)", copy["lines"][0])
        self.assertIn("nothing is deleted", copy["lines"][1])
        self.assertIn("Module 4 totals start at zero", copy["lines"][2])
        self.assertIn("still count toward Module 3", copy["lines"][3])
        self.assertEqual(copy["lines"][-1], "This can't be undone.")
        blob = " ".join(copy["lines"]).lower()
        self.assertNotIn("frozen", blob)
        self.assertNotIn("un-mark", blob)
        self.assertNotIn("undo wrap-up", blob)

    def test_disabled_reasons(self) -> None:
        self.assertEqual(
            disabled_reason(live=True, held=4, is_last=False),
            "End the live class first.",
        )
        self.assertEqual(
            disabled_reason(live=False, held=0, is_last=False),
            "No classes held in this module yet.",
        )
        self.assertEqual(
            disabled_reason(live=True, held=0, is_last=True),
            "This is the last module.",
        )
        self.assertEqual(disabled_reason(live=False, held=1, is_last=False), "")

    def test_wrapped_label_uses_the_stored_day(self) -> None:
        self.assertEqual(wrapped_up_label("2026-10-14T18:05:00"), "Wrapped up Oct 14")

    def test_toast_reload_matches_the_open_module_only(self) -> None:
        self.assertEqual(
            toast_for_reload(4, "3", "4"),
            "Module 3 wrapped up. Module 4 has started.",
        )
        self.assertEqual(toast_for_reload(5, "3", "4"), "")

    def test_attendance_counts_a_slot_once(self) -> None:
        meetings = [
            {"slot": "C1", "session_ids": [1, 2]},
            {"slot": "C2", "session_ids": [3]},
        ]
        present = {1: {10}, 3: {10, 11}}
        held, attended = tally_attendance(meetings, present, [10, 11, 12])
        self.assertEqual(held, 2)
        self.assertEqual(attended, {10: 2, 11: 1, 12: 0})


class BeginModuleTests(Base):
    """Stored rows stay, and a second section of the same pack stays put."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        _sign_in(self.client, self.school, "teacher@gmail.com")

    def test_column_is_added_for_the_section_pointer(self) -> None:
        columns = {
            str(row[1])
            for row in self.school.conn.execute("PRAGMA table_info(classes)")
        }
        self.assertIn("current_live_module", columns)

    def test_plan_has_twenty_modules(self) -> None:
        plan = self.school._begin_module_plan(self.class_id)
        self.assertEqual(len(plan), 20)
        self.assertEqual(plan[0][0], "M1")
        self.assertEqual(plan[-1][0], "M20")
        self.assertEqual(plan[0][1], ["C1", "C2", "C3", "C4"])

    def test_double_click_changes_nothing_the_second_time(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        before = _counts(self.school.conn, self.class_id)
        first = self.school.begin_new_module(self.class_id, "M1", user_id=int(self.teacher["id"]))
        midpoint = _counts(self.school.conn, self.class_id)
        stamp = self.school.conn.execute(
            """
            SELECT updated_at FROM participation_module_marks
            WHERE class_id = ? AND live_module = 'M1'
            """,
            (self.class_id,),
        ).fetchone()["updated_at"]
        second = self.school.begin_new_module(self.class_id, "M1", user_id=int(self.teacher["id"]))
        after = _counts(self.school.conn, self.class_id)
        self.assertTrue(first["changed"])
        self.assertEqual(first["current_module"], "M2")
        self.assertFalse(second["changed"])
        self.assertEqual(second["current_module"], "M2")
        self.assertEqual(midpoint, after)
        self.assertEqual(after["participation_module_marks"], before["participation_module_marks"] + 1)
        for table in KEPT:
            self.assertEqual(after[table], before[table], table)
        again = self.school.conn.execute(
            """
            SELECT updated_at, complete FROM participation_module_marks
            WHERE class_id = ? AND live_module = 'M1'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertEqual(again["updated_at"], stamp)
        self.assertEqual(int(again["complete"]), 1)
        board = self.school.begin_module_board(self.class_id)
        self.assertEqual(board["current_module"], "M2")
        self.assertEqual(board["students"][0]["attended_text"], "0 / 0")
        self.assertEqual(board["students"][0]["participation_text"], "0 / 0")
        self.assertEqual(board["students"][0]["percent_text"], "—")
        self.assertEqual(board["past"][0]["module"], "M1")
        self.assertTrue(board["past"][0]["wrapped"].startswith("Wrapped up "))
        self.assertIn("1 / 1", board["past"][0]["students"][0]["attended_text"])

    def test_late_catchup_stays_on_the_wrapped_module(self) -> None:
        self.run_class("M1", "C1", individual=2, group=0, answers={self.students[0]: 1})
        before_answers = _counts(self.school.conn, self.class_id)["participation_answers"]
        started = self.school.begin_new_module(self.class_id, "M1", user_id=int(self.teacher["id"]))
        self.assertTrue(started["changed"])
        link = next(row for row in self.school.list_catchup_links(self.class_id) if row["live_module"] == "M1")
        self.assertTrue(
            self.school.submit_catchup_answer(link["token"], self.students[0], "item:M1-q1", "kept")
        )
        after = _counts(self.school.conn, self.class_id)
        self.assertEqual(after["participation_answers"], before_answers + 1)
        stored = self.school.conn.execute(
            """
            SELECT live_module, source FROM participation_answers
            WHERE class_id = ? AND student_id = ? AND question_key = 'item:M1-q1'
            ORDER BY id DESC LIMIT 1
            """,
            (self.class_id, self.students[0]),
        ).fetchone()
        self.assertEqual(stored["live_module"], "M1")
        self.assertEqual(stored["source"], "catchup")
        old = self.school.participation_student_module_answered(self.class_id, "M1", self.students[0])
        new = self.school.participation_student_module_answered(self.class_id, "M2", self.students[0])
        self.assertGreaterEqual(old, 1)
        self.assertEqual(new, 0)
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))

    def test_wrap_up_is_final_for_teacher_and_admin(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        self.school.begin_new_module(self.class_id, "M1", user_id=int(self.teacher["id"]))
        before = _counts(self.school.conn, self.class_id)
        keys_before = [
            tuple(row)
            for row in self.school.conn.execute(
                """
                SELECT question_key, run_key, live_module
                FROM participation_answers
                WHERE class_id = ?
                ORDER BY id
                """,
                (self.class_id,),
            )
        ]
        page = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=attendance")
        html = page.get_data(as_text=True)
        self.assertNotIn("Un-mark", html)
        self.assertNotIn("Undo wrap-up", html)
        self.assertNotIn("You can undo this from Past modules.", html)
        refused = self.client.post(
            f"/api/classes/{self.class_id}/participation/modules/begin",
            json={"undo": True, "module": "M1"},
        )
        self.assertEqual(refused.status_code, 400)
        stranger = self.school.register_staff("other-staff@example.com")
        self.school.conn.execute(
            "UPDATE users SET role = 'it' WHERE id = ?",
            (int(stranger["id"]),),
        )
        self.school.conn.commit()
        admin = self.app.test_client()
        _sign_in(admin, self.school, "other-staff@example.com")
        admin_refused = admin.post(
            f"/api/classes/{self.class_id}/participation/modules/begin",
            json={"undo": True, "module": "M1"},
        )
        self.assertEqual(admin_refused.status_code, 400)
        self.assertTrue(self.school.set_module_complete(self.class_id, "M1", False))
        after = _counts(self.school.conn, self.class_id)
        keys_after = [
            tuple(row)
            for row in self.school.conn.execute(
                """
                SELECT question_key, run_key, live_module
                FROM participation_answers
                WHERE class_id = ?
                ORDER BY id
                """,
                (self.class_id,),
            )
        ]
        self.assertEqual(after, before)
        self.assertEqual(keys_after, keys_before)
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        mark = self.school.conn.execute(
            """
            SELECT complete FROM participation_module_marks
            WHERE class_id = ? AND live_module = 'M1'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertEqual(int(mark["complete"]), 1)

    def test_other_section_of_the_same_pack_stays_on_its_module(self) -> None:
        first = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCR3U",
            int(self.teacher["id"]),
        )
        second_offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCR3U",
            new_section=True,
        )
        pack = self.school.create_library("MCR3U", origin="upload")
        offering = self.school.attach_library(int(first["id"]), int(pack["id"]))
        second_offering = self.school.attach_library(int(second_offering["id"]), int(pack["id"]))
        self.assertEqual(int(offering["library_id"]), int(second_offering["library_id"]))
        self.assertNotEqual(int(offering["id"]), int(second_offering["id"]))
        other_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["One", "Two"],
                offering_id=int(second_offering["id"]),
                teacher_user_id=int(self.teacher["id"]),
            )["id"]
        )
        other_students = [
            int(row[0])
            for row in self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                (other_id,),
            )
        ]
        _seed_held_class(self.school, self.class_id, self.students)
        _seed_held_class(self.school, other_id, other_students)
        other_before = _counts(self.school.conn, other_id)
        slots_before = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM course_class_slots WHERE offering_id = ?",
            (int(second_offering["id"]),),
        ).fetchone()["n"]
        self.school.begin_new_module(self.class_id, "M1", user_id=int(self.teacher["id"]))
        other_after = _counts(self.school.conn, other_id)
        self.assertEqual(other_before, other_after)
        self.assertEqual(self.school._begin_module_pointer(other_id), "")
        other_board = self.school.begin_module_board(other_id)
        self.assertEqual(other_board["current_module"], "M1")
        self.assertEqual(other_board["past"], [])
        slots_after = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM course_class_slots WHERE offering_id = ?",
            (int(second_offering["id"]),),
        ).fetchone()["n"]
        self.assertEqual(int(slots_after), int(slots_before))
        moved = self.school.begin_module_board(self.class_id)
        self.assertEqual(moved["current_module"], "M2")

    def test_disabled_states_on_the_page(self) -> None:
        empty = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=attendance")
        html = empty.get_data(as_text=True)
        self.assertEqual(empty.status_code, 200)
        self.assertIn("No classes held in this module yet.", html)
        self.assertIn("disabled", html)
        self.assertIn('id="begin-new-module"', html)
        self.assertIn("Take Attendance", html)
        self.assertNotIn("frozen", html.lower())
        _seed_held_class(self.school, self.class_id, self.students)
        ready = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=attendance")
        ready_html = ready.get_data(as_text=True)
        self.assertIn("This can't be undone.", ready_html)
        self.assertIn("Start Module 2", ready_html)
        self.assertNotIn("Un-mark", ready_html)
        self.assertNotIn("Undo wrap-up", ready_html)
        self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        live = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=attendance")
        live_html = live.get_data(as_text=True)
        self.assertIn("End the live class first.", live_html)
        self.assertNotIn("frozen", live_html.lower())
        self.school.conn.execute(
            "UPDATE classes SET current_live_module = 'M20' WHERE id = ?",
            (self.class_id,),
        )
        self.school.conn.commit()
        _seed_held_class(self.school, self.class_id, self.students, module="M20", slot="C1")
        last = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=participation")
        last_html = last.get_data(as_text=True)
        self.assertIn("This is the last module.", last_html)
        self.assertIn("Current module: Module 20", last_html)
        self.assertNotIn("frozen", last_html.lower())

    def test_teacher_and_admin_can_begin_and_a_stranger_cannot(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        stranger = self.school.register_staff("other-staff@example.com")
        other = self.app.test_client()
        _sign_in(other, self.school, "other-staff@example.com")
        denied = other.post(
            f"/api/classes/{self.class_id}/participation/modules/begin",
            json={"from_module": "M1"},
        )
        self.assertEqual(denied.status_code, 403)
        anon = self.app.test_client()
        locked = anon.post(
            f"/api/classes/{self.class_id}/participation/modules/begin",
            json={"from_module": "M1"},
        )
        self.assertEqual(locked.status_code, 401)
        started = self.client.post(
            f"/api/classes/{self.class_id}/participation/modules/begin",
            json={"from_module": "M1"},
        )
        self.assertEqual(started.status_code, 200)
        body = started.get_json()
        self.assertTrue(body["changed"])
        self.assertNotIn("name", body)
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")

    def test_simultaneous_clicks_make_one_change(self) -> None:
        """Two overlapping Begin new module calls write the section once."""
        _seed_held_class(self.school, self.class_id, self.students)
        barrier = threading.Barrier(2)
        results: list[dict] = []
        errors: list[Exception] = []
        gate = threading.Lock()

        def click() -> None:
            """One Begin new module call, released together with the other."""
            try:
                barrier.wait(timeout=5)
                outcome = self.school.begin_new_module(
                    self.class_id,
                    "M1",
                    user_id=int(self.teacher["id"]),
                )
            except Exception as exc:  # noqa: BLE001
                with gate:
                    errors.append(exc)
                return
            with gate:
                results.append(outcome)

        threads = [threading.Thread(target=click) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for row in results if row["changed"]), 1)
        self.assertTrue(all(row["current_module"] == "M2" for row in results))
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")
        marks = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM participation_module_marks
            WHERE class_id = ? AND live_module = 'M1' AND complete = 1
            """,
            (self.class_id,),
        ).fetchone()
        self.assertEqual(int(marks["n"]), 1)


def _chrome() -> str:
    for path in (os.environ.get("LLOVES_E2E_CHROME", ""), "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if path and Path(path).exists():
            return path
    return ""


def _begin_module_shot_dir(fallback: Path) -> Path:
    """Directory for the Begin new module dialog screenshots.

    ``LLOVES_BEGIN_MODULE_SHOT_DIR`` picks the directory. Otherwise the
    shots stay in the test's temporary folder.

    Args:
        fallback: Temporary directory for this test.

    Returns:
        The directory, created when it is missing.
    """
    raw = os.environ.get("LLOVES_BEGIN_MODULE_SHOT_DIR", "").strip()
    out = Path(raw) if raw else fallback
    out.mkdir(parents=True, exist_ok=True)
    return out


def _playwright_ready() -> bool:
    if not (shutil.which("node") and _chrome()):
        return False
    node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "/tmp/pw/node_modules")
    probe = subprocess.run(
        ["node", "-e", "require.resolve('playwright-core')"],
        env={**os.environ, "NODE_PATH": node_path},
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@unittest.skipUnless(_playwright_ready(), "node, Chrome, and playwright-core are required")
class BeginModuleBrowserTests(Base):
    """Dialog, escape, and the wrapped module at 1280px."""

    def setUp(self) -> None:
        super().setUp()
        game = self.school.game.conn
        game.execute(
            "UPDATE students SET codename = 'One', first_name = 'One' WHERE id = ?",
            (self.students[0],),
        )
        game.execute(
            "UPDATE students SET codename = 'Two', first_name = 'Two' WHERE id = ?",
            (self.students[1],),
        )
        game.commit()
        self.client = self.app.test_client()
        _sign_in(self.client, self.school, "teacher@gmail.com")
        run_key = self.run_class(
            "M1", "C1", individual=2, group=0, answers={self.students[0]: 1}
        )
        _mark_present(self.school, self.class_id, run_key, self.students)

    def tearDown(self) -> None:
        server = getattr(self, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
        super().tearDown()

    def test_dialog_at_1280(self) -> None:
        from werkzeug.serving import make_server

        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        out_dir = _begin_module_shot_dir(Path(self.tmp.name))
        shot = out_dir / "begin-module-1280.png"
        dialog_shot = out_dir / "begin-module-dialog-1280.png"
        cookies = {cookie.key: cookie.value for cookie in self.client._cookies.values()}
        spec = {
            "port": self.server.server_port,
            "chrome": _chrome(),
            "cookies": cookies,
            "path": f"/staff/class/{self.class_id}?tab=ap&view=attendance",
            "title": "Start Module 2?",
            "confirm": "Start Module 2",
            "lines": [
                "This wraps up Module 1 for this section (1 of 4 classes held).",
                "Your Module 1 totals move to Past modules, and nothing is deleted.",
                "Module 2 totals start at zero.",
                "Catch-up answers that come in late still count toward Module 1.",
                "This can't be undone.",
            ],
            "afterCurrent": "Current module: Module 2",
            "toast": "Module 1 wrapped up. Module 2 has started.",
            "pastLabel": "Module 1",
            "emptyReason": "No classes held in this module yet.",
            "shot": str(shot),
            "dialogShot": str(dialog_shot),
        }
        cfg = Path(self.tmp.name) / "begin-module-e2e.json"
        cfg.write_text(json.dumps(spec), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(E2E), str(cfg)],
            env={**os.environ, "NODE_PATH": os.environ.get("LLOVES_E2E_NODE_PATH", "/tmp/pw/node_modules")},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, proc.stderr[-2000:] + proc.stdout[-2000:])
        out = json.loads(lines[-1])
        self.assertEqual(out["errors"], [], out)
        self.assertEqual(out["width"], 1280)
        failed = [row for row in out["steps"] if not row["ok"]]
        self.assertEqual(failed, [], json.dumps(out["steps"], indent=1))
        self.assertTrue(shot.is_file())
        self.assertTrue(dialog_shot.is_file())


class BeginModuleShotDirTests(unittest.TestCase):
    """Dialog screenshots stay out of a fixed artifacts directory."""

    def test_dialog_shots_use_a_temp_or_configured_dir(self) -> None:
        previous = os.environ.pop("LLOVES_BEGIN_MODULE_SHOT_DIR", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = _begin_module_shot_dir(Path(tmp))
                self.assertEqual(out, Path(tmp))
                self.assertFalse(str(out).startswith("/opt/cursor/artifacts"))
            with tempfile.TemporaryDirectory() as configured:
                os.environ["LLOVES_BEGIN_MODULE_SHOT_DIR"] = configured
                unused = Path(tempfile.gettempdir()) / "unused-begin-module-shots"
                out = _begin_module_shot_dir(unused)
                self.assertEqual(out, Path(configured))
                self.assertFalse(unused.exists())
        finally:
            if previous is None:
                os.environ.pop("LLOVES_BEGIN_MODULE_SHOT_DIR", None)
            else:
                os.environ["LLOVES_BEGIN_MODULE_SHOT_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
