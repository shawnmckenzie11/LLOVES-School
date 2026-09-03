#!/usr/bin/env python3
"""Staff component tabs: Pages, Assignments, Quizzes, Question banks."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1" identifier="man1">
  <resources>
    <resource identifier="wiki1" type="webcontent" href="wiki_content/lesson-1.html"/>
    <resource identifier="asg1" type="associatedcontent" href="gasg/assignment_settings.xml"/>
    <resource identifier="quiz1" type="imsqti_xmlv1p2/imscc_xmlv1p1/assessment"/>
  </resources>
</manifest>
"""

MODULES = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="m1">
    <title>Module 1: Start</title>
    <position>1</position>
    <items>
      <item identifier="i1">
        <title>Lesson 1</title><content_type>WikiPage</content_type>
        <position>1</position><identifierref>wiki1</identifierref>
      </item>
      <item identifier="i2">
        <title>Task 1</title><content_type>Assignment</content_type>
        <position>2</position><identifierref>asg1</identifierref>
      </item>
      <item identifier="i3">
        <title>Check-in</title><content_type>Quizzes::Quiz</content_type>
        <position>3</position><identifierref>gquiz</identifierref>
      </item>
      <item identifier="i4">
        <title>Reference deck</title><content_type>ExternalUrl</content_type>
        <position>4</position>
        <url>https://docs.google.com/presentation/d/deck/edit</url>
      </item>
    </items>
  </module>
</modules>
"""

PAGE = """<!DOCTYPE html>
<html><head><title>Lesson 1</title></head>
<body><h1>Lesson 1</h1><p>Uploaded pack body.</p>
<img src="$IMS-CC-FILEBASE$/diagram.png"></body></html>
"""

ASSIGNMENT = """<?xml version="1.0" encoding="UTF-8"?>
<assignment identifier="gasg" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Task 1</title>
  <points_possible>15.0</points_possible>
</assignment>
"""

QUIZ_META = """<?xml version="1.0" encoding="UTF-8"?>
<quiz identifier="gquiz" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Check-in</title>
  <quiz_type>assignment</quiz_type>
</quiz>
"""

QTI = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="gquiz" title="Check-in">
    <section ident="root">
      <item ident="q1" title="Photosynthesis">
        <itemmetadata><qtimetadata><qtimetadatafield>
          <fieldlabel>question_type</fieldlabel>
          <fieldentry>essay_question</fieldentry>
        </qtimetadatafield></qtimetadata></itemmetadata>
        <presentation><material>
          <mattext texttype="text/html">Explain photosynthesis.</mattext>
        </material></presentation>
      </item>
      <item ident="q2" title="Chloroplast">
        <itemmetadata><qtimetadata>
          <qtimetadatafield>
            <fieldlabel>question_type</fieldlabel>
            <fieldentry>multiple_choice_question</fieldentry>
          </qtimetadatafield>
          <qtimetadatafield>
            <fieldlabel>points_possible</fieldlabel>
            <fieldentry>1.0</fieldentry>
          </qtimetadatafield>
        </qtimetadata></itemmetadata>
        <presentation>
          <material>
            <mattext texttype="text/html">&lt;p&gt;Where does photosynthesis happen?&lt;/p&gt;
&lt;img src="$IMS-CC-FILEBASE$/diagram.png"&gt;</mattext>
          </material>
          <response_lid ident="response1" rcardinality="Single">
            <render_choice>
              <response_label ident="c1">
                <material><mattext texttype="text/plain">In the chloroplast</mattext></material>
              </response_label>
              <response_label ident="c2">
                <material><mattext texttype="text/plain">In the nucleus</mattext></material>
              </response_label>
            </render_choice>
          </response_lid>
        </presentation>
        <resprocessing>
          <outcomes>
            <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
          </outcomes>
          <respcondition continue="No">
            <conditionvar><varequal respident="response1">c1</varequal></conditionvar>
            <setvar action="Set" varname="SCORE">100</setvar>
          </respcondition>
        </resprocessing>
      </item>
    </section>
  </assessment>
</questestinterop>
"""

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _imscc_bytes() -> bytes:
    """Return a cartridge with a page, asset, assignment, quiz, and QTI."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("imsmanifest.xml", MANIFEST)
        archive.writestr("course_settings/module_meta.xml", MODULES)
        archive.writestr("wiki_content/lesson-1.html", PAGE)
        archive.writestr("gasg/assignment_settings.xml", ASSIGNMENT)
        archive.writestr("gquiz/assessment_meta.xml", QUIZ_META)
        archive.writestr("gquiz/assessment_qti.xml", QTI)
        archive.writestr("web_resources/diagram.png", PNG)
    return buf.getvalue()


class CatalogTabTests(unittest.TestCase):
    """Component tabs read from the shared library, not an unpacked tree."""

    def setUp(self) -> None:
        """Create an app, assign SBI3U, and attach a cartridge as IT."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
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
        self.cls = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="SBI3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self._login_it()
        rv = self.client.post(
            f"/it/offerings/{self.offering['id']}/module-pack",
            data={"module_pack": (io.BytesIO(_imscc_bytes()), "bio.imscc")},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self._login_staff()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_it(self) -> None:
        """Sign the client in as the bootstrap IT user."""
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _login_staff(self) -> None:
        """Sign the client in as the assigned teacher."""
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def test_tabs_render_in_course_nav(self) -> None:
        """All four component tabs appear alongside Modules and Syllabus."""
        rv = self.client.get(f"/staff/class/{self.cls['id']}")
        html = rv.get_data(as_text=True)
        for label in ("Pages", "Assignments", "Quizzes", "Question banks"):
            self.assertIn(f">{label}<", html)

    def test_pages_tab_lists_html_and_google_kinds(self) -> None:
        """The Pages catalog includes the wiki page and the Google deck."""
        data = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/pages"
        ).get_json()
        self.assertTrue(data["ok"])
        kinds = {item["kind"]: item["title"] for item in data["items"]}
        self.assertEqual(kinds.get("html"), "Lesson 1")
        self.assertEqual(kinds.get("gslides"), "Reference deck")

    def test_assignments_and_quizzes_tabs(self) -> None:
        """Assignment points and quiz question counts come from the import."""
        asg = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/assignments"
        ).get_json()
        self.assertEqual(asg["items"][0]["title"], "Task 1")
        self.assertEqual(asg["items"][0]["points"], 15.0)

        quiz = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/quizzes"
        ).get_json()
        self.assertEqual(quiz["items"][0]["title"], "Check-in")
        self.assertEqual(quiz["items"][0]["question_count"], 2)

    def test_question_bank_tab_and_questions(self) -> None:
        """Banks list with counts, and questions load for one bank."""
        banks = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/question-banks"
        ).get_json()
        self.assertEqual(banks["items"][0]["question_count"], 2)
        bank_id = banks["items"][0]["id"]
        questions = self.client.get(
            f"/api/staff/class/{self.cls['id']}/question-bank/{bank_id}"
        ).get_json()
        self.assertEqual(questions["questions"][0]["item_type"], "essay_question")
        self.assertIn(
            "photosynthesis",
            questions["questions"][0]["payload"]["stem_html"].lower(),
        )
        choices = questions["questions"][1]["payload"]["choices"]
        self.assertEqual(
            [(c["html"], c["correct"]) for c in choices],
            [("In the chloroplast", True), ("In the nucleus", False)],
        )

    def _quiz_preview_html(self) -> str:
        """Fetch the rendered quiz preview page for the imported quiz."""
        quizzes = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/quizzes"
        ).get_json()
        quiz_id = quizzes["items"][0]["id"]
        rv = self.client.get(
            f"/staff/class/{self.cls['id']}/component/quiz/{quiz_id}"
        )
        self.assertEqual(rv.status_code, 200)
        return rv.get_data(as_text=True)

    def test_quiz_preview_lists_stems_and_choices(self) -> None:
        """The quiz page renders each stem plus its options, correct one marked."""
        html = self._quiz_preview_html()
        self.assertIn("Explain photosynthesis.", html)
        self.assertIn("Where does photosynthesis happen?", html)
        self.assertIn("In the chloroplast", html)
        self.assertIn("In the nucleus", html)
        self.assertIn("Question 1", html)
        self.assertIn("Question 2", html)
        self.assertIn("Multiple choice", html)
        self.assertIn("Essay", html)
        correct = html[html.index("In the chloroplast") - 200 : html.index("In the chloroplast")]
        self.assertIn('class="correct"', correct)

    def test_quiz_preview_resolves_stem_images_through_blobs(self) -> None:
        """A ``$IMS-CC-FILEBASE$`` image in a stem becomes a servable URL."""
        html = self._quiz_preview_html()
        self.assertNotIn("$IMS-CC-FILEBASE$", html)
        expected = f"/staff/class/{self.cls['id']}/module-files/web_resources/diagram.png"
        self.assertIn(expected, html)
        rv = self.client.get(expected)
        self.assertEqual(rv.status_code, 200)

    def test_question_bank_preview_page_shows_questions(self) -> None:
        """The Question banks tab previews real stems, not just a count."""
        banks = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/question-banks"
        ).get_json()
        bank_id = banks["items"][0]["id"]
        rv = self.client.get(
            f"/staff/class/{self.cls['id']}/component/bank/{bank_id}"
        )
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertIn("Explain photosynthesis.", html)
        self.assertIn("In the chloroplast", html)

    def test_module_items_link_to_each_component_type(self) -> None:
        """Modules nav resolves page, assignment, quiz, and Google URL items."""
        nav = self.client.get(
            f"/api/staff/class/{self.cls['id']}/modules"
        ).get_json()
        types = [i["component_type"] for i in nav["modules"][0]["items"]]
        self.assertEqual(types, ["page", "assignment", "quiz", "page"])
        for item in nav["modules"][0]["items"]:
            rv = self.client.get(
                f"/staff/class/{self.cls['id']}/module-item?item={item['id']}"
            )
            self.assertEqual(rv.status_code, 200, item["title"])

    def test_page_asset_serves_from_blob_store(self) -> None:
        """A page's rewritten asset URL resolves through library_files."""
        rv = self.client.get(
            f"/staff/class/{self.cls['id']}/module-files/web_resources/diagram.png"
        )
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_data(), PNG)

    def test_unpacked_tree_is_reclaimed_after_ingest(self) -> None:
        """Once components are stored, the expanded cartridge tree is deleted."""
        root = Path(self.tmp.name)
        nav = self.client.get(f"/api/staff/class/{self.cls['id']}/modules")
        self.assertFalse(nav.get_json().get("empty"))
        trees = [p for p in root.rglob("unpacked") if p.is_dir()]
        self.assertEqual(trees, [], f"unpacked tree survived: {trees}")
        wiki = list(root.rglob("wiki_content"))
        self.assertEqual(wiki, [])

    def test_component_preview_is_owner_scoped(self) -> None:
        """Another teacher cannot preview this class's components."""
        other = self.school.register_staff("other@gmail.com")
        self.assertTrue(other)
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=other@gmail.com&name=O")
        user = self.school.get_user_by_email("other@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        rv = self.client.get(
            f"/api/staff/class/{self.cls['id']}/components/pages"
        )
        self.assertEqual(rv.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
