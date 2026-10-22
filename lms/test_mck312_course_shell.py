#!/usr/bin/env python3
"""Default course shell and admin module-pack backfill."""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
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
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from course_shell import (  # noqa: E402
    SHELL_MODULE_COUNT,
    SHELL_SLOT_COUNT,
    _protected_snapshot,
    ensure_default_course_shell,
)
from test_catalog import _imscc_bytes  # noqa: E402

SHELL_TABS = ("live", "lesson-slides", "ap", "question-banks")
KEPT_PAGE = "<p>kept-page</p>"
KEPT_QUESTION = '{"kept":true}'
EDITED_QUESTION = '{"edited":true}'


class CourseShellTests(unittest.TestCase):
    """New courses get one shell. Backfill merges and does not replace."""

    def setUp(self) -> None:
        """Isolated sqlite, one staff user, and an active semester."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")

    def tearDown(self) -> None:
        """Close the database and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_staff(self) -> None:
        """Sign the client in as the assigned teacher."""
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _login_it(self) -> None:
        """Sign the client in as admin."""
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _assign(self, code: str) -> dict:
        """Assign one new course to the staff user.

        Args:
            code: Ontario course code.
        """
        return self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code=code
        )

    def _class_for(self, offering: dict) -> dict:
        """Create one class on an offering.

        Args:
            offering: Offering row.
        """
        semester = self.school.get_semester(int(offering["semester_id"]))
        return self.school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=str(offering["ontario_code"]),
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["A1"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )

    def _tab_names(self, class_id: int) -> list[str]:
        """Return tab keys rendered in the course nav.

        Args:
            class_id: Staff class id.
        """
        html = self.client.get(f"/staff/class/{class_id}").get_data(as_text=True)
        panel = re.search(r'id="course-tabs-panel".*?</div>', html, re.S)
        self.assertIsNotNone(panel)
        return re.findall(r"[?&]tab=([a-z0-9-]+)", panel.group(0))

    def _counts(self, offering_id: int) -> dict[str, int]:
        """Return shell row counts for one offering.

        Args:
            offering_id: ``course_offerings.id``.
        """
        offering = self.school.get_offering(int(offering_id))
        library_id = int(offering["shell_library_id"])
        conn = self.school.conn
        slots = conn.execute(
            "SELECT COUNT(*) AS n FROM course_class_slots WHERE offering_id = ?",
            (int(offering_id),),
        ).fetchone()["n"]
        modules = conn.execute(
            """
            SELECT COUNT(DISTINCT module_number) AS n
            FROM course_class_slots WHERE offering_id = ?
            """,
            (int(offering_id),),
        ).fetchone()["n"]
        banks = conn.execute(
            "SELECT COUNT(*) AS n FROM question_banks WHERE library_id = ?",
            (library_id,),
        ).fetchone()["n"]
        questions = conn.execute(
            """
            SELECT COUNT(*) AS n FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ?
            """,
            (library_id,),
        ).fetchone()["n"]
        pages = conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE library_id = ?",
            (library_id,),
        ).fetchone()["n"]
        libraries = conn.execute(
            """
            SELECT COUNT(*) AS n FROM content_libraries
            WHERE ontario_code = ? AND origin = 'shell'
            """,
            (str(offering["ontario_code"]),),
        ).fetchone()["n"]
        return {
            "slots": int(slots),
            "modules": int(modules),
            "banks": int(banks),
            "questions": int(questions),
            "pages": int(pages),
            "libraries": int(libraries),
        }

    def test_packless_science_and_math_show_four_tabs(self) -> None:
        """A new pack-less science course and math course show the same four tabs."""
        self._login_staff()
        for code in ("SBI4U", "MCF3M"):
            offering = self._assign(code)
            class_id = int(self._class_for(offering)["id"])
            self.assertEqual(self._tab_names(class_id), list(SHELL_TABS))
            html = self.client.get(f"/staff/class/{class_id}").get_data(as_text=True)
            self.assertNotIn(">Grades</a>", html)
            self.assertNotIn(">Modules</a>", html)
            self.assertIsNone(offering.get("library_id"))

    def test_shell_has_twenty_by_four_slots(self) -> None:
        """Each new course stores twenty modules with four class slots."""
        offering = self._assign("SBI4U")
        counts = self._counts(int(offering["id"]))
        self.assertEqual(counts["modules"], SHELL_MODULE_COUNT)
        self.assertEqual(counts["slots"], SHELL_MODULE_COUNT * SHELL_SLOT_COUNT)
        per_module = self.school.conn.execute(
            """
            SELECT module_number, COUNT(*) AS n
            FROM course_class_slots WHERE offering_id = ?
            GROUP BY module_number
            """,
            (int(offering["id"]),),
        ).fetchall()
        self.assertEqual(len(per_module), SHELL_MODULE_COUNT)
        self.assertTrue(all(int(row["n"]) == SHELL_SLOT_COUNT for row in per_module))
        self.assertEqual(counts["banks"], SHELL_MODULE_COUNT + 1)
        self.assertEqual(counts["questions"], 0)
        self.assertEqual(str(offering.get("course_shell")), "default")

    def test_running_the_shell_twice_does_not_duplicate(self) -> None:
        """A second shell build and a second assign leave row counts unchanged."""
        offering = self._assign("MCF3M")
        before = self._counts(int(offering["id"]))
        again = ensure_default_course_shell(self.school, int(offering["id"]))
        repeated = self._assign("MCF3M")
        after = self._counts(int(offering["id"]))
        self.assertEqual(int(again["id"]), int(offering["id"]))
        self.assertEqual(int(repeated["id"]), int(offering["id"]))
        self.assertEqual(after, before)
        self.assertEqual(
            int(again["shell_library_id"]), int(offering["shell_library_id"])
        )

    def test_existing_offering_is_not_reshelled(self) -> None:
        """Returning an offering that already exists does not mark the shell."""
        offering = self._assign("MCF3M")
        self.school.conn.execute(
            """
            UPDATE course_offerings
            SET course_shell = NULL, shell_library_id = NULL
            WHERE id = ?
            """,
            (int(offering["id"]),),
        )
        returned = self._assign("MCF3M")
        self.assertEqual(int(returned["id"]), int(offering["id"]))
        self.assertFalse(returned.get("course_shell"))
        self._login_staff()
        class_id = int(self._class_for(returned)["id"])
        names = self._tab_names(class_id)
        self.assertIn("gradebook", names)
        self.assertIn("modules", names)

    def test_classic_pack_keeps_the_full_tab_bar(self) -> None:
        """A course that already points at an uploaded pack keeps Grades and More."""
        library = self.school.create_library("MCF3M", origin="upload")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCF3M",
            library_id=int(library["id"]),
        )
        self.assertEqual(int(offering["library_id"]), int(library["id"]))
        self.assertEqual(self._counts(int(offering["id"]))["slots"], 80)
        self._login_staff()
        class_id = int(self._class_for(offering)["id"])
        names = self._tab_names(class_id)
        self.assertIn("gradebook", names)
        self.assertIn("modules", names)
        self.assertIn("library", names)

    def _plant_teacher_rows(self, offering: dict, class_id: int) -> None:
        """Store teacher content and roster marks before a backfill.

        Args:
            offering: Offering row with a shell library.
            class_id: Class that holds the roster row.
        """
        library_id = int(offering["shell_library_id"])
        conn = self.school.conn
        conn.execute(
            """
            INSERT INTO pages (
                library_id, import_key, kind, title, html_text, created_at
            ) VALUES (?, 'wiki_content/lesson-1.html', 'html', 'Kept', ?, 't')
            """,
            (library_id, KEPT_PAGE),
        )
        conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, 'bank:gquiz', 'Kept bank', '{}', 't')
            """,
            (library_id,),
        )
        bank_id = int(
            conn.execute(
                """
                SELECT id FROM question_banks
                WHERE library_id = ? AND import_key = 'bank:gquiz'
                """,
                (library_id,),
            ).fetchone()["id"]
        )
        conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, 'q1', 'essay', 'Kept', ?, 't')
            """,
            (bank_id, KEPT_QUESTION),
        )
        course_bank = int(
            conn.execute(
                """
                SELECT id FROM question_banks
                WHERE library_id = ? AND import_key = 'shell-course'
                """,
                (library_id,),
            ).fetchone()["id"]
        )
        conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, 'teacher-kept', 'essay', 'Kept', ?, 't')
            """,
            (course_bank, KEPT_QUESTION),
        )
        student_id = int(
            conn.execute(
                "SELECT id FROM students WHERE class_id = ?",
                (int(class_id),),
            ).fetchone()["id"]
        )
        conn.execute(
            "UPDATE students SET pin_hash = ? WHERE id = ?",
            ("kept-hash", student_id),
        )
        conn.execute(
            """
            INSERT INTO sessions (class_id, starts_at, header_label, status, source)
            VALUES (?, 't', 'H', 'ended', 'game')
            """,
            (int(class_id),),
        )
        session_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT INTO session_scores (session_id, student_id, present, points)
            VALUES (?, ?, 1, 1)
            """,
            (session_id, student_id),
        )
        conn.execute(
            """
            INSERT INTO lesson_slide_decks (
                class_id, module_number, live_index, presentation_url,
                created_at, updated_at
            ) VALUES (?, 1, 1, 'deck-kept', 't', 't')
            """,
            (int(class_id),),
        )
        conn.execute(
            """
            INSERT INTO lesson_slides_latest (
                class_id, live_module, live_slot, is_test, run_key,
                ended_at, questions_json, updated_at
            ) VALUES (?, 'M1', 'C1', 0, 'kept-run', 't', '[]', 't')
            """,
            (int(class_id),),
        )
        conn.execute(
            """
            INSERT INTO class_live_deck_seeds (
                class_id, module, slot, mode, created_at, updated_at
            ) VALUES (?, 'M1', 'C1', 'copy', 't', 't')
            """,
            (int(class_id),),
        )

    def _post_backfill(self, offering_id: int, payload: bytes | None = None):
        """POST a cartridge to the admin backfill route.

        Args:
            offering_id: ``course_offerings.id``.
            payload: Cartridge bytes. Defaults to the shared fixture.
        """
        body = _imscc_bytes() if payload is None else payload
        return self.client.post(
            f"/it/offerings/{int(offering_id)}/module-pack/backfill",
            data={"module_pack": (io.BytesIO(body), "course.imscc")},
            follow_redirects=False,
        )

    def _question_payload(self, library_id: int, import_key: str) -> str:
        """Return one question payload in the course library.

        Args:
            library_id: Shell ``content_libraries.id``.
            import_key: ``questions.import_key``.
        """
        row = self.school.conn.execute(
            """
            SELECT q.payload_json FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ? AND q.import_key = ?
            """,
            (int(library_id), import_key),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row["payload_json"])

    def test_backfill_keeps_teacher_rows_and_is_idempotent(self) -> None:
        """Backfill adds pack rows, leaves teacher rows, and a second run adds nothing."""
        offering = self._assign("SBI4U")
        cls = self._class_for(offering)
        class_id = int(cls["id"])
        self._plant_teacher_rows(offering, class_id)
        library_id = int(offering["shell_library_id"])
        before_counts = self._counts(int(offering["id"]))
        roster_before = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM students WHERE class_id = ?",
            (class_id,),
        ).fetchone()["n"]
        protected = _protected_snapshot(self.school)
        self._login_it()
        first = self._post_backfill(int(offering["id"]))
        self.assertEqual(first.status_code, 302, first.get_data(as_text=True))
        offering = self.school.get_offering(int(offering["id"]))
        self.assertIsNone(offering.get("library_id"))
        self.assertFalse(offering.get("imscc_path"))
        page = self.school.conn.execute(
            """
            SELECT title, html_text FROM pages
            WHERE library_id = ? AND import_key = 'wiki_content/lesson-1.html'
            """,
            (library_id,),
        ).fetchone()
        self.assertEqual(page["title"], "Kept")
        self.assertEqual(page["html_text"], KEPT_PAGE)
        self.assertEqual(self._question_payload(library_id, "q1"), KEPT_QUESTION)
        self.assertEqual(
            self._question_payload(library_id, "teacher-kept"), KEPT_QUESTION
        )
        self._question_payload(library_id, "q2")
        added = self._counts(int(offering["id"]))
        assignments = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM assignments WHERE library_id = ?",
            (library_id,),
        ).fetchone()["n"]
        self.assertGreater(int(assignments), 0)
        self.assertGreater(added["pages"], before_counts["pages"])
        self.assertGreater(added["questions"], before_counts["questions"])
        self.assertEqual(added["slots"], before_counts["slots"])
        self.assertEqual(added["libraries"], before_counts["libraries"])
        self.assertEqual(_protected_snapshot(self.school), protected)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM students WHERE class_id = ?",
                (class_id,),
            ).fetchone()["n"],
            roster_before,
        )
        staging = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM content_libraries WHERE origin = 'backfill-staging'"
        ).fetchone()["n"]
        self.assertEqual(int(staging), 0)
        self.school.conn.execute(
            """
            UPDATE questions SET payload_json = ?
            WHERE import_key = 'q2'
              AND bank_id IN (
                SELECT id FROM question_banks WHERE library_id = ?
              )
            """,
            (EDITED_QUESTION, library_id),
        )
        second = self._post_backfill(int(offering["id"]))
        self.assertEqual(second.status_code, 302, second.get_data(as_text=True))
        self.assertEqual(self._counts(int(offering["id"])), added)
        self.assertEqual(self._question_payload(library_id, "q2"), EDITED_QUESTION)
        self.assertEqual(self._question_payload(library_id, "q1"), KEPT_QUESTION)
        kept_page = self.school.conn.execute(
            """
            SELECT html_text FROM pages
            WHERE library_id = ? AND import_key = 'wiki_content/lesson-1.html'
            """,
            (library_id,),
        ).fetchone()
        self.assertEqual(kept_page["html_text"], KEPT_PAGE)
        self.assertEqual(_protected_snapshot(self.school), protected)
        audit = self.school.conn.execute(
            """
            SELECT detail_json FROM access_audit_log
            WHERE action = 'offering.pack_backfill'
            """
        ).fetchall()
        self.assertGreaterEqual(len(audit), 1)
        for row in audit:
            detail = json.loads(row["detail_json"])
            self.assertEqual(set(detail), {"pages", "questions"})

    def test_backfill_is_admin_only(self) -> None:
        """Anonymous callers are sent to Admin sign-in. Staff get 403."""
        offering = self._assign("SBI4U")
        before = self._counts(int(offering["id"]))
        anonymous = self._post_backfill(int(offering["id"]))
        self.assertEqual(anonymous.status_code, 302)
        location = anonymous.headers.get("Location") or ""
        self.assertIn("/auth/google", location)
        self.assertIn("portal=it", location)
        self._login_staff()
        staff = self._post_backfill(int(offering["id"]))
        self.assertEqual(staff.status_code, 403)
        self.assertIn("Ask Admin to grant access.", staff.get_data(as_text=True))
        self.assertEqual(self._counts(int(offering["id"])), before)
        self._login_it()
        missing = self.client.post(
            f"/it/offerings/{int(offering['id'])}/module-pack/backfill",
            data={},
            follow_redirects=False,
        )
        self.assertEqual(missing.status_code, 400)
        missing_offering = self._post_backfill(999999)
        self.assertEqual(missing_offering.status_code, 404)

    def test_teacher_api_reads_backfilled_content(self) -> None:
        """Question banks, pages, and live search read the shell after backfill."""
        offering = self._assign("SBI4U")
        cls = self._class_for(offering)
        class_id = int(cls["id"])
        self._login_staff()
        banks = self.client.get(
            f"/api/staff/class/{class_id}/components/question-banks"
        ).get_json()
        self.assertTrue(banks.get("ok"))
        self.assertFalse(banks.get("empty"))
        course_bank = next(item for item in banks["items"] if item["title"] == "Course")
        detail = self.client.get(
            f"/api/staff/class/{class_id}/question-bank/{int(course_bank['id'])}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["bank"]["title"], "Course")
        self._login_it()
        posted = self._post_backfill(int(offering["id"]))
        self.assertEqual(posted.status_code, 302)
        self._login_staff()
        pages = self.client.get(
            f"/api/staff/class/{class_id}/components/pages"
        ).get_json()
        self.assertTrue(pages.get("ok"))
        self.assertFalse(pages.get("empty"))
        self.assertIn("Lesson 1", [item["title"] for item in pages["items"]])
        banks = self.client.get(
            f"/api/staff/class/{class_id}/components/question-banks"
        ).get_json()
        check_in = next(item for item in banks["items"] if item["title"] == "Check-in")
        detail = self.client.get(
            f"/api/staff/class/{class_id}/question-bank/{int(check_in['id'])}"
        )
        self.assertEqual(detail.status_code, 200)
        titles = [row.get("title") for row in detail.get_json()["questions"]]
        self.assertIn("Chloroplast", titles)
        self.assertIn("Photosynthesis", titles)
        live = self.client.get(
            f"/api/staff/class/{class_id}/module-banks/all/mc-search"
            "?q=Chloroplast&staff_types=1"
        )
        self.assertEqual(live.status_code, 200)
        body = live.get_json()
        self.assertTrue(body.get("ok"))
        self.assertNotEqual(body.get("error"), "No module pack")

    def test_backfill_merges_into_existing_pack(self) -> None:
        """A course that already has a pack gains rows in that library only."""
        offering = self._assign("MCF3M")
        offering_id = int(offering["id"])
        shell_id = int(offering["shell_library_id"])
        upload = self.school.create_library("MCF3M", origin="upload")
        upload_id = int(upload["id"])
        self.school.conn.execute(
            """
            UPDATE course_offerings
            SET library_id = ?, shell_library_id = NULL, course_shell = NULL
            WHERE id = ?
            """,
            (upload_id, offering_id),
        )
        self.school.conn.execute(
            "DELETE FROM content_libraries WHERE id = ?",
            (shell_id,),
        )
        self.school.conn.execute(
            """
            INSERT INTO pages (
                library_id, import_key, kind, title, html_text, created_at
            ) VALUES (?, 'wiki_content/lesson-1.html', 'html', 'Kept', ?, 't')
            """,
            (upload_id, KEPT_PAGE),
        )
        self.school.conn.commit()
        cls = self._class_for(self.school.get_offering(offering_id))
        self._login_it()
        posted = self._post_backfill(offering_id)
        self.assertEqual(posted.status_code, 302, posted.get_data(as_text=True))
        fresh = self.school.get_offering(offering_id)
        self.assertEqual(int(fresh["library_id"]), upload_id)
        self.assertFalse(fresh.get("shell_library_id"))
        shells = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM content_libraries
            WHERE origin = 'shell' AND ontario_code = 'MCF3M'
            """
        ).fetchone()[0]
        self.assertEqual(shells, 0)
        staging = self.school.conn.execute(
            "SELECT COUNT(*) FROM content_libraries WHERE origin = 'backfill-staging'"
        ).fetchone()[0]
        self.assertEqual(staging, 0)
        kept = self.school.conn.execute(
            """
            SELECT html_text FROM pages
            WHERE library_id = ? AND import_key = 'wiki_content/lesson-1.html'
            """,
            (upload_id,),
        ).fetchone()
        self.assertEqual(kept["html_text"], KEPT_PAGE)
        assignments = self.school.conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE library_id = ?",
            (upload_id,),
        ).fetchone()[0]
        self.assertGreater(assignments, 0)
        self._login_staff()
        pages = self.client.get(
            f"/api/staff/class/{int(cls['id'])}/components/pages"
        ).get_json()
        self.assertIn("Kept", [item["title"] for item in pages["items"]])
        self._login_it()
        again = self._post_backfill(offering_id)
        self.assertEqual(again.status_code, 302)
        kept = self.school.conn.execute(
            """
            SELECT html_text FROM pages
            WHERE library_id = ? AND import_key = 'wiki_content/lesson-1.html'
            """,
            (upload_id,),
        ).fetchone()
        self.assertEqual(kept["html_text"], KEPT_PAGE)
        page_count = self.school.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE library_id = ?",
            (upload_id,),
        ).fetchone()[0]
        third = self._post_backfill(offering_id)
        self.assertEqual(third.status_code, 302)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM pages WHERE library_id = ?",
                (upload_id,),
            ).fetchone()[0],
            page_count,
        )

    def test_backfill_rejects_a_foreign_origin(self) -> None:
        """An admin POST from another site does not write rows."""
        offering = self._assign("SBI4U")
        offering_id = int(offering["id"])
        library_id = int(offering["shell_library_id"])
        before_pages = self.school.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE library_id = ?",
            (library_id,),
        ).fetchone()[0]
        before_libraries = self.school.conn.execute(
            "SELECT COUNT(*) FROM content_libraries"
        ).fetchone()[0]
        self._login_it()
        response = self.client.post(
            f"/it/offerings/{offering_id}/module-pack/backfill",
            data={"module_pack": (io.BytesIO(_imscc_bytes()), "course.imscc")},
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Forbidden", response.get_data(as_text=True))
        origin_only = self.client.post(
            f"/it/offerings/{offering_id}/module-pack/backfill",
            data={"module_pack": (io.BytesIO(_imscc_bytes()), "course.imscc")},
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )
        self.assertEqual(origin_only.status_code, 403)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM pages WHERE library_id = ?",
                (library_id,),
            ).fetchone()[0],
            before_pages,
        )
        self.assertEqual(
            self.school.conn.execute("SELECT COUNT(*) FROM content_libraries").fetchone()[0],
            before_libraries,
        )

    def test_one_shell_library_per_course(self) -> None:
        """A course cannot store two shell libraries, even under contention."""
        offering = self._assign("SBI4U")
        offering_id = int(offering["id"])
        shell_id = int(offering["shell_library_id"])
        self.school.conn.execute(
            "UPDATE course_offerings SET shell_library_id = NULL WHERE id = ?",
            (offering_id,),
        )
        self.school.conn.execute(
            "DELETE FROM content_libraries WHERE id = ?",
            (shell_id,),
        )
        self.school.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.school.conn.execute(
                """
                INSERT INTO content_libraries (
                    ontario_code, origin, created_at, shell_offering_id
                ) VALUES ('SBI4U', 'shell', 't', ?)
                """,
                (offering_id,),
            )
            self.school.conn.execute(
                """
                INSERT INTO content_libraries (
                    ontario_code, origin, created_at, shell_offering_id
                ) VALUES ('SBI4U', 'shell', 't2', ?)
                """,
                (offering_id,),
            )
        self.school.conn.rollback()
        self.school.conn.execute(
            "DELETE FROM content_libraries WHERE shell_offering_id = ?",
            (offering_id,),
        )
        self.school.conn.commit()
        barrier = threading.Barrier(8)
        errors: list[BaseException] = []

        def worker() -> None:
            """Create the shell once the other callers are waiting."""
            try:
                barrier.wait(timeout=5)
                ensure_default_course_shell(self.school, offering_id)
            except BaseException as exc:  # noqa: BLE001 - the test records it
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        fresh = self.school.get_offering(offering_id)
        library_id = int(fresh["shell_library_id"])
        rows = self.school.conn.execute(
            """
            SELECT id FROM content_libraries
            WHERE origin = 'shell'
              AND (id = ? OR shell_offering_id = ?)
            """,
            (library_id, offering_id),
        ).fetchall()
        self.assertEqual({int(row[0]) for row in rows}, {library_id})
        slots = self.school.conn.execute(
            "SELECT COUNT(*) FROM course_class_slots WHERE offering_id = ?",
            (offering_id,),
        ).fetchone()[0]
        self.assertEqual(slots, SHELL_MODULE_COUNT * SHELL_SLOT_COUNT)

