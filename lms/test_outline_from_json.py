#!/usr/bin/env python3
"""JSON module-lessons seeder, pack-free Admin assign, syllabus, portfolio."""

from __future__ import annotations

import json
import os
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
from outline_from_json import (  # noqa: E402
    json_outline_available,
    normalize_lesson_title,
    seed_json_library,
)
from portfolio.assemble import list_modules  # noqa: E402

# Same shape as MCF3M module-lessons.json, including M4 Lesson2: and M7 bare titles.
MCF3M_MODULE_LESSONS = {
    "course_code": "MCF3M",
    "pack_title": "Functions and Applications, Grade 11, University/College Preparation",
    "total_lessons": 39,
    "modules": [
        {
            "module": 1,
            "title": "Introduction to the Quadratic Function",
            "lessons": [
                "Lesson 1: Characteristics of a Function",
                "Lesson 2: Comparing Rates of Change in Linear & Quadratic Functions",
                "Lesson 3: Function Notation",
                "Lesson 4: Exploring Transformations of Quadratic Functions",
                "Lesson 5: Graphing Quadratic Functions using Transformations",
                "Lesson 6: Using Multiple Transformations to Graph Quadratic Functions",
                "Lesson 7: The Domain and Range of a Quadratic Function",
            ],
        },
        {
            "module": 2,
            "title": "Algebra of Quadratic Expressions",
            "lessons": [
                "Lesson 1: Expanding Polynomials PART 1",
                "Lesson 2: Expanding Polynomials PART 2",
                "Lesson 3: Common Factoring",
                "Lesson 4: Factoring Simple Trinomials",
                "Lesson 5: Factoring Harder Trinomials",
                "Lesson 6: Special Cases",
            ],
        },
        {
            "module": 3,
            "title": "Working with Quadratic Functions",
            "lessons": [
                "Lesson 1: Relating Standard & Factored Form",
                "Lesson 2: Solving Quadratic Equations by Graphing",
                "Lesson 3: Solving Quadratic Functions by Factoring",
                "Lesson 4: Solving Problems Involving Quadratics",
                "Lesson 5: Creating a Quadratic Model from Data",
            ],
        },
        {
            "module": 4,
            "title": "Quadratic Models",
            "lessons": [
                "Lesson 1: The Vertex Form of a Quadratic Function",
                "Lesson2: Relating the Standard & Vertex Forms: Completing the Square",
                "Lesson 3: Solving Quadratic Equations: Quadratic Formula",
                "Lesson 4: Discriminant",
                "Lesson 5: Using Quadratic Models to Solve Problems",
            ],
        },
        {
            "module": 5,
            "title": "Trigonometry & Acute Triangles",
            "lessons": [
                "Lesson 1: Trigonometric Ratios",
                "Lesson 2: Applying the Primary Trig Ratios",
                "Lesson 3: Solving Problems by Using Right-Triangle Models",
                "Lesson 4: Cosine & Sine Law",
                "Lesson 5: Solving Problems by Using Acute-Triangle Models",
            ],
        },
        {
            "module": 6,
            "title": "Sinusoidal Functions",
            "lessons": [
                "Lesson 1: Periodic Functions",
                "Lesson 2: Sinusoidal Functions",
                "Lesson 3: Transformations of the Sine Function",
            ],
        },
        {
            "module": 7,
            "title": "Exponential Functions",
            "lessons": [
                "Laws of Exponents & Integer Exponents",
                "Working with Rational Exponents",
                "Exploring Exponential Functions, Growth & Decay",
            ],
        },
        {
            "module": 8,
            "title": "Solving Financial Problems Involving Exponential Functions",
            "lessons": [
                "Lesson 1: Simple & Compound Interest",
                "Lesson 2: Compound Interest - Future Value",
                "Lesson 3: Compound Interest - Present Value",
                "Lesson 4: Annuities - Future Value",
                "Lesson 5: Regular/Simple Annuities - Present Value",
            ],
        },
    ],
}

MCF3M_STRAND_MAP_MIN = {
    "course_code": "MCF3M",
    "modules": [
        {
            "module_number": 1,
            "title": "Introduction to the Quadratic Function",
            "strand": "A",
            "strand_name": "Quadratic Functions",
            "expectation_codes": ["A2.1"],
        }
    ],
    "specifics": [
        {
            "code": "A2.1",
            "statement": "demonstrate an understanding of functions",
            "module_numbers": [1],
            "overall": False,
        }
    ],
}


def _write_json(path: Path, payload: dict) -> None:
    """Write UTF-8 JSON for a temp curriculum cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class OutlineFromJsonTests(unittest.TestCase):
    """Seeder, IT assign without IMSCC, syllabus left panel, portfolio dropdown."""

    def setUp(self) -> None:
        """Isolated sqlite plus a temp ``module-lessons.json`` cache."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache_root = self.root / "curriculum"
        _write_json(
            self.cache_root / "MCF3M" / "module-lessons.json",
            MCF3M_MODULE_LESSONS,
        )
        _write_json(
            self.cache_root / "MCF3M" / "module-strand-map.json",
            MCF3M_STRAND_MAP_MIN,
        )
        self.app = create_app(
            db_path=self.root / "lloves.sqlite",
            data_dir=self.root / "data",
            testing=True,
            curriculum_cache_root=self.cache_root,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        if self.school.get_ontario_course("SBI3U") is None:
            self.school.upsert_ontario_course(
                "SBI3U",
                "Biology, Grade 11, University Preparation",
                grade=11,
                pathway="U",
                expectations_status="unverified",
            )

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_it(self) -> None:
        """Finish mock Google + 2SV as the bootstrap IT user."""
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _login_staff(self, email: str, name: str = "T") -> dict:
        """Sign in a staff user, registering the email when needed."""
        user = self.school.get_user_by_email(email)
        if user is None:
            user = self.school.register_staff(email)
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name={name}")
        user = self.school.get_user_by_email(email)
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        return user

    def test_normalize_lesson2_and_bare_m7(self) -> None:
        """Lesson2: gains a space; titles without Lesson get a numbered prefix."""
        self.assertEqual(
            normalize_lesson_title(
                "Lesson2: Relating the Standard & Vertex Forms: Completing the Square",
                2,
            ),
            "Lesson 2: Relating the Standard & Vertex Forms: Completing the Square",
        )
        self.assertEqual(
            normalize_lesson_title("Laws of Exponents & Integer Exponents", 1),
            "Lesson 1: Laws of Exponents & Integer Exponents",
        )

    def test_seeder_eight_outlines_thirty_nine_lessons(self) -> None:
        """JSON seed writes 8 outlines and 39 Lesson-titled pages; M4/M7 fixups."""
        lib = seed_json_library(self.school, "MCF3M", cache_root=self.cache_root)
        self.assertEqual(lib["origin"], "json")
        self.assertIsNone(lib["source_path"])
        outlines = self.school.conn.execute(
            """
            SELECT title FROM module_outlines
            WHERE library_id = ? ORDER BY position, id
            """,
            (int(lib["id"]),),
        ).fetchall()
        self.assertEqual(len(outlines), 8)
        self.assertEqual(
            outlines[0]["title"],
            "Module 1: Introduction to the Quadratic Function",
        )
        self.assertEqual(outlines[6]["title"], "Module 7: Exponential Functions")
        pages = self.school.conn.execute(
            """
            SELECT title, html_text FROM pages
            WHERE library_id = ? ORDER BY id
            """,
            (int(lib["id"]),),
        ).fetchall()
        self.assertEqual(len(pages), 39)
        for row in pages:
            self.assertIn("Lesson", row["title"])
            self.assertIn("Minds-On", row["html_text"] or "")
            self.assertIn("Summary: Need to Know", row["html_text"] or "")
        titles = [row["title"] for row in pages]
        self.assertIn(
            "Lesson 2: Relating the Standard & Vertex Forms: Completing the Square",
            titles,
        )
        self.assertIn("Lesson 1: Laws of Exponents & Integer Exponents", titles)
        self.assertIn("Lesson 2: Working with Rational Exponents", titles)
        self.assertIn(
            "Lesson 3: Exploring Exponential Functions, Growth & Decay",
            titles,
        )
        again = seed_json_library(self.school, "MCF3M", cache_root=self.cache_root)
        self.assertEqual(int(again["id"]), int(lib["id"]))
        count = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM content_libraries WHERE ontario_code = 'MCF3M'"
        ).fetchone()["n"]
        self.assertEqual(int(count), 1)

    def test_it_staff_assign_mcf3m_without_file_seeds_json(self) -> None:
        """POST /it/staff/<id>/assign with no pack seeds origin=json, null path."""
        self._login_it()
        staff = self.school.register_staff("jsonassign@gmail.com")
        rv = self.client.post(
            f"/it/staff/{int(staff['id'])}/assign",
            data={
                "ontario_code": "MCF3M",
                "live_days": "M/W/F",
                "live_time": "2:00pm",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True))
        offering = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCF3M",
            int(staff["id"]),
        )
        assert offering is not None
        self.assertTrue(offering.get("library_id"))
        lib = self.school.get_library(int(offering["library_id"]))
        assert lib is not None
        self.assertEqual(lib["origin"], "json")
        self.assertIsNone(lib["source_path"])
        self.assertFalse(offering.get("imscc_path"))

    def test_it_offerings_assign_without_file_seeds_json(self) -> None:
        """POST /it/offerings without a file also seeds the JSON library."""
        self._login_it()
        staff = self.school.register_staff("jsonoffer@gmail.com")
        rv = self.client.post(
            "/it/offerings",
            data={
                "teacher_user_id": str(staff["id"]),
                "ontario_code": "MCF3M",
                "live_days": "T/Th/F",
                "live_time": "10:40am",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True))
        offering = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCF3M",
            int(staff["id"]),
        )
        assert offering is not None
        lib = self.school.get_library(int(offering["library_id"]))
        assert lib is not None
        self.assertEqual(lib["origin"], "json")
        self.assertIsNone(lib["source_path"])

    def test_assign_hint_mentions_json_outline(self) -> None:
        """Assign form says the pack is optional when a JSON outline exists."""
        self._login_it()
        staff = self.school.register_staff("hint@gmail.com")
        rv = self.client.get(f"/it/staff/{int(staff['id'])}/assign")
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertIn("JSON outline", html)
        self.assertIn("Module pack", html)
        self.assertIn(">None</option>", html)
        self.assertNotIn("Course template (default)", html)

    def test_syllabus_editor_lists_json_modules_and_lessons(self) -> None:
        """Syllabus editor GET 200 includes Module 1 and Lesson 1 titles."""
        self._login_it()
        staff = self.school.register_staff("syllabusjson@gmail.com")
        self.client.post(
            f"/it/staff/{int(staff['id'])}/assign",
            data={
                "ontario_code": "MCF3M",
                "live_days": "M/W/F",
                "live_time": "2:00pm",
            },
        )
        self._login_staff("syllabusjson@gmail.com")
        offering = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCF3M",
            int(staff["id"]),
        )
        assert offering is not None
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        class_id = int(created.get_json()["class"]["id"])
        editor = self.client.get(f"/staff/class/{class_id}/syllabus/editor")
        self.assertEqual(editor.status_code, 200, editor.get_data(as_text=True))
        html = editor.get_data(as_text=True)
        self.assertIn("Module 1: Introduction to the Quadratic Function", html)
        self.assertIn("Lesson 1: Characteristics of a Function", html)
        self.assertNotIn("IMSCC cartridge", html)

    def test_list_modules_prefers_json_eight_rows(self) -> None:
        """``list_modules('MCF3M')`` returns eight JSON module titles."""
        rows = list_modules("MCF3M", cache_root=self.cache_root)
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["title"], "Introduction to the Quadratic Function")
        self.assertEqual(rows[6]["title"], "Exponential Functions")
        self.assertEqual(rows[0]["strand"], "A")

    def test_portfolio_context_module_one_ok(self) -> None:
        """Portfolio context API for module=1 returns 200 after JSON assign."""
        self._login_it()
        staff = self.school.register_staff("pfjson@gmail.com")
        self.client.post(
            f"/it/staff/{int(staff['id'])}/assign",
            data={
                "ontario_code": "MCF3M",
                "live_days": "M/W/F",
                "live_time": "2:00pm",
            },
        )
        self._login_staff("pfjson@gmail.com")
        offering = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCF3M",
            int(staff["id"]),
        )
        assert offering is not None
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Birch"],
            },
        )
        class_id = int(created.get_json()["class"]["id"])
        ctx = self.client.get(f"/api/classes/{class_id}/portfolio/context?module=1")
        self.assertEqual(ctx.status_code, 200, ctx.get_data(as_text=True))
        body = ctx.get_json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(len(body.get("modules") or []), 8)
        self.assertEqual(
            body["modules"][0]["title"],
            "Introduction to the Quadratic Function",
        )

    def test_sbi3u_assign_without_pack_or_json_fails(self) -> None:
        """SBI3U has no JSON outline, so assign without a pack still errors."""
        self.assertFalse(json_outline_available("SBI3U", cache_root=self.cache_root))
        self._login_it()
        staff = self.school.register_staff("sbi3u@gmail.com")
        rv = self.client.post(
            f"/it/staff/{int(staff['id'])}/assign",
            data={
                "ontario_code": "SBI3U",
                "live_days": "M/W/F",
                "live_time": "2:00pm",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 400)
        self.assertIn("JSON outline is required", rv.get_data(as_text=True))
        offering = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "SBI3U",
            int(staff["id"]),
        )
        self.assertIsNone(offering)

    def test_sbi3u_syllabus_empty_asks_for_json_or_pack(self) -> None:
        """Empty syllabus copy no longer mentions an IMSCC cartridge."""
        teacher = self.school.register_staff("sbi-syllabus@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="SBI3U"
        )
        cls = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="SBI3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        self._login_staff("sbi-syllabus@gmail.com")
        editor = self.client.get(f"/staff/class/{cls['id']}/syllabus/editor")
        html = editor.get_data(as_text=True)
        self.assertIn("Ask Admin to assign a module outline (JSON or pack).", html)
        self.assertNotIn("IMSCC cartridge", html)

    def test_sbi3u_modules_nav_still_failing_soft(self) -> None:
        """Codes without a pack or JSON outline keep an empty Modules tree."""
        teacher = self.school.register_staff("sbi-mod@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="SBI3U"
        )
        cls = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="SBI3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Cedar"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        self._login_staff("sbi-mod@gmail.com")
        nav = self.client.get(f"/api/staff/class/{cls['id']}/modules")
        body = nav.get_json()
        self.assertTrue(body.get("empty"))
        self.assertIn("Ask Admin to attach a module pack", body.get("message") or "")


if __name__ == "__main__":
    unittest.main()
