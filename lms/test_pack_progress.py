#!/usr/bin/env python3
"""Admin pack badge truth, base-layer notes, and syllabus due-date seed."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from pack_progress import library_pack_summary, pack_ui_state  # noqa: E402
from syllabus_seed import parse_due_date, snap_to_content_day  # noqa: E402


def _cartridge_with_due_dates() -> bytes:
    """Tiny IMSCC: one page, assignment, and quiz with school-day due dates."""
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1" identifier="man1">
  <resources>
    <resource identifier="wiki1" type="webcontent" href="wiki_content/lesson-1.html"/>
    <resource identifier="gassign" type="associatedcontent" href="gassign/assignment_settings.xml"/>
    <resource identifier="gquiz" type="imsqti_xmlv1p2/imscc_xmlv1p1/assessment">
      <file href="gquiz/assessment_meta.xml"/>
    </resource>
  </resources>
</manifest>
"""
    modules = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="m1">
    <title>Module 1: Start</title>
    <position>1</position>
    <items>
      <item identifier="i1">
        <title>Lesson 1</title>
        <content_type>WikiPage</content_type>
        <identifierref>wiki1</identifierref>
        <position>1</position>
      </item>
      <item identifier="i2">
        <title>Lab writeup</title>
        <content_type>Assignment</content_type>
        <identifierref>gassign</identifierref>
        <position>2</position>
      </item>
      <item identifier="i3">
        <title>Unit 1 Quiz</title>
        <content_type>Quizzes::Quiz</content_type>
        <identifierref>gquiz</identifierref>
        <position>3</position>
      </item>
    </items>
  </module>
</modules>
"""
    assignment = """<?xml version="1.0" encoding="UTF-8"?>
<assignment identifier="gassign" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Lab writeup</title>
  <points_possible>20.0</points_possible>
  <due_at>2026-09-11T23:59:00-04:00</due_at>
</assignment>
"""
    quiz = """<?xml version="1.0" encoding="UTF-8"?>
<quiz identifier="gquiz" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Unit 1 Quiz</title>
  <quiz_type>assignment</quiz_type>
  <points_possible>10.0</points_possible>
  <due_at>2026-10-12T23:59:00-04:00</due_at>
</quiz>
"""
    qti = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="gquiz" title="Unit 1 Quiz">
    <section ident="root_section">
      <item ident="q1" title="Warmup">
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>multiple_choice_question</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
        <presentation>
          <material><mattext texttype="text/plain">2+2?</mattext></material>
          <response_lid ident="response1" rcardinality="Single">
            <render_choice>
              <response_label ident="a1">
                <material><mattext texttype="text/plain">4</mattext></material>
              </response_label>
            </render_choice>
          </response_lid>
        </presentation>
      </item>
    </section>
  </assessment>
</questestinterop>
"""
    page = """<!DOCTYPE html>
<html><head><title>Lesson 1</title></head>
<body><h1>Lesson 1</h1><p>Body.</p></body></html>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("imsmanifest.xml", manifest)
        archive.writestr("course_settings/module_meta.xml", modules)
        archive.writestr("wiki_content/lesson-1.html", page)
        archive.writestr("gassign/assignment_settings.xml", assignment)
        archive.writestr("gquiz/assessment_meta.xml", quiz)
        archive.writestr("gquiz/assessment_qti.xml", qti)
    return buf.getvalue()


class PackProgressTests(unittest.TestCase):
    """Badge truth, pack notes, staff progress hook, and syllabus seed."""

    def test_pack_ui_state_loading_installed_failed(self) -> None:
        """Busy+library is Loading; done is Installed; error is Failed."""
        loading = pack_ui_state(
            {"stage": "ingest", "busy": True, "detail": "Loading modules…"},
            has_library=True,
        )
        self.assertEqual(loading["badge"], "Loading")
        done = pack_ui_state(
            {
                "stage": "done",
                "busy": False,
                "detail": "Module pack installed — 1 modules · 1 pages · 1 assignments · 1 tests · banks ok",
            },
            has_library=True,
        )
        self.assertEqual(done["badge"], "Installed")
        self.assertIn("1 modules", done["line"])
        failed = pack_ui_state(
            {"stage": "error", "busy": False, "error": "zip corrupt"},
            has_library=True,
        )
        self.assertEqual(failed["badge"], "Failed")
        self.assertIn("zip corrupt", failed["line"])

    def test_parse_due_and_snap_thanksgiving(self) -> None:
        """Canvas due_at becomes a Toronto date; holidays snap backward."""
        self.assertEqual(
            parse_due_date("2026-09-11T23:59:00-04:00"), date(2026, 9, 11)
        )
        snapped = snap_to_content_day(
            date(2026, 10, 12),
            [date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 13)],
        )
        self.assertEqual(snapped, date(2026, 10, 9))

    def setUp(self) -> None:
        """Isolated sqlite + Flask client with an assigned teacher."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
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
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI3U"
        )
        self.school.set_offering_schedule(
            int(self.offering["id"]), live_days="M/W/F", live_time="2:00pm"
        )
        self.offering = self.school.get_offering(int(self.offering["id"]))

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

    def _login_teacher(self, email: str = "teacher@gmail.com") -> None:
        """Sign in as a staff user."""
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def test_instances_include_pack_note(self) -> None:
        """GET /it/instances exposes pack_note instead of a boolean only."""
        self._login_it()
        rv = self.client.get("/it/instances?code=SBI3U")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertIn("template_note", body)
        row = next(
            r for r in body["instances"] if r["offering_id"] == int(self.offering["id"])
        )
        self.assertIn("pack_note", row)
        self.assertIn("has_pack", row)

    def test_dashboard_pack_line_markup(self) -> None:
        """Course Offerings Pack Status is a single it-pack-line."""
        self._login_it()
        html = self.client.get("/it").get_data(as_text=True)
        self.assertIn("it-pack-line", html)

    def test_json_replace_pack_ingests_and_seeds_syllabus(self) -> None:
        """XHR replace-pack redirects to Offerings and seeds due-dated items."""
        self._login_it()
        payload = _cartridge_with_due_dates()
        rv = self.client.post(
            f"/it/offerings/{self.offering['id']}/module-pack",
            data={"module_pack": (io.BytesIO(payload), "bio.imscc")},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertTrue(body.get("ok"))
        self.assertIn("tab=offerings", body.get("redirect") or "")
        offering = self.school.get_offering(int(self.offering["id"]))
        lib_id = int(offering["library_id"])
        summary = library_pack_summary(self.school, lib_id)
        self.assertEqual(summary["modules"], 1)
        self.assertEqual(summary["pages"], 1)
        self.assertEqual(summary["assignments"], 1)
        self.assertEqual(summary["tests"], 1)
        self.assertTrue(summary["banks_ok"])
        status = self.client.get(
            f"/it/offerings/{self.offering['id']}/module-pack/status"
        ).get_json()
        self.assertEqual(status["badge"], "Installed")
        self.assertEqual(status["stage"], "done")
        html_path = Path(self.tmp.name) / offering["instance_relpath"] / "syllabus"
        found = list(html_path.glob("*.html"))
        self.assertTrue(found)
        html = found[0].read_text(encoding="utf-8")
        self.assertIn("Lab writeup", html)
        self.assertIn("Unit 1 Quiz", html)

    def test_other_teacher_cannot_poll_pack_status(self) -> None:
        """A teacher who does not own the offering gets 403."""
        other = self.school.register_staff("other@gmail.com")
        self._login_teacher("other@gmail.com")
        rv = self.client.get(
            f"/staff/offerings/{self.offering['id']}/module-pack/status"
        )
        self.assertEqual(rv.status_code, 403)
        self.assertTrue(other)

    def test_staff_home_has_pack_progress_hook(self) -> None:
        """Staff home cards expose a status URL and bottom progress bar."""
        self._login_teacher()
        html = self.client.get("/staff").get_data(as_text=True)
        self.assertIn(
            f"/staff/offerings/{self.offering['id']}/module-pack/status", html
        )
        self.assertIn("course-card-pack-progress", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
