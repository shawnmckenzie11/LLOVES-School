#!/usr/bin/env python3
"""MCK-499: question fit, fresh results, numeric credit, and the board push."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import test_live_class_backend_state as backend
from board_poll_score import wipe_removed_question
from engagement_rollup import _canonical_mode

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent


def _numeric_item() -> dict:
    """One integer question with no student text in the stem."""
    return {
        "id": "q-numeric",
        "ref": "test/question/q-numeric",
        "item_type": "question",
        "stage": "round",
        "page_number": 1,
        "order": 1,
        "type": "numeric",
        "text": "Type the integer",
        "options": [],
        "correct_answer": "4",
        "integer_only": True,
        "placeholder": "Enter a number",
        "default_status": "inactive",
        "publish_modes": ["individual"],
        "response_mode": "individual",
    }


class NumericEngagementTests(unittest.TestCase):
    """A numeric submit is one individual credit, and old rows stay stored."""

    def setUp(self) -> None:
        """One live class, without collecting the shared backend suite."""
        self.host = backend.LiveBackendStateTests(
            "test_v2_join_question_requires_explicit_publish"
        )
        self.host.setUp()
        self.school = self.host.school
        self.class_id = self.host.class_id
        self.session_id = self.host.session_id
        self.student_ids = self.host.student_ids
        self.students = self.host.students

    def tearDown(self) -> None:
        """Close the temporary school database."""
        self.host.tearDown()

    def _begin_and_join(self, count: int = 1) -> None:
        """Join the first ``count`` roster rows."""
        self.host._begin_and_join(count)

    def _publish_numeric(self, mode: str = "individual") -> tuple[dict, int]:
        """Publish the numeric fixture and return the item plus prompt id."""
        item = _numeric_item()
        self.school.live_class_metadata_for_session = lambda _sid: {
            **backend.metadata_fixture(),
            "questions": [item],
            "items": [item],
        }
        rows = self.school.ensure_live_session_items(self.session_id)
        row = next(found for found in rows if found["item_id"] == "q-numeric")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode=mode
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["kind"], "numeric")
        return published, int(prompt["id"])

    def _answer_events(self) -> list[dict]:
        """Answer events for this run. Payloads are not printed."""
        rows = self.school.list_engagement_events(
            self.class_id, live_session=self.session_id
        )
        return [
            row
            for row in rows
            if str(row.get("kind") or "") in {"answer.submit", "answer.change"}
        ]

    def _person_key(self, student_id: int) -> str:
        """Person key for one joined roster id."""
        for row in self.school.list_live_session_attendees(self.session_id):
            if int(row.get("student_id") or 0) == int(student_id):
                return str(row.get("participant_uuid") or "")
        self.fail("joined roster row has no person key")
        return ""

    def test_numeric_submit_is_one_credit_and_replay_is_quiet(self) -> None:
        """Submitting, repeating, and changing a number stay one credit."""
        self._begin_and_join(1)
        _published, prompt_id = self._publish_numeric()
        sid = self.student_ids[0]
        before = int(getattr(self.school, "_live_card_gen", 0))
        self.school.submit_live_prompt_response(prompt_id, sid, {"value": 4})
        self.assertGreater(int(self.school._live_card_gen), before)
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, sid
            )["tier1"],
            "1/1",
        )
        self.school.submit_live_prompt_response(prompt_id, sid, {"value": 4})
        self.assertEqual([row["kind"] for row in self._answer_events()], ["answer.submit"])
        self.school.submit_live_prompt_response(prompt_id, sid, {"value": 9})
        kinds = [row["kind"] for row in self._answer_events()]
        self.assertEqual(kinds, ["answer.submit", "answer.change"])
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, sid
            )["tier1"],
            "1/1",
        )
        for row in self._answer_events():
            blob = json.dumps(row.get("payload") or {})
            self.assertNotIn("value", blob)
            self.assertNotIn("name", blob)

    def test_person_key_only_resolves_the_roster_id(self) -> None:
        """A submit with only the person key still credits the roster student."""
        self._begin_and_join(1)
        _published, prompt_id = self._publish_numeric()
        sid = self.student_ids[0]
        person = self._person_key(sid)
        self.school.submit_live_prompt_response(
            prompt_id, None, {"value": 4}, participant_uuid=person
        )
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, sid
            )["tier1"],
            "1/1",
        )
        with self.school._lock:
            stored = self.school.conn.execute(
                "SELECT student_id FROM live_session_responses WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()
        self.assertEqual(int(stored["student_id"]), sid)

    def test_guest_submit_does_not_crash_or_count(self) -> None:
        """A guest answer is stored and does not create an engagement event."""
        self._begin_and_join(1)
        _published, prompt_id = self._publish_numeric()
        joined = self.school.join_live_class_session(
            self.session_id, None, codename="Visitor", unmatched=True
        )
        person = str(joined["attendee"]["participant_uuid"])
        saved = self.school.submit_live_prompt_response(
            prompt_id, None, {"value": 4}, participant_uuid=person
        )
        self.assertTrue(saved)
        self.assertEqual(self._answer_events(), [])

    def test_late_joiner_denominator_follows_presence(self) -> None:
        """An unanswered early question stays out until the late student answers."""
        self._begin_and_join(1)
        _published, prompt_id = self._publish_numeric()
        early = self.student_ids[0]
        late = self.student_ids[1]
        self.school.submit_live_prompt_response(prompt_id, early, {"value": 4})
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, late
            )["tier1"],
            "0/0",
        )
        self.school.join_live_class_session(
            self.session_id, late, codename=str(self.students[1]["codename"])
        )
        store = self.school.engagement
        store.conn.execute(
            "UPDATE engagement_events SET ts = ? WHERE kind = 'item.offered'",
            ("2026-11-23T10:00:00-05:00",),
        )
        store.conn.execute(
            """
            UPDATE engagement_events SET ts = ?
            WHERE kind = 'present.join' AND student_id = ?
            """,
            ("2026-11-23T09:50:00-05:00", early),
        )
        store.conn.execute(
            """
            UPDATE engagement_events SET ts = ?
            WHERE kind = 'present.join' AND student_id = ?
            """,
            ("2026-11-23T10:05:00-05:00", late),
        )
        store.conn.commit()
        self.school.bump_live_card_cache()
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, late
            )["tier1"],
            "0/0",
        )
        self.school.submit_live_prompt_response(prompt_id, late, {"value": 4})
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, late
            )["tier1"],
            "1/1",
        )

    def test_blank_publish_mode_still_offers_and_counts(self) -> None:
        """A blank stored mode is individual for the offer and the End capture."""
        self.assertEqual(_canonical_mode("   "), "individual")
        self.assertEqual(_canonical_mode(""), "individual")
        self._begin_and_join(1)
        published, prompt_id = self._publish_numeric()
        sid = self.student_ids[0]
        offers = [
            row
            for row in self.school.list_engagement_events(
                self.class_id, live_session=self.session_id
            )
            if str(row.get("kind") or "") == "item.offered"
        ]
        self.assertTrue(offers)
        self.assertEqual(str((offers[-1].get("payload") or {}).get("mode") or ""), "individual")
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_items
                SET publish_mode = '', response_mode = ''
                WHERE id = ?
                """,
                (int(published["id"]),),
            )
            self.school.conn.commit()
        self.school.submit_live_prompt_response(prompt_id, sid, {"value": 4})
        self.school.end_live_class_session(self.session_id, record_participation=True)
        with self.school._lock:
            found = self.school.conn.execute(
                """
                SELECT prompt_kind FROM participation_items
                WHERE class_id = ? AND prompt_kind = 'numeric'
                """,
                (self.class_id,),
            ).fetchall()
        self.assertTrue(found)

    def test_wipe_drops_numeric_events_and_keeps_answer_rows(self) -> None:
        """Removing the question clears credit and leaves the stored answer."""
        self._begin_and_join(1)
        published, prompt_id = self._publish_numeric()
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[0], {"value": 4}
        )
        self.assertTrue(self._answer_events())
        with self.school._lock:
            before = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_session_responses WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()
        wipe_removed_question(self.school, self.class_id, str(published["item_id"]))
        self.assertEqual(self._answer_events(), [])
        with self.school._lock:
            after = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_session_responses WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()
        self.assertEqual(int(after["n"]), int(before["n"]))
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, self.student_ids[0]
            )["tier1"],
            "0/0",
        )


class QuestionFitSourceTests(unittest.TestCase):
    """The student card, the teacher empty state, and the board toggle stay put."""

    def test_card_script(self) -> None:
        """Image buttons, badge copy, and compact layout pass the node check."""
        node = LMS_DIR / "static" / "live_question_card.test.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_question_lightbox_has_close_only(self) -> None:
        """Enlarged image view keeps Close; Fit and paging controls are gone."""
        js = (LMS_DIR / "static" / "live_question_card.js").read_text(encoding="utf-8")
        block = js.split("function ensureQuestionLightbox")[1].split("export function mountQuestionLightbox")[0]
        self.assertIn("data-live-q-close", block)
        self.assertNotIn("data-live-q-zoom", block)
        self.assertNotIn("data-live-q-prev", block)
        self.assertNotIn("data-live-q-next", block)

    def test_student_css_caps_the_image_and_stacks_in_flow(self) -> None:
        """Desktop and phone caps, compact thumbs, and the dialog are in the sheet."""
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn("width: clamp(240px, 30vw, 420px)", css)
        self.assertIn("max-height: 38dvh", css)
        self.assertIn("max-height: 28dvh", css)
        self.assertIn("height: 96px", css)
        self.assertIn("max-height: 40%", css)
        stack = css.split(".live-question-stack {")[1].split("}")[0]
        # MCK-504: question stack is edge-to-edge so the board can use full height.
        self.assertIn("left: 0", stack)
        self.assertIn("right: 0", stack)
        self.assertIn("width: auto", stack)
        self.assertNotIn("min(22rem", stack)
        self.assertIn("flex-direction: column", stack)
        dialog = css.split(".live-q-lightbox {")[1].split(".live-q-lightbox::backdrop")[0]
        self.assertIn("92vw", dialog)
        self.assertIn("rgba(8, 15, 25, 0.92)", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("touch-action: pinch-zoom", css)

    def test_show_button_flips_before_the_request(self) -> None:
        """Show on board paints Stop showing before the server answers."""
        board = (LMS_DIR / "static" / "formative_live_board.js").read_text(encoding="utf-8")
        handler = board.split('part("show")?.addEventListener')[1].split("const ap =")[0]
        self.assertLess(handler.index("paintShowButton()"), handler.index("fetch("))
        self.assertIn("Couldn't update the board.", handler)
        self.assertIn("aria-pressed", board)

    def test_board_show_does_not_reload_state(self) -> None:
        """The class push paints the drawing and does not call /state together."""
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        wire = portal.split('event.type === "board_ink"')[1].split("scheduleNewsState")[0]
        self.assertIn("syncPollFromPayload", wire)
        self.assertIn("scheduleBoardShowFetch", wire)
        self.assertNotIn("void tick()", wire)
        self.assertNotIn("scheduleNewsState()", wire)
        self.assertIn("return;", wire)


if __name__ == "__main__":
    unittest.main()
