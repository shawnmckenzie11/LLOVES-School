"""MCK-281: a deck saved as Rank together opens as Take turns.

The stored ``group_rank_mode`` and any answer already on the team stay
as they were written. Nothing here migrates a row.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_group_mc_pick_turns_mck155 as turns_base  # noqa: E402


class SavedRankTogetherTests(unittest.TestCase):
    """Read-time fallback for a question saved as Rank together."""

    _turns_setup = turns_base.PickThenAgreeAndTurnsTests.setUp
    tearDown = turns_base.PickThenAgreeAndTurnsTests.tearDown
    _post = turns_base.PickThenAgreeAndTurnsTests._post
    _card = turns_base.PickThenAgreeAndTurnsTests._card
    _opts = turns_base.PickThenAgreeAndTurnsTests._opts
    _rank_row = turns_base.PickThenAgreeAndTurnsTests._rank_row
    _publish = turns_base.PickThenAgreeAndTurnsTests._publish
    _view = turns_base.PickThenAgreeAndTurnsTests._view

    def setUp(self) -> None:
        self._turns_setup()

    def _item_json(self, live_item_id: int) -> str:
        row = self.school.conn.execute(
            "SELECT item_json FROM live_session_items WHERE id = ?",
            (int(live_item_id),),
        ).fetchone()
        return str(row["item_json"] or "")

    def test_open_and_read_leave_the_saved_token_and_answer(self) -> None:
        """Play is Take turns. The question token and a past answer are untouched."""
        row = self._rank_row()
        rv = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
            json={"group_rank_mode": "together", "publish_mode": "group_submit"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = self._publish(row)
        live_id = int(item["id"])
        question_before = self._item_json(live_id)
        self.assertIn('"group_rank_mode": "together"', question_before)

        options = self._opts(item)
        past_order = list(reversed(options))
        final = json.dumps({"kind": "rank", "order": past_order, "why": "", "locked_by": "team"})
        proposed = json.dumps({"kind": "rank", "order": past_order, "complete": True})
        team_id = int(self._card("Ava", item)["team_id"])
        updated = self.school.conn.execute(
            """
            UPDATE live_group_responses
            SET status = 'submitted', proposed_answer_json = ?, final_answer_json = ?,
                submit_count = 1, updated_at = 't0'
            WHERE live_item_id = ? AND team_id = ?
            """,
            (proposed, final, live_id, team_id),
        )
        self.assertEqual(updated.rowcount, 1)
        self.school.conn.commit()

        card = self._card("Ava", item)
        self.assertEqual(card["rank_mode"], "turns")
        self.assertEqual(card.get("submitted_order"), past_order)
        self._view(item)
        self.students["Ava"].get("/api/student/state")
        self.school.get_live_session_item(self.session_id, live_id)
        self.school.ensure_live_session_items(self.session_id)

        self.assertEqual(self._item_json(live_id), question_before)
        kept = self.school.conn.execute(
            """
            SELECT proposed_answer_json, final_answer_json, submit_count
            FROM live_group_responses
            WHERE live_item_id = ? AND team_id = ?
            """,
            (live_id, team_id),
        ).fetchone()
        self.assertEqual(kept["final_answer_json"], final)
        self.assertEqual(kept["proposed_answer_json"], proposed)
        self.assertEqual(int(kept["submit_count"]), 1)

        rv = self._post("Ava", item, "group-draft", {"order": options})
        self.assertEqual(rv.status_code, 400)
        still = self.school.conn.execute(
            "SELECT final_answer_json FROM live_group_responses WHERE live_item_id = ? AND team_id = ?",
            (live_id, team_id),
        ).fetchone()
        self.assertEqual(still["final_answer_json"], final)


if __name__ == "__main__":
    unittest.main()
