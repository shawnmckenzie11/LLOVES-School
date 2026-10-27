#!/usr/bin/env python3
"""MCK-282: a copied deck's module-file images follow the destination class."""

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
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from content_store import ContentBlobStore  # noqa: E402
from question_math import rebase_module_file_text  # noqa: E402
from deck_copy_image_repair import (  # noqa: E402
    apply_deck_copy_image_repairs,
    plan_deck_copy_image_repairs,
)
from school_db import SchoolDB, _now  # noqa: E402

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_RELPATH = "web_resources/diagram.png"


class DeckCopyImageTests(unittest.TestCase):
    """Copy a deck with a graph, then fetch it as each class's student."""

    def setUp(self) -> None:
        """Two sections of one course, plus an unrelated class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school: SchoolDB = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.other = self.school.register_staff("other-teacher@gmail.com")
        self.tid = int(self.teacher["id"])
        self.oid = int(self.other["id"])
        self.source_offering = self.school.assign_course(
            teacher_user_id=self.tid, ontario_code="MCF3M"
        )
        self.dest_offering = self.school.assign_course(
            teacher_user_id=self.tid, ontario_code="MCF3M", new_section=True
        )
        self.other_offering = self.school.assign_course(
            teacher_user_id=self.oid, ontario_code="MCR3U"
        )
        self.source_library = int(
            self.school.create_library("MCF3M", origin="upload")["id"]
        )
        self.dest_library = int(
            self.school.create_library("MCF3M", origin="upload")["id"]
        )
        self.other_library = int(
            self.school.create_library("MCR3U", origin="upload")["id"]
        )
        self.school.attach_library(int(self.source_offering["id"]), self.source_library)
        self.school.attach_library(int(self.dest_offering["id"]), self.dest_library)
        self.school.attach_library(int(self.other_offering["id"]), self.other_library)
        self.source_class = self._make_class(self.source_offering, self.tid, "Cedar")
        self.dest_class = self._make_class(self.dest_offering, self.tid, "Maple")
        self.other_class = self._make_class(self.other_offering, self.oid, "Birch")
        self._put_png(self.source_library)
        self._mark_ingested(self.dest_library)
        self._mark_ingested(self.other_library)
        self._place_graph(self.source_class)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp school."""
        self.school.close()
        self.tmp.cleanup()

    def _make_class(self, offering: dict, teacher_id: int, codename: str) -> int:
        """Create one game-show class on an offering."""
        semester = self.school.get_semester(int(offering["semester_id"]))
        created = self.school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=str(offering["ontario_code"]),
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=[codename],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher_id),
        )
        return int(created["id"])

    def _put_png(self, library_id: int) -> None:
        """Store the graph in one library and mark that library ingested."""
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(_TINY_PNG, filename=_RELPATH, mime="image/png")
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(library_id), _RELPATH, stored.sha256, _now()),
        )
        self._mark_ingested(int(library_id))
        self.school.conn.commit()

    def _mark_ingested(self, library_id: int) -> None:
        """Insert one outline so module-file serving does not re-ingest."""
        self.school.conn.execute(
            """
            INSERT INTO module_outlines (
                library_id, import_key, title, position, created_at
            ) VALUES (?, ?, 'Module 1', 1, ?)
            """,
            (int(library_id), f"mod-{int(library_id)}", _now()),
        )
        self.school.conn.commit()

    def _place_graph(self, class_id: int) -> None:
        """Store one question whose graph URL names this class."""
        image = f"/staff/class/{int(class_id)}/module-files/{_RELPATH}"
        payload = {
            "id": "q-graph-copy",
            "item_id": "q-graph-copy",
            "type": "mc",
            "text": "Read the graph",
            "text_html": f'<p>Graph</p><img src="{image}">',
            "image_url": image,
            "options": ["Up", "Down"],
            "key": "A",
        }
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (?, 'M1', 'C1', 1, 'round', 1, ?, 'q-graph-copy', ?, NULL, ?)
            """,
            (
                int(class_id),
                f"graph-{int(class_id)}",
                json.dumps(payload),
                _now(),
            ),
        )
        self.school.conn.commit()

    def _copied_image_url(self) -> str:
        """Return the student URL stored on the destination deck."""
        row = self.school.conn.execute(
            """
            SELECT item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND item_id = 'q-graph-copy'
            """,
            (self.dest_class,),
        ).fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(row["item_json"])
        return str(payload["image_url"])

    def _join(self, class_id: int, teacher_id: int, name: str):
        """Start that class and return a student client bound to it."""
        live = self.school.start_live_class_session(int(class_id), int(teacher_id))
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": name},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302)
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        return student

    def test_copied_deck_image_is_readable_only_by_the_destination_class(self) -> None:
        """Destination students get the graph. Another class's student does not."""
        self.school.apply_class_deck_seed(
            self.dest_class,
            "M1",
            "C1",
            mode="course",
            source_module="M1",
            source_slot="C1",
            source_class_id=self.source_class,
            teacher_user_id=self.tid,
        )
        stored = self._copied_image_url()
        self.assertIn(f"/staff/class/{self.dest_class}/module-files/", stored)
        self.assertNotIn(f"/staff/class/{self.source_class}/", stored)
        pointed = self.school.conn.execute(
            """
            SELECT 1 FROM library_files
            WHERE library_id = ? AND relpath = ?
            """,
            (self.dest_library, _RELPATH),
        ).fetchone()
        self.assertIsNotNone(pointed)
        source_still = self.school.conn.execute(
            """
            SELECT item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND item_id = 'q-graph-copy'
            """,
            (self.source_class,),
        ).fetchone()
        self.assertIn(
            f"/staff/class/{self.source_class}/",
            str(source_still["item_json"]),
        )
        public = stored.replace("/staff/class/", "/api/classes/")
        source_public = (
            f"/api/classes/{self.source_class}/module-files/{_RELPATH}"
        )
        dest_student = self._join(self.dest_class, self.tid, "Maple")
        fetched = dest_student.get(public)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.get_data(), _TINY_PNG)
        self.assertEqual(dest_student.get(source_public).status_code, 403)
        other_student = self._join(self.other_class, self.oid, "Birch")
        self.assertEqual(other_student.get(public).status_code, 403)
        anon = self.app.test_client()
        self.assertEqual(anon.get(public).status_code, 403)


class DeckCopyImageRepairTests(unittest.TestCase):
    """Already-copied rows are reported in dry-run and fixed only on apply."""

    def test_rebase_text_keeps_a_traversal_out_of_the_copy_list(self) -> None:
        """A ``..`` path is retargeted and is not offered as a file copy."""
        text, refs = rebase_module_file_text(
            "/staff/class/9/module-files/../../etc/passwd", 8
        )
        self.assertIn("/staff/class/8/module-files/", text)
        self.assertEqual(refs, [])

    def test_dry_run_leaves_rows_and_apply_rewrites_them(self) -> None:
        """The planner sees a stale class id. Apply rebases it once."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        school: SchoolDB = app.config["SCHOOL_DB"]
        self.addCleanup(school.close)
        school.activate_from_semester_json()
        teacher = school.register_staff("teacher@gmail.com")
        offering = school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        library = int(school.create_library("MCF3M", origin="upload")["id"])
        school.attach_library(int(offering["id"]), library)
        semester = school.get_semester(int(offering["semester_id"]))
        created = school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        class_id = int(created["id"])
        stale = (
            f'<img src="/api/classes/9/module-files/{_RELPATH}">'
        )
        school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (?, 'M1', 'C1', 1, 'round', 1, 'stale', 'q-stale', ?, NULL, ?)
            """,
            (class_id, json.dumps({"id": "q-stale", "text_html": stale}), _now()),
        )
        school.conn.commit()
        before = plan_deck_copy_image_repairs(school.conn)
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0].class_id, class_id)
        self.assertIn(f"/api/classes/{class_id}/module-files/", before[0].new_text)
        self.assertNotIn("/api/classes/9/", before[0].new_text)
        dry = plan_deck_copy_image_repairs(school.conn)
        self.assertEqual(dry[0].old_text, before[0].old_text)
        written = apply_deck_copy_image_repairs(school.conn, before, stamp=_now())
        school.conn.commit()
        self.assertEqual(written["rows"], 1)
        again = plan_deck_copy_image_repairs(school.conn)
        self.assertEqual(again, [])
        second = apply_deck_copy_image_repairs(school.conn, again, stamp=_now())
        self.assertEqual(second["rows"], 0)


class DeckCopyImageScriptTests(unittest.TestCase):
    """The repair script refuses production and stays dry-run by default."""

    def test_production_path_is_refused_before_open(self) -> None:
        """``/data`` is not opened without the production confirmation pair."""
        import importlib.util

        script = REPO_ROOT / "scripts" / "repair_deck_copy_images.py"
        spec = importlib.util.spec_from_file_location("repair_deck_copy_script", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        code = module.main(["--apply", "--db", "/data/lloves.sqlite"])
        self.assertEqual(code, 2)
        allowed = module.production_gate(
            module._parse_args(
                ["--db", "/data/lloves.sqlite", "--production", "--confirm", "MCK-282"]
            ),
            module.refusal_reason(Path("/data/lloves.sqlite")),
        )
        self.assertIsNone(allowed)
