#!/usr/bin/env python3
"""MCK-488: one bank-import question on the student card.

The same bank question can be the live slide item and a catch-up pin
(a second lifecycle row with the same item id and no page). The student
phone used to paint both. Answers stay on the card that remains.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402

STAFF_EMAIL = "staff-mck488@example.com"
STUDENT = "Maple"
ITEM_ID = "bank-import-28920"
OTHER_ID = "seed-question-other"
STEM = "P(2, -3) lies on the terminal arm of an angle in standard position."


def _question(item_id: str, text: str) -> dict:
    """One student-safe multiple choice payload.

    Args:
        item_id: Catalogue id such as ``bank-import-28920``.
        text: Stem the student should see once.
    """
    return {
        "id": item_id,
        "item_id": item_id,
        "item_type": "question",
        "type": "mc",
        "text": text,
        "text_html": f"<p>{text}</p>",
        "options": ["First", "Second", "Third", "Fourth"],
        "correct_answer": "A",
        "import_source": "module_bank",
        "stage": "round",
        "publish_modes": ["individual"],
        "response_mode": "individual",
    }


class BankImportCatchupPinTests(unittest.TestCase):
    """Slide item plus catch-up pin is one student question."""

    def setUp(self) -> None:
        """One class, one student, and a live session on slide 3."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff(STAFF_EMAIL)
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={STAFF_EMAIL}&name=Teacher")
        code = self.school.get_user_by_email(STAFF_EMAIL)["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": [STUDENT],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            page_number=3,
            page_id="slide-3",
            stage="round",
            live_module="M1",
            live_slot="C2",
            student_view={"questions": "student"},
        )
        student = self.school.game.find_student_by_codename(self.class_id, STUDENT)
        self.assertIsNotNone(student)
        self.student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, self.student_id, codename=STUDENT
        )
        self._place(ITEM_ID, STEM)
        self._place(OTHER_ID, "A different question.")
        self.slide_id = self._lifecycle("slide-bank-28920", ITEM_ID, STEM, page=3, order=1)
        self.pin_id = self._lifecycle("catchup-pin-bank-28920", ITEM_ID, STEM, page=None, order=2)
        self.other_id = self._lifecycle("slide-other", OTHER_ID, "A different question.", page=3, order=3)
        self.school.publish_live_session_item(self.session_id, self.slide_id)
        self.school.publish_live_session_item(self.session_id, self.pin_id)
        self.school.publish_live_session_item(self.session_id, self.other_id)

    def tearDown(self) -> None:
        """Close the temp database."""
        self.school.close()
        self.tmp.cleanup()

    def _place(self, item_id: str, text: str) -> None:
        """Put one bank question on the class deck so the student filter keeps it.

        Args:
            item_id: Catalogue id.
            text: Stem stored on the placement.
        """
        module_key, slot_key = self.school._deck_storage_slot_keys(
            self.class_id, "M1", "C2"
        )
        payload = _question(item_id, text)
        payload["page_number"] = 3
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (?, ?, ?, 3, 'round', 1, ?, ?, ?, NULL, 't')
            """,
            (
                self.class_id,
                module_key,
                slot_key,
                f"class:{self.class_id}:import:{item_id}",
                item_id,
                json.dumps(payload),
            ),
        )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=self.class_id)

    def _lifecycle(
        self,
        placement_key: str,
        item_id: str,
        text: str,
        *,
        page: int | None,
        order: int,
    ) -> int:
        """Insert one inactive lifecycle row and return its id.

        Args:
            placement_key: Unique placement for this session.
            item_id: Catalogue id. The slide and the pin share one.
            text: Question stem.
            page: Deck page, or None for an unpaged catch-up pin.
            order: Sort order.
        """
        cur = self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, page_number,
                sort_order, kind, item_json, status, publish_mode, response_mode,
                show_live_results, created_at, updated_at
            ) VALUES (?, ?, ?, 'round', ?, ?, 'question', ?, 'inactive',
                      'individual', 'individual', 1, 't', 't')
            """,
            (
                self.session_id,
                placement_key,
                item_id,
                page,
                order,
                json.dumps(_question(item_id, text)),
            ),
        )
        self.school.conn.commit()
        return int(cur.lastrowid)

    def _copies(self, payload: dict) -> list[dict]:
        """Every student card of the bank question, across live and parked lists.

        Args:
            payload: ``student_live_prompt_payload`` or the items slice.
        """
        found = []
        for name in ("active_questions", "closed_results", "saved_cards"):
            for row in payload.get(name) or []:
                if str(row.get("item_id") or "") == ITEM_ID:
                    found.append(row)
        return found

    def test_slide_item_and_catchup_pin_appear_once(self) -> None:
        """On slide 3 the bank question is one card, and a different question stays."""
        payload = self.school.student_live_prompt_payload(self.session_id, self.student_id)
        copies = self._copies(payload)
        self.assertEqual(len(copies), 1, copies)
        kept = copies[0]
        self.assertEqual(int(kept["page_number"]), 3)
        self.assertEqual(int(kept["id"]), self.slide_id)
        self.assertEqual(int(payload["prompt"]["id"]), int(kept["prompt"]["id"]))
        others = [
            row
            for row in payload.get("active_questions") or []
            if str(row.get("item_id") or "") == OTHER_ID
        ]
        self.assertEqual(len(others), 1)
        stems = [
            str((row.get("content") or {}).get("text") or "")
            for row in (payload.get("active_questions") or [])
        ]
        self.assertEqual(stems.count(STEM), 1)

    def test_answer_and_score_stay_on_the_kept_question(self) -> None:
        """A submit lands on the slide prompt, not the catch-up pin's prompt."""
        before = self.school.student_live_prompt_payload(self.session_id, self.student_id)
        kept = self._copies(before)[0]
        prompt_id = int(kept["prompt"]["id"])
        pin = self.school.get_live_session_item(self.session_id, self.pin_id)
        pin_prompt_id = int(pin["prompt_id"])
        self.assertNotEqual(prompt_id, pin_prompt_id)
        self.school.submit_live_prompt_response(
            prompt_id, self.student_id, {"choice": "First"}
        )
        again = self.school.student_live_prompt_payload(self.session_id, self.student_id)
        copies = self._copies(again)
        self.assertEqual(len(copies), 1)
        response = copies[0].get("my_response") or {}
        self.assertEqual((response.get("response") or {}).get("choice"), "First")
        self.assertEqual(int(again["prompt"]["id"]), prompt_id)
        self.assertEqual((again.get("my_response") or {}).get("response", {}).get("choice"), "First")
        pin_rows = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_session_responses WHERE prompt_id = ?",
            (pin_prompt_id,),
        ).fetchone()
        kept_rows = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_session_responses WHERE prompt_id = ?",
            (prompt_id,),
        ).fetchone()
        self.assertEqual(int(pin_rows["n"]), 0)
        self.assertEqual(int(kept_rows["n"]), 1)

    def test_catchup_pin_alone_shows_once_off_the_slide(self) -> None:
        """Off the slide, the unpaged pin is the one card. The slide copy stays parked once."""
        self.school.set_live_session_teacher_state(
            self.session_id,
            page_number=5,
            page_id="slide-5",
            stage="round",
            live_module="M1",
            live_slot="C2",
            student_view={"questions": "student"},
        )
        payload = self.school.student_live_prompt_payload(self.session_id, self.student_id)
        copies = self._copies(payload)
        self.assertEqual(len(copies), 1, copies)
        self.assertIsNone(copies[0].get("page_number"))
        self.assertEqual(int(copies[0]["id"]), self.pin_id)

    def test_live_slide_item_alone_shows_once(self) -> None:
        """A bank question with no catch-up pin is still the one live card."""
        self.school.conn.execute(
            "DELETE FROM live_session_items WHERE id = ?",
            (self.pin_id,),
        )
        self.school.conn.commit()
        payload = self.school.student_live_items_payload(self.session_id, self.student_id)
        copies = self._copies(payload)
        self.assertEqual(len(copies), 1)
        self.assertEqual(int(copies[0]["id"]), self.slide_id)
        self.assertEqual(str(copies[0]["status"]), "active")


if __name__ == "__main__":
    unittest.main()
