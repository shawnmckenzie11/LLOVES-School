#!/usr/bin/env python3
"""Per-class MC import overlay and metadata merge tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from content_store import ContentBlobStore  # noqa: E402
from school_db import _now  # noqa: E402

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _insert_bank(
    school: Any,
    library_id: int,
    *,
    title: str,
    import_key: str,
) -> int:
    """Insert one question bank row for tests."""
    cur = school.conn.execute(
        """
        INSERT INTO question_banks (
            library_id, import_key, title, settings_json, created_at
        ) VALUES (?, ?, ?, '{}', ?)
        """,
        (int(library_id), import_key, title, _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


def _insert_mc_question(
    school: Any,
    bank_id: int,
    *,
    import_key: str,
    title: str,
    stem: str,
    correct_index: int = 0,
    extra_payload: dict[str, Any] | None = None,
) -> int:
    """Insert one multiple-choice question row for tests."""
    choices = [
        {"id": "a1", "html": "First option", "correct": correct_index == 0},
        {"id": "a2", "html": "Second option", "correct": correct_index == 1},
        {"id": "a3", "html": "Third option", "correct": correct_index == 2},
    ]
    payload = {
        "stem_html": stem,
        "points_possible": 1.0,
        "choices": choices,
        "correct_ids": [choices[correct_index]["id"]],
    }
    if extra_payload:
        payload.update(extra_payload)
    cur = school.conn.execute(
        """
        INSERT INTO questions (
            bank_id, import_key, item_type, title, payload_json, created_at
        ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
        """,
        (int(bank_id), import_key, title, json.dumps(payload), _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


class ModuleBankImportMergeTests(unittest.TestCase):
    """Metadata merge and import persistence without HTTP."""

    def setUp(self) -> None:
        """Teacher-owned MCR3U class with attached library and confirmed M1 bank."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        self.m1_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Chapter 1 MC",
            import_key="bank:ch1",
        )
        self.m1_q = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q1",
            title="Factoring",
            stem="Factor x squared minus one",
        )
        self.school.confirm_module_bank_links(
            self.library_id, 1, [self.m1_bank]
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
        )

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_import_merges_into_live_metadata(self) -> None:
        """Imported MC appears in session metadata questions for the class."""
        before = self.school.live_class_metadata_for_session(self.session_id)
        seed_count = len(before.get("questions") or [])
        placement = self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        self.assertTrue(str(placement.get("placement_key", "")).startswith("class:"))
        after = self.school.live_class_metadata_for_session(self.session_id)
        self.assertGreater(len(after.get("questions") or []), seed_count)
        imported = [
            row
            for row in after.get("questions") or []
            if str(row.get("id", "")).startswith("bank-import-")
        ]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["text"], "Factor x squared minus one")
        self.assertEqual(imported[0]["correct_answer"], "A")

    def test_student_payload_rewrites_staff_bank_image_urls(self) -> None:
        """Published bank graphs leave the staff-only module-files path."""
        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-graph",
            title="Graph",
            stem=(
                '<p>Refer to the graph.</p>'
                '<img src="$IMS-CC-FILEBASE$/diagram.png">'
            ),
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_id,
            library_id=self.library_id,
            page_number=1,
            stage="round",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        with self.school.game._lock:
            student = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchone()
        student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, student_id, codename="Maple"
        )
        payload = self.school.student_live_items_payload(
            self.session_id, student_id
        )
        content = (payload.get("active_questions") or [{}])[0].get("content") or {}
        blob = json.dumps(content)
        staff_root = f"/staff/class/{self.class_id}/module-files/"
        student_root = f"/api/classes/{self.class_id}/module-files/"
        self.assertIn("<img", str(content.get("text_html") or ""))
        self.assertIn(student_root, blob)
        self.assertNotIn(staff_root, blob)

    def test_student_payload_remirrors_leftover_canvas_images(self) -> None:
        """Stored Instructure imgs remirror onto the student module-files URL."""
        remote = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "70463/files/188452/download?verifier=test"
        )
        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-canvas-graph",
            title="Canvas graph",
            stem=f'<p>Refer to the graph.</p><img src="{remote}">',
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_id,
            library_id=self.library_id,
            page_number=1,
            stage="round",
        )
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(
            _TINY_PNG, filename="web_resources/diagram.png", mime="image/png"
        )
        try:
            from bank_image_mirror import ensure_bank_image_cache_schema
        except ImportError:
            from lms.bank_image_mirror import ensure_bank_image_cache_schema
        ensure_bank_image_cache_schema(self.school)
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                self.library_id,
                "web_resources/diagram.png",
                stored.sha256,
                _now(),
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO bank_image_cache (
                library_id, source_url, relpath, blob_sha, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                self.library_id,
                remote,
                "web_resources/diagram.png",
                stored.sha256,
                _now(),
            ),
        )
        self.school.conn.commit()
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        with self.school.game._lock:
            student = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchone()
        student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, student_id, codename="Maple"
        )
        payload = self.school.student_live_items_payload(
            self.session_id, student_id
        )
        content = (payload.get("active_questions") or [{}])[0].get("content") or {}
        blob = json.dumps(content)
        self.assertIn(
            f"/api/classes/{self.class_id}/module-files/web_resources/diagram.png",
            blob,
        )
        self.assertNotIn("instructure.com", blob)
        self.assertNotIn(f"/staff/class/{self.class_id}/module-files/", blob)

    def test_import_creates_lifecycle_row_on_active_session(self) -> None:
        """Import during an active session seeds a new lifecycle placement."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        items = self.school.ensure_live_session_items_if_stale(self.session_id)
        imported = [
            row
            for row in items
            if str(row.get("item_id") or "").startswith("bank-import-")
        ]
        self.assertEqual(len(imported), 1)
        published = self.school.publish_live_session_item(
            self.session_id, int(imported[0]["id"])
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        payload = prompt.get("payload") if isinstance(prompt, dict) else {}
        self.assertEqual(str(payload.get("correct_answer") or payload.get("key")), "A")

    def test_overlay_edit_reflected_in_search(self) -> None:
        """Staff overlay edits change module search output without touching ingest."""
        self.school.upsert_library_question_overlay(
            self.library_id,
            self.m1_q,
            stem_text="Edited stem for import test",
            options=["Alpha", "Beta", "Gamma"],
            correct_answer="B",
            points=2.0,
        )
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "Edited")["items"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["text"], "Edited stem for import test")
        self.assertEqual(hits[0]["correct_answer"], "B")

    def test_move_import_changes_stage(self) -> None:
        """Imported MC can move to another page in the same lesson deck."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        result = self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=2,
        )
        self.assertEqual(result["action"], "moved")
        self.assertEqual(result["stage"], "teams")
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        moved = [
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == item_id
        ]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["stage"], "teams")

    def test_move_published_item_resets_unpublished_and_keeps_points(self) -> None:
        """A move lands unpublished, clears answers, and leaves session points."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == item_id
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        with self.school.game._lock:
            student = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchone()
        student_id = int(student["id"])
        self.school.game.begin_game(self.class_id)
        self.school.join_live_class_session(
            self.session_id, student_id, codename="Maple"
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), student_id, {"choice": "A"}
        )
        self.school.award_live_prompt_points(
            self.session_id,
            int(prompt["id"]),
            mode="manual",
            student_ids=[student_id],
            amount=3,
        )
        before_points = self.school.live_awarded_session_points(self.class_id)
        self.assertEqual(before_points.get(student_id), 3)
        self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=2,
        )
        after_points = self.school.live_awarded_session_points(self.class_id)
        self.assertEqual(after_points.get(student_id), 3)
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        moved = next(
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == item_id
        )
        self.assertEqual(moved["stage"], "teams")
        lifecycle = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == item_id
        )
        self.assertEqual(lifecycle["status"], "inactive")
        self.assertIsNone(lifecycle.get("published_at"))
        linked = self.school._prompt_for_live_item(lifecycle)
        if linked is not None:
            self.assertFalse(bool(linked.get("active")))
            self.assertEqual(
                self.school.list_live_prompt_responses(int(linked["id"])),
                [],
            )

    def test_remove_import_deletes_placement(self) -> None:
        """Removing an imported MC deletes its sqlite placement row."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", item_id
        )
        rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(rows, [])
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        imported = [
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "").startswith("bank-import-")
        ]
        self.assertEqual(imported, [])

    def test_remove_seed_question_hides_from_metadata(self) -> None:
        """Seed questions persist a hide override instead of editing git JSON."""
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        join_questions = [
            row
            for row in metadata.get("questions") or []
            if row.get("stage") == "join"
        ]
        self.assertTrue(join_questions)
        item_id = str(join_questions[0].get("id") or "")
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", item_id
        )
        after = self.school.live_class_metadata_for_session(self.session_id)
        remaining = [
            row
            for row in after.get("questions") or []
            if str(row.get("id") or "") == item_id
        ]
        self.assertEqual(remaining, [])

    def test_move_seed_question_changes_stage(self) -> None:
        """Seed questions can move to another page via sqlite override."""
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        join_questions = [
            row
            for row in metadata.get("questions") or []
            if row.get("stage") == "join"
        ]
        item_id = str(join_questions[0].get("id") or "")
        self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=3,
        )
        after = self.school.live_class_metadata_for_session(self.session_id)
        moved = [
            row
            for row in after.get("questions") or []
            if str(row.get("id") or "") == item_id
        ]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["stage"], "meet")

    def test_move_then_dest_page_cards_include_item(self) -> None:
        """After a move, dest metadata and dest-page cards keep the item."""

        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        self.school.ensure_live_session_items(self.session_id)
        dest_meta = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2"
        )
        dest_pages = [
            row
            for row in dest_meta.get("pages") or []
            if isinstance(row, dict) and row.get("stage")
        ]
        dest = dest_pages[1]
        dest_stage = str(dest.get("stage") or "").strip().lower()
        dest_page = int(dest.get("page_number") or 2)
        self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=2,
        )
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        moved = next(
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == item_id
        )
        self.assertEqual(str(moved.get("stage") or ""), dest_stage)
        self.assertEqual(int(moved.get("page_number") or 0), dest_page)
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage=dest_stage,
            page_id=str(dest.get("id") or ""),
        )
        cards = self.school.live_session_question_cards(self.session_id)
        dest_card = next(
            row for row in cards if str(row.get("id") or "") == item_id
        )
        self.assertEqual(int(dest_card.get("page_number") or 0), dest_page)
        self.assertEqual(str(dest_card.get("stage") or ""), dest_stage)

    def test_add_staff_mc_on_current_page_unpublished(self) -> None:
        """Add New MC lands unpublished on the current overlay page."""

        placement = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Which parent is this?",
            page_number=4,
            stage="round",
            options=["Linear", "Quadratic", "Cubic", "Absolute"],
            correct_index=1,
            equation="y=x^2",
        )
        item = placement.get("item") or {}
        self.assertEqual(item.get("type"), "mc")
        self.assertEqual(item.get("options"), ["Linear", "Quadratic", "Cubic", "Absolute"])
        self.assertEqual(item.get("key"), "B")
        self.assertEqual(item.get("equation_latex"), "y=x^2")
        self.assertEqual(item.get("page_number"), 4)
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        added = next(
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == str(item.get("id") or "")
        )
        self.assertEqual(added["page_number"], 4)
        lifecycle = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == str(item.get("id") or "")
        )
        self.assertEqual(lifecycle["status"], "inactive")

    def test_add_staff_numeric_and_poll_shapes(self) -> None:
        """Numeric stores tolerance; poll has no key."""

        numeric = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="numeric",
            text="Estimate the vertex y-value",
            page_number=2,
            stage="teams",
            correct_answer="4",
            tolerance=10,
            tolerance_kind="percent",
        )["item"]
        self.assertEqual(numeric.get("type"), "numeric")
        self.assertEqual(str(numeric.get("correct_answer")), "4")
        self.assertEqual(numeric.get("tolerance"), 10.0)
        self.assertEqual(numeric.get("tolerance_kind"), "percent")
        poll = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="poll",
            text="What do you notice?",
            page_number=2,
            stage="teams",
        )["item"]
        self.assertEqual(poll.get("type"), "poll")
        self.assertFalse(poll.get("key"))
        self.assertFalse(poll.get("correct_answer"))

    def test_add_staff_question_saves_course_wide_bank(self) -> None:
        """Course-wide bank save is importable from another module picker."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Course-wide staff MC",
            page_number=1,
            stage="join",
            options=["W", "X", "Y", "Z"],
            correct_index=2,
            save_to_bank=True,
            bank_scope="course",
            library_id=self.library_id,
        )
        source_id = int(placed.get("source_question_id") or 0)
        self.assertGreater(source_id, 0)
        m2_hits = self.school.search_module_bank_mcs(
            self.library_id, 2, "Course-wide staff"
        )["items"]
        self.assertTrue(
            any(int(row.get("question_id") or 0) == source_id for row in m2_hits)
        )
        m1_hits = self.school.search_module_bank_mcs(
            self.library_id, 1, "Course-wide staff"
        )["items"]
        self.assertTrue(
            any(int(row.get("question_id") or 0) == source_id for row in m1_hits)
        )

    def test_add_staff_question_saves_module_two_bank(self) -> None:
        """``M2`` / ``2`` bank scope links only that module."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Module two staff MC",
            page_number=1,
            stage="join",
            options=["W", "X", "Y", "Z"],
            correct_index=0,
            save_to_bank=True,
            bank_scope="M2",
            library_id=self.library_id,
        )
        source_id = int(placed.get("source_question_id") or 0)
        self.assertGreater(source_id, 0)
        m2_hits = self.school.search_module_bank_mcs(
            self.library_id, 2, "Module two staff"
        )["items"]
        self.assertTrue(
            any(int(row.get("question_id") or 0) == source_id for row in m2_hits)
        )
        m1_hits = self.school.search_module_bank_mcs(
            self.library_id, 1, "Module two staff"
        )["items"]
        self.assertFalse(
            any(int(row.get("question_id") or 0) == source_id for row in m1_hits)
        )
        self.assertEqual(
            self.school._bank_scope_module_numbers("2", 1),
            [2],
        )
        self.assertEqual(
            self.school._bank_scope_module_numbers("module", 3),
            [3],
        )

    def test_add_staff_question_strips_wrapping_equation_dollars(self) -> None:
        """Stored equation_latex is raw TeX without wrapping dollar signs."""

        self.assertEqual(self.school.strip_equation_latex("  $y=x^2$  "), "y=x^2")
        self.assertEqual(
            self.school.strip_equation_latex(r"$$\frac{a}{b}$$"), r"\frac{a}{b}"
        )
        self.assertEqual(self.school.strip_equation_latex("y=x^2"), "y=x^2")
        self.assertEqual(self.school.strip_equation_latex("$$"), "")
        item = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="poll",
            text="What does the graph show?",
            page_number=1,
            stage="round",
            equation="$y = x^{2}$",
        )["item"]
        self.assertEqual(item.get("equation_latex"), "y = x^{2}")
        self.assertNotIn("$", str(item.get("equation_latex") or ""))

    def test_import_search_excludes_warmup_unless_scoped(self) -> None:
        """Process Import hides warmup tags until kind is warmup."""

        warmup_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-warmup",
            title="Icebreaker",
            stem="Notice and wonder about equal groups",
            extra_payload={"kind": "warmup"},
        )
        tagged_warmup_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-warmup-tag",
            title="Tagged opener",
            stem="Rotating opener about attendance",
            extra_payload={"tags": ["warmup"]},
        )
        contest_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-contest",
            title="Contest",
            stem="Ferris wheel first height",
            extra_payload={"kind": "contest"},
        )
        standard_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-standard",
            title="Standard",
            stem="Equal-group factor pairs",
            extra_payload={"kind": "standard"},
        )
        default_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(self.library_id, 1, "")[
                "items"
            ]
        }
        self.assertNotIn(warmup_id, default_ids)
        self.assertNotIn(tagged_warmup_id, default_ids)
        self.assertIn(contest_id, default_ids)
        self.assertIn(standard_id, default_ids)
        self.assertIn(self.m1_q, default_ids)
        warmup_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="warmup"
            )["items"]
        }
        self.assertEqual(warmup_ids, {warmup_id, tagged_warmup_id})
        contest_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="contest"
            )["items"]
        }
        self.assertEqual(contest_ids, {contest_id})

    def test_course_bank_scope_search_is_unique_across_modules(self) -> None:
        """Course Wide search returns each linked question once."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Course scope dedupe MC",
            page_number=1,
            stage="join",
            options=["W", "X", "Y", "Z"],
            correct_index=1,
            save_to_bank=True,
            bank_scope="course",
            library_id=self.library_id,
        )
        source_id = int(placed.get("source_question_id") or 0)
        hits = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "Course scope dedupe"
        )
        ids = [int(row.get("question_id") or 0) for row in hits["items"]]
        self.assertEqual(ids.count(source_id), 1)
        self.assertEqual(hits["filtered"], 1)
        module_only = self.school.search_bank_scope_mcs(
            self.library_id, "M2", 1, "Course scope dedupe"
        )
        module_ids = [
            int(row.get("question_id") or 0) for row in module_only["items"]
        ]
        self.assertIn(source_id, module_ids)

    def test_numeric_tolerance_scores_nearby_answers(self) -> None:
        """Absolute tolerance marks nearby numeric responses correct."""

        self.assertTrue(
            self.school._numeric_within_tolerance(10.4, 10, 0.5, "absolute")
        )
        self.assertFalse(
            self.school._numeric_within_tolerance(11, 10, 0.5, "absolute")
        )
        self.assertTrue(
            self.school._numeric_within_tolerance(105, 100, 10, "percent")
        )
        self.assertFalse(
            self.school._numeric_within_tolerance(120, 100, 10, "percent")
        )


class ModuleBankImportApiTests(unittest.TestCase):
    """HTTP endpoints for import and overlay edit."""

    def setUp(self) -> None:
        """Teacher-owned class with library, banks, and live session."""
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
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        self.m1_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Unit 1 Quiz",
            import_key="bank:unit1",
        )
        self.m2_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Module 2 Test",
            import_key="bank:m2",
        )
        self.m1_q = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-m1",
            title="Roots",
            stem="What is a square root?",
        )
        self.m2_q = _insert_mc_question(
            self.school,
            self.m2_bank,
            import_key="q-m2",
            title="Vertex",
            stem="Find the vertex of a parabola",
        )
        self.school.confirm_module_bank_links(
            self.library_id, 1, [self.m1_bank]
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self._login("teacher@gmail.com")

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login(self, email: str) -> None:
        """Complete local staff login for API calls."""
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email(email)["verification_code"]
            },
        )

    def test_import_mc_api(self) -> None:
        """POST import-mc persists placement and returns item payload."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={
                "question_id": self.m1_q,
                "page_number": 4,
                "stage": "round",
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json()
        self.assertTrue(body.get("ok"))
        placement = body.get("placement") or {}
        self.assertEqual(placement.get("item_id"), f"bank-import-{self.m1_q}")
        rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(len(rows), 1)

    def test_import_rejects_unconfirmed_module_bank(self) -> None:
        """M2 question cannot import while only M1 banks are confirmed."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m2_q, "page_number": 1, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 404, rv.get_json())

    def test_overlay_patch_api(self) -> None:
        """PATCH overlay saves staff edits for one question."""
        rv = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-overlays/{self.m1_q}",
            json={
                "stem_text": "Patched stem",
                "options": ["One", "Two", "Three"],
                "correct_answer": "C",
                "points": 3,
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "Patched")["items"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct_answer"], "C")


    def test_import_mc_api_returns_live_metadata(self) -> None:
        """POST import-mc returns merged deck metadata with the new placement."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 2, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        meta = body.get("live_metadata") or {}
        ids = [row.get("id") for row in meta.get("questions") or []]
        self.assertIn(f"bank-import-{self.m1_q}", ids)

    def test_live_lesson_deck_api(self) -> None:
        """GET deck returns merged metadata for staff lesson editing."""
        self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 2, "stage": "round"},
        )
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/deck"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        ids = [
            row.get("id")
            for row in (rv.get_json() or {}).get("live_metadata", {}).get("questions") or []
        ]
        self.assertIn(f"bank-import-{self.m1_q}", ids)

    def test_relocate_playlist_item_post_api(self) -> None:
        """POST playlist-item removes an imported MC from the class deck."""
        item_id = f"bank-import-{self.m1_q}"
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 2, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/playlist-item",
            json={"item_id": item_id, "action": "remove"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertTrue((rv.get_json() or {}).get("ok"))
        rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(rows, [])

    def test_relocate_move_api_returns_live_metadata(self) -> None:
        """POST playlist-item move returns merged metadata with the dest page."""

        item_id = f"bank-import-{self.m1_q}"
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="round"
        )
        self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 4, "stage": "round"},
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/playlist-item",
            json={"item_id": item_id, "target_page_index": 2},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("action"), "moved")
        questions = (body.get("live_metadata") or {}).get("questions") or []
        moved = next(
            row for row in questions if str(row.get("id") or "") == item_id
        )
        self.assertEqual(moved.get("stage"), "teams")
        self.assertIn("question_cards", body)

    def test_add_question_api_creates_unpublished_mc(self) -> None:
        """POST add-question creates an unpublished current-page MC."""

        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="round"
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={
                "type": "mc",
                "text": "API added MC",
                "options": ["A1", "A2", "A3", "A4"],
                "correct_index": 0,
                "page_number": 4,
                "stage": "round",
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        item = (body.get("placement") or {}).get("item") or {}
        self.assertEqual(item.get("type"), "mc")
        self.assertEqual(item.get("key"), "A")
        ids = [
            str(row.get("id") or "")
            for row in (body.get("live_metadata") or {}).get("questions") or []
        ]
        self.assertIn(str(item.get("id") or ""), ids)

    def _put_library_png(self, relpath: str = "web_resources/diagram.png") -> None:
        """Store one PNG in the class library blob store for image-route tests."""
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(_TINY_PNG, filename=relpath, mime="image/png")
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.library_id, relpath, stored.sha256, _now()),
        )
        self.school.conn.execute(
            """
            INSERT INTO module_outlines (
                library_id, import_key, title, position, created_at
            ) VALUES (?, 'mod1', 'Module 1', 1, ?)
            """,
            (self.library_id, _now()),
        )
        self.school.conn.commit()

    def _join_maple_student(self) -> Any:
        """Return a student test client after Maple joins the live session."""
        live = self.school.get_live_session(self.session_id)
        assert live is not None
        student = self.app.test_client()
        join = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302, join.headers.get("Location"))
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        return student

    def test_student_can_get_rewritten_bank_module_file(self) -> None:
        """Student GET of the rewritten module-files URL returns the graph."""
        self._put_library_png()
        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-graph-http",
            title="Graph HTTP",
            stem=(
                '<p>Refer to the graph.</p>'
                '<img src="$IMS-CC-FILEBASE$/diagram.png">'
            ),
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="round"
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_id,
            library_id=self.library_id,
            page_number=1,
            stage="round",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        student = self._join_maple_student()
        state = student.get("/api/student/state").get_json() or {}
        cards = state.get("active_questions") or []
        self.assertTrue(cards, state)
        blob = json.dumps(cards[0].get("content") or {})
        public_url = (
            f"/api/classes/{self.class_id}/module-files/web_resources/diagram.png"
        )
        self.assertIn(public_url, blob)
        self.assertNotIn(f"/staff/class/{self.class_id}/module-files/", blob)
        rv = student.get(public_url)
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_data(), _TINY_PNG)
        blocked = student.get(
            f"/staff/class/{self.class_id}/module-files/web_resources/diagram.png"
        )
        self.assertIn(blocked.status_code, {302, 403})
        anon = self.app.test_client()
        self.assertEqual(anon.get(public_url).status_code, 403)

    def test_student_can_get_live_question_image(self) -> None:
        """Staff-authored live-question-images are GET-able by the student."""
        stored = self.school.store_live_question_image(
            self.class_id, _TINY_PNG, filename="graph.png"
        )
        student = self._join_maple_student()
        rv = student.get(stored["image_url"])
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_data(), _TINY_PNG)
        anon = self.app.test_client()
        self.assertEqual(anon.get(stored["image_url"]).status_code, 403)


if __name__ == "__main__":
    unittest.main()
