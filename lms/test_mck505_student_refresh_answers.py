#!/usr/bin/env python3
"""MCK-505: student refresh restores ``my_response`` without resubmit side effects."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any

import test_live_class_backend_state as backend

LMS_DIR = Path(__file__).resolve().parent
PORTAL_JS = LMS_DIR / "static" / "student-portal.js"


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

    def test_preload_skips_participation_when_live_rows_present(self) -> None:
        """No ``get_live_session`` or participation read when live answers exist."""
        self._begin_and_join(1)
        _item_id, prompt_id = self._publish_q_one()
        sid = self.student_ids[0]
        self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        calls: list[int] = []
        real = self.school.get_live_session

        def wrapped(session_id: int) -> dict[str, Any] | None:
            calls.append(int(session_id))
            return real(int(session_id))

        items = self.school.ensure_live_session_items(self.session_id)
        self.school.get_live_session = wrapped  # type: ignore[method-assign]
        calls.clear()
        self.school._preload_student_prompt_reads(
            items, sid, "", session_id=self.session_id
        )
        self.assertEqual(calls, [])

    def test_rank_reorder_after_answer_when_can_submit_false(self) -> None:
        """Rank Change answer: resubmit updates the row though ``can_submit`` is false."""
        meta = dict(backend.metadata_fixture())
        rank_q = {
            "id": "rank-one",
            "ref": "test/question/rank-one",
            "item_type": "question",
            "stage": "round",
            "page_number": 1,
            "order": 3,
            "type": "rank",
            "text": "Order these",
            "options": ["First", "Second"],
            "rank_options": [
                {"id": "o1", "label": "First"},
                {"id": "o2", "label": "Second"},
            ],
            "default_status": "inactive",
            "publish_modes": ["individual"],
            "response_mode": "individual",
        }
        meta["questions"] = list(meta["questions"]) + [rank_q]
        meta["items"] = list(meta["items"]) + [rank_q]
        self.school.live_class_metadata_for_session = lambda _: meta
        self._begin_and_join(1)
        self.school.set_live_session_teacher_state(
            self.session_id, stage="round", student_view={"questions": "student"}
        )
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "rank-one")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        prompt_id = int(prompt["id"])
        sid = self.student_ids[0]
        first_order = ["o1", "o2"]
        second_order = ["o2", "o1"]
        self.school.submit_live_prompt_response(
            prompt_id, sid, {"order": first_order}
        )
        payload = self.school.student_live_items_payload(self.session_id, sid)
        card = next(
            item
            for item in payload.get("active_questions") or []
            if str(item.get("item_id") or "") == "rank-one"
        )
        self.assertFalse(card.get("can_submit"), card)
        self.assertEqual(
            (card.get("my_response") or {}).get("response", {}).get("order"),
            first_order,
        )
        changed = self.school.submit_live_prompt_response(
            prompt_id, sid, {"order": second_order}
        )
        self.assertNotIn("idempotent_replay", changed)
        after = self.school.student_live_items_payload(self.session_id, sid)
        card2 = next(
            item
            for item in after.get("active_questions") or []
            if str(item.get("item_id") or "") == "rank-one"
        )
        self.assertEqual(
            (card2.get("my_response") or {}).get("response", {}).get("order"),
            second_order,
        )
        kinds = self._engagement_answer_kinds()
        self.assertIn("answer.submit", kinds)
        self.assertIn("answer.change", kinds)


class Mck505ClientTests(unittest.TestCase):
    """Browser draft map must survive a poll-time hydrate (MCK-505 QA)."""

    def _node(self, script: str) -> None:
        """Run an ES module snippet from ``lms/`` and expect it to print ok."""
        done = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=LMS_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
        self.assertIn("ok", done.stdout)

    def test_hydrate_server_saved_answers_uses_has_guards(self) -> None:
        """Source mirrors ``hydrateUnsentDrafts``: never overwrite an existing key."""
        portal = PORTAL_JS.read_text(encoding="utf-8")
        block = portal.split("function hydrateServerSavedAnswers", 1)[1].split(
            "function holdUnsentLiveAnswer", 1
        )[0]
        self.assertGreaterEqual(block.count("if (!liveCardDrafts.has("), 4)

    def test_poll_hydrate_does_not_clobber_in_progress_drafts(self) -> None:
        """Half-typed group / vote drafts survive a second hydrate (one poll paint)."""
        self._node(
            r"""
import { readFileSync } from "fs";
const portal = readFileSync("./static/student-portal.js", "utf8");
const start = portal.indexOf("function hydrateServerSavedAnswers");
const end = portal.indexOf("function holdUnsentLiveAnswer", start);
const src = portal.slice(start, end);
const liveCardDrafts = new Map();
const itemIdForPrompt = () => 0;
const hydrateServerSavedAnswers = new Function(
  "liveCardDrafts",
  "itemIdForPrompt",
  `${src}\nreturn hydrateServerSavedAnswers;`
)(liveCardDrafts, itemIdForPrompt);
const groupPayload = {
  active_questions: [{
    id: 9,
    response_mode: "group_submit",
    group_submit: { choice: "Server", why: "" },
  }],
};
liveCardDrafts.set("9:group", { choice: "Half", why: "still typing" });
hydrateServerSavedAnswers(groupPayload);
if (liveCardDrafts.get("9:group").why !== "still typing") {
  throw new Error("group draft clobbered");
}
liveCardDrafts.set("9:vote", { choice: "B" });
hydrateServerSavedAnswers({
  active_questions: [{
    id: 9,
    response_mode: "group_consensus",
    group_consensus: { my_vote: { choice: "A" } },
  }],
});
if (liveCardDrafts.get("9:vote").choice !== "B") {
  throw new Error("vote draft clobbered");
}
console.log("ok");
"""
        )

    def test_rank_change_answer_ui_still_opens_controls(self) -> None:
        """``rankEdit`` keeps rank controls when ``can_submit`` is false after submit."""
        portal = PORTAL_JS.read_text(encoding="utf-8")
        self.assertIn(
            "(rankKind && rankEdit)",
            portal,
        )
        self.assertIn(
            "!hasStoredIndividualAnswer(item)) || (rankKind && rankEdit)",
            portal,
        )


def load_tests(loader: unittest.TestLoader, _tests: Any, _pattern: Any) -> unittest.TestSuite:
    """Run MCK-505 tests only, not the inherited live-backend suite."""
    suite = unittest.TestSuite()
    names = [
        name
        for name, value in vars(Mck505StudentRefreshAnswersTests).items()
        if name.startswith("test_") and callable(value)
    ]
    suite.addTests(Mck505StudentRefreshAnswersTests(name) for name in sorted(names))
    suite.addTests(loader.loadTestsFromTestCase(Mck505ClientTests))
    return suite


if __name__ == "__main__":
    unittest.main()