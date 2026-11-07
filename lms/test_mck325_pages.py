#!/usr/bin/env python3
"""MCK-325: pages are slide numbers, with no stage locks or silent re-pull."""

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
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_teacher_state import (  # noqa: E402
    apply_teacher_state_update,
    student_should_mount_canvas,
    student_should_mount_media,
)
from school_db import SlideCountMismatch  # noqa: E402

SEED_C2 = LMS_DIR / "seeds" / "live_classes" / "MCR3U" / "M1" / "C2.json"

# 1x1 PNG. Slide storage checks the signature and keeps the file.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc00000000300010005fe02fedccc59e700000000"
    "49454e44ae426082"
)


class MCK325PageNumberTests(unittest.TestCase):
    """Page number is the live identity. Stage ids on old decks stay readable."""

    def setUp(self) -> None:
        """Teacher-owned MCR3U class with an active M1 C2 session."""
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
        )
        self.seed_before = SEED_C2.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _question_pages(self) -> dict[str, int]:
        """Return catalogue question id to page number for this lesson."""
        metadata = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2"
        )
        pages: dict[str, int] = {}
        for row in metadata.get("questions") or []:
            if not isinstance(row, dict):
                continue
            token = str(row.get("id") or "").strip()
            if not token or row.get("page_number") in (None, ""):
                continue
            pages[token] = int(row["page_number"])
        return pages

    def test_page_change_keeps_surfaces_and_does_not_spawn_stages(self) -> None:
        """Forward to a later page leaves the open surfaces and the timer."""
        opened = self.school.set_live_session_teacher_state(
            self.session_id,
            unlocks={"media": True, "canvas": True, "slides": True},
            student_view={
                "media": "student",
                "canvas": "team",
                "slides": "student",
                "questions": "student",
            },
        )
        self.school.game.start_session_timer(self.class_id, 5)
        before = self.school.game.game_state(self.class_id)
        moved = self.school.set_live_session_teacher_state(
            self.session_id, page_number=4
        )
        after = self.school.game.game_state(self.class_id)
        self.assertEqual(moved.get("page_number"), 4)
        self.assertEqual(moved.get("stage"), opened.get("stage"))
        self.assertEqual(moved.get("student_view"), opened.get("student_view"))
        self.assertIsNone(moved.get("meet_chain"))
        self.assertNotEqual(moved.get("prompt_ref"), "teams-spark")
        self.assertNotEqual(moved.get("cue_id"), "cue.teams_spark")
        self.assertNotEqual(moved.get("cue_id"), "cue.meet_open")
        self.assertTrue(student_should_mount_media(moved))
        self.assertTrue(student_should_mount_canvas(moved))
        self.assertEqual(after.get("round_started_at"), before.get("round_started_at"))
        self.assertEqual(
            after.get("round_duration_sec"), before.get("round_duration_sec")
        )
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)

    def test_round_page_shows_its_questions_without_changing_stage(self) -> None:
        """A question stored on the old Round page is ordinary content on page 4."""
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        question = next(
            row
            for row in metadata.get("questions") or []
            if isinstance(row, dict) and int(row.get("page_number") or 0) == 4
        )
        self.school.ensure_live_session_items(self.session_id)
        self.school.set_live_session_teacher_state(self.session_id, page_number=4)
        cards = self.school.live_session_question_cards(self.session_id)
        card_ids = {str(row.get("id") or "") for row in cards}
        self.assertIn(str(question.get("id") or ""), card_ids)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher.get("stage"), "join")
        published = self.school.publish_live_session_item(
            self.session_id, str(question.get("id") or "")
        )
        self.assertEqual(published.get("status"), "active")
        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert maple is not None
        payload = self.school.student_live_items_payload(
            self.session_id, int(maple["id"])
        )
        facing = payload.get("active_questions") or payload.get("live_items") or []
        match = [
            row
            for row in facing
            if str(row.get("item_id") or "") == str(question.get("id") or "")
            or str((row.get("content") or {}).get("id") or "")
            == str(question.get("id") or "")
        ]
        self.assertTrue(match, payload)
        self.assertTrue(any(row.get("can_submit") for row in match))

    def test_legacy_stage_blob_stays_readable(self) -> None:
        """An old session blob keeps its stage id and is not rewritten into a page type."""
        self.school._write_teacher_state(
            self.session_id,
            {"stage": "meet", "page_id": "meet", "state_seq": 2},
        )
        read = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(read.get("stage"), "meet")
        self.assertEqual(read.get("page_id"), "meet")
        self.assertIsNone(read.get("page_number"))
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)

    def test_class_start_fixes_the_page_list_and_add_does_not_reindex(self) -> None:
        """Start locks the lesson page count. Adding a page appends a new number."""
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        before_pages = self._question_pages()
        self.assertGreaterEqual(int(teacher.get("fixed_page_count") or 0), 7)
        frozen = int(teacher["fixed_page_count"])
        added = self.school.add_class_playlist_page(
            self.class_id,
            "M1",
            "C2",
            name="Extra practice",
            after_page_id="join",
        )
        new_number = int(added["page"]["page_number"])
        self.assertGreater(new_number, frozen)
        self.assertEqual(self._question_pages(), before_pages)
        after = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(int(after.get("fixed_page_count") or 0), new_number)
        self.assertEqual(after.get("stage"), "join")
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)

    def test_repull_slide_count_mismatch_requires_confirm(self) -> None:
        """A shorter re-pull warns and does not delete or renumber questions."""
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertGreater(int(teacher.get("fixed_page_count") or 0), 2)
        with self.assertRaises(SlideCountMismatch) as first_block:
            self.school.save_course_slide_deck(
                self.class_id,
                "M1",
                "C2",
                title="First",
                source="pptx",
                page_pngs=[_PNG, _PNG],
                session_id=self.session_id,
            )
        self.assertIn("could be lost", str(first_block.exception))
        self.assertIsNone(self.school.course_slide_deck_summary(self.class_id, "M1", "C2"))
        first = self.school.save_course_slide_deck(
            self.class_id,
            "M1",
            "C2",
            title="First",
            source="pptx",
            page_pngs=[_PNG, _PNG],
            session_id=self.session_id,
            confirm_page_loss=True,
        )
        self.assertEqual(len((first.get("google_slides") or {}).get("pages") or []), 2)
        before_pages = self._question_pages()
        self.assertTrue(any(page > 1 for page in before_pages.values()))
        with self.assertRaises(SlideCountMismatch) as caught:
            self.school.save_course_slide_deck(
                self.class_id,
                "M1",
                "C2",
                title="Shorter",
                source="pptx",
                page_pngs=[_PNG],
                session_id=self.session_id,
            )
        message = str(caught.exception)
        self.assertIn("could be lost", message)
        self.assertIn("missing pages", message.lower())
        self.assertTrue(caught.exception.missing_pages)
        self.assertEqual(self._question_pages(), before_pages)
        kept = self.school.course_slide_deck_summary(self.class_id, "M1", "C2")
        self.assertEqual(int(kept.get("page_count") or 0), 2)
        saved = self.school.save_course_slide_deck(
            self.class_id,
            "M1",
            "C2",
            title="Shorter",
            source="pptx",
            page_pngs=[_PNG],
            session_id=self.session_id,
            confirm_page_loss=True,
        )
        self.assertEqual(len((saved.get("google_slides") or {}).get("pages") or []), 1)
        self.assertEqual(self._question_pages(), before_pages)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher.get("fixed_page_count"), 1)
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)

    def test_page_number_patch_does_not_reset_projection(self) -> None:
        """A page-number patch is not a stage entry."""
        current = apply_teacher_state_update(
            None,
            unlocks={"media": True, "canvas": True},
            student_view={
                "media": "student",
                "canvas": "team",
                "slides": "none",
                "questions": "student",
            },
        )
        moved = apply_teacher_state_update(current, page_number=6)
        self.assertEqual(moved["page_number"], 6)
        self.assertEqual(moved["stage"], current["stage"])
        self.assertEqual(moved["student_view"], current["student_view"])
        self.assertEqual(moved["state_seq"], current["state_seq"] + 1)
