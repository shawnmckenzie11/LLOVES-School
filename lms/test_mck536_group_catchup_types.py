#!/usr/bin/env python3
"""MCK-536: Catch-Up Group lists MC, Numeric, Poll, and Show Your Work.

End Class writes those catalogue rows without changing live or team
point totals. Catch-up answers are capped at 1/1 with source='catchup'
and do not touch live_group_responses or team rows.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from board_poll_score import _ensure_group_item
from group_engagement import group_catchup_score
from test_group_engagement_mck293 import TEAM_A, TEAM_B
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness


BANNED_PAYLOAD = (
    "rank_key",
    "correct_index",
    "correct",
    "answer_key",
    "correct_answer",
    "pin",
    "email",
    "codename",
    "session_code",
)


class GroupCatchupScoreRuleTests(unittest.TestCase):
    """Pure cap. No database."""

    def test_group_catchup_score_is_one_of_one(self) -> None:
        self.assertEqual(group_catchup_score(None), (1, 1))
        self.assertEqual(group_catchup_score(0), (1, 1))
        self.assertEqual(group_catchup_score(1), (1, 1))
        self.assertEqual(group_catchup_score(2), (2, 1))
        self.assertEqual(group_catchup_score(4), (4, 1))


class GroupCatchupTypesTests(ChallengeHarness):
    """End Class catalogue widening plus Catch-Up Group answers."""

    def _ids(self, names: tuple[str, ...]) -> list[int]:
        return [self.ids[name] for name in names]

    def _end(self) -> None:
        self.school.close_live_class_for_celebration(self.class_id)

    def _rows(self) -> dict[tuple[int, str], tuple[int, int, str]]:
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
        live = self._live(item)
        placement = str(live.get("placement_key") or "").strip() or str(live["id"])
        return f"item:{placement}"

    def _run_key(self) -> str:
        with self.school._lock:
            row = self.school.conn.execute(
                "SELECT run_key FROM live_class_sessions WHERE id = ?",
                (self.session_id,),
            ).fetchone()
        return str(row["run_key"] or "")

    def _open(self) -> dict[str, Any]:
        row = self._rank_row(key=True)
        rv = self._settings(row, {"group_rank_mode": "turns"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:180])
        return self._publish(row)

    def _publish_group_catalogue(self, body: dict[str, Any]) -> dict[str, Any]:
        """Publish, then mark group_submit. The API only group-publishes MC and rank."""
        row = self._add(body)
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:200])
        item = rv.get_json()["item"]
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_items
                SET publish_mode = 'group_submit', response_mode = 'group_submit'
                WHERE id = ?
                """,
                (int(item["id"]),),
            )
            if self.school.conn.in_transaction:
                self.school.conn.commit()
        return item

    def _mark_one_team_sent(self, item: dict[str, Any]) -> None:
        """A send with no members, so End writes the catalogue row and no points."""
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_group_responses (
                    live_item_id, team_id, submit_count, final_answer_json,
                    engagement_member_ids_json, created_at, updated_at
                ) VALUES (?, 1, 1, '{}', '[]', '2026-11-27T18:00:00+00:00',
                           '2026-11-27T18:00:00+00:00')
                """,
                (int(item["id"]),),
            )
            if self.school.conn.in_transaction:
                self.school.conn.commit()

    def _place_only(self, item: dict[str, Any], names: tuple[str, ...], order: list[str]) -> None:
        for token in order:
            status = 0
            for name in names:
                rv = self._post(name, item, "rank-turn", {"option_id": token})
                status = rv.status_code
                if status == 200:
                    break
            else:
                self.fail(f"place status {status}")

    def _agree(self, item: dict[str, Any], names: tuple[str, ...], choice: str) -> None:
        for name in names:
            rv = self._post(name, item, "group-pick", {"choice": choice})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:200])
        sent = self._post(names[0], item, "group-submit", {})
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:240])

    def _token(self) -> str:
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

    def _clear_board_cache(self) -> None:
        self.school._board_group_cache = {}

    def _totals_snapshot(self) -> str:
        students = sorted(self.ids.values())
        self._clear_board_cache()
        with self.school._lock:
            live = {
                str(sid): list(
                    self.school._live_group_engagement_map(self.session_id).get(sid, (0, 0))
                )
                for sid in students
            }
            board = {
                str(sid): list(
                    self.school._stored_group_engagement_unlocked(
                        self.class_id, self._run_key(), sid
                    )
                )
                for sid in students
            }
        grid = {
            str(sid): frac
            for sid, frac in sorted(
                self.school.group_engagement_fractions(
                    self.class_id, run_key=self._run_key()
                ).items()
            )
        }
        counts = self.school._lesson_slides_group_engagement_index(
            self.class_id, self._run_key(), set()
        )
        counts_s = {
            key: {
                "kind": value["kind"],
                "catchup_completed": value["catchup_completed"],
            }
            for key, value in sorted(counts.items())
        }
        return json.dumps(
            {"live": live, "grid": grid, "board": board, "counts": counts_s},
            sort_keys=True,
            separators=(",", ":"),
        )

    def _responses_snapshot(self) -> str:
        with self.school._lock:
            rows = self.school.conn.execute(
                """
                SELECT live_item_id, team_id, submit_count, final_answer_json,
                       engagement_member_ids_json
                FROM live_group_responses
                ORDER BY live_item_id, team_id
                """
            ).fetchall()
        payload = [
            {
                "live_item_id": int(row["live_item_id"]),
                "team_id": int(row["team_id"]),
                "submit_count": int(row["submit_count"] or 0),
                "final_answer_json": str(row["final_answer_json"] or ""),
                "engagement_member_ids_json": str(row["engagement_member_ids_json"] or ""),
            }
            for row in rows
        ]
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _team_rows_snapshot(self, names: tuple[str, ...]) -> str:
        ids = set(self._ids(names))
        rows = {
            f"{sid}:{key}": list(value)
            for (sid, key), value in self._rows().items()
            if sid in ids
        }
        return json.dumps(rows, sort_keys=True, separators=(",", ":"))

    def _item_copy(self, question_key: str) -> tuple[str, str]:
        with self.school._lock:
            row = self.school.conn.execute(
                """
                SELECT item_json, prompt_kind FROM group_engagement_items
                WHERE class_id = ? AND run_key = ? AND question_key = ?
                """,
                (self.class_id, self._run_key(), question_key),
            ).fetchone()
        self.assertIsNotNone(row)
        return str(row["item_json"] or ""), str(row["prompt_kind"] or "")

    def _seed_class(self) -> dict[str, str]:
        rank = self._open()
        self._place_only(rank, TEAM_A, KEY_IDS)
        sent = self._post("Ava", rank, "group-submit", {})
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:200])

        mc = self._publish(
            self._add(
                {
                    "type": "mc",
                    "text": "Pick a direction.",
                    "options": ["North", "East", "South", "West"],
                    "correct_index": 0,
                }
            )
        )
        self._agree(mc, TEAM_A, "North")

        poll = self._publish_group_catalogue(
            {
                "type": "poll",
                "text": "Favorite color?",
                "options": ["Red", "Blue", "Green"],
            }
        )
        self._mark_one_team_sent(poll)

        numeric = self._publish_group_catalogue({"type": "numeric", "text": "How many sides?", "correct_answer": "4"})
        board = self._publish(
            self._add(
                {
                    "type": "team_draw",
                    "text": "Show the graph.",
                    "answer_with": "board",
                }
            )
        )
        keys = {
            "rank": self._question_key(rank),
            "mc": self._question_key(mc),
            "poll": self._question_key(poll),
            "numeric": self._question_key(numeric),
            "board": self._question_key(board),
        }
        live_board = self._live(board)
        _ensure_group_item(
            self.school,
            self.session_id,
            self._run_key(),
            keys["board"],
            live_board,
        )
        now = "2026-11-27T18:00:00+00:00"
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES (?, ?, ?, ?, 1, 1, 'live', ?)
                """,
                (
                    self.class_id,
                    self._run_key(),
                    keys["board"],
                    self.ids["Ava"],
                    now,
                ),
            )
            if self.school.conn.in_transaction:
                self.school.conn.commit()
        return keys

    def test_end_class_keeps_totals_and_lists_all_four_group_types(self) -> None:
        keys = self._seed_class()
        board_json, board_kind = self._item_copy(keys["board"])
        self.assertEqual(board_kind, "board")
        before = json.loads(self._totals_snapshot())
        # Live totals are taken before End: End Class drops live_group_responses.
        self.assertEqual(before["live"][str(self.ids["Ava"])], [3, 2])
        self.assertEqual(before["live"][str(self.ids["Cy"])], [3, 2])
        self.assertEqual(before["live"][str(self.ids["Eli"])], [3, 2])
        self.assertEqual(before["live"][str(self.ids["Ben"])], [0, 2])
        self._end()
        after = json.loads(self._totals_snapshot())
        self.assertEqual(after["board"], before["board"])
        after_json, after_kind = self._item_copy(keys["board"])
        self.assertEqual(after_kind, "board")
        self.assertEqual(after_json, board_json)
        # Existing rank/mc/poll-as-mc plus Ava's board points only.
        ava = str(self.ids["Ava"])
        cy = str(self.ids["Cy"])
        eli = str(self.ids["Eli"])
        ben = str(self.ids["Ben"])
        self.assertEqual(after["grid"][ava], "4/3")
        self.assertEqual(after["grid"][cy], "3/2")
        self.assertEqual(after["grid"][eli], "3/2")
        self.assertEqual(after["grid"][ben], "0/2")
        self.assertEqual(after["counts"][keys["rank"]]["kind"], "rank")
        self.assertEqual(after["counts"][keys["mc"]]["kind"], "mc")
        self.assertEqual(after["counts"][keys["poll"]]["kind"], "mc")
        self.assertNotIn(keys["numeric"], after["counts"])
        self.assertNotIn(keys["board"], after["counts"])
        with self.school._lock:
            numeric_points = self.school.conn.execute(
                """
                SELECT COUNT(*) AS n FROM group_engagement
                WHERE class_id = ? AND question_key = ?
                """,
                (self.class_id, keys["numeric"]),
            ).fetchone()
        self.assertEqual(int(numeric_points["n"] or 0), 0)

        token = self._token()
        questions = self.school.catchup_questions(token, self.ids["Ben"])
        group = [q for q in questions if q.get("group_item") or q.get("category") == "group"]
        labels = {q.get("type_label") for q in group}
        self.assertEqual(
            labels,
            {"Rank", "MC", "Poll", "Numeric", "Show Your Work"},
        )
        by_label = {q.get("type_label"): q for q in group}
        for label in ("MC", "Numeric", "Poll", "Show Your Work"):
            self.assertIn(label, by_label)
            raw = json.dumps(by_label[label].get("content") or {})
            for banned in BANNED_PAYLOAD:
                self.assertNotIn(banned, raw)
            self.assertNotIn("name", (by_label[label].get("content") or {}))

    def test_group_catchup_answers_cap_at_one_and_leave_team_totals(self) -> None:
        keys = self._seed_class()
        self._end()
        token = self._token()
        link = self.school.get_catchup_link(token)
        ben = self.ids["Ben"]
        team_before = self._team_rows_snapshot(TEAM_A)
        responses_before = self._responses_snapshot()
        live_before = json.loads(self._totals_snapshot())["live"]

        questions = {
            q["question_key"]: q
            for q in self.school.catchup_questions(token, ben)
            if q.get("group_item")
        }
        mc = questions[keys["mc"]]
        options = (mc.get("content") or {}).get("options") or []
        mc_choice = ""
        if options and isinstance(options[0], dict):
            mc_choice = str(options[0].get("label") or options[0].get("text") or "North")
        else:
            mc_choice = str(options[0] if options else "North")
        poll = questions[keys["poll"]]
        poll_opts = (poll.get("content") or {}).get("options") or []
        poll_choice = ""
        if poll_opts and isinstance(poll_opts[0], dict):
            poll_choice = str(poll_opts[0].get("label") or poll_opts[0].get("text") or "Blue")
        else:
            poll_choice = str(poll_opts[0] if poll_opts else "Blue")

        self.assertTrue(self.school.submit_catchup_answer(token, ben, keys["mc"], mc_choice))
        self.assertTrue(self.school.submit_catchup_answer(token, ben, keys["poll"], poll_choice))
        self.assertTrue(self.school.submit_catchup_answer(token, ben, keys["numeric"], "12"))
        self.assertTrue(
            self.school._store_group_engagement_catchup(link, ben, keys["board"], "done")
        )
        for key in (keys["mc"], keys["poll"], keys["numeric"], keys["board"]):
            self.assertEqual(self._rows()[(ben, key)], (1, 1, "catchup"), key)
            # Cap holds on a second write.
            self.assertTrue(
                self.school._write_group_catchup_score_row(link, ben, key)
            )
            self.assertEqual(self._rows()[(ben, key)], (1, 1, "catchup"), key)

        self.assertEqual(self._team_rows_snapshot(TEAM_A), team_before)
        self.assertEqual(self._responses_snapshot(), responses_before)
        live_after = json.loads(self._totals_snapshot())["live"]
        self.assertEqual(live_after, live_before)

    def test_group_catchup_phone_presence_saves_and_is_refused_after_submit(self) -> None:
        """Phone points and Clear persist on a Group catch-up board.

        Presence and wipe do not write a score or participation row. A
        later Submit sees the saved ink. After Submit, Clear is refused.
        """
        from catchup_session import establish_catchup_identity
        from question_board import LOCKED_LINE

        board = self._publish(
            self._add(
                {
                    "type": "team_draw",
                    "text": "Show the graph.",
                    "answer_with": "board",
                }
            )
        )
        key = self._question_key(board)
        _ensure_group_item(
            self.school,
            self.session_id,
            self._run_key(),
            key,
            self._live(board),
        )
        self._end()
        token = self._token()
        # A roster student with no live board completion.
        sid = self.ids["Ben"]
        self.school.set_student_pin(self.class_id, sid, "2468")
        phone = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        with phone.session_transaction() as sess:
            establish_catchup_identity(sess, self.school, self.class_id, sid, stamp)
        base = f"/api/catchup/{token}/group-boards/{key}/board"

        def _score_rows() -> int:
            with self.school._lock:
                row = self.school.conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM group_engagement
                    WHERE class_id = ? AND question_key = ? AND student_id = ?
                    """,
                    (self.class_id, key, sid),
                ).fetchone()
                part = self.school.conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM participation_answers
                    WHERE class_id = ? AND student_id = ? AND source = 'catchup'
                    """,
                    (self.class_id, sid),
                ).fetchone()
            return int(row["n"] or 0), int(part["n"] or 0)

        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.1, 0.2], [0.4, 0.5], [0.7, 0.3]],
                "stroke_id": "g-ink",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        loaded = phone.get(base)
        self.assertEqual(loaded.status_code, 200, loaded.get_json())
        stroke_ids = {
            str(row.get("id") or "")
            for row in (loaded.get_json().get("canvas_view") or {}).get("strokes") or []
        }
        self.assertIn("g-ink", stroke_ids)
        self.assertEqual(_score_rows(), (0, 0))

        wiped = phone.post(
            f"{base}/ops",
            json={"op": "board_wipe", "wipe_id": "g-wipe", "id": "g-wipe"},
        )
        self.assertEqual(wiped.status_code, 200, wiped.get_json())
        after_wipe = phone.get(base)
        self.assertEqual(after_wipe.status_code, 200, after_wipe.get_json())
        wiped_ids = {
            str(row.get("id") or "")
            for row in (after_wipe.get_json().get("canvas_view") or {}).get("strokes") or []
        }
        self.assertNotIn("g-ink", wiped_ids)
        self.assertEqual(_score_rows(), (0, 0))

        kept = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.2, 0.3], [0.5, 0.6], [0.8, 0.4]],
                "stroke_id": "g-kept",
                "ended": True,
            },
        )
        self.assertEqual(kept.status_code, 200, kept.get_json())
        submitted = phone.post(f"{base}/submit", json={"text": ""})
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        self.assertTrue((submitted.get_json() or {}).get("editing_locked"))
        after_submit = phone.get(base)
        self.assertEqual(after_submit.status_code, 200, after_submit.get_json())
        saved_ids = {
            str(row.get("id") or "")
            for row in (after_submit.get_json().get("canvas_view") or {}).get("strokes") or []
        }
        self.assertIn("g-kept", saved_ids)
        self.assertNotIn("g-ink", saved_ids)

        late = phone.post(
            f"{base}/ops",
            json={"op": "board_wipe", "wipe_id": "late-wipe", "id": "late-wipe"},
        )
        self.assertEqual(late.status_code, 409, late.get_json())
        self.assertEqual((late.get_json() or {}).get("error"), LOCKED_LINE)
        still = phone.get(base)
        still_ids = {
            str(row.get("id") or "")
            for row in (still.get_json().get("canvas_view") or {}).get("strokes") or []
        }
        self.assertIn("g-kept", still_ids)


if __name__ == "__main__":
    unittest.main()
