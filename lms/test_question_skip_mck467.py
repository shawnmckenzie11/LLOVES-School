#!/usr/bin/env python3
"""MCK-467: teacher Skip for one student on the current group question.

The skipped student stays in the class and the group, inherits no live
group award, and finishes that question alone in catch-up at most 1/1.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from live_news_wire import NewsAudience, event_visible, log_for
from school_db import QuestionSkipStale
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness

STAFF_JS = LMS_DIR / "static" / "staff_ap.js"


def _function_source(js: str, name: str) -> str:
    """Return one top-level ``function name`` from staff script source.

    Args:
        js: Full ``staff_ap.js`` text.
        name: Function name.
    """
    start = js.index(f"\nfunction {name}(") + 1
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        if js[index] == "{":
            depth += 1
        elif js[index] == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


class QuestionSkipTests(ChallengeHarness):
    """Skip, scoring rows, catch-up, auth, and group-scoped refresh."""

    def _skip(self, item: dict[str, Any], name: str, client: Any | None = None):
        """POST Skip for one roster student.

        Args:
            item: Published lifecycle row.
            name: Synthetic codename.
            client: Staff client. Defaults to the owning teacher.
        """
        http = client or self.client
        return http.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/question-skip",
            json={"student_id": self.ids[name]},
        )

    def _mc(self) -> dict[str, Any]:
        """Publish one keyed group multiple choice."""
        row = self._add(
            {
                "type": "mc",
                "text": "Slope of y = 2x + 1?",
                "options": ["2", "1", "-2", "0"],
                "correct_index": 0,
            }
        )
        return self._publish(row)

    def _turns(self) -> dict[str, Any]:
        """Publish one keyed take-turns rank."""
        row = self._rank_row(key=True)
        rv = self._settings(row, {"group_rank_mode": "turns"})
        self.assertEqual(rv.status_code, 200, rv.status_code)
        return self._publish(row)

    def _waits(self, item: dict[str, Any], name: str) -> list[dict[str, Any]]:
        """Teacher wait rows for this student's team."""
        team_id = self._card(name, item)["team_id"]
        board = self._view(item)
        rows = board.get("turns") or board.get("status_board") or board.get("teams") or []
        for row in rows:
            if int(row.get("team_id") or 0) == int(team_id):
                return list(row.get("question_waits") or [])
        turns = board.get("turns") or []
        for row in turns:
            if int(row.get("team_id") or 0) == int(team_id):
                return list(row.get("question_waits") or [])
        return []

    def _skip_rows(self) -> list[tuple[int, str, str]]:
        """Saved skip identity rows: student, question key, created_at."""
        with self.school._lock:
            found = self.school.conn.execute(
                """
                SELECT student_id, question_key, created_at
                FROM live_question_skips
                WHERE class_id = ?
                ORDER BY student_id, question_key
                """,
                (self.class_id,),
            ).fetchall()
        return [
            (int(row["student_id"]), str(row["question_key"]), str(row["created_at"]))
            for row in found
        ]

    def _members(self, item: dict[str, Any]) -> list[tuple[int, int]]:
        """Live group membership rows for one item."""
        with self.school._lock:
            found = self.school.conn.execute(
                """
                SELECT team_id, student_id FROM live_group_members
                WHERE live_item_id = ?
                ORDER BY team_id, student_id
                """,
                (int(item["id"]),),
            ).fetchall()
        return [(int(row["team_id"]), int(row["student_id"])) for row in found]

    def _token(self) -> str:
        """Catch-up token for this run. The value is not returned in messages."""
        with self.school._lock:
            row = self.school.conn.execute(
                """
                SELECT token FROM catchup_links
                WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, self._run_key()),
            ).fetchone()
        self.assertIsNotNone(row)
        return str(row["token"])

    def _end(self) -> None:
        """End the class the way a real run records group points."""
        self.school.close_live_class_for_celebration(self.class_id)

    def _run_key(self) -> str:
        """Run key for this live session."""
        with self.school._lock:
            row = self.school.conn.execute(
                "SELECT run_key FROM live_class_sessions WHERE id = ?",
                (self.session_id,),
            ).fetchone()
        return str(row["run_key"] or "")

    def _rows(self) -> dict[tuple[int, str], tuple[int, int, str]]:
        """Saved group points: ``(student, question)`` to earned, possible, source."""
        with self.school._lock:
            found = self.school.conn.execute(
                """
                SELECT student_id, question_key, earned, possible, source
                FROM group_engagement WHERE class_id = ?
                """,
                (self.class_id,),
            ).fetchall()
        return {
            (int(row["student_id"]), str(row["question_key"])): (
                int(row["earned"]),
                int(row["possible"]),
                str(row["source"]),
            )
            for row in found
        }

    def _question_key(self, item: dict[str, Any]) -> str:
        """``item:`` key End uses for this lifecycle row."""
        live = self._live(item)
        placement = str(live.get("placement_key") or "").strip() or str(live["id"])
        return f"item:{placement}"

    def _assert_team(
        self,
        question_key: str,
        names: tuple[str, ...],
        earned: int,
        possible: int = 1,
        source: str = "live",
    ) -> None:
        """Assert each named student stored the same group row."""
        rows = self._rows()
        for name in names:
            self.assertEqual(
                rows.get((self.ids[name], question_key)),
                (earned, possible, source),
                name,
            )

    def _stamp(self, name: str) -> str:
        """Per-student poll stamp."""
        return self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[name]
        )

    def test_group_mc_skip_unblocks_and_caps_catchup(self) -> None:
        """Skip one waiter. The others score 1/1. Catch-up for the skip is 1/1."""
        item = self._mc()
        self._post("Ava", item, "group-pick", {"choice": "2"})
        self._post("Cy", item, "group-pick", {"choice": "2"})
        waiting = self._waits(item, "Eli")
        eli = next(row for row in waiting if row["student_id"] == self.ids["Eli"])
        self.assertFalse(eli["skipped"])
        self.assertEqual(eli["name"], "Eli")
        membership = self._members(item)
        teams = self.school.game.team_membership_index(self.class_id)
        before = {name: self._stamp(name) for name in ("Ava", "Cy", "Eli", "Ben", "Dee", "Fay")}
        seq = int(
            self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0
        )
        tip = 0
        rows = log_for(self.school.data_dir).since(self.session_id, 0)
        if rows:
            tip = int(rows[-1]["id"])

        first = self._skip(item, "Eli")
        self.assertEqual(first.status_code, 200, first.status_code)
        body = first.get_json()
        self.assertFalse(body["repeated"])
        saved = self._skip_rows()
        self.assertEqual([row[0] for row in saved], [self.ids["Eli"]])
        again = self._skip(item, "Eli")
        self.assertEqual(again.status_code, 200, again.status_code)
        self.assertTrue(again.get_json()["repeated"])
        self.assertEqual(self._skip_rows(), saved)
        self.assertEqual(self._members(item), membership)
        self.assertEqual(self.school.game.team_membership_index(self.class_id), teams)
        with self.school._lock:
            left = self.school.conn.execute(
                """
                SELECT COUNT(*) AS n FROM live_session_attendees
                WHERE live_session_id = ? AND left_at IS NOT NULL
                """,
                (self.session_id,),
            ).fetchone()
        self.assertEqual(int(left["n"]), 0)
        skipped = self._waits(item, "Eli")
        self.assertEqual(
            [(row["student_id"], row["skipped"]) for row in skipped],
            [(self.ids["Eli"], True)],
        )
        self.assertEqual(
            int(self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0),
            seq,
        )
        for name in ("Ava", "Cy", "Eli"):
            self.assertNotEqual(self._stamp(name), before[name], name)
        for name in ("Ben", "Dee", "Fay"):
            self.assertEqual(self._stamp(name), before[name], name)
        news = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, tip)
            if row.get("type") == "response_landed"
        ]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].get("scope"), "group")
        team_id = int(news[0]["team_id"])
        self.assertTrue(event_visible(news[0], NewsAudience("staff")))
        self.assertTrue(
            event_visible(
                news[0],
                NewsAudience("student", student_id=self.ids["Ava"], team_ids={team_id}),
            )
        )
        other = self._card("Ben", item)["team_id"]
        self.assertFalse(
            event_visible(
                news[0],
                NewsAudience("student", student_id=self.ids["Ben"], team_ids={int(other)}),
            )
        )
        self.assertFalse(
            any(
                row.get("type") == "state_seq"
                for row in log_for(self.school.data_dir).since(self.session_id, tip)
            )
        )
        late = self._post("Eli", item, "group-pick", {"choice": "2"})
        self.assertEqual(late.status_code, 400, late.status_code)
        card = self._card("Ava", item)
        self.assertNotIn("Eli", card.get("waiting_names") or [])
        self.assertTrue(card["match"]["ready"])
        sent = self._post("Ava", item, "group-submit", {"choice": "2", "why": ""})
        self.assertEqual(sent.status_code, 200, sent.status_code)
        with self.school._lock:
            snap = self.school.conn.execute(
                """
                SELECT engagement_member_ids_json FROM live_group_responses
                WHERE live_item_id = ? AND team_id = ?
                """,
                (int(item["id"]), int(card["team_id"])),
            ).fetchone()
        members = json.loads(snap["engagement_member_ids_json"])
        self.assertIn(self.ids["Ava"], members)
        self.assertIn(self.ids["Cy"], members)
        self.assertNotIn(self.ids["Eli"], members)
        stale = self._skip(item, "Ava")
        self.assertEqual(stale.status_code, 409, stale.status_code)
        self.assertEqual([row[0] for row in self._skip_rows()], [self.ids["Eli"]])
        student = self._skip(item, "Eli", client=self.students["Eli"])
        self.assertEqual(student.status_code, 401, student.status_code)
        key = self._question_key(item)
        self._end()
        self._assert_team(key, ("Ava", "Cy"), 1, source="live")
        self.assertNotIn((self.ids["Eli"], key), self._rows())
        report = self.school.group_engagement_fractions(self.class_id, run_key=self._run_key())
        self.assertEqual(report.get(self.ids["Ava"]), "1/1")
        self.assertEqual(report.get(self.ids["Cy"]), "1/1")
        self.assertNotEqual(report.get(self.ids["Eli"]), "2/1")
        token = self._token()
        eli_keys = {
            q["question_key"]
            for q in self.school.catchup_questions(token, self.ids["Eli"])
            if q.get("question_skip") and not q.get("answered")
        }
        self.assertIn(key, eli_keys)
        for name in ("Ava", "Cy", "Ben"):
            keys = {
                q["question_key"]
                for q in self.school.catchup_questions(token, self.ids[name])
                if q.get("prompt_kind") == "mc" or q.get("question_skip")
            }
            self.assertNotIn(key, keys, name)
        self.assertTrue(
            self.school.submit_catchup_answer(token, self.ids["Eli"], key, "2")
        )
        self.assertEqual(self._rows()[(self.ids["Eli"], key)], (1, 1, "catchup"))
        self.assertFalse(
            self.school.submit_catchup_answer(token, self.ids["Eli"], key, "1")
        )
        link = self.school.get_catchup_link(token)
        self.assertTrue(
            self.school._store_skipped_question_catchup(link, self.ids["Eli"], key, "1")
        )
        self.assertEqual(self._rows()[(self.ids["Eli"], key)], (1, 1, "catchup"))
        self.assertEqual(
            self.school.group_engagement_fractions(self.class_id, run_key=self._run_key()).get(
                self.ids["Eli"]
            ),
            "1/1",
        )
        answered = [
            q
            for q in self.school.catchup_questions(token, self.ids["Eli"])
            if q["question_key"] == key
        ]
        self.assertTrue(answered and answered[0]["answered"])

    def test_rank_skip_keeps_the_bonus_off_catchup(self) -> None:
        """A no-check rank stays 2/1 for the group and 1/1 for the skip."""
        item = self._turns()
        first = self._post("Ava", item, "rank-turn", {"option_id": KEY_IDS[0]})
        self.assertEqual(first.status_code, 200, first.status_code)
        waiting = self._waits(item, "Eli")
        self.assertIn(self.ids["Eli"], [row["student_id"] for row in waiting if not row["skipped"]])
        skipped = self._skip(item, "Eli")
        self.assertEqual(skipped.status_code, 200, skipped.status_code)
        for token in KEY_IDS[1:]:
            status = 0
            for name in ("Ava", "Cy"):
                rv = self._post(name, item, "rank-turn", {"option_id": token})
                status = rv.status_code
                if status == 200:
                    break
            else:
                self.fail(str(status))
        late = self._post("Eli", item, "rank-turn", {"option_id": KEY_IDS[1]})
        self.assertEqual(late.status_code, 400, late.status_code)
        card = self._card("Ava", item)
        if card.get("turns", {}).get("done") and not card.get("submitted"):
            sent = self._post("Ava", item, "group-submit", {})
            self.assertEqual(sent.status_code, 200, sent.status_code)
        key = self._question_key(item)
        self._end()
        self._assert_team(key, ("Ava", "Cy"), 2, source="live")
        self.assertNotIn((self.ids["Eli"], key), self._rows())
        report = self.school.group_engagement_fractions(self.class_id, run_key=self._run_key())
        self.assertEqual(report.get(self.ids["Ava"]), "2/1")
        self.assertNotIn(self.ids["Eli"], report)
        token = self._token()
        questions = [
            q
            for q in self.school.catchup_questions(token, self.ids["Eli"])
            if q["question_key"] == key
        ]
        self.assertEqual(len(questions), 1)
        self.assertTrue(questions[0].get("group_rank"))
        self.assertNotIn("rank_key", json.dumps(questions[0]["content"]))
        content = questions[0]["content"]
        options = content.get("rank_options") or content.get("options") or []
        order = [str(opt["id"]) for opt in options]
        self.assertTrue(
            self.school.submit_catchup_answer(token, self.ids["Eli"], key, order)
        )
        self.assertEqual(self._rows()[(self.ids["Eli"], key)], (1, 1, "catchup"))
        shown = self.school.group_engagement_fractions(self.class_id, run_key=self._run_key())
        self.assertEqual(shown.get(self.ids["Ava"]), "2/1")
        self.assertEqual(shown.get(self.ids["Eli"]), "1/1")

    def test_unsent_group_still_leaves_individual_catchup(self) -> None:
        """The obligation does not depend on the group finishing."""
        item = self._mc()
        self.assertEqual(self._skip(item, "Eli").status_code, 200)
        key = self._question_key(item)
        self._end()
        self.assertNotIn((self.ids["Eli"], key), self._rows())
        self.assertNotIn((self.ids["Ava"], key), self._rows())
        token = self._token()
        eli = [
            q
            for q in self.school.catchup_questions(token, self.ids["Eli"])
            if q["question_key"] == key
        ]
        self.assertEqual(len(eli), 1)
        self.assertFalse(eli[0]["answered"])
        self.assertEqual(eli[0]["prompt_kind"], "mc")
        ava = [
            q["question_key"]
            for q in self.school.catchup_questions(token, self.ids["Ava"])
        ]
        self.assertNotIn(key, ava)
        self.assertFalse(
            self.school.submit_catchup_answer(token, self.ids["Eli"], key, "")
        )
        self.assertNotIn((self.ids["Eli"], key), self._rows())
        self.assertTrue(
            self.school.submit_catchup_answer(token, self.ids["Eli"], key, "2")
        )
        self.assertEqual(self._rows()[(self.ids["Eli"], key)], (1, 1, "catchup"))

    def test_saved_award_is_not_rewritten(self) -> None:
        """A positive stored award rejects Skip and stays as written."""
        item = self._mc()
        key = self._question_key(item)
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES (?, ?, ?, ?, 2, 1, 'live', '2026-11-09T12:00:00')
                """,
                (self.class_id, self._run_key(), key, self.ids["Eli"]),
            )
            self.school.conn.commit()
        rv = self._skip(item, "Eli")
        self.assertEqual(rv.status_code, 409, rv.status_code)
        self.assertEqual(self._rows()[(self.ids["Eli"], key)], (2, 1, "live"))
        self.assertEqual(self._skip_rows(), [])
        self.assertIsInstance(QuestionSkipStale("already saved"), QuestionSkipStale)

    def test_other_teacher_cannot_skip(self) -> None:
        """Only the teacher who owns the class can skip."""
        item = self._mc()
        self.school.register_staff("other-teacher@example.com")
        other = self.app.test_client()
        other.get("/auth/google?portal=staff")
        other.get("/auth/google/callback?email=other-teacher@example.com&name=Other")
        code = self.school.get_user_by_email("other-teacher@example.com")["verification_code"]
        other.post("/verify-email", data={"code": code})
        rv = self._skip(item, "Eli", client=other)
        self.assertEqual(rv.status_code, 403, rv.status_code)
        self.assertEqual(self._skip_rows(), [])

    def test_consensus_skip_advances_without_points(self) -> None:
        """A missing vote no longer blocks, and the skip gets no team points."""
        row = self._add(
            {
                "type": "numeric",
                "text": "What is 3 + 4?",
                "correct_answer": "7",
                "integer_only": True,
            }
        )
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "group"},
        )
        self.assertEqual(rv.status_code, 200, rv.status_code)
        item = rv.get_json()["item"]
        for name in ("Ava", "Cy"):
            vote = self._post(name, item, "vote", {"value": "7"})
            self.assertEqual(vote.status_code, 200, vote.status_code)
        held = self.school.student_group_consensus_state(self._live(item), self.ids["Ava"])
        self.assertEqual(held["status"], "collecting_votes")
        skipped = self._skip(item, "Eli")
        self.assertEqual(skipped.status_code, 200, skipped.status_code)
        moved = self.school.student_group_consensus_state(self._live(item), self.ids["Ava"])
        self.assertNotEqual(moved["status"], "collecting_votes")
        late = self._post("Eli", item, "vote", {"value": "8"})
        self.assertEqual(late.status_code, 400, late.status_code)
        team_id = int(moved["team_id"])
        finalized = self.school.finalize_group_consensus_answer(
            self.session_id, int(item["id"]), self.ids["Ava"], {"value": "7"}
        )
        self.assertEqual(finalized["status"], "finalized")
        awarded = self.school.award_group_consensus_points(
            self.session_id, int(item["id"]), team_id=team_id, amount=1
        )
        self.assertIn(self.ids["Ava"], awarded["awarded_student_ids"])
        self.assertIn(self.ids["Cy"], awarded["awarded_student_ids"])
        self.assertNotIn(self.ids["Eli"], awarded["awarded_student_ids"])

    def test_teacher_panel_names_the_action_and_the_status(self) -> None:
        """The waiting control and the skipped line render together."""
        js = STAFF_JS.read_text(encoding="utf-8")
        src = "\n".join(
            [
                js[js.index("const QUESTION_SKIP_COPY") : js.index("});", js.index("const QUESTION_SKIP_COPY")) + 3],
                "function questionsMaskStudentDetail() { return false; }",
                "function escapeHtml(s) { return String(s ?? ''); }",
                _function_source(js, "questionWaitHtml"),
            ]
        )
        script = src + """
const html = questionWaitHtml([
  { student_id: 4, name: "Cy", skipped: false },
  { student_id: 9, name: "Eli", skipped: true }
], 3, 1);
console.log(html);
"""
        done = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        html = done.stdout
        self.assertIn("Skip for this question", html)
        self.assertIn("Skipped — catch-up required.", html)
        self.assertIn('data-question-skip="3"', html)
        self.assertIn('data-student-id="4"', html)
        self.assertNotIn('data-student-id="9"', html)
        self.assertIn("Eli", html)
        self.assertIn("Cy", html)


if __name__ == "__main__":
    unittest.main()
