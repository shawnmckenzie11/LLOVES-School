#!/usr/bin/env python3
"""IT semester activation, MCF3M assignment, and Populate Class gating."""

from __future__ import annotations

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
from school_db import format_human_datetime  # noqa: E402


class ItTests(unittest.TestCase):
    """IT dashboard backend: calendar clone and course assignment."""

    def test_format_human_datetime_never_and_today(self) -> None:
        """Blank stamps say Never; same-day ISO stamps say Today."""
        self.assertEqual(format_human_datetime(None), "Never")
        self.assertEqual(format_human_datetime(""), "Never")
        from datetime import datetime

        stamp = datetime.now().replace(hour=17, minute=40, second=0, microsecond=0).isoformat()
        self.assertTrue(format_human_datetime(stamp).startswith("Today · "))
        self.assertIn("5:40 PM", format_human_datetime(stamp))

    def setUp(self) -> None:
        """Isolated sqlite + Flask test client."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()

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

    def _ensure_library(self, code: str = "MCF3M") -> dict:
        """Create a content library so Admin HTTP assign passes pack gating.

        Args:
            code: Ontario course code for the library row.

        Returns:
            The ``content_libraries`` row.
        """
        existing = self.school.latest_library_for_code(code)
        if existing:
            return existing
        return self.school.create_library(code, origin="upload")

    def test_activate_2026_2027_s1_copies_dates(self) -> None:
        """Activating the default semester copies instructional dates from JSON."""
        row = self.school.activate_from_semester_json()
        self.assertEqual(row["label"], "2026-2027 S1")
        self.assertEqual(row["instructional_first"], "2026-09-08")
        self.assertEqual(row["instructional_last"], "2027-01-25")
        self.assertEqual(row["year_display"], "2026/27")
        self.assertEqual(row["term"], "Semester 1")
        self.assertEqual(row["is_active"], 1)
        exam = row["exam_window_json"]
        self.assertIn("2027-01-26", exam)

    def test_assign_mcf3m_attaches_a1_and_a1_3(self) -> None:
        """MCF3M inherits overall A1 and specific A1.3 from the verified seed."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.assertEqual(len(offering["live_access_code"]), 8)
        codes = {row["code"]: row for row in self.school.list_expectations("MCF3M")}
        self.assertIn("A1", codes)
        self.assertEqual(codes["A1"]["kind"], "overall")
        self.assertIn("A1.3", codes)
        self.assertEqual(codes["A1.3"]["kind"], "specific")
        self.assertEqual(codes["A1.3"]["parent_code"], "A1")
        self.assertTrue(codes["A1"]["statement"])

    def test_second_teacher_reuses_live_access_code(self) -> None:
        """One student key per (semester, Ontario course), shared across teachers."""
        self.school.activate_from_semester_json()
        a = self.school.register_staff("a@gmail.com")
        b = self.school.register_staff("b@gmail.com")
        first = self.school.assign_course(
            teacher_user_id=int(a["id"]), ontario_code="MCF3M"
        )
        second = self.school.assign_course(
            teacher_user_id=int(b["id"]), ontario_code="MCF3M"
        )
        self.assertEqual(first["live_access_code"], second["live_access_code"])
        self.assertNotEqual(first["id"], second["id"])
        first_root = Path(self.school.data_dir) / str(first["instance_relpath"])
        second_root = Path(self.school.data_dir) / str(second["instance_relpath"])
        self.assertTrue(first_root.is_dir())
        self.assertTrue(second_root.is_dir())
        self.assertNotEqual(first_root, second_root)
        self.assertTrue(first_root.name.startswith("t"))
        self.assertNotEqual(first.get("copied_from_offering_id"), second["id"])
        # No git template: both teachers share the absence of a library until Admin uploads.
        self.assertIsNone(first.get("library_id"))
        self.assertEqual(first["library_id"], second["library_id"])
        self.assertFalse((first_root / "pack" / "course.imscc").exists())
        self.assertFalse((second_root / "pack" / "course.imscc").exists())
        self.assertEqual(list(Path(self.school.data_dir).rglob("*.imscc")), [])

    def test_staff_without_offering_cannot_populate(self) -> None:
        """Populate Class is forbidden until IT assigns a course."""
        self.school.activate_from_semester_json()
        self.school.register_staff("lonely@gmail.com")
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=lonely@gmail.com&name=Lonely")
        user = self.school.get_user_by_email("lonely@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": 0,
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(rv.status_code, 403)

    def test_it_activate_endpoint(self) -> None:
        """IT dashboard POST clones the school calendar."""
        self._login_it()
        rv = self.client.post("/it/semesters/activate", follow_redirects=False)
        self.assertEqual(rv.status_code, 302)
        active = self.school.get_active_semester()
        self.assertIsNotNone(active)
        self.assertEqual(active["label"], "2026-2027 S1")

    def test_it_assign_form_owns_upload_input(self) -> None:
        """Assign page for a staff member is multipart and accepts a .imscc."""
        self.school.activate_from_semester_json()
        self._login_it()
        staff = self.school.register_staff("teacher@example.com")
        staff_id = int(staff["id"])
        rv = self.client.get(f"/it/staff/{staff_id}/assign")
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('name="module_pack"', html)
        self.assertIn("Module pack", html)
        self.assertIn('name="live_days"', html)
        self.assertIn('name="live_time"', html)
        self.assertIn("M/W/F", html)
        # dashboard still links to the assign page
        rv2 = self.client.get("/it")
        self.assertEqual(rv2.status_code, 200)
        self.assertIn(f"/it/staff/{staff_id}/assign", rv2.get_data(as_text=True))

    def test_assigned_codes_exclude_archived_offerings(self) -> None:
        """Staff tab Current Courses omits soft-archived offerings."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("archivecodes@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.school.set_offering_schedule(
            int(offering["id"]), live_days="M/W/F", live_time="2:00pm"
        )
        before = {
            int(p["id"]): p for p in self.school.list_staff(include_archived=False)
        }
        self.assertIn("MCF3M", before[int(teacher["id"])]["assigned_codes"] or "")
        self.school.archive_offering(int(offering["id"]))
        after = {
            int(p["id"]): p for p in self.school.list_staff(include_archived=False)
        }
        self.assertIsNone(after[int(teacher["id"])]["assigned_codes"])

    def test_admin_assign_persists_live_schedule(self) -> None:
        """POST /it/staff/<id>/assign stores days/time for Populate Class."""
        self.school.activate_from_semester_json()
        self._ensure_library("MCF3M")
        self._login_it()
        staff = self.school.register_staff("scheduleteacher@gmail.com")
        rv = self.client.post(
            f"/it/staff/{int(staff['id'])}/assign",
            data={
                "ontario_code": "MCF3M",
                "live_days": "T/Th/F",
                "live_time": "10:40am",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        offering = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCF3M",
            int(staff["id"]),
        )
        assert offering is not None
        self.assertEqual(offering["live_days"], "T/Th/F")
        self.assertEqual(offering["live_time"], "10:40am")

    def test_populate_uses_admin_schedule_over_body(self) -> None:
        """Staff populate ignores client days/time when Admin locked the schedule."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("locked@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.school.set_offering_schedule(
            int(offering["id"]), live_days="T/Th/F", live_time="9:15am"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=locked@gmail.com&name=L")
        user = self.school.get_user_by_email("locked@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Cedar"],
            },
        )
        self.assertEqual(rv.status_code, 200)
        created = rv.get_json()["class"]
        self.assertEqual(created["days"], "Tue/Thu/Fri")
        self.assertEqual(created["time"], "9:15am")

    def test_imscc_max_bytes_allows_650mb_class_packs(self) -> None:
        """App content-length ceiling is above a ~639 MB IMSCC."""
        from modules import IMSCC_MAX_BYTES

        self.assertGreaterEqual(IMSCC_MAX_BYTES, 650 * 1024 * 1024)
        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], IMSCC_MAX_BYTES)

    def test_settings_tab_live_day_gate(self) -> None:
        """Admin Settings API toggles only_live_class_days."""
        self._login_it()
        dash = self.client.get("/it")
        self.assertEqual(dash.status_code, 200)
        html = dash.get_data(as_text=True)
        self.assertIn("tab-settings", html)
        self.assertIn("only-live-class-days", html)
        self.assertIn("it-wrap", html)
        self.assertIn("it-dashboard", html)
        self.assertIn("Today · ", html)
        before = self.client.get("/api/it/settings").get_json()
        self.assertFalse(before["only_live_class_days"])
        updated = self.client.post(
            "/api/it/settings",
            json={"only_live_class_days": True},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.get_json()["only_live_class_days"])
        self.assertTrue(self.school.only_live_class_days())
        self.client.post("/api/it/settings", json={"only_live_class_days": False})

    def test_syllabus_editor_needs_imscc_gracefully(self) -> None:
        """Editor route does not crash when the teacher has a class."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        rv = self.client.get(f"/staff/class/{created['id']}/syllabus/editor")
        self.assertIn(rv.status_code, {200, 302})

    def test_extract_open_and_university_codes_from_text(self) -> None:
        """TOC parser keeps Open courses (no 'Preparation') and university codes."""
        from curriculum import extract_courses_from_pdf_text

        text = (
            "Grade 11\n"
            "English, Grade 11, University Preparation (ENG3U)\n"
            "Media Studies, Grade 11, Open (EMS3O)\n"
            "Functions, University Preparation (MCR3U)\n"
        )
        courses = {row["code"]: row for row in extract_courses_from_pdf_text(text)}
        self.assertIn("ENG3U", courses)
        self.assertIn("EMS3O", courses)
        self.assertIn("MCR3U", courses)
        self.assertIn("Open", courses["EMS3O"]["title"])
        self.assertNotIn("Open Preparation", courses["EMS3O"]["title"])
        hale = extract_courses_from_pdf_text(
            "Grade 9\nHealthy Active Living Education, Grade 9, Open (PPL1O)\n",
            grade_digits="1234",
        )
        self.assertEqual(hale[0]["code"], "PPL1O")
        self.assertEqual(hale[0]["grade"], 9)
        spaced = extract_courses_from_pdf_text(
            "Biology, Grade 11, College Preparation (SBI3C )\n"
        )
        self.assertEqual(spaced[0]["code"], "SBI3C")

    def test_catalog_is_math_science_hpe_only(self) -> None:
        """IT documents this round are Mathematics, Science, and HPE — not English/etc."""
        from curriculum import ONTARIO_DOCUMENTS

        subjects = {spec["subject"] for spec in ONTARIO_DOCUMENTS}
        self.assertIn("Mathematics", subjects)
        self.assertIn("Science", subjects)
        self.assertTrue(any("Health and Physical Education" in s for s in subjects))
        self.assertNotIn("English", subjects)
        self.assertNotIn("The Arts", subjects)
        self.assertNotIn("Business Studies", subjects)

    def test_it_search_and_assign_non_math_when_extracted(self) -> None:
        """IT autocomplete can assign a science or HPE code extracted from a local PDF."""
        from curriculum import ONTARIO_DOCUMENTS, extract_courses_from_pdf
        from paths import REPO_ROOT

        catalog = {
            row["code"]: row
            for row in self.school.search_ontario_courses("", limit=300)
        }
        self.assertIn("MCF3M", catalog)
        extracted = []
        for spec in ONTARIO_DOCUMENTS:
            if spec.get("subject") == "Mathematics":
                continue
            rel = spec.get("local_path") or ""
            if not rel:
                continue
            extracted.extend(
                extract_courses_from_pdf(
                    REPO_ROOT / rel,
                    grade_digits=spec.get("code_grade_digits") or "34",
                )
            )
        if not extracted:
            self.skipTest("No science/HPE Ontario PDF extract in this environment")
        found = [row for row in extracted if row["code"] in catalog]
        self.assertTrue(
            found,
            "seed_curriculum did not store extracted science/HPE codes",
        )
        picked = next(
            (row for row in found if row["code"] in {"SBI3U", "PPL3O", "SNC3M"}),
            found[0],
        )
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("engteacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code=picked["code"]
        )
        self.assertEqual(offering["ontario_code"], picked["code"])
        self.assertEqual(offering["expectations_status"], "unverified")
        self._login_it()
        search = self.client.get(f"/it/courses?q={picked['code']}")
        self.assertEqual(search.status_code, 200)
        codes = {row["code"] for row in search.get_json()["courses"]}
        self.assertIn(picked["code"], codes)


    def test_it_instances_lists_priors(self) -> None:
        """GET /it/instances?code= lists prior offerings for the base picker."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("prior@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self._login_it()
        rv = self.client.get("/it/instances?code=MCF3M")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertTrue(body.get("ok"))
        ids = {row["offering_id"] for row in body["instances"]}
        self.assertIn(int(offering["id"]), ids)
        row = next(r for r in body["instances"] if r["offering_id"] == int(offering["id"]))
        self.assertEqual(row["year"], "2026-2027")
        self.assertEqual(row["term"], "S1")
        self.assertEqual(row["teacher_email"], "prior@gmail.com")
        self.assertIn("has_pack", row)

    def test_it_assign_with_base_sets_copied_from(self) -> None:
        """POST /it/offerings with a base forks that offering's pack."""
        self.school.activate_from_semester_json()
        self._ensure_library("MCF3M")
        first_teacher = self.school.register_staff("base@gmail.com")
        second_teacher = self.school.register_staff("fork@gmail.com")
        first = self.school.assign_course(
            teacher_user_id=int(first_teacher["id"]), ontario_code="MCF3M"
        )
        self._login_it()
        rv = self.client.post(
            "/it/offerings",
            data={
                "teacher_user_id": str(second_teacher["id"]),
                "ontario_code": "MCF3M",
                "copied_from_offering_id": str(first["id"]),
                "live_days": "M/W/F",
                "live_time": "2:00pm",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        second = self.school.get_offering_for(
            int(first["semester_id"]), "MCF3M", int(second_teacher["id"])
        )
        assert second is not None
        self.assertEqual(int(second["copied_from_offering_id"]), int(first["id"]))
        self.assertNotEqual(second["instance_relpath"], first["instance_relpath"])
        self.assertEqual(first["live_access_code"], second["live_access_code"])
        self.assertIsNotNone(first.get("library_id"))
        first_root = Path(self.school.data_dir) / first["instance_relpath"]
        second_root = Path(self.school.data_dir) / second["instance_relpath"]
        self.assertTrue((first_root / "manifest.json").is_file())
        self.assertTrue((second_root / "manifest.json").is_file())
        self.assertFalse((first_root / "pack" / "course.imscc").exists())
        self.assertFalse((second_root / "pack" / "course.imscc").exists())
        self.assertEqual(first["library_id"], second["library_id"])
        self.assertEqual(list(Path(self.school.data_dir).rglob("*.imscc")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
