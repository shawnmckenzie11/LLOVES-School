#!/usr/bin/env python3
"""MCK-293 group-rank participation points.

A keyed rank sent all correct, without Check our ranking, stores 4 on a
question whose maximum is 2. Every other completion stores 2. The check
may be used once and returns a count only. Catch-up stores 2, never 4.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from group_engagement import catchup_earned, check_message, send_earned
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness

TEAM_A = ("Ava", "Cy", "Eli")
TEAM_B = ("Ben", "Dee", "Fay")
COUNT_ONLY = re.compile(r"^Checked: \d+ of \d+ in the right spot$")
CHECK_KEYS = {"available", "used", "right", "total", "message"}
BANNED = "A" + "+"
FEATURE_ROOTS = (
    Path(__file__).resolve().parent,
    Path(__file__).resolve().parents[1] / "scripts" / "shoot.py",
)


class PointRuleTests(unittest.TestCase):
    """Pure point rules. No database and no answer order."""

    def test_bonus_only_without_the_check(self) -> None:
        self.assertEqual(send_earned(keyed=True, all_correct=True, check_used=False), 4)
        self.assertEqual(send_earned(keyed=True, all_correct=True, check_used=True), 2)
        self.assertEqual(send_earned(keyed=True, all_correct=False, check_used=False), 2)
        self.assertEqual(send_earned(keyed=False, all_correct=True, check_used=False), 2)
        self.assertEqual(catchup_earned(None), 2)
        self.assertEqual(catchup_earned(0), 2)
        self.assertEqual(catchup_earned(1), 2)
        self.assertEqual(catchup_earned(2), 2)
        self.assertEqual(catchup_earned(4), 4)
        self.assertEqual(check_message(2, 4), "Checked: 2 of 4 in the right spot")
        self.assertEqual(check_message(1, 1), "Checked: 1 of 1 in the right spot")

    def test_feature_files_have_no_extra_grade_word(self) -> None:
        """The withdrawn grade word does not appear in this feature."""
        hits: list[str] = []
        for root in FEATURE_ROOTS:
            files = [root] if root.is_file() else root.rglob("*")
            for path in files:
                if not path.is_file():
                    continue
                if "vendor" in path.parts or path.suffix not in {".py", ".js", ".mjs", ".html", ".css"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if BANNED in text:
                    hits.append(str(path.relative_to(path.parents[2] if len(path.parents) > 2 else path.parent)))
        self.assertEqual(hits, [])


class GroupEngagementTests(ChallengeHarness):
    """End writes one row per send-time member, keyed by class and run."""

    def _ids(self, names: tuple[str, ...]) -> list[int]:
        return [self.ids[name] for name in names]

    def _open(self, *, key: bool = True) -> dict[str, Any]:
        """Publish one group rank as take turns."""
        row = self._rank_row(key=key)
        rv = self._settings(row, {"group_rank_mode": "turns"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:180])
        return self._publish(row)

    def _place(self, item: dict[str, Any], names: tuple[str, ...], order: list[str]) -> None:
        """Place ``order`` one spot at a time on that team's turn."""
        for token in order:
            status = 0
            for name in names:
                rv = self._post(name, item, "rank-turn", {"option_id": token})
                status = rv.status_code
                if status == 200:
                    break
            else:
                self.fail(f"place status {status}")
        card = self._card(names[0], item)
        if card.get("turns", {}).get("done") and not card.get("submitted") and not card.get("race"):
            sent = self._post(names[0], item, "group-submit", {})
            self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:200])

    def _check(self, item: dict[str, Any], name: str = "Ava"):
        return self._post(name, item, "rank-check", {})

    def _end(self) -> None:
        self.school.close_live_class_for_celebration(self.class_id)

    def _rows(self) -> dict[tuple[int, str], tuple[int, int, str]]:
        """``(student_id, question_key)`` to earned, possible, source."""
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

    def _assert_team(
        self,
        question_key: str,
        names: tuple[str, ...],
        earned: int,
        possible: int = 2,
        source: str = "live",
    ) -> None:
        rows = self._rows()
        for sid in self._ids(names):
            self.assertEqual(rows.get((sid, question_key)), (earned, possible, source), sid)

    def test_schema_has_no_session_id(self) -> None:
        with self.school._lock:
            for table in ("group_engagement", "group_engagement_items"):
                cols = {
                    str(row[1])
                    for row in self.school.conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                self.assertNotIn("live_session_id", cols, table)
                self.assertIn("run_key", cols, table)
                self.assertIn("class_id", cols, table)

    def test_one_check_then_a_correct_send_is_one(self) -> None:
        item = self._open()
        empty = self._check(item)
        self.assertEqual(empty.status_code, 400, empty.get_data(as_text=True)[:180])
        self._place(item, TEAM_A, KEY_IDS[:2])
        first = self._check(item)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True)[:240])
        body = first.get_json()
        raw = first.get_data(as_text=True)
        self.assertNotIn("rank_key", raw)
        check = body["group_submit"]["check"]
        self.assertTrue(set(check).issubset(CHECK_KEYS))
        self.assertNotIn("spots", check)
        self.assertFalse(check["available"])
        self.assertTrue(check["used"])
        self.assertEqual(check["message"], "Checked: 2 of 4 in the right spot")
        self.assertRegex(check["message"], COUNT_ONLY)
        for label in ("Two points", "A graph", "An equation", "A table"):
            self.assertNotIn(label, check["message"])
        again = self._check(item)
        self.assertEqual(again.status_code, 400)
        self.assertNotIn("rank_key", again.get_data(as_text=True))
        held = self._card("Ava", item)["check"]
        self.assertEqual(held["right"], 2)
        self.assertEqual(held["total"], 4)
        self.assertEqual(held["message"], "Checked: 2 of 4 in the right spot")
        self._place(item, TEAM_A, KEY_IDS[2:])
        sent = self._check(item)
        self.assertEqual(sent.status_code, 400)
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 2)
        self._assert_team(key, TEAM_B, 0)
        with self.school._lock:
            items = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM participation_items WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        self.assertEqual(int(items["n"]), 0)

    def test_perfect_send_without_a_check_is_two(self) -> None:
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        card = self._card("Ava", item)
        self.assertFalse(card["check"]["used"])
        self.assertFalse(card["check"]["available"])
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 4)
        self._assert_team(key, TEAM_B, 0)
        run_key = self._run_key()
        ava = self.ids["Ava"]
        ben = self.ids["Ben"]
        dash = self.school.student_dashboard_payload(self.class_id, ava)
        self.assertEqual(dash["group_engagement"], "4/2")
        self.assertEqual(dash["individual_engagement"], "0/0")
        other = self.school.student_dashboard_payload(self.class_id, ben)
        self.assertEqual(other["group_engagement"], "0/2")
        summary = self.school.engagement_summary_for_run(self.class_id, run_key)
        self.assertIsNotNone(summary)
        stamped = {}
        for group in list(summary["groups"]) + [{"students": summary["ungrouped"]}]:
            for student in group.get("students") or []:
                if "group_engagement" in student:
                    stamped[int(student["student_id"])] = student["group_engagement"]
        self.assertEqual(stamped.get(ava), "4/2")
        self.assertEqual(stamped.get(ben), "0/2")
        runs = {row["run_key"] for row in self.school.list_engagement_summary_runs(self.class_id)}
        self.assertIn(run_key, runs)
        grid = self.school.participation_week_grid(self.class_id)
        self.assertEqual(grid["group_engagement"].get(str(ava)), "4/2")
        self.assertEqual(grid["group_engagement"].get(str(ben)), "0/2")
        self.school.delete_lesson_slides_entry(self.class_id, "M1", "C2", False, run_key)
        self._assert_team(key, TEAM_A, 4)

    def test_unkeyed_and_wrong_orders_are_one(self) -> None:
        plain = self._open(key=False)
        keyed = self._open()
        self._place(plain, TEAM_A, KEY_IDS)
        self._place(keyed, TEAM_A, list(reversed(KEY_IDS)))
        refused = self._check(plain)
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("rank_key", refused.get_data(as_text=True))
        offered = self._card("Ava", plain)["check"]
        self.assertEqual(offered, {"available": False, "used": False})
        plain_key = self._question_key(plain)
        keyed_key = self._question_key(keyed)
        self._end()
        self._assert_team(plain_key, TEAM_A, 2)
        self._assert_team(keyed_key, TEAM_A, 2)
        dash = self.school.student_dashboard_payload(self.class_id, self.ids["Ava"])
        self.assertEqual(dash["group_engagement"], "4/4")

    def test_a_test_run_writes_nothing(self) -> None:
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_class_sessions SET is_test = 1, created_as_test = 1 WHERE id = ?",
                (self.session_id,),
            )
            self.school.conn.commit()
        self._end()
        self.assertEqual(self._rows(), {})

    def test_quit_writes_nothing(self) -> None:
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        self.school.cleanup_live_session_response_data(self.session_id, discard=True)
        self.assertEqual(self._rows(), {})

    def test_close_without_end_writes_nothing(self) -> None:
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        self.school.cleanup_live_session_response_data(
            self.session_id, record_participation=False
        )
        self.assertEqual(self._rows(), {})

    def test_send_time_roster_not_a_later_member(self) -> None:
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        team_id = self._team("Ava", item)
        ava = self.ids["Ava"]
        ben = self.ids["Ben"]
        with self.school._lock:
            self.school.conn.execute(
                """
                DELETE FROM live_group_members
                WHERE live_item_id = ? AND student_id = ?
                """,
                (int(item["id"]), ava),
            )
            self.school.conn.execute(
                """
                INSERT OR IGNORE INTO live_group_members (
                    live_item_id, team_id, student_id, joined_at
                ) VALUES (?, ?, ?, '2026-10-20T00:00:00+00:00')
                """,
                (int(item["id"]), int(team_id), ben),
            )
            self.school.conn.commit()
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 4)
        self.assertEqual(self._rows()[(ben, key)][0], 0)

    def test_catchup_stays_at_one_even_for_the_answer_order(self) -> None:
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        key = self._question_key(item)
        self._end()
        with self.school._lock:
            link_row = self.school.conn.execute(
                """
                SELECT token FROM catchup_links
                WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, self._run_key()),
            ).fetchone()
        self.assertIsNotNone(link_row)
        token = str(link_row["token"])
        ava = self.ids["Ava"]
        ben = self.ids["Ben"]
        ava_questions = self.school.catchup_questions(token, ava)
        self.assertFalse(any(q.get("group_rank") for q in ava_questions))
        ben_questions = [q for q in self.school.catchup_questions(token, ben) if q.get("group_rank")]
        self.assertEqual(len(ben_questions), 1)
        content = ben_questions[0]["content"]
        self.assertNotIn("rank_key", json.dumps(content))
        options = content.get("rank_options") or content.get("options") or []
        public_ids = [str(opt["id"]) for opt in options]
        self.assertEqual(len(public_ids), 4)
        link = self.school.get_catchup_link(token)
        stored_row = None
        with self.school._lock:
            stored_row = self.school.conn.execute(
                """
                SELECT item_json FROM group_engagement_items
                WHERE class_id = ? AND question_key = ?
                """,
                (self.class_id, key),
            ).fetchone()
        stored = json.loads(stored_row["item_json"])
        aliases = self.school.student_rank_aliases(stored)
        self.assertIsNotNone(aliases)
        answer_order = [aliases.out(opt) for opt in stored["rank_key"]]
        self.assertTrue(
            self.school.submit_catchup_answer(token, ben, key, answer_order)
        )
        self.assertEqual(self._rows()[(ben, key)], (2, 2, "catchup"))
        self.assertTrue(
            self.school._store_group_engagement_catchup(link, ben, key, list(reversed(public_ids)))
        )
        self.assertEqual(self._rows()[(ben, key)], (2, 2, "catchup"))
        self.assertTrue(
            self.school._store_group_engagement_catchup(link, ava, key, answer_order)
        )
        self.assertEqual(self._rows()[(ava, key)], (4, 2, "live"))
        again = self.school.catchup_questions(token, ben)
        answered = next(q for q in again if q.get("question_key") == key)
        self.assertTrue(answered["answered"])
        self.assertFalse(self.school.submit_catchup_answer(token, ben, key, public_ids))
        self.assertEqual(self._rows()[(ben, key)], (2, 2, "catchup"))

    def _item_keys(self) -> list[str]:
        """Question keys stored for this class."""
        with self.school._lock:
            found = self.school.conn.execute(
                """
                SELECT question_key FROM group_engagement_items
                WHERE class_id = ?
                ORDER BY question_key
                """,
                (self.class_id,),
            ).fetchall()
        return [str(row["question_key"]) for row in found]

    def test_a_question_nobody_sent_writes_no_rows(self) -> None:
        """A published rank with zero sends is not a counted question."""
        quiet = self._open()
        key = self._question_key(quiet)
        self._end()
        self.assertEqual(self._rows(), {})
        self.assertNotIn(key, self._item_keys())

    def test_one_team_sending_still_scores_the_other_zero(self) -> None:
        """When any team sends, members who did not send still store 0/2."""
        sent = self._open()
        quiet = self._open()
        self._place(sent, TEAM_A, KEY_IDS)
        sent_key = self._question_key(sent)
        quiet_key = self._question_key(quiet)
        self._end()
        self._assert_team(sent_key, TEAM_A, 4)
        self._assert_team(sent_key, TEAM_B, 0)
        rows = self._rows()
        for sid in self._ids(TEAM_A + TEAM_B):
            self.assertNotIn((sid, quiet_key), rows)
        self.assertEqual(self._item_keys(), [sent_key])


if __name__ == "__main__":
    unittest.main()
