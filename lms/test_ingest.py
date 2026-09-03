#!/usr/bin/env python3
"""Tests for course-code-generic IMSCC ingest into normalized components."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from ingest import classify_url, ingest_library  # noqa: E402
from school_db import LovesDB  # noqa: E402

MCF3M_UNPACKED = REPO_ROOT / "lms" / "fixtures" / "mcf3m-unpacked"  # optional local pack

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="m1" xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
  <resources>
    <resource identifier="res-page" type="webcontent" href="wiki_content/intro.html">
      <file href="wiki_content/intro.html"/>
    </resource>
    <resource identifier="res-assign" type="associatedcontent" href="gassign/assignment_settings.xml">
      <file href="gassign/assignment_settings.xml"/>
    </resource>
    <resource identifier="res-quiz" type="imsqti_xmlv1p2/imscc_xmlv1p1/assessment">
      <file href="gquiz/assessment_meta.xml"/>
    </resource>
  </resources>
</manifest>
"""

MODULE_META = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="mod-1">
    <title>Unit 1: Cells</title>
    <position>1</position>
    <items>
      <item identifier="it-1">
        <content_type>WikiPage</content_type>
        <title>Intro</title>
        <identifierref>res-page</identifierref>
        <position>1</position>
      </item>
      <item identifier="it-2">
        <content_type>Assignment</content_type>
        <title>Lab writeup</title>
        <identifierref>res-assign</identifierref>
        <position>2</position>
      </item>
      <item identifier="it-3">
        <content_type>Quizzes::Quiz</content_type>
        <title>Unit 1 Quiz</title>
        <identifierref>gquiz</identifierref>
        <position>3</position>
      </item>
      <item identifier="it-4">
        <content_type>ExternalUrl</content_type>
        <title>Slide deck</title>
        <url>https://docs.google.com/presentation/d/abc/edit</url>
        <position>4</position>
      </item>
      <item identifier="it-5">
        <content_type>ContextModuleSubHeader</content_type>
        <title>Readings</title>
        <position>5</position>
      </item>
    </items>
  </module>
</modules>
"""

ASSIGNMENT = """<?xml version="1.0" encoding="UTF-8"?>
<assignment identifier="gassign" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Lab writeup</title>
  <points_possible>20.0</points_possible>
  <submission_types>online_upload</submission_types>
</assignment>
"""

QUIZ_META = """<?xml version="1.0" encoding="UTF-8"?>
<quiz identifier="gquiz" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Unit 1 Quiz</title>
  <quiz_type>assignment</quiz_type>
  <points_possible>10.0</points_possible>
</quiz>
"""

QTI = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="gquiz" title="Unit 1 Quiz">
    <section ident="root_section">
      <item ident="q1" title="Cell wall">
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>multiple_choice_question</fieldentry>
            </qtimetadatafield>
            <qtimetadatafield>
              <fieldlabel>points_possible</fieldlabel>
              <fieldentry>2.0</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
        <presentation>
          <material><mattext texttype="text/html">What is a cell wall?</mattext></material>
          <response_lid ident="response1" rcardinality="Single">
            <render_choice>
              <response_label ident="a1">
                <material><mattext texttype="text/plain">A rigid outer layer</mattext></material>
              </response_label>
              <response_label ident="a2">
                <material><mattext texttype="text/plain">A type of enzyme</mattext></material>
              </response_label>
              <response_label ident="a3">
                <material><mattext texttype="text/html">&lt;em&gt;An organelle&lt;/em&gt;</mattext></material>
              </response_label>
            </render_choice>
          </response_lid>
        </presentation>
        <resprocessing>
          <outcomes>
            <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
          </outcomes>
          <respcondition continue="No">
            <conditionvar><varequal respident="response1">a1</varequal></conditionvar>
            <setvar action="Set" varname="SCORE">100</setvar>
          </respcondition>
          <respcondition continue="Yes">
            <conditionvar><varequal respident="response1">a2</varequal></conditionvar>
            <setvar action="Set" varname="SCORE">0</setvar>
          </respcondition>
        </resprocessing>
      </item>
      <item ident="q2" title="Osmosis">
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>short_answer_question</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
        <presentation>
          <material><mattext texttype="text/html">Define osmosis.</mattext></material>
          <response_str ident="response1" rcardinality="Single">
            <render_fib><response_label ident="answer1"/></render_fib>
          </response_str>
        </presentation>
        <resprocessing>
          <outcomes>
            <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
          </outcomes>
          <respcondition continue="No">
            <conditionvar><varequal respident="response1">diffusion of water</varequal></conditionvar>
            <setvar action="Set" varname="SCORE">100</setvar>
          </respcondition>
        </resprocessing>
      </item>
    </section>
  </assessment>
</questestinterop>
"""

# A Canvas quiz that draws from banks: the Common Cartridge copy is emptied and
# the native copy carries the group draw plus one pinned bank item.
THIN_QTI = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="gbanked" title="Unit 2 Test">
    <section ident="root_section"/>
  </assessment>
</questestinterop>
"""

NATIVE_QTI = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="gbanked" title="Unit 2 Test">
    <section ident="root_section">
      <item ident="own1" title="Reflect">
        <itemmetadata><qtimetadata><qtimetadatafield>
          <fieldlabel>question_type</fieldlabel>
          <fieldentry>essay_question</fieldentry>
        </qtimetadatafield></qtimetadata></itemmetadata>
        <presentation>
          <material><mattext texttype="text/html">Explain diffusion.</mattext></material>
        </presentation>
      </item>
      <bankentry_item sourcebank_ref="gbank" item_ref="bank2"
                      points_possible="1.0" entry_type="Item"/>
      <section ident="grp1" title="Cell group">
        <selection_ordering>
          <selection>
            <sourcebank_ref>gbank</sourcebank_ref>
            <selection_number>1</selection_number>
            <selection_extension><points_per_item>3.0</points_per_item></selection_extension>
          </selection>
        </selection_ordering>
      </section>
    </section>
  </assessment>
</questestinterop>
"""

BANK_QTI = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <objectbank ident="gbank">
    <qtimetadata>
      <qtimetadatafield>
        <fieldlabel>bank_title</fieldlabel>
        <fieldentry>Unit 2 pool</fieldentry>
      </qtimetadatafield>
    </qtimetadata>
    <item ident="bank1" title="Mitochondria">
      <itemmetadata><qtimetadata><qtimetadatafield>
        <fieldlabel>question_type</fieldlabel>
        <fieldentry>true_false_question</fieldentry>
      </qtimetadatafield></qtimetadata></itemmetadata>
      <presentation>
        <material><mattext texttype="text/html">Mitochondria make ATP.</mattext></material>
        <response_lid ident="response1" rcardinality="Single">
          <render_choice>
            <response_label ident="t">
              <material><mattext texttype="text/plain">True</mattext></material>
            </response_label>
            <response_label ident="f">
              <material><mattext texttype="text/plain">False</mattext></material>
            </response_label>
          </render_choice>
        </response_lid>
      </presentation>
      <resprocessing>
        <outcomes>
          <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
        </outcomes>
        <respcondition continue="No">
          <conditionvar><varequal respident="response1">t</varequal></conditionvar>
          <setvar action="Set" varname="SCORE">100</setvar>
        </respcondition>
      </resprocessing>
    </item>
    <item ident="bank2" title="Ribosomes">
      <itemmetadata><qtimetadata><qtimetadatafield>
        <fieldlabel>question_type</fieldlabel>
        <fieldentry>essay_question</fieldentry>
      </qtimetadatafield></qtimetadata></itemmetadata>
      <presentation>
        <material><mattext texttype="text/html">Describe ribosomes.</mattext></material>
      </presentation>
    </item>
  </objectbank>
</questestinterop>
"""

BANKED_QUIZ_META = """<?xml version="1.0" encoding="UTF-8"?>
<quiz identifier="gbanked" xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <title>Unit 2 Test</title>
  <quiz_type>assignment</quiz_type>
  <description>&lt;p&gt;Closed book.&lt;/p&gt;</description>
</quiz>
"""

PAGE_HTML = """<html><head>
<meta name="identifier" content="page-intro"/>
<meta name="title" content="Course Intro"/>
<title>Course Intro</title></head>
<body><p>Welcome.</p></body></html>
"""

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def build_cartridge(root: Path) -> Path:
    """Write a small course-code-agnostic cartridge tree.

    Args:
        root: Directory that will hold the unpacked cartridge.

    Returns:
        The cartridge root path.
    """
    (root / "wiki_content").mkdir(parents=True, exist_ok=True)
    (root / "course_settings").mkdir(parents=True, exist_ok=True)
    (root / "gassign").mkdir(parents=True, exist_ok=True)
    (root / "gquiz").mkdir(parents=True, exist_ok=True)
    (root / "web_resources").mkdir(parents=True, exist_ok=True)
    (root / "imsmanifest.xml").write_text(MANIFEST, encoding="utf-8")
    (root / "course_settings" / "module_meta.xml").write_text(
        MODULE_META, encoding="utf-8"
    )
    (root / "wiki_content" / "intro.html").write_text(PAGE_HTML, encoding="utf-8")
    (root / "gassign" / "assignment_settings.xml").write_text(
        ASSIGNMENT, encoding="utf-8"
    )
    (root / "gquiz" / "assessment_meta.xml").write_text(QUIZ_META, encoding="utf-8")
    (root / "gquiz" / "assessment_qti.xml").write_text(QTI, encoding="utf-8")
    (root / "web_resources" / "handout.pdf").write_bytes(MINIMAL_PDF)
    return root


def add_banked_quiz(root: Path) -> Path:
    """Add a bank-drawing quiz with an emptied Common Cartridge copy.

    Mirrors how Canvas exports a quiz built from question banks: the in-folder
    ``assessment_qti.xml`` has an empty ``root_section`` and the real content
    lives in ``non_cc_assessments/`` alongside a standalone ``objectbank``.

    Args:
        root: Existing unpacked cartridge root.

    Returns:
        The cartridge root path.
    """
    (root / "gbanked").mkdir(parents=True, exist_ok=True)
    (root / "non_cc_assessments").mkdir(parents=True, exist_ok=True)
    (root / "gbanked" / "assessment_meta.xml").write_text(
        BANKED_QUIZ_META, encoding="utf-8"
    )
    (root / "gbanked" / "assessment_qti.xml").write_text(THIN_QTI, encoding="utf-8")
    (root / "non_cc_assessments" / "gbanked.xml.qti").write_text(
        NATIVE_QTI, encoding="utf-8"
    )
    (root / "non_cc_assessments" / "gbank.xml.qti").write_text(
        BANK_QTI, encoding="utf-8"
    )
    return root


class ClassifyUrlTests(unittest.TestCase):
    """External URL classification for Google Workspace pages."""

    def test_slides_and_docs_and_other(self) -> None:
        """Slides, Docs, and non-Google URLs map to distinct kinds."""
        self.assertEqual(
            classify_url("https://docs.google.com/presentation/d/x/edit"), "gslides"
        )
        self.assertEqual(
            classify_url("https://docs.google.com/document/d/x/edit"), "gdoc"
        )
        self.assertIsNone(classify_url("https://example.com/notes"))


class IngestTests(unittest.TestCase):
    """Generic cartridge ingest produces components and a linked outline."""

    def setUp(self) -> None:
        """Create an isolated database, data volume, and cartridge."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = LovesDB(self.root / "lloves.sqlite")
        self.library = self.db.create_library("SBI3U", origin="upload")
        self.unpacked = build_cartridge(self.root / "unpacked")

    def tearDown(self) -> None:
        """Close sqlite and remove temporary files."""
        self.db.close()
        self.tmp.cleanup()

    def _library_id(self) -> int:
        """Return the id of the library under test."""
        return int(self.library["id"])

    def _payload(self, import_key: str) -> dict:
        """Return the decoded payload of one stored question.

        Args:
            import_key: ``questions.import_key`` (the QTI item ident).
        """
        row = self.db.conn.execute(
            "SELECT payload_json FROM questions WHERE import_key = ?",
            (import_key,),
        ).fetchone()
        assert row is not None, import_key
        return json.loads(row["payload_json"])

    def test_ingest_creates_components(self) -> None:
        """Pages, assignment, quiz, bank, and questions are all written."""
        summary = ingest_library(
            self.db, self.root, self._library_id(), self.unpacked
        )
        self.assertEqual(summary["assignments"], 1)
        self.assertEqual(summary["quizzes"], 1)
        self.assertEqual(summary["question_banks"], 1)
        self.assertEqual(summary["questions"], 2)
        self.assertEqual(summary["outlines"], 1)
        self.assertEqual(summary["items"], 5)

        kinds = {
            row["kind"]: row["title"]
            for row in self.db.conn.execute(
                "SELECT kind, title FROM pages WHERE library_id = ?",
                (self._library_id(),),
            )
        }
        self.assertEqual(kinds.get("html"), "Course Intro")
        self.assertEqual(kinds.get("pdf"), "handout")
        self.assertEqual(kinds.get("gslides"), "Slide deck")

    def test_outline_items_link_to_components(self) -> None:
        """Every non-header item resolves to a stored component id."""
        ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        rows = list(
            self.db.conn.execute(
                """
                SELECT mi.title, mi.component_type, mi.component_id
                FROM module_items mi
                JOIN module_outlines mo ON mo.id = mi.outline_id
                WHERE mo.library_id = ?
                ORDER BY mi.position
                """,
                (self._library_id(),),
            )
        )
        types = [row["component_type"] for row in rows]
        self.assertEqual(
            types, ["page", "assignment", "quiz", "page", "header"]
        )
        for row in rows[:4]:
            self.assertIsNotNone(row["component_id"], row["title"])

    def test_pdf_page_points_at_shared_blob(self) -> None:
        """A PDF page references blob bytes stored once in the volume."""
        ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        row = self.db.conn.execute(
            "SELECT blob_sha FROM pages WHERE kind = 'pdf' AND library_id = ?",
            (self._library_id(),),
        ).fetchone()
        self.assertIsNotNone(row["blob_sha"])
        blob = self.db.get_blob(row["blob_sha"])
        self.assertEqual(blob["bytes"], len(MINIMAL_PDF))

    def test_multiple_choice_question_keeps_choices_and_answer(self) -> None:
        """A choice question stores every option with the correct one flagged."""
        ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        payload = self._payload("q1")
        self.assertIn("cell wall", payload["stem_html"].lower())
        self.assertEqual(payload["points_possible"], 2.0)
        self.assertEqual(
            [(c["id"], c["correct"]) for c in payload["choices"]],
            [("a1", True), ("a2", False), ("a3", False)],
        )
        self.assertEqual(payload["correct_ids"], ["a1"])
        self.assertEqual(payload["position"], 1)
        # Plain-text choices are escaped, HTML choices pass through.
        self.assertEqual(payload["choices"][0]["html"], "A rigid outer layer")
        self.assertEqual(payload["choices"][2]["html"], "<em>An organelle</em>")

    def test_short_answer_question_keeps_accepted_text(self) -> None:
        """A free-text question stores its accepted answers, not choices."""
        ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        payload = self._payload("q2")
        self.assertEqual(payload["correct_answers"], ["diffusion of water"])
        self.assertNotIn("choices", payload)
        row = self.db.conn.execute(
            "SELECT item_type FROM questions WHERE import_key = 'q2'"
        ).fetchone()
        self.assertEqual(row["item_type"], "short_answer_question")

    def test_removed_questions_are_pruned_on_reingest(self) -> None:
        """A question dropped from the cartridge disappears from its bank."""
        ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        trimmed = QTI.replace(
            QTI[QTI.index('<item ident="q2"') : QTI.index("</section>")], ""
        )
        (self.unpacked / "gquiz" / "assessment_qti.xml").write_text(
            trimmed, encoding="utf-8"
        )
        summary = ingest_library(
            self.db, self.root, self._library_id(), self.unpacked
        )
        self.assertEqual(summary["questions"], 1)
        keys = [
            row["import_key"]
            for row in self.db.conn.execute("SELECT import_key FROM questions")
        ]
        self.assertEqual(keys, ["q1"])

    def test_ingest_is_idempotent(self) -> None:
        """Re-running ingest refreshes rows instead of duplicating them."""
        first = ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        second = ingest_library(self.db, self.root, self._library_id(), self.unpacked)
        self.assertEqual(first["items"], second["items"])
        self.assertEqual(first["questions"], second["questions"])
        self.assertEqual(first["question_banks"], second["question_banks"])
        banks = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM question_banks WHERE library_id = ?",
            (self._library_id(),),
        ).fetchone()["n"]
        self.assertEqual(banks, first["question_banks"])
        questions = self.db.conn.execute(
            """
            SELECT COUNT(*) AS n FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ?
            """,
            (self._library_id(),),
        ).fetchone()["n"]
        self.assertEqual(questions, first["questions"])
        for table in ("pages", "assignments", "quizzes", "module_items"):
            column = "outline_id" if table == "module_items" else "library_id"
            if column == "library_id":
                count = self.db.conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE library_id = ?",
                    (self._library_id(),),
                ).fetchone()["n"]
            else:
                count = self.db.conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM {table} t
                    JOIN module_outlines mo ON mo.id = t.outline_id
                    WHERE mo.library_id = ?
                    """,
                    (self._library_id(),),
                ).fetchone()["n"]
            self.assertEqual(count, first[_count_key(table)], table)

    def test_missing_directory_is_reported_not_raised(self) -> None:
        """A absent cartridge yields an empty summary with a skip note."""
        summary = ingest_library(
            self.db, self.root, self._library_id(), self.root / "nope"
        )
        self.assertEqual(summary["pages"], 0)
        self.assertTrue(summary["skipped"])


def _count_key(table: str) -> str:
    """Map a component table name to its summary count key."""
    return {"module_items": "items"}.get(table, table)


class BankedQuizIngestTests(unittest.TestCase):
    """Quizzes that draw from Canvas question banks pull the real items."""

    def setUp(self) -> None:
        """Create a cartridge whose second quiz draws from an object bank."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = LovesDB(self.root / "lloves.sqlite")
        self.library = self.db.create_library("SBI3U", origin="upload")
        self.unpacked = add_banked_quiz(build_cartridge(self.root / "unpacked"))
        self.summary = ingest_library(
            self.db, self.root, int(self.library["id"]), self.unpacked
        )

    def tearDown(self) -> None:
        """Close sqlite and remove temporary files."""
        self.db.close()
        self.tmp.cleanup()

    def _bank_questions(self, import_key: str) -> list[dict]:
        """Return questions of one bank in stored order.

        Args:
            import_key: ``question_banks.import_key``.
        """
        return [
            dict(row)
            for row in self.db.conn.execute(
                """
                SELECT q.import_key, q.item_type, q.payload_json
                FROM questions q
                JOIN question_banks b ON b.id = q.bank_id
                WHERE b.library_id = ? AND b.import_key = ?
                ORDER BY q.id
                """,
                (int(self.library["id"]), import_key),
            )
        ]

    def test_bank_reference_expands_into_the_quiz(self) -> None:
        """The quiz's own item, its pinned item, and the drawn pool all land."""
        rows = self._bank_questions("bank:gbanked")
        self.assertEqual(
            [row["import_key"] for row in rows], ["own1", "bank2", "bank1"]
        )
        positions = [json.loads(row["payload_json"])["position"] for row in rows]
        self.assertEqual(positions, [1, 2, 3])
        pinned = json.loads(rows[1]["payload_json"])
        self.assertEqual(pinned["bank_title"], "Unit 2 pool")

    def test_object_bank_becomes_its_own_browsable_bank(self) -> None:
        """The standalone Canvas bank is stored under its own title."""
        bank = self.db.conn.execute(
            "SELECT title FROM question_banks WHERE import_key = 'objectbank:gbank'"
        ).fetchone()
        self.assertEqual(bank["title"], "Unit 2 pool")
        rows = self._bank_questions("objectbank:gbank")
        self.assertEqual([row["import_key"] for row in rows], ["bank1", "bank2"])

    def test_true_false_answer_survives_the_bank_hop(self) -> None:
        """A bank item keeps its choices and answer key when pulled into a quiz."""
        payload = json.loads(self._bank_questions("bank:gbanked")[2]["payload_json"])
        self.assertEqual(
            [(c["html"], c["correct"]) for c in payload["choices"]],
            [("True", True), ("False", False)],
        )

    def test_thin_cartridge_copy_does_not_double_questions(self) -> None:
        """Only one QTI copy is used, so re-ingest keeps the count stable."""
        again = ingest_library(
            self.db, self.root, int(self.library["id"]), self.unpacked
        )
        self.assertEqual(again["questions"], self.summary["questions"])
        total = self.db.conn.execute(
            """
            SELECT COUNT(*) AS n FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ?
            """,
            (int(self.library["id"]),),
        ).fetchone()["n"]
        self.assertEqual(total, self.summary["questions"])

    def test_quiz_settings_record_the_group_draw(self) -> None:
        """The quiz keeps its description and the size of each bank draw."""
        row = self.db.conn.execute(
            "SELECT settings_json FROM quizzes WHERE import_key = 'gbanked'"
        ).fetchone()
        settings = json.loads(row["settings_json"])
        self.assertIn("Closed book", settings["description_html"])
        group = settings["question_groups"][0]
        self.assertEqual(group["pick"], 1)
        self.assertEqual(group["available"], 2)
        self.assertEqual(group["points_per_item"], 3.0)


@unittest.skipUnless(
    MCF3M_UNPACKED.is_dir(), "MCF3M exemplar cartridge not unpacked locally"
)
class RealCartridgeSmokeTests(unittest.TestCase):
    """Smoke test against the MCF3M export used as the exemplar course."""

    def setUp(self) -> None:
        """Create an isolated database and data volume."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = LovesDB(self.root / "lloves.sqlite")
        self.library = self.db.create_library("MCF3M", origin="template")

    def tearDown(self) -> None:
        """Close sqlite and remove temporary files."""
        self.db.close()
        self.tmp.cleanup()

    def test_exemplar_ingest_populates_outline(self) -> None:
        """The real export yields modules, pages, and linked items."""
        summary = ingest_library(
            self.db, self.root, int(self.library["id"]), MCF3M_UNPACKED
        )
        self.assertGreater(summary["outlines"], 0)
        self.assertGreater(summary["pages"], 0)
        self.assertGreater(summary["items"], 0)
        unresolved = self.db.conn.execute(
            """
            SELECT COUNT(*) AS n FROM module_items mi
            JOIN module_outlines mo ON mo.id = mi.outline_id
            WHERE mo.library_id = ? AND mi.component_type = 'unsupported'
            """,
            (int(self.library["id"]),),
        ).fetchone()["n"]
        self.assertLess(unresolved, summary["items"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
