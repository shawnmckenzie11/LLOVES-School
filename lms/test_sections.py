#!/usr/bin/env python3
"""Multiple sections of one Ontario code for one teacher (MCF3M, MCF3M-2, …)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
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
from school_db import section_code  # noqa: E402

LEGACY_OFFERINGS_SQL = """
CREATE TABLE course_offerings (
    id INTEGER PRIMARY KEY,
    semester_id INTEGER NOT NULL REFERENCES semesters(id),
    ontario_code TEXT NOT NULL REFERENCES ontario_courses(code),
    teacher_user_id INTEGER NOT NULL REFERENCES users(id),
    live_access_code TEXT NOT NULL,
    imscc_path TEXT,
    expectations_status TEXT NOT NULL DEFAULT 'unverified',
    student_options_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    copied_from_offering_id INTEGER,
    instance_relpath TEXT,
    UNIQUE(semester_id, ontario_code, teacher_user_id)
);
"""


class SectionTests(unittest.TestCase):
    """A teacher assigned the same code twice gets two labelled offerings."""

    def setUp(self) -> None:
        """Isolated sqlite + data volume in a temp dir, with an active semester."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "lloves.sqlite"
        self.app = create_app(
            db_path=self.db_path, data_dir=self.root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()

    def tearDown(self) -> None:
        """Close the database and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _teacher(self, email: str = "sections@gmail.com") -> dict:
        """Register and return a staff user.

        Args:
            email: Google address for the staff allowlist.
        """
        return self.school.register_staff(email)


    def _ensure_mcf3m_library(self) -> dict:
        """Create a shared MCF3M content library when none exists yet.

        Returns:
            The ``content_libraries`` row used by subsequent assigns.
        """
        existing = self.school.latest_library_for_code("MCF3M")
        if existing:
            return existing
        return self.school.create_library("MCF3M", origin="upload")

    def _assign(self, teacher: dict, code: str = "MCF3M") -> dict:
        """Assign one more section of a course code to a teacher.

        Args:
            teacher: Staff user dict.
            code: Ontario course code.
        """
        return self.school.assign_course(
            teacher_user_id=int(teacher["id"]),
            ontario_code=code,
            new_section=True,
        )

    def _login(self, email: str) -> None:
        """Finish the mock Google + email-code login for a staff user.

        Args:
            email: Registered staff address.
        """
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _populate(self, offering: dict, codename: str) -> dict:
        """Create a class section on an offering so it has an Explore Course link.

        Args:
            offering: Offering dict.
            codename: One roster Codename (rosters may not be empty).
        """
        semester = self.school.get_semester(int(offering["semester_id"]))
        return self.school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=str(offering["ontario_code"]),
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=[codename],
            offering_id=int(offering["id"]),
            teacher_user_id=int(offering["teacher_user_id"]),
        )

    def test_section_code_helper_only_suffixes_after_the_first(self) -> None:
        """``section_code`` leaves section 1 plain and suffixes 2, 3, …."""
        self.assertEqual(section_code("MCF3M", 1), "MCF3M")
        self.assertEqual(section_code("MCF3M", 2), "MCF3M-2")
        self.assertEqual(section_code("MCF3M", 3), "MCF3M-3")
        self.assertEqual(section_code("mcf3m", None), "MCF3M")
        self.assertEqual(section_code("MCF3M", 0), "MCF3M")

    def test_single_assignment_has_no_suffix(self) -> None:
        """One assignment stays plain ``MCF3M`` with section index 1."""
        teacher = self._teacher()
        offering = self._assign(teacher)
        self.assertEqual(offering["section_index"], 1)
        self.assertEqual(offering["section_code"], "MCF3M")
        rows = self.school.list_offerings(teacher_user_id=int(teacher["id"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["section_code"], "MCF3M")

    def test_second_assignment_becomes_dash_two(self) -> None:
        """Assigning MCF3M twice to one teacher yields MCF3M and MCF3M-2."""
        teacher = self._teacher()
        first = self._assign(teacher)
        second = self._assign(teacher)
        self.assertNotEqual(int(first["id"]), int(second["id"]))
        self.assertEqual(second["section_index"], 2)
        self.assertEqual(second["section_code"], "MCF3M-2")
        codes = [
            row["section_code"]
            for row in self.school.list_offerings(teacher_user_id=int(teacher["id"]))
        ]
        self.assertEqual(codes, ["MCF3M", "MCF3M-2"])

    def test_third_assignment_becomes_dash_three(self) -> None:
        """A third assignment of the same code is labelled MCF3M-3."""
        teacher = self._teacher()
        self._assign(teacher)
        self._assign(teacher)
        third = self._assign(teacher)
        self.assertEqual(third["section_index"], 3)
        self.assertEqual(third["section_code"], "MCF3M-3")

    def test_sections_share_one_content_library(self) -> None:
        """Extra sections reuse the shared library — no second IMSCC or unpack."""
        self._ensure_mcf3m_library()
        teacher = self._teacher()
        first = self._assign(teacher)
        second = self._assign(teacher)
        self.assertIsNotNone(first.get("library_id"))
        self.assertEqual(first["library_id"], second["library_id"])
        libraries = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM content_libraries WHERE ontario_code = 'MCF3M'"
        ).fetchone()["n"]
        self.assertEqual(int(libraries), 1)
        self.assertEqual(list(self.root.rglob("*.imscc")), [])
        self.assertEqual(list(self.root.rglob("unpacked")), [])

    def test_sections_get_separate_instance_trees(self) -> None:
        """Each section owns its own syllabus instance folder, first one unchanged."""
        teacher = self._teacher()
        first = self._assign(teacher)
        second = self._assign(teacher)
        self.assertNotEqual(first["instance_relpath"], second["instance_relpath"])
        self.assertTrue(str(first["instance_relpath"]).endswith(f"t{teacher['id']}"))
        self.assertTrue(str(second["instance_relpath"]).endswith(f"t{teacher['id']}-2"))
        self.assertTrue((self.root / first["instance_relpath"]).is_dir())
        self.assertTrue((self.root / second["instance_relpath"]).is_dir())

    def test_different_teachers_each_start_at_no_suffix(self) -> None:
        """Two teachers of MCF3M are each their own section 1 — neither is suffixed."""
        one = self._teacher("one@gmail.com")
        two = self._teacher("two@gmail.com")
        first = self._assign(one)
        second = self._assign(two)
        self.assertEqual(first["section_code"], "MCF3M")
        self.assertEqual(second["section_code"], "MCF3M")
        self.assertEqual(first["live_access_code"], second["live_access_code"])
        self.assertEqual(first["library_id"], second["library_id"])

    def test_staff_home_renders_two_clickable_cards(self) -> None:
        """The teacher dashboard shows MCF3M and MCF3M-2 as separate open links."""
        teacher = self._teacher()
        first = self._assign(teacher)
        second = self._assign(teacher)
        first_class = self._populate(first, "Maple")
        second_class = self._populate(second, "Birch")
        self._login("sections@gmail.com")
        rv = self.client.get("/staff")
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertEqual(html.count('data-ontario-code="MCF3M"'), 2)
        self.assertIn("<h2 class=\"course-card-code\">MCF3M</h2>", html)
        self.assertIn("<h2 class=\"course-card-code\">MCF3M-2</h2>", html)
        self.assertIn(f'/staff/class/{first_class["id"]}"', html)
        self.assertIn(f'/staff/class/{second_class["id"]}"', html)
        page = self.client.get(f"/staff/class/{second_class['id']}")
        self.assertEqual(page.status_code, 200)
        course_html = page.get_data(as_text=True)
        self.assertIn("<h1>MCF3M-2</h1>", course_html)
        self.assertIn(">Dashboard</a>", course_html)
        self.assertNotIn("Staff home", course_html)
        self.assertIn(">Expectations</a>", course_html)
        self.assertIn("Attendance &amp; Participation", course_html)
        # A&P and Grades sit beside Modules in the tab row.
        modules_at = course_html.find(">Modules</a>")
        ap_at = course_html.find("Attendance &amp; Participation")
        grades_at = course_html.find(">Grades</a>")
        self.assertGreater(ap_at, modules_at)
        self.assertGreater(grades_at, modules_at)
        self.assertLess(ap_at, grades_at)
        self.assertNotIn("Inherited curriculum expectations", course_html)
        expectations_page = self.client.get(
            f"/staff/class/{second_class['id']}?tab=expectations"
        )
        self.assertEqual(expectations_page.status_code, 200)
        exp_html = expectations_page.get_data(as_text=True)
        self.assertIn("Curriculum expectations", exp_html)
        plain = self.client.get(f"/staff/class/{first_class['id']}")
        self.assertEqual(plain.status_code, 200)
        self.assertIn("<h1>MCF3M</h1>", plain.get_data(as_text=True))

    def test_it_dashboard_distinguishes_sections(self) -> None:
        """IT's offerings table and staff list show MCF3M-2, not two MCF3M rows."""
        teacher = self._teacher()
        self._assign(teacher)
        self._assign(teacher)
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert it_user is not None
        self.client.post("/verify-email", data={"code": it_user["verification_code"]})
        rv = self.client.get("/it")
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertIn("MCF3M-2", html)
        self.assertIn("MCF3M, MCF3M-2", html)

    def test_it_assign_route_adds_a_second_section(self) -> None:
        """POST /it/offerings twice for one teacher creates two offerings."""
        self._ensure_mcf3m_library()
        teacher = self._teacher()
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert it_user is not None
        self.client.post("/verify-email", data={"code": it_user["verification_code"]})
        for _ in range(2):
            rv = self.client.post(
                "/it/offerings",
                data={
                    "teacher_user_id": str(teacher["id"]),
                    "ontario_code": "MCF3M",
                    "live_days": "M/W/F",
                    "live_time": "2:00pm",
                },
                follow_redirects=False,
            )
            self.assertEqual(rv.status_code, 302)
        rows = self.school.list_offerings(teacher_user_id=int(teacher["id"]))
        self.assertEqual([row["section_code"] for row in rows], ["MCF3M", "MCF3M-2"])

    def test_deleting_a_section_relabels_lone_survivor_plain(self) -> None:
        """With only one active section left, the display code drops the suffix."""
        teacher = self._teacher()
        first = self._assign(teacher)
        second = self._assign(teacher)
        self.school.conn.execute(
            "DELETE FROM course_offerings WHERE id = ?", (int(first["id"]),)
        )
        self.school.conn.commit()
        rows = self.school.list_offerings(teacher_user_id=int(teacher["id"]))
        self.assertEqual([row["section_code"] for row in rows], ["MCF3M"])
        self.assertEqual(
            self.school.get_offering(int(second["id"]))["section_code"], "MCF3M"
        )

    def test_archived_section_does_not_force_suffix_on_reassign(self) -> None:
        """Archived offerings are ignored when choosing the next display code."""
        teacher = self._teacher()
        first = self._assign(teacher)
        self.assertEqual(first["section_code"], "MCF3M")
        self.school.archive_offering(int(first["id"]))
        again = self._assign(teacher)
        self.assertNotEqual(int(first["id"]), int(again["id"]))
        self.assertEqual(again["section_code"], "MCF3M")
        active = self.school.list_offerings(
            teacher_user_id=int(teacher["id"]), include_archived=False
        )
        self.assertEqual([row["section_code"] for row in active], ["MCF3M"])

    def _downgrade_to_pre_section_schema(self) -> None:
        """Rewrite ``course_offerings`` to its pre-section shape on the temp file.

        Reproduces a live Fly database created before ``section_index`` existed,
        including the narrower ``UNIQUE(semester, code, teacher)`` constraint.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, semester_id, ontario_code, teacher_user_id, live_access_code,
                   imscc_path, expectations_status, student_options_json, created_at,
                   copied_from_offering_id, instance_relpath
            FROM course_offerings
            """
        ).fetchall()
        conn.executescript(f"PRAGMA foreign_keys=OFF;\nDROP TABLE course_offerings;\n{LEGACY_OFFERINGS_SQL}")
        conn.executemany(
            """
            INSERT INTO course_offerings (
                id, semester_id, ontario_code, teacher_user_id, live_access_code,
                imscc_path, expectations_status, student_options_json, created_at,
                copied_from_offering_id, instance_relpath
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [tuple(row) for row in rows],
        )
        conn.commit()
        conn.close()

    def test_migration_adds_sections_to_a_pre_existing_database(self) -> None:
        """A database without ``section_index`` migrates and keeps plain labels."""
        teacher = self._teacher("legacy@gmail.com")
        legacy = self._assign(teacher)
        legacy_id = int(legacy["id"])
        legacy_relpath = str(legacy["instance_relpath"])
        self.school.close()
        self._downgrade_to_pre_section_schema()

        probe = sqlite3.connect(str(self.db_path))
        probe.row_factory = sqlite3.Row
        cols = {
            row["name"] for row in probe.execute("PRAGMA table_info(course_offerings)")
        }
        probe.close()
        self.assertNotIn("section_index", cols)

        self.app = create_app(db_path=self.db_path, data_dir=self.root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()

        migrated = self.school.get_offering(legacy_id)
        self.assertEqual(migrated["section_index"], 1)
        self.assertEqual(migrated["section_code"], "MCF3M")
        self.assertEqual(migrated["instance_relpath"], legacy_relpath)

        self._ensure_mcf3m_library()
        extra = self.school.assign_course(
            teacher_user_id=int(teacher["id"]),
            ontario_code="MCF3M",
            new_section=True,
        )
        self.assertEqual(extra["section_code"], "MCF3M-2")
        rehomed = self.school.ensure_offering_instance(
            self.school.get_offering(legacy_id)
        )
        self.assertIsNotNone(extra.get("library_id"))
        self.assertEqual(rehomed["library_id"], extra["library_id"])
        libraries = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM content_libraries WHERE ontario_code = 'MCF3M'"
        ).fetchone()["n"]
        self.assertEqual(int(libraries), 1)

    def test_reopening_a_migrated_database_is_a_no_op(self) -> None:
        """Opening an already-migrated database does not rebuild or renumber."""
        teacher = self._teacher("stable@gmail.com")
        self._assign(teacher)
        second = self._assign(teacher)
        self.school.close()
        self.app = create_app(db_path=self.db_path, data_dir=self.root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        again = self.school.get_offering(int(second["id"]))
        self.assertEqual(again["section_code"], "MCF3M-2")
        self.assertEqual(
            len(self.school.list_offerings(teacher_user_id=int(teacher["id"]))), 2
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
