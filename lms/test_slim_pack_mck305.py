#!/usr/bin/env python3
"""MCK-305: slim course packs and a lean, resumable import."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import tracemalloc
import unittest
import zipfile
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from ingest import INGEST_INSERT_BATCH, ingest_library  # noqa: E402
from school_db import LovesDB  # noqa: E402
from slim_pack import build_slim_imscc, materialize_import_tree  # noqa: E402

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _manifest() -> str:
    """Manifest that lists kept files and files the slim pack must drop."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="m1" xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
  <resources>
    <resource identifier="res-page" type="webcontent" href="wiki_content/intro.html">
      <file href="wiki_content/intro.html"/>
    </resource>
    <resource identifier="res-quiz" type="imsqti_xmlv1p2/imscc_xmlv1p1/assessment" href="gquiz/assessment_meta.xml">
      <file href="gquiz/assessment_meta.xml"/>
      <file href="gquiz/assessment_qti.xml"/>
      <file href="gquiz/question_metadata.xml"/>
    </resource>
    <resource identifier="res-assign" type="associatedcontent" href="gassign/assignment_settings.xml">
      <file href="gassign/assignment_settings.xml"/>
    </resource>
    <resource identifier="res-pdf" type="webcontent" href="web_resources/handout.pdf">
      <file href="web_resources/handout.pdf"/>
    </resource>
    <resource identifier="res-video" type="webcontent" href="web_resources/lecture.mp4">
      <file href="web_resources/lecture.mp4"/>
    </resource>
  </resources>
</manifest>
"""


def _module_meta() -> str:
    """One module linking the page, quiz, assignment, and PDF."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="mod-1">
    <title>Unit 1</title>
    <position>1</position>
    <items>
      <item identifier="it-1">
        <content_type>WikiPage</content_type>
        <title>Intro</title>
        <identifierref>res-page</identifierref>
        <position>1</position>
      </item>
      <item identifier="it-2">
        <content_type>Quizzes::Quiz</content_type>
        <title>Unit quiz</title>
        <identifierref>res-quiz</identifierref>
        <position>2</position>
      </item>
      <item identifier="it-3">
        <content_type>Assignment</content_type>
        <title>Lab</title>
        <identifierref>res-assign</identifierref>
        <position>3</position>
      </item>
      <item identifier="it-4">
        <content_type>Attachment</content_type>
        <title>Handout</title>
        <identifierref>res-pdf</identifierref>
        <position>4</position>
      </item>
    </items>
  </module>
</modules>
"""


def _qti() -> str:
    """One multiple-choice item that references a single image."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="gquiz" title="Unit quiz">
    <section ident="root">
      <item ident="q1" title="Graph">
        <itemmetadata><qtimetadata><qtimetadatafield>
          <fieldlabel>question_type</fieldlabel>
          <fieldentry>multiple_choice_question</fieldentry>
        </qtimetadatafield></qtimetadata></itemmetadata>
        <presentation>
          <material><mattext texttype="text/html">&lt;img src="$IMS-CC-FILEBASE$/graph.png"&gt;</mattext></material>
          <response_lid ident="response1" rcardinality="Single">
            <render_choice>
              <response_label ident="a1"><material><mattext texttype="text/plain">up</mattext></material></response_label>
            </render_choice>
          </response_lid>
        </presentation>
      </item>
    </section>
  </assessment>
</questestinterop>
"""


def _cartridge_bytes(*, media_bytes: int = 4096, wrapper: str = "export/") -> bytes:
    """Build a small cartridge with banks, a quiz, and unreferenced media.

    Args:
        media_bytes: Size of the unreferenced video member.
        wrapper: Prefix inside the zip. The slim pack and the importer both
            strip a single wrapper so files land at the root.
    """
    prefix = wrapper
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix + "imsmanifest.xml", _manifest())
        archive.writestr(prefix + "course_settings/module_meta.xml", _module_meta())
        archive.writestr(
            prefix + "course_settings/course_settings.xml",
            "<course><title>Unit course</title><course_code>SBI3U</course_code></course>",
        )
        archive.writestr(
            prefix + "course_settings/assignment_groups.xml",
            "<assignment_groups/>",
        )
        archive.writestr(prefix + "course_settings/rubrics.xml", "<rubrics/>")
        archive.writestr(
            prefix + "course_settings/learning_outcomes.xml",
            "<learning_outcomes/>",
        )
        archive.writestr(
            prefix + "wiki_content/intro.html",
            "<html><head><title>Intro</title></head><body><p>Welcome.</p></body></html>",
        )
        archive.writestr(
            prefix + "gquiz/assessment_meta.xml",
            '<quiz identifier="gquiz"><title>Unit quiz</title></quiz>',
        )
        archive.writestr(prefix + "gquiz/assessment_qti.xml", _qti())
        archive.writestr(
            prefix + "gquiz/question_metadata.xml",
            "<question_metadata><question identifier=\"q1\"/></question_metadata>",
        )
        archive.writestr(
            prefix + "non_cc_assessments/gbank.xml.qti",
            "<questestinterop><objectbank ident=\"gbank\"/></questestinterop>",
        )
        archive.writestr(
            prefix + "gassign/assignment_settings.xml",
            '<assignment identifier="gassign"><title>Lab</title><points_possible>5</points_possible></assignment>',
        )
        archive.writestr(prefix + "gassign/lab.html", "<p>Write up.</p>")
        archive.writestr(prefix + "web_resources/graph.png", PNG)
        archive.writestr(prefix + "web_resources/unused.png", PNG)
        archive.writestr(prefix + "web_resources/handout.pdf", PDF)
        info = zipfile.ZipInfo(prefix + "web_resources/lecture.mp4")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, os.urandom(media_bytes))
    return buf.getvalue()


def _tree_bytes(root: Path) -> int:
    """Return the total size of files under ``root``.

    Args:
        root: Directory to measure.
    """
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


class SlimPackBuilderTests(unittest.TestCase):
    """The builder keeps referenced files and drops only unreferenced ones."""

    def test_slim_pack_keeps_import_files_at_zip_root(self) -> None:
        """Slim output keeps the outline, banks, assignment, PDF, and image."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "full.imscc"
            dest = root / "slim.imscc"
            source.write_bytes(_cartridge_bytes())
            result = build_slim_imscc(source, dest)
            with zipfile.ZipFile(dest) as archive:
                names = set(archive.namelist())
            self.assertIn("imsmanifest.xml", names)
            self.assertNotIn("export/imsmanifest.xml", names)
            self.assertTrue(all(not name.startswith("export/") for name in names))
            for required in (
                "course_settings/module_meta.xml",
                "course_settings/course_settings.xml",
                "non_cc_assessments/gbank.xml.qti",
                "gquiz/assessment_meta.xml",
                "gquiz/assessment_qti.xml",
                "gquiz/question_metadata.xml",
                "gassign/assignment_settings.xml",
                "gassign/lab.html",
                "wiki_content/intro.html",
                "web_resources/graph.png",
                "web_resources/handout.pdf",
            ):
                self.assertIn(required, names, required)
            for dropped in (
                "course_settings/assignment_groups.xml",
                "course_settings/rubrics.xml",
                "course_settings/learning_outcomes.xml",
                "web_resources/unused.png",
                "web_resources/lecture.mp4",
            ):
                self.assertNotIn(dropped, names, dropped)
            self.assertEqual(set(result.kept), names)
            self.assertGreater(result.dropped_members, 0)
            with zipfile.ZipFile(dest) as archive:
                manifest = archive.read("imsmanifest.xml").decode("utf-8")
            self.assertNotIn("lecture.mp4", manifest)
            self.assertNotIn("assignment_groups.xml", manifest)
            self.assertNotIn("rubrics.xml", manifest)
            self.assertIn("wiki_content/intro.html", manifest)
            self.assertIn("gquiz/question_metadata.xml", manifest)
            self.assertIn("handout.pdf", manifest)
            self.assertIn("gassign/assignment_settings.xml", manifest)

    def test_slim_archive_still_imports_banks_and_referenced_image(self) -> None:
        """Importing the slim pack loads the quiz, the page, and the graph."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            slim = root / "slim.imscc"
            (root / "full.imscc").write_bytes(_cartridge_bytes(media_bytes=128))
            build_slim_imscc(root / "full.imscc", slim)
            out = root / "unpacked"
            materialize_import_tree(slim, out, clean=True)
            db = LovesDB(root / "lloves.sqlite")
            try:
                library = db.create_library("SBI3U", origin="upload")
                summary = ingest_library(db, root, int(library["id"]), out)
                paths = {
                    row["relpath"]
                    for row in db.conn.execute(
                        "SELECT relpath FROM library_files WHERE library_id = ?",
                        (int(library["id"]),),
                    )
                }
                items = {
                    row["title"]: row["component_type"]
                    for row in db.conn.execute(
                        """
                        SELECT mi.title, mi.component_type
                        FROM module_items mi
                        JOIN module_outlines mo ON mo.id = mi.outline_id
                        WHERE mo.library_id = ?
                        """,
                        (int(library["id"]),),
                    )
                }
            finally:
                db.close()
            self.assertGreaterEqual(summary["quizzes"], 1)
            self.assertGreaterEqual(summary["questions"], 1)
            self.assertGreaterEqual(summary["pages"], 1)
            self.assertGreaterEqual(summary["assignments"], 1)
            self.assertIn("web_resources/graph.png", paths)
            self.assertIn("web_resources/handout.pdf", paths)
            self.assertNotIn("web_resources/lecture.mp4", paths)
            self.assertNotIn("web_resources/unused.png", paths)
            self.assertEqual(items.get("Lab"), "assignment")
            self.assertEqual(items.get("Handout"), "page")
            self.assertNotIn("unsupported", items.values())


    def test_uploaded_media_path_keeps_the_space(self) -> None:
        """Canvas ``Uploaded Media/<uuid>`` paths are captured up to the quote."""
        from slim_pack import referenced_media_paths

        uid = "01234567-89ab-cdef-0123-456789abcdef"
        expected = f"web_resources/Uploaded Media/{uid}"
        samples = (
            f'<img src="$IMS-CC-FILEBASE$/Uploaded Media/{uid}">',
            f"<img src='%24IMS-CC-FILEBASE%24/Uploaded Media/{uid}'>",
            f'<a href="../web_resources/Uploaded Media/{uid}">notes</a>',
        )
        for sample in samples:
            found = referenced_media_paths(sample)
            self.assertEqual(found, {expected}, sample)
        unquoted = referenced_media_paths("$IMS-CC-FILEBASE$/graph.png")
        self.assertEqual(unquoted, {"web_resources/graph.png"})

    def test_slim_pack_keeps_every_referenced_file(self) -> None:
        """Referenced PDFs, docs, video, and extensionless images stay in the slim pack."""
        uid = "01234567-89ab-cdef-0123-456789abcdef"
        wiki = (
            "<html><head><title>Intro</title></head><body>"
            f'<img src="$IMS-CC-FILEBASE$/Uploaded Media/{uid}">'
            '<a href="$IMS-CC-FILEBASE$/guides/lab notes.pdf">pdf</a>'
            '<a href="$IMS-CC-FILEBASE$/guides/brief.docx">doc</a>'
            '<a href="$IMS-CC-FILEBASE$/clips/demo.mp4">video</a>'
            "</body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "full.imscc"
            buf = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(_cartridge_bytes(media_bytes=32))) as src, zipfile.ZipFile(
                buf, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for info in src.infolist():
                    payload = wiki.encode() if info.filename.endswith("wiki_content/intro.html") else src.read(info)
                    archive.writestr(info.filename, payload)
                archive.writestr(f"export/web_resources/Uploaded Media/{uid}", PNG)
                archive.writestr("export/web_resources/guides/lab notes.pdf", PDF)
                archive.writestr("export/web_resources/guides/brief.docx", b"docx")
                archive.writestr("export/web_resources/clips/demo.mp4", b"video")
                archive.writestr("export/web_resources/clips/unused.mp4", b"nope")
            source.write_bytes(buf.getvalue())
            dest = root / "slim.imscc"
            build_slim_imscc(source, dest)
            with zipfile.ZipFile(dest) as archive:
                names = set(archive.namelist())
            for required in (
                f"web_resources/Uploaded Media/{uid}",
                "web_resources/guides/lab notes.pdf",
                "web_resources/guides/brief.docx",
                "web_resources/clips/demo.mp4",
                "web_resources/handout.pdf",
                "gassign/assignment_settings.xml",
            ):
                self.assertIn(required, names, required)
            self.assertNotIn("web_resources/clips/unused.mp4", names)
            self.assertNotIn("web_resources/lecture.mp4", names)

    def test_cli_writes_slim_archive(self) -> None:
        """The helper CLI exits 0 and writes the slim archive."""
        from slim_imscc import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "full.imscc"
            dest = root / "slim.imscc"
            source.write_bytes(_cartridge_bytes(media_bytes=32, wrapper=""))
            code = main(["--imscc", str(source), "--out", str(dest)])
            self.assertEqual(code, 0)
            self.assertTrue(dest.is_file())
            with zipfile.ZipFile(dest) as archive:
                self.assertIn("imsmanifest.xml", archive.namelist())


class LeanImportTests(unittest.TestCase):
    """Import streams needed members and skips unreferenced media."""

    def test_materialize_skips_unreferenced_media_without_loading_it(self) -> None:
        """A multi-megabyte unreferenced video is not extracted or held in RAM."""
        media_bytes = 2_000_000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            imscc = root / "course.imscc"
            imscc.write_bytes(_cartridge_bytes(media_bytes=media_bytes))
            out = root / "unpacked"
            tracemalloc.start()
            try:
                written = materialize_import_tree(imscc, out, clean=True)
                _current, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertGreater(written, 0)
            self.assertTrue((out / "imsmanifest.xml").is_file())
            self.assertFalse((out / "export").exists())
            self.assertTrue((out / "gassign" / "assignment_settings.xml").is_file())
            self.assertTrue((out / "web_resources" / "graph.png").is_file())
            self.assertTrue((out / "web_resources" / "handout.pdf").is_file())
            self.assertFalse((out / "web_resources" / "lecture.mp4").exists())
            self.assertFalse((out / "web_resources" / "unused.png").exists())
            self.assertFalse((out / "course_settings" / "rubrics.xml").exists())
            self.assertLess(_tree_bytes(out), media_bytes // 2)
            self.assertLess(peak, media_bytes // 2)

    def test_full_pack_import_keeps_assignments_and_referenced_files(self) -> None:
        """A full cartridge still ingests assignments, banks, and referenced files."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            imscc = root / "course.imscc"
            imscc.write_bytes(_cartridge_bytes(media_bytes=8192))
            out = root / "unpacked"
            materialize_import_tree(imscc, out, clean=True)
            db = LovesDB(root / "lloves.sqlite")
            try:
                library = db.create_library("SBI3U", origin="upload")
                summary = ingest_library(db, root, int(library["id"]), out)
                self.assertGreaterEqual(summary["assignments"], 1)
                self.assertGreaterEqual(summary["quizzes"], 1)
                self.assertGreaterEqual(summary["questions"], 1)
                self.assertGreaterEqual(summary["outlines"], 1)
                paths = {
                    row["relpath"]
                    for row in db.conn.execute(
                        "SELECT relpath FROM library_files WHERE library_id = ?",
                        (int(library["id"]),),
                    )
                }
            finally:
                db.close()
            self.assertIn("web_resources/graph.png", paths)
            self.assertIn("web_resources/handout.pdf", paths)
            self.assertNotIn("web_resources/lecture.mp4", paths)
            self.assertNotIn("web_resources/unused.png", paths)

    def test_question_inserts_are_batched(self) -> None:
        """A bank of many questions commits in batches, not once per row."""
        import ingest as ingest_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unpacked = root / "unpacked"
            (unpacked / "course_settings").mkdir(parents=True)
            (unpacked / "gquiz").mkdir()
            (unpacked / "imsmanifest.xml").write_text(
                "<manifest><resources>"
                '<resource identifier="res-quiz" href="gquiz/assessment_meta.xml"/>'
                "</resources></manifest>",
                encoding="utf-8",
            )
            (unpacked / "course_settings" / "module_meta.xml").write_text(
                "<modules><module identifier=\"m1\"><title>Unit</title><position>1</position>"
                "<items><item identifier=\"i1\"><title>Quiz</title>"
                "<content_type>Quizzes::Quiz</content_type>"
                "<identifierref>res-quiz</identifierref><position>1</position>"
                "</item></items></module></modules>",
                encoding="utf-8",
            )
            (unpacked / "gquiz" / "assessment_meta.xml").write_text(
                '<quiz identifier="gquiz"><title>Quiz</title></quiz>',
                encoding="utf-8",
            )
            items = []
            for index in range(25):
                items.append(
                    f'<item ident="q{index}" title="Item {index}">'
                    "<itemmetadata><qtimetadata><qtimetadatafield>"
                    "<fieldlabel>question_type</fieldlabel>"
                    "<fieldentry>essay_question</fieldentry>"
                    "</qtimetadatafield></qtimetadata></itemmetadata>"
                    "<presentation><material><mattext texttype=\"text/plain\">"
                    f"Prompt {index}</mattext></material></presentation></item>"
                )
            (unpacked / "gquiz" / "assessment_qti.xml").write_text(
                "<questestinterop><assessment ident=\"gquiz\" title=\"Quiz\">"
                f"<section ident=\"root\">{''.join(items)}</section>"
                "</assessment></questestinterop>",
                encoding="utf-8",
            )
            db = LovesDB(root / "lloves.sqlite")
            begins = {"n": 0}
            commits = {"n": 0}

            def _trace(sql: str) -> None:
                """Count explicit transaction boundaries during ingest."""
                text = sql.strip().upper()
                if text == "BEGIN":
                    begins["n"] += 1
                elif text == "COMMIT":
                    commits["n"] += 1

            previous = ingest_mod.INGEST_INSERT_BATCH
            ingest_mod.INGEST_INSERT_BATCH = 10
            db.conn.set_trace_callback(_trace)
            try:
                library = db.create_library("SBI3U", origin="upload")
                summary = ingest_library(db, root, int(library["id"]), unpacked)
            finally:
                ingest_mod.INGEST_INSERT_BATCH = previous
                db.conn.set_trace_callback(None)
                db.close()
            self.assertEqual(summary["questions"], 25)
            self.assertGreaterEqual(begins["n"], 3)
            self.assertLess(begins["n"], 25)
            self.assertLess(commits["n"], 25)
            self.assertEqual(INGEST_INSERT_BATCH, 100)

    def test_size_cap_reports_a_clear_error(self) -> None:
        """An over-size upload names the ceiling instead of failing silently."""
        from modules import assert_imscc_within_size, configured_imscc_max_bytes

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "course.imscc"
            path.write_bytes(b"PK" + b"x" * 5000)
            with self.assertRaises(ValueError) as raised:
                assert_imscc_within_size(path, 100)
            message = str(raised.exception)
            self.assertIn("too large", message)
            self.assertIn("max", message)
        previous = os.environ.get("LLOVES_IMSCC_MAX_BYTES")
        try:
            os.environ["LLOVES_IMSCC_MAX_BYTES"] = "none"
            self.assertIsNone(configured_imscc_max_bytes())
            os.environ["LLOVES_IMSCC_MAX_BYTES"] = "4096"
            self.assertEqual(configured_imscc_max_bytes(), 4096)
        finally:
            if previous is None:
                os.environ.pop("LLOVES_IMSCC_MAX_BYTES", None)
            else:
                os.environ["LLOVES_IMSCC_MAX_BYTES"] = previous



    def test_unpack_rejects_an_archive_that_expands_past_the_cap(self) -> None:
        """A small zip whose uncompressed members exceed the cap writes nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "bomb.imscc"
            source.write_bytes(_cartridge_bytes(media_bytes=64))
            out = root / "unpacked"
            with self.assertRaises(ValueError) as raised:
                materialize_import_tree(
                    source,
                    out,
                    clean=True,
                    max_uncompressed_bytes=128,
                )
            self.assertIn("unpack limit", str(raised.exception))
            self.assertFalse(out.exists())
            written = [
                path for path in root.rglob("*") if path.is_file() and path != source
            ]
            self.assertEqual(written, [])

    def test_stream_stops_before_a_member_passes_the_remaining_budget(self) -> None:
        """Byte counting stops a member whose header under-reports its size."""
        from slim_pack import UncompressedLimit, _stream_member

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "one.zip"
            payload = b"x" * 400
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("imsmanifest.xml", payload)
            target = root / "out" / "imsmanifest.xml"
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaises(UncompressedLimit):
                    _stream_member(
                        archive, "imsmanifest.xml", target, budget=[50]
                    )
            self.assertFalse(target.exists())
            parts = list(root.rglob("*.part"))
            self.assertEqual(parts, [])


class ResumeImportTests(unittest.TestCase):
    """A dead installer retries from the cartridge already on disk."""

    def test_abandoned_install_resumes_from_saved_pack(self) -> None:
        """A busy status with a dead pid imports the saved pack on retry."""
        from app import create_app
        from instances import library_root
        from modules import read_pack_status, write_pack_status
        from pack_install import resume_interrupted_packs

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        school = app.config["SCHOOL_DB"]
        self.addCleanup(school.close)
        school.activate_from_semester_json()
        if school.get_ontario_course("SBI3U") is None:
            school.upsert_ontario_course(
                "SBI3U",
                "Biology, Grade 11, University Preparation",
                grade=11,
                pathway="U",
                expectations_status="unverified",
            )
        teacher = school.register_staff("packtest@gmail.com")
        offering = school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="SBI3U"
        )
        library = school.create_library("SBI3U", origin="upload")
        school.attach_library(int(offering["id"]), int(library["id"]))
        dest = library_root(root, int(library["id"]))
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "course.imscc").write_bytes(_cartridge_bytes(media_bytes=1024))
        write_pack_status(dest, stage="ingest", detail="Loading modules…")
        status_path = dest / "install_status.json"
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        payload["pid"] = 999_999_999
        payload["updated_at"] = 1
        status_path.write_text(json.dumps(payload), encoding="utf-8")
        visible = read_pack_status(dest)
        self.assertEqual(visible["stage"], "queued")
        self.assertIn("resume", visible["detail"])
        self.assertNotIn("Upload the .imscc again", visible["detail"])
        retried = resume_interrupted_packs(school)
        self.assertEqual(retried, 1)
        outlines = school.conn.execute(
            "SELECT COUNT(*) AS n FROM module_outlines WHERE library_id = ?",
            (int(library["id"]),),
        ).fetchone()["n"]
        self.assertGreater(outlines, 0)
        assignments = school.conn.execute(
            "SELECT COUNT(*) AS n FROM assignments WHERE library_id = ?",
            (int(library["id"]),),
        ).fetchone()["n"]
        self.assertGreater(assignments, 0)
        paths = {
            row["relpath"]
            for row in school.conn.execute(
                "SELECT relpath FROM library_files WHERE library_id = ?",
                (int(library["id"]),),
            )
        }
        self.assertNotIn("web_resources/lecture.mp4", paths)
        self.assertIn("web_resources/graph.png", paths)
        self.assertFalse((dest / "unpacked").exists())
        again = resume_interrupted_packs(school)
        self.assertEqual(again, 0)
        done = read_pack_status(dest)
        self.assertEqual(done["stage"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BootResumeTests(unittest.TestCase):
    """Pack resume must not block application startup."""

    def test_schedule_pack_resume_returns_before_the_scan_finishes(self) -> None:
        """The boot helper starts a daemon thread and returns immediately."""
        import threading
        import time

        import pack_install

        started = threading.Event()
        release = threading.Event()

        def _slow(_school: object) -> int:
            """Block until the test releases the stand-in scan."""
            started.set()
            release.wait(2)
            return 0

        original = pack_install.resume_interrupted_packs
        pack_install.resume_interrupted_packs = _slow
        try:
            began = time.monotonic()
            pack_install.schedule_pack_resume(object())
            elapsed = time.monotonic() - began
            self.assertLess(elapsed, 0.5)
            self.assertTrue(started.wait(1))
        finally:
            release.set()
            pack_install.resume_interrupted_packs = original
