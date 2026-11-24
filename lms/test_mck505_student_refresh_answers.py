#!/usr/bin/env python3
"""MCK-505: student refresh restores ``my_response`` without resubmit side effects."""

from __future__ import annotations

import json
import unittest
from typing import Any

import test_live_class_backend_state as backend


class Mck505StudentRefreshAnswersTests(backend.LiveBackendStateTests):
    """Answer → rebuild state → answer still present; no cross-student leak."""

    def _publish_q_one(self) -> tuple[int, int]:
        """Open q-one individually; return (live_item_id, prompt_id)."""
        self.school.set_live_session_teacher_state(
            self.session_id, stage="round", student_view={"questions": "student"}
        )
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "q-one")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        return int(published["id"]), int(prompt["id"])

    def _card_for_q_one(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return the active q-one lifecycle row from a student payload."""
        for pool in ("active_questions", "closed_results", "saved_cards"):
            for item in payload.get(pool) or []:
                if str(item.get("item_id") or "") == "q-one":
                    return item
        self.fail(f"q-one missing from payload keys={list(payload.keys())}")

    def _engagement_answer_kinds(self) -> list[str]:
        """Individual answer engagement kinds recorded for this session."""
        rows = self.school.list_engagement_events(
            self.class_id, live_session=self.session_id
        )
        return [
            str(row["kind"])
            for row in rows
            if str(row["kind"]) in {"answer.submit", "answer.change"}
        ]

    def test_refresh_restores_my_response_and_can_submit_false(self) -> None:
        """Second state build is read-only: same answer, submit closed."""
        self._begin_and_join(1)
        _item_id, prompt_id = self._publish_q_one()
        sid = self.student_ids[0]
        choice = {"choice": "A"}
        self.school.submit_live_prompt_response(prompt_id, sid, choice)
        kinds_after_submit = list(self._engagement_answer_kinds())

        first = self.school.student_live_items_payload(self.session_id, sid)
        card1 = self._card_for_q_one(first)
        self.assertEqual(
            ((card1.get("my_response") or {}).get("response") or {}).get("choice"),
            "A",
            card1,
        )
        self.assertFalse(card1.get("can_submit"), card1)

        second = self.school.student_live_items_payload(self.session_id, sid)
        card2 = self._card_for_q_one(second)
        self.assertEqual(
            ((card2.get("my_response") or {}).get("response") or {}).get("choice"),
            "A",
            card2,
        )
        self.assertFalse(card2.get("can_submit"), card2)
        self.assertEqual(self._engagement_answer_kinds(), kinds_after_submit)

    def test_refresh_does_not_leak_peer_answers(self) -> None:
        """Each student sees only their own ``my_response`` on rebuild."""
        self._begin_and_join(2)
        _item_id, prompt_id = self._publish_q_one()
        a_id, b_id = self.student_ids[0], self.student_ids[1]
        self.school.submit_live_prompt_response(prompt_id, a_id, {"choice": "A"})
        self.school.submit_live_prompt_response(prompt_id, b_id, {"choice": "B"})

        a_payload = self.school.student_live_items_payload(self.session_id, a_id)
        b_payload = self.school.student_live_items_payload(self.session_id, b_id)
        a_card = self._card_for_q_one(a_payload)
        b_card = self._card_for_q_one(b_payload)
        self.assertEqual(
            (a_card.get("my_response") or {}).get("response", {}).get("choice"),
            "A",
        )
        self.assertEqual(
            (b_card.get("my_response") or {}).get("response", {}).get("choice"),
            "B",
        )
        # Refresh both again — still isolated.
        a2 = self._card_for_q_one(
            self.school.student_live_items_payload(self.session_id, a_id)
        )
        b2 = self._card_for_q_one(
            self.school.student_live_items_payload(self.session_id, b_id)
        )
        self.assertEqual(
            (a2.get("my_response") or {}).get("response", {}).get("choice"),
            "A",
        )
        self.assertEqual(
            (b2.get("my_response") or {}).get("response", {}).get("choice"),
            "B",
        )

    def test_closed_question_refresh_unchanged(self) -> None:
        """After close, refresh still returns the saved answer; no new submit path."""
        self._begin_and_join(1)
        item_id, prompt_id = self._publish_q_one()
        sid = self.student_ids[0]
        self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        self.school.close_live_session_item(self.session_id, item_id)
        kinds = list(self._engagement_answer_kinds())

        payload = self.school.student_live_items_payload(self.session_id, sid)
        card = self._card_for_q_one(payload)
        self.assertEqual(card.get("status"), "closed", card)
        self.assertFalse(card.get("can_submit"), card)
        self.assertEqual(
            (card.get("my_response") or {}).get("response", {}).get("choice"),
            "A",
        )
        again = self._card_for_q_one(
            self.school.student_live_items_payload(self.session_id, sid)
        )
        self.assertEqual(
            (again.get("my_response") or {}).get("response", {}).get("choice"),
            "A",
        )
        self.assertEqual(self._engagement_answer_kinds(), kinds)

    def test_participation_fallback_supplies_my_response(self) -> None:
        """When live row is gone, batched participation read still restores."""
        self._begin_and_join(1)
        item_id, prompt_id = self._publish_q_one()
        sid = self.student_ids[0]
        body = {"choice": "A"}
        self.school.submit_live_prompt_response(prompt_id, sid, body)
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(r for r in items if int(r["id"]) == int(item_id))
        placement = str(row["placement_key"])
        live = self.school.get_live_session(self.session_id)
        assert live is not None
        run_key = str(live["run_key"])
        qkey = f"item:{placement}"
        with self.school._lock:
            self.school.conn.execute(
                "DELETE FROM live_session_responses WHERE prompt_id = ?",
                (int(prompt_id),),
            )
            self.school.conn.execute(
                """
                INSERT INTO participation_answers (
                    class_id, run_key, student_id, question_key,
                    answer_json, source, answered_at
                ) VALUES (?, ?, ?, ?, ?, 'live', datetime('now'))
                """,
                (
                    int(self.class_id),
                    run_key,
                    int(sid),
                    qkey,
                    json.dumps(body),
                ),
            )
            self.school.conn.commit()
        payload = self.school.student_live_items_payload(self.session_id, sid)
        card = self._card_for_q_one(payload)
        self.assertEqual(
            ((card.get("my_response") or {}).get("response") or {}).get("choice"),
            "A",
        )
        self.assertFalse(card.get("can_submit"), card)

    def test_http_state_refresh_without_stamp(self) -> None:
        """GET ``/api/student/state`` rebuild shows answer; polls add no engagement."""
        item_id, prompt_id = self._publish_q_one()
        live = self.school.get_live_session(self.session_id)
        assert live is not None
        client = self.app.test_client()
        joined = client.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302)
        self.assertEqual(client.post("/student/mood", data={"mood": "good"}).status_code, 302)
        self.assertEqual(
            client.post("/student/character", data={"character": "fox"}).status_code, 302
        )
        submit = client.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"choice": "A"}},
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        kinds_after_submit = list(self._engagement_answer_kinds())

        state = client.get("/api/student/state")
        self.assertEqual(state.status_code, 200, state.get_json())
        body = state.get_json()
        card = self._card_for_q_one(body)
        self.assertEqual(
            (card.get("my_response") or {}).get("response", {}).get("choice"),
            "A",
        )
        self.assertFalse(card.get("can_submit"), card)

        refresh = client.get("/api/student/state")
        self.assertEqual(refresh.status_code, 200)
        card2 = self._card_for_q_one(refresh.get_json())
        self.assertEqual(
            (card2.get("my_response") or {}).get("response", {}).get("choice"),
            "A",
        )
        self.assertFalse(card2.get("can_submit"), card2)
        self.assertEqual(self._engagement_answer_kinds(), kinds_after_submit)
        self.school.close_live_session_item(self.session_id, item_id)


if __name__ == "__main__":
    unittest.main()