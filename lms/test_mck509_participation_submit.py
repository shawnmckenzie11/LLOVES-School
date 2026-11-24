#!/usr/bin/env python3
"""MCK-509: participation credit and card state update on Submit, not on Close."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import test_live_class_backend_state as backend
from live_news_wire import log_for
from test_group_engagement_mck293 import TEAM_A, GroupEngagementTests
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent


class SubmitEmitsSelfTests(unittest.TestCase):
    """Individual Submit pushes engagement_self without bumping state_seq."""

    def setUp(self) -> None:
        """One live class without the full numeric suite."""
        self.host = backend.LiveBackendStateTests(
            "test_v2_join_question_requires_explicit_publish"
        )
        self.host.setUp()
        self.school = self.host.school
        self.class_id = self.host.class_id
        self.session_id = self.host.session_id
        self.student_ids = self.host.student_ids

    def tearDown(self) -> None:
        """Close the temporary school database."""
        self.host.tearDown()

    def _begin_and_join(self, count: int = 1) -> None:
        self.host._begin_and_join(count)

    def _publish_numeric(self) -> tuple[dict, int]:
        item = {
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
        self.school.live_class_metadata_for_session = lambda _sid: {
            **backend.metadata_fixture(),
            "questions": [item],
            "items": [item],
        }
        rows = self.school.ensure_live_session_items(self.session_id)
        row = next(found for found in rows if found["item_id"] == "q-numeric")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        return published, int(prompt["id"])

    def test_numeric_submit_emits_engagement_self(self) -> None:
        """Tier-1 credit is visible before Close and does not fan out state_seq."""
        self._begin_and_join(1)
        _published, prompt_id = self._publish_numeric()
        sid = self.student_ids[0]
        prior = log_for(self.school.data_dir).since(self.session_id, 0)
        cursor = max((int(row.get("id") or 0) for row in prior), default=0)
        self.school.submit_live_prompt_response(prompt_id, sid, {"value": 4})
        self.assertEqual(
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, sid
            )["tier1"],
            "1/1",
        )
        tape = log_for(self.school.data_dir).since(self.session_id, cursor)
        self.assertTrue(any(row.get("type") == "engagement_self" for row in tape))
        self.assertFalse(any(row.get("type") == "state_seq" for row in tape))


class GroupSubmitPersistTests(GroupEngagementTests):
    """Group rank rows land at Submit; Close does not insert again."""

    def test_group_rank_close_does_not_double_award(self) -> None:
        """``group_engagement`` is written once on Send, unchanged on Close."""
        ava = self.ids["Ava"]
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        key = self._question_key(item)
        with self.school._lock:
            before = self.school.conn.execute(
                """
                SELECT COUNT(*) AS n FROM group_engagement
                WHERE student_id = ? AND question_key = ?
                """,
                (ava, key),
            ).fetchone()
        self.assertGreaterEqual(int(before["n"] or 0), 1)
        earned_before = self.school.conn.execute(
            """
            SELECT earned FROM group_engagement
            WHERE student_id = ? AND question_key = ?
            """,
            (ava, key),
        ).fetchone()
        self.school.close_live_session_item(self.session_id, int(item["id"]))
        with self.school._lock:
            after = self.school.conn.execute(
                """
                SELECT COUNT(*) AS n, SUM(earned) AS earned
                FROM group_engagement
                WHERE student_id = ? AND question_key = ?
                """,
                (ava, key),
            ).fetchone()
        self.assertEqual(int(after["n"]), int(before["n"]))
        self.assertEqual(int(after["earned"] or 0), int(earned_before["earned"] or 0))

    def test_second_invalidate_after_end_keeps_group_engagement(self) -> None:
        """A repeat close must not purge rows the first End saved."""
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        self.school.end_live_class_session(self.session_id, record_participation=True)
        rows_after_end = dict(self._rows())
        self.assertTrue(rows_after_end)
        session = self.school.get_live_session(self.session_id)
        self.assertEqual(str(session.get("status")), "ended")
        self.school.invalidate_live_session_code(
            self.session_id, record_participation=False
        )
        self.assertEqual(dict(self._rows()), rows_after_end)


class PortalRegressionTests(unittest.TestCase):
    """Frontend helpers for Submitted badges and open answered cards."""

    def test_live_question_card_layout_stays_open(self) -> None:
        """Answered lifecycle cards no longer auto-compact (MCK-509)."""
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "live_question_card.test.mjs")],
            cwd=str(LMS_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_submitted_badge_in_student_portal(self) -> None:
        """Open answered questions show a Submitted badge, not Active."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        badge = js.split("function studentLiveCardBadge(")[1].split("function ")[0]
        self.assertIn('return "Submitted"', badge)
        self.assertIn("item?.my_response", badge)


if __name__ == "__main__":
    unittest.main()