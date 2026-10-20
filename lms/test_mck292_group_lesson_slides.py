#!/usr/bin/env python3
"""MCK-292: Lesson Slides counts teams on group_submit questions."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from test_participation_mck198 import Base

LMS_DIR = Path(__file__).resolve().parent


class GroupLessonSlidesCountTests(Base):
    """End stores team finals only, and Lesson Slides counts those teams."""

    def _rows(self, run_key: str, questions: list[dict]) -> dict[str, dict]:
        """Lesson Slides question rows keyed by question key.

        Args:
            run_key: Participation run key for this ended class.
            questions: Stored question dicts for that run.

        Returns:
            Mapping of question key to the display row.
        """
        rows = self.school.lesson_slides_question_rows(
            self.class_id,
            "M8",
            "C2",
            latest={"run_key": run_key, "questions": questions},
        )
        return {str(row["question_key"]): row for row in rows}

    def _stored_questions(self, run_key: str) -> list[dict]:
        """Questions saved on the Lesson Slides row for one run.

        Args:
            run_key: Participation run key.

        Returns:
            The stored question list.
        """
        latest = self.school.list_lesson_slides_latest(self.class_id)
        match = next(row for row in latest if str(row.get("run_key") or "") == run_key)
        questions = match.get("questions")
        self.assertIsInstance(questions, list)
        return list(questions)

    def test_group_submit_end_counts_teams_and_keeps_individual_counts(self) -> None:
        """A group_submit End with only team finals shows the team count.

        Individual questions still count distinct students. A snapshot that
        shares the session id but not the run key does not add a team.
        """
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        now = "2026-10-19T18:05:00"
        c.execute(
            "UPDATE live_class_sessions SET started_at = ?, teacher_state_json = ? WHERE id = ?",
            (now, json.dumps({"live_module": "M8", "live_slot": "C2"}), sid),
        )
        specs = (
            ("M8-q0", "individual", "individual", "Solo choice"),
            ("M8-q1", "group_submit", "group_submit", "Shared choice"),
            ("M8-q2", "group_submit", "group_submit", "Unanswered shared choice"),
        )
        for index, (key, publish_mode, response_mode, stem) in enumerate(specs, start=1):
            prompt_id = c.execute(
                "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
                "created_at, updated_at) VALUES (?, ?, 'poll', '{}', ?, ?)",
                (sid, index, now, now),
            ).lastrowid
            c.execute(
                "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
                "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'round', 'poll', ?, ?, 'closed', ?, ?, ?, ?, ?)",
                (
                    sid,
                    key,
                    key,
                    json.dumps(
                        {
                            "text": stem,
                            "type": "mc",
                            "options": [{"text": "A"}, {"text": "B"}],
                            "correct_answer": "A",
                            "key": "A",
                        }
                    ),
                    prompt_id,
                    publish_mode,
                    response_mode,
                    now,
                    now,
                    now,
                ),
            )
        solo_prompt = c.execute(
            "SELECT prompt_id FROM live_session_items WHERE live_session_id = ? AND placement_key = ?",
            (sid, "M8-q0"),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO live_session_responses (prompt_id, student_id, response_json, created_at, updated_at) "
            "VALUES (?, ?, '{}', ?, ?)",
            (int(solo_prompt), int(self.students[0]), now, now),
        )
        group_item = int(
            c.execute(
                "SELECT id FROM live_session_items WHERE live_session_id = ? AND placement_key = ?",
                (sid, "M8-q1"),
            ).fetchone()[0]
        )
        for team_id in (41, 42):
            c.execute(
                """
                INSERT INTO live_group_responses (
                    live_item_id, team_id, status, vote_summary_json,
                    proposed_answer_json, final_answer_json, finalizer_student_id,
                    awarded_points, why_text, submit_count, submitter_ids_json,
                    editor_ids_json, created_at, updated_at
                ) VALUES (?, ?, 'submitted', '[]', NULL, ?, NULL, NULL, '', 1, '[]', '[]', ?, ?)
                """,
                (group_item, team_id, json.dumps({"kind": "choice", "value": "A"}), now, now),
            )
        run_key = str(
            c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0]
            or ""
        )
        c.execute(
            "UPDATE live_class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (now, sid),
        )
        c.commit()
        self.school.cleanup_live_session_response_data(sid, live_run=True)

        finals = c.execute(
            """
            SELECT student_id, team_id, live_session_id
            FROM live_result_snapshots
            WHERE class_id = ? AND run_key = ? AND question_key = ? AND source = 'group_final'
            ORDER BY team_id
            """,
            (self.class_id, run_key, "item:M8-q1"),
        ).fetchall()
        self.assertEqual([int(row["team_id"]) for row in finals], [41, 42])
        self.assertTrue(all(row["student_id"] is None for row in finals))
        self.assertTrue(all(int(row["live_session_id"]) == sid for row in finals))
        self.assertEqual(
            int(
                c.execute(
                    """
                    SELECT COUNT(*) FROM participation_answers
                    WHERE class_id = ? AND run_key = ? AND question_key LIKE 'item:M8-q%'
                      AND question_key != 'item:M8-q0'
                    """,
                    (self.class_id, run_key),
                ).fetchone()[0]
            ),
            0,
        )
        self.assertEqual(
            int(
                c.execute(
                    """
                    SELECT COUNT(*) FROM participation_items
                    WHERE class_id = ? AND run_key = ? AND question_key != 'item:M8-q0'
                    """,
                    (self.class_id, run_key),
                ).fetchone()[0]
            ),
            0,
        )
        self.assertEqual(
            {
                str(row["question_key"]): int(row["n"])
                for row in c.execute(
                    """
                    SELECT question_key, COUNT(*) AS n
                    FROM participation_answers
                    WHERE class_id = ? AND run_key = ?
                    GROUP BY question_key
                    """,
                    (self.class_id, run_key),
                ).fetchall()
            },
            {"item:M8-q0": 1},
        )

        stored = self._stored_questions(run_key)
        shown = self._rows(run_key, stored)
        solo = shown["item:M8-q0"]
        self.assertEqual(solo["mode"], "individual")
        self.assertEqual(solo["live_completed"], 1)
        self.assertNotIn("group_completed", solo)
        self.assertEqual(solo["catchup_completed"], 0)
        answered = shown["item:M8-q1"]
        self.assertEqual(answered["mode"], "group")
        self.assertEqual(answered["group_completed"], 2)
        self.assertEqual(answered["live_completed"], 0)
        quiet = shown["item:M8-q2"]
        self.assertEqual(quiet["mode"], "group")
        self.assertEqual(quiet["group_completed"], 0)

        # Rows saved before group_submit was labeled group still say individual.
        rewritten = []
        for question in stored:
            copy = dict(question)
            if copy.get("question_key") == "item:M8-q1":
                copy["mode"] = "individual"
            rewritten.append(copy)
        c.execute(
            "UPDATE lesson_slides_latest SET questions_json = ? WHERE class_id = ? AND run_key = ?",
            (json.dumps(rewritten), self.class_id, run_key),
        )
        c.commit()
        promoted = self._rows(run_key, self._stored_questions(run_key))
        self.assertEqual(promoted["item:M8-q1"]["mode"], "group")
        self.assertEqual(promoted["item:M8-q1"]["group_completed"], 2)
        self.assertEqual(promoted["item:M8-q0"]["live_completed"], 1)
        self.assertNotIn("group_completed", promoted["item:M8-q0"])

        def _insert_final(other_run: str, team_id: int | None, session_id: int) -> None:
            """Insert one team-final snapshot without going through End.

            Args:
                other_run: Run key stored on the snapshot.
                team_id: Team id, or None when the row should not count.
                session_id: Session id, which must not decide the count.
            """
            c.execute(
                """
                INSERT INTO live_result_snapshots (
                    live_session_id, class_id, session_code, run_key,
                    live_module, live_slot, source, question_key, question_json,
                    student_id, participant_uuid, codename, team_id,
                    answer_json, awarded_points, answered_at, snapshot_at
                ) VALUES (?, ?, '', ?, 'M8', 'C2', 'group_final', 'item:M8-q1', '{}',
                          NULL, NULL, '', ?, '{}', NULL, ?, ?)
                """,
                (session_id, self.class_id, other_run, team_id, now, now),
            )

        _insert_final("other-run", 99, sid)
        _insert_final(run_key, 41, sid + 50)
        _insert_final(run_key, None, sid)
        c.commit()
        isolated = self._rows(run_key, self._stored_questions(run_key))
        self.assertEqual(isolated["item:M8-q1"]["group_completed"], 2)
        self.assertEqual(isolated["item:M8-q0"]["live_completed"], 1)


class LessonSlidesTeamLabelTests(unittest.TestCase):
    """The Lesson Slides script labels team totals and leaves student totals numeric."""

    def test_live_done_label(self) -> None:
        """Group rows say how many teams responded. Individual rows keep the number."""
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
          [{ mode: "group", group_completed: 2 }, "2 teams responded"],
          [{ mode: "group", group_completed: 1 }, "1 team responded"],
          [{ mode: "group", group_completed: 0 }, "no responses"],
          [{ mode: "individual", live_completed: 4 }, "4"],
          [{ mode: "individual", live_completed: 0 }, "0"],
        ];
        for (const [input, expected] of cases) {
          const got = mod.liveDoneLabel(input);
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


if __name__ == "__main__":
    unittest.main()
