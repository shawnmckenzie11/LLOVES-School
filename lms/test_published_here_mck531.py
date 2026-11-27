#!/usr/bin/env python3
"""MCK-531: a question published from another slide stays live until the teacher moves.

Class 85 (Nov 27): the push postcard painted a slide-bound MC, then the next
full ``/state`` parked it because the teacher was on a different slide or
stage, and the phone dropped the card. Synthetic roster labels only.
"""

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
from live_teacher_state import PUBLISHED_HERE_KEY  # noqa: E402

MC = {
    "type": "mc",
    "text": "Slide-bound MC",
    "options": ["Red", "Blue", "Green", "Gold"],
    "correct_index": 0,
}
NUMERIC = {"type": "numeric", "text": "Slide-bound numeric", "correct_answer": "4"}


class PublishedHereTests(unittest.TestCase):
    """Publish wins over the slide/stage park until the next teacher move."""

    def setUp(self) -> None:
        """One staff session on page 4 / round and two synthetic students."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "l.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("fixture-staff@example.com")
        self.teacher_id = int(teacher["id"])
        offering = self.school.assign_course(
            teacher_user_id=self.teacher_id, ontario_code="MCR3U"
        )
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=fixture-staff@example.com&name=T")
        code = self.school.get_user_by_email("fixture-staff@example.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["S1", "S2"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        self.session_id = self._start()
        self.s1 = int(self.school.game.find_student_by_codename(self.class_id, "S1")["id"])

    def tearDown(self) -> None:
        """Close sqlite."""
        self.school.close()
        self.tmp.cleanup()

    def _start(self) -> int:
        """Start a live session on page 4 / round with both students joined."""
        live = self.school.start_live_class_session(
            self.class_id, self.teacher_id, is_test=True
        )
        sid = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_number": 4,
                "class_set": True,
            },
        )
        self.school.game.begin_game(self.class_id)
        for label in ("S1", "S2"):
            student = self.school.game.find_student_by_codename(self.class_id, label)
            self.school.join_live_class_session(sid, int(student["id"]), codename=label)
        return sid

    def _add(self, body: dict[str, Any], *, page: int = 4, stage: str = "round") -> dict[str, Any]:
        """Place one inactive question bound to ``page`` / ``stage``."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={"page_number": page, "stage": stage, "save_to_bank": False, **body},
        )
        self.assertLess(rv.status_code, 400, rv.get_json())
        item_id = str(rv.get_json()["placement"]["item"]["id"])
        self.school.ensure_live_session_items(self.session_id)
        return next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == item_id
        )

    def _move(self, *, page: int | None = None, stage: str = "round") -> None:
        """Teacher slide/stage change through the normal write path."""
        kwargs: dict[str, Any] = {"live_module": "M1", "live_slot": "C2", "stage": stage}
        if page is not None:
            kwargs["page_number"] = page
        self.school.set_live_session_teacher_state(self.session_id, **kwargs)

    def _publish(self, row: dict[str, Any]) -> dict[str, Any]:
        """Publish like the staff route does."""
        return self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )

    def _pool(self, item_id: str) -> tuple[str, dict[str, Any] | None]:
        """Which pool of the full student reply holds this question."""
        payload = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.s1
        )
        for pool in ("active_questions", "saved_cards", "closed_results"):
            for row in payload.get(pool) or []:
                if str(row.get("item_id") or "") == item_id:
                    return pool, row
        return "missing", None

    def _assert_live(self, row: dict[str, Any]) -> dict[str, Any]:
        """The question is facing and answerable."""
        pool, card = self._pool(str(row["item_id"]))
        self.assertEqual(pool, "active_questions")
        assert card is not None
        self.assertTrue(card.get("can_submit"))
        self.assertFalse(card.get("parked"))
        return card

    def _assert_parked(self, row: dict[str, Any]) -> None:
        """MCK-477 park: saved card, not answerable."""
        pool, card = self._pool(str(row["item_id"]))
        self.assertEqual(pool, "saved_cards")
        assert card is not None
        self.assertFalse(card.get("can_submit"))

    def test_class85_repro_mc_published_from_other_slide_stays_live(self) -> None:
        """Teacher on slide 9 publishes an MC bound to slide 4: it faces."""
        row = self._add(MC, page=4)
        self._move(page=9)
        published = self._publish(row)
        card = self._assert_live(row)
        push = self.school.public_push_card(published)
        self.assertIsNotNone(push)
        assert push is not None
        # Push and full reply agree on the same live question and choices.
        self.assertEqual(int(push["item_id"]), int(card["id"]))
        self.assertEqual(
            len(push.get("options_html") or push.get("options") or []),
            len((card.get("content") or {}).get("options") or []),
        )
        encoded = json.dumps(push)
        self.assertNotIn("correct_index", encoded)
        self.assertNotIn("S1", encoded)

    def test_numeric_bound_elsewhere_also_stays_live(self) -> None:
        """Not MC-specific: any slide-bound question gets the same rule."""
        row = self._add(NUMERIC, page=4)
        self._move(page=9)
        self._publish(row)
        self._assert_live(row)

    def test_stage_mismatch_on_own_slide_stays_live(self) -> None:
        """Right slide, other stage: published still faces."""
        row = self._add(MC, page=4, stage="round")
        self._move(page=4, stage="play")
        self._publish(row)
        self._assert_live(row)

    def test_later_slide_change_parks_as_mck477(self) -> None:
        """Leaving the slide after the publish parks the question again."""
        row = self._add(MC, page=4)
        self._move(page=9)
        self._publish(row)
        self._assert_live(row)
        self._move(page=10)
        self._assert_parked(row)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertNotIn(PUBLISHED_HERE_KEY, teacher)
        # Coming back to slide 9 does not revive it.
        self._move(page=9)
        self._assert_parked(row)

    def test_later_stage_change_parks(self) -> None:
        """A stage change alone also ends the window."""
        row = self._add(MC, page=4)
        self._move(page=9)
        self._publish(row)
        self._move(page=9, stage="play")
        self._assert_parked(row)

    def test_same_slide_teacher_writes_keep_it_live(self) -> None:
        """A teacher-state write that does not move slide or stage keeps it."""
        row = self._add(MC, page=4)
        self._move(page=9)
        self._publish(row)
        self.school.set_live_session_teacher_state(
            self.session_id, scoreboard_visible=True
        )
        self._assert_live(row)

    def _publish_between_read_and_write(self, row: dict[str, Any]) -> None:
        """Land a publish after the teacher write read its state, before it writes."""
        original = self.school._write_teacher_state
        fired = {"done": False}

        def hooked(session_id: int, payload: dict[str, Any], **kwargs: Any) -> Any:
            if not fired["done"]:
                fired["done"] = True
                self._publish(row)
            return original(session_id, payload, **kwargs)

        self.school._write_teacher_state = hooked  # type: ignore[method-assign]
        self.addCleanup(setattr, self.school, "_write_teacher_state", original)
        return None

    def test_same_slide_write_racing_a_publish_keeps_it_live(self) -> None:
        """A same-slide teacher write in flight cannot drop a new publish."""
        first = self._add(MC, page=4)
        second = self._add({**MC, "text": "Second slide-bound MC"}, page=5)
        self._move(page=9)
        self._publish(first)
        self._publish_between_read_and_write(second)
        self.school.set_live_session_teacher_state(
            self.session_id, scoreboard_visible=True
        )
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(
            teacher.get(PUBLISHED_HERE_KEY), [int(first["id"]), int(second["id"])]
        )
        self._assert_live(first)
        self._assert_live(second)

    def test_slide_change_racing_a_publish_still_clears(self) -> None:
        """A slide move that overlaps a publish still ends the window."""
        first = self._add(MC, page=4)
        second = self._add({**MC, "text": "Second slide-bound MC"}, page=5)
        self._move(page=9)
        self._publish(first)
        self._publish_between_read_and_write(second)
        self._move(page=10)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertNotIn(PUBLISHED_HERE_KEY, teacher)
        self._assert_parked(first)
        self._assert_parked(second)

    def test_unpublished_other_slide_question_still_parks(self) -> None:
        """Without a publish here, the MCK-477 rule is unchanged."""
        row = self._add(MC, page=4)
        self._publish(row)
        self._assert_live(row)
        self._move(page=9)
        self._assert_parked(row)

    def test_on_slide_publish_unchanged(self) -> None:
        """The workaround path (own slide and stage) still faces."""
        row = self._add(MC, page=4)
        self._publish(row)
        self._assert_live(row)

    def test_answer_in_window_saved_and_in_end_class_results(self) -> None:
        """An answer given in the window keeps its ids and survives End Class."""
        row = self._add(MC, page=4)
        self._move(page=9)
        published = self._publish(row)
        card = self._assert_live(row)
        prompt_id = int(card["prompt"]["id"])
        self.assertEqual(int(card["id"]), int(published["id"]))
        self.assertEqual(prompt_id, int(self.school._prompt_for_live_item(published)["id"]))
        self.school.submit_live_prompt_response(prompt_id, self.s1, {"choice": "Red"})
        _pool, after = self._pool(str(row["item_id"]))
        assert after is not None
        self.assertIsNotNone(after.get("my_response"))
        self.school.end_live_class_session(self.session_id, record_participation=True)
        snap = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM live_result_snapshots
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, self.s1),
        ).fetchone()
        self.assertGreaterEqual(int(snap["n"]), 1)

    def _student_client(self, student_id: int):
        """Flask client authenticated as one joined synthetic student."""
        attendee = self.school.get_live_session_attendee(self.session_id, student_id)
        self.assertIsNotNone(attendee)
        client = self.app.test_client()
        client.environ_base["HTTP_X_STUDENT_VISIT_TOKEN"] = str(attendee["visit_token"])
        return client

    def test_http_state_and_submit_in_window(self) -> None:
        """The real /state route faces it and the submit route accepts it."""
        row = self._add(MC, page=4)
        self._move(page=9)
        published = self._publish(row)
        phone = self._student_client(self.s1)
        state = phone.get("/api/student/state")
        self.assertEqual(state.status_code, 200)
        body = state.get_json()
        ids = [int(card["id"]) for card in body.get("active_questions") or []]
        self.assertIn(int(published["id"]), ids)
        prompt_id = int(self.school._prompt_for_live_item(published)["id"])
        sent = phone.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"choice": "Red"}},
        )
        self.assertEqual(sent.status_code, 200, sent.get_json())
        self._move(page=10)
        later = phone.get("/api/student/state").get_json()
        later_ids = [int(card["id"]) for card in later.get("active_questions") or []]
        self.assertNotIn(int(published["id"]), later_ids)

    def test_window_does_not_carry_into_next_class(self) -> None:
        """End Class, then a new session: no published-here ids carry over."""
        row = self._add(MC, page=4)
        self._move(page=9)
        self._publish(row)
        self.school.end_live_class_session(self.session_id, record_participation=True)
        ended = self.school.student_live_items_payload(self.session_id, self.s1)
        self.assertEqual(ended["active_questions"], [])
        self.session_id = self._start()
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertNotIn(PUBLISHED_HERE_KEY, teacher)


class LightboxGuardTests(unittest.TestCase):
    """The image enlarge decoration cannot block a card paint."""

    def test_portal_wraps_lightbox_sync(self) -> None:
        """Both paint sites call the guarded helper, not the raw sync."""
        text = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertFalse(
            "  syncQuestionLightbox(liveQuestionStackBody);" in text, "raw stack sync"
        )
        self.assertFalse(
            "if (promptShell) syncQuestionLightbox(promptShell);" in text, "raw prompt sync"
        )
        self.assertTrue("safeSyncQuestionLightbox(liveQuestionStackBody);" in text)
        self.assertTrue("safeSyncQuestionLightbox(promptShell);" in text)
        start = text.index("function safeSyncQuestionLightbox")
        self.assertIn("try {", text[start : start + 600])


if __name__ == "__main__":
    unittest.main()
