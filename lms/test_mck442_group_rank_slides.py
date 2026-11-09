#!/usr/bin/env python3
"""MCK-442: group rank scores 2/1 and 1/1, and Lesson Slides reads them.

Forward saves are 2/1 for a no-check all-correct rank, 1/1 for any other
completion, and at most 1/1 for catch-up. Doubled rows stay stored.
Lesson Slides shows those terms from group_engagement and leaves
individual participation on its own scale.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from group_engagement import display_points, legacy_doubled_rank
from lesson_slides_table import (
    MC_WEIGHT_LABEL,
    RANK_CATCHUP_LABEL,
    RANK_WEIGHT_LABEL,
    lesson_slide_engagement_kind,
)
from participation_weight import item_participation_weight
from test_group_engagement_mck293 import TEAM_A, TEAM_B, GroupEngagementTests
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness


class LessonSlideKindTests(unittest.TestCase):
    """Question-type recognition without a database."""

    def test_team_rank_plain_group_and_individual_stay_distinct(self) -> None:
        """team_rank and plain group are group questions. Individual rank is not."""
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "team_rank", "mode": "individual"}),
            "rank",
        )
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "rank", "mode": "group"}),
            "rank",
        )
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "ranking", "publish_mode": "group"}),
            "rank",
        )
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "mc", "mode": "group"}),
            "mc",
        )
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "poll", "response_mode": "group_submit"}),
            "mc",
        )
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "rank", "mode": "individual"}),
            "",
        )
        self.assertEqual(
            lesson_slide_engagement_kind({"type": "team_draw", "mode": "group"}),
            "",
        )
        self.assertEqual(item_participation_weight("mc", publish_mode="individual"), 1)
        self.assertEqual(item_participation_weight("rank", publish_mode="individual"), 1)
        self.assertEqual(item_participation_weight("rank", publish_mode="group_submit"), 0)
        self.assertEqual(item_participation_weight("team_rank", publish_mode="group"), 0)
        self.assertEqual(item_participation_weight("mc", publish_mode="group_submit"), 0)

    def test_weight_and_catchup_labels_in_the_page_script(self) -> None:
        """The Lesson Slides script shows 2/1 and max 1/1, not a bare 4."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required")
        script = r"""
        import { pathToFileURL } from "node:url";
        globalThis.document = {
          getElementById() { return null; },
          addEventListener() {},
          querySelectorAll() { return []; },
        };
        globalThis.HTMLInputElement = class HTMLInputElement {};
        const mod = await import(pathToFileURL(process.argv[1]).href);
        const cases = [
          [mod.weightLabel, { weight_label: "2/1", weight: 1 }, "2/1"],
          [mod.weightLabel, { weight_label: "1/1", weight: 1 }, "1/1"],
          [mod.weightLabel, { weight: 1 }, "1"],
          [mod.catchupAvailabilityLabel, { catchup_label: "max 1/1", in_catchup: true }, "max 1/1"],
          [mod.catchupAvailabilityLabel, { in_catchup: true }, "yes"],
          [mod.catchupAvailabilityLabel, { in_catchup: false }, "no"],
        ];
        for (const [fn, input, expected] of cases) {
          const got = fn(input);
          if (got !== expected) {
            console.error(JSON.stringify({ expected, got }));
            process.exit(1);
          }
        }
        console.log("ok");
        """
        js = LMS_DIR / "static" / "staff_lesson_slides.js"
        result = subprocess.run(
            [node, "--input-type=module", "-e", script, str(js)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)


class LessonSlidesGroupTests(ChallengeHarness):
    """Saved rank rows and the teacher Lesson Slides read of them."""

    _ids = GroupEngagementTests._ids
    _open = GroupEngagementTests._open
    _place = GroupEngagementTests._place
    _end = GroupEngagementTests._end
    _rows = GroupEngagementTests._rows
    _question_key = GroupEngagementTests._question_key
    _run_key = GroupEngagementTests._run_key

    def _slides(self, run_key: str) -> dict[str, dict]:
        """Lesson Slides rows for one run, keyed by question key.

        Args:
            run_key: Participation run key.
        """
        latest = next(
            row
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if str(row.get("run_key") or "") == run_key and not int(row.get("is_test") or 0)
        )
        shown = self.school.lesson_slides_question_rows(
            self.class_id,
            "M1",
            "C2",
            latest=latest,
        )
        return {str(row["question_key"]): row for row in shown}

    def _stored_weight(self, run_key: str, question_key: str) -> int:
        """Weight saved on the Lesson Slides row, before the read overlay.

        Args:
            run_key: Participation run key.
            question_key: ``item:`` key.
        """
        latest = next(
            row
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if str(row.get("run_key") or "") == run_key
        )
        match = next(
            question
            for question in latest["questions"]
            if str(question.get("question_key") or "") == question_key
        )
        return int(match.get("weight") or 0)

    def test_lesson_slides_shows_rank_scale_and_keeps_individual(self) -> None:
        """A sent rank shows 2/1 and max 1/1. An individual MC stays 1 and yes.

        The stored Lesson Slides JSON keeps weight 0 for the rank. The
        page overlay does not write it back. A rank nobody sent is not a
        catch-up question. The rank is not an individual participation item.
        """
        rank = self._open()
        self._place(rank, TEAM_A, KEY_IDS)
        quiet = self._open()
        solo = self._add(
            {
                "type": "mc",
                "text": "Solo choice.",
                "options": ["A", "B", "C", "D"],
                "correct_index": 0,
            }
        )
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{solo['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(published.status_code, 200, published.get_data(as_text=True)[:180])
        rank_key = self._question_key(rank)
        quiet_key = self._question_key(quiet)
        solo_key = self._question_key(solo)
        self._end()
        run_key = self._run_key()
        self.assertEqual(self._rows()[(self.ids["Ava"], rank_key)], (2, 1, "live"))
        self.assertNotIn((self.ids["Ava"], quiet_key), self._rows())
        before = self._rows()
        shown = self._slides(run_key)
        self.assertEqual(self._rows(), before)
        rank_row = shown[rank_key]
        self.assertEqual(rank_row["mode"], "group")
        self.assertEqual(rank_row["weight"], 1)
        self.assertEqual(rank_row["weight_label"], RANK_WEIGHT_LABEL)
        self.assertTrue(rank_row["in_catchup"])
        self.assertEqual(rank_row["catchup_label"], RANK_CATCHUP_LABEL)
        self.assertEqual(rank_row["catchup_completed"], 0)
        self.assertEqual(self._stored_weight(run_key, rank_key), 0)
        quiet_row = shown[quiet_key]
        self.assertEqual(quiet_row["weight_label"], RANK_WEIGHT_LABEL)
        self.assertEqual(quiet_row["catchup_label"], "no")
        self.assertFalse(quiet_row["in_catchup"])
        solo_row = shown[solo_key]
        self.assertEqual(solo_row["mode"], "individual")
        self.assertEqual(solo_row["weight"], 1)
        self.assertNotIn("weight_label", solo_row)
        self.assertTrue(solo_row["in_catchup"])
        self.assertNotIn("group_completed", solo_row)
        with self.school._lock:
            keys = {
                str(row["question_key"])
                for row in self.school.conn.execute(
                    "SELECT question_key FROM participation_items WHERE class_id = ?",
                    (self.class_id,),
                ).fetchall()
            }
        self.assertIn(solo_key, keys)
        self.assertNotIn(rank_key, keys)
        self.assertNotIn(quiet_key, keys)
        with self.school._lock:
            link_row = self.school.conn.execute(
                """
                SELECT token FROM catchup_links
                WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, run_key),
            ).fetchone()
        token = str(link_row["token"])
        ben = self.ids["Ben"]
        questions = self.school.catchup_questions(token, ben)
        self.assertTrue(any(q.get("question_key") == rank_key for q in questions))
        content = next(q["content"] for q in questions if q.get("question_key") == rank_key)
        public_ids = [
            str(opt["id"]) for opt in (content.get("rank_options") or content.get("options") or [])
        ]
        self.assertTrue(self.school.submit_catchup_answer(token, ben, rank_key, public_ids))
        self.assertEqual(self._rows()[(ben, rank_key)], (1, 1, "catchup"))
        after = self._slides(run_key)
        self.assertEqual(after[rank_key]["catchup_completed"], 1)
        self.assertEqual(after[rank_key]["catchup_label"], RANK_CATCHUP_LABEL)
        self.assertEqual(after[solo_key]["weight"], 1)
        self.assertNotIn("weight_label", after[solo_key])

    def test_doubled_rows_stay_stored_and_display_as_one_point(self) -> None:
        """A synthetic 4/2 row is not rewritten. Catch-up of a 0 saves 1/1.

        Display of 4/2 is 2/1. Reading Lesson Slides does not change the
        stored earned or possible. Catch-up leaves the 4/2 row alone and
        writes 1/1 for an incomplete doubled row.
        """
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        key = self._question_key(item)
        ava = self.ids["Ava"]
        ben = self.ids["Ben"]
        run_key = self._run_key()
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES (?, ?, ?, ?, 4, 2, 'live', '2026-09-01T00:00:00Z')
                """,
                (self.class_id, run_key, key, ava),
            )
            self.school.conn.commit()
        self._end()
        self.assertEqual(self._rows()[(ava, key)], (4, 2, "live"))
        self.assertEqual(
            self.school.group_engagement_fractions(self.class_id, run_key=run_key)[ava],
            "2/1",
        )
        self.assertEqual(self._rows()[(ava, key)], (4, 2, "live"))
        self.assertTrue(legacy_doubled_rank(2, "rank"))
        self.assertEqual(display_points(4, 2, "rank"), (2, 1))
        before = self._rows()[(ava, key)]
        self._slides(run_key)
        self.assertEqual(self._rows()[(ava, key)], before)
        with self.school._lock:
            link_row = self.school.conn.execute(
                """
                SELECT token FROM catchup_links
                WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, run_key),
            ).fetchone()
            self.school.conn.execute(
                """
                UPDATE group_engagement
                SET earned = 0, possible = 2, source = 'live'
                WHERE class_id = ? AND run_key = ? AND question_key = ? AND student_id = ?
                """,
                (self.class_id, run_key, key, ben),
            )
            self.school.conn.commit()
        token = str(link_row["token"])
        self.assertTrue(any(q.get("group_rank") for q in self.school.catchup_questions(token, ben)))
        self.assertFalse(any(q.get("group_rank") for q in self.school.catchup_questions(token, ava)))
        link = self.school.get_catchup_link(token)
        content = next(
            q["content"]
            for q in self.school.catchup_questions(token, ben)
            if q.get("question_key") == key
        )
        public_ids = [
            str(opt["id"]) for opt in (content.get("rank_options") or content.get("options") or [])
        ]
        self.assertTrue(self.school.submit_catchup_answer(token, ben, key, public_ids))
        self.assertEqual(self._rows()[(ben, key)], (1, 1, "catchup"))
        self.assertTrue(self.school._store_group_engagement_catchup(link, ava, key, public_ids))
        self.assertEqual(self._rows()[(ava, key)], (4, 2, "live"))
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement_items (
                    class_id, run_key, question_key, live_module, live_slot,
                    item_json, prompt_kind, offered_at
                ) VALUES (?, ?, 'item:legacy-doubled', 'M1', 'C2', '{}', 'rank',
                          '2026-09-01T00:00:00Z')
                """,
                (self.class_id, run_key),
            )
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES (?, ?, 'item:legacy-doubled', ?, 2, 2, 'live', '2026-09-01T00:00:00Z')
                """,
                (self.class_id, run_key, ava),
            )
            self.school.conn.commit()
        shown = self.school.group_engagement_fractions(self.class_id, run_key=run_key)
        self.assertEqual(shown[ava], "3/2")
        self.assertEqual(self._rows()[(ava, "item:legacy-doubled")], (2, 2, "live"))
        self._slides(run_key)
        self.assertEqual(self._rows()[(ava, "item:legacy-doubled")], (2, 2, "live"))

    def test_team_rank_and_plain_group_read_engagement(self) -> None:
        """team_rank and a plain group MC use engagement, not a zero weight.

        An individual rank in the same list keeps its numeric weight.
        Reading the rows does not change the synthetic engagement table.
        """
        run_key = "synthetic-mck-442"
        now = "2026-10-01T00:00:00Z"
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement_items (
                    class_id, run_key, question_key, live_module, live_slot,
                    item_json, prompt_kind, offered_at
                ) VALUES
                    (?, ?, 'item:team-rank', 'M1', 'C2', '{"type":"team_rank"}', 'rank', ?),
                    (?, ?, 'item:group-mc', 'M1', 'C2', '{"type":"mc"}', 'mc', ?)
                """,
                (self.class_id, run_key, now, self.class_id, run_key, now),
            )
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES
                    (?, ?, 'item:team-rank', ?, 2, 1, 'live', ?),
                    (?, ?, 'item:team-rank', ?, 1, 1, 'catchup', ?),
                    (?, ?, 'item:group-mc', ?, 1, 1, 'live', ?)
                """,
                (
                    self.class_id,
                    run_key,
                    self.ids["Ava"],
                    now,
                    self.class_id,
                    run_key,
                    self.ids["Ben"],
                    now,
                    self.class_id,
                    run_key,
                    self.ids["Ava"],
                    now,
                ),
            )
            self.school.conn.commit()
        before = self._rows()
        questions = [
            {
                "question_key": "item:team-rank",
                "type": "team_rank",
                "mode": "individual",
                "weight": 0,
                "in_catchup": False,
            },
            {
                "question_key": "item:group-mc",
                "type": "mc",
                "mode": "group",
                "publish_mode": "group",
                "weight": 0,
                "in_catchup": False,
            },
            {
                "question_key": "item:solo-rank",
                "type": "rank",
                "mode": "individual",
                "weight": 1,
                "in_catchup": True,
            },
        ]
        shown = {
            str(row["question_key"]): row
            for row in self.school.lesson_slides_question_rows(
                self.class_id,
                "M1",
                "C2",
                latest={"run_key": run_key, "questions": questions},
            )
        }
        self.assertEqual(self._rows(), before)
        team = shown["item:team-rank"]
        self.assertEqual(team["mode"], "group")
        self.assertEqual(team["weight_label"], RANK_WEIGHT_LABEL)
        self.assertEqual(team["catchup_label"], RANK_CATCHUP_LABEL)
        self.assertEqual(team["catchup_completed"], 1)
        group_mc = shown["item:group-mc"]
        self.assertEqual(group_mc["weight_label"], MC_WEIGHT_LABEL)
        self.assertEqual(group_mc["weight"], 1)
        self.assertEqual(group_mc["catchup_label"], "no")
        self.assertFalse(group_mc["in_catchup"])
        solo = shown["item:solo-rank"]
        self.assertEqual(solo["mode"], "individual")
        self.assertEqual(solo["weight"], 1)
        self.assertNotIn("weight_label", solo)
        self.assertTrue(solo["in_catchup"])


if __name__ == "__main__":
    unittest.main()
