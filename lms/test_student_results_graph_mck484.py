#!/usr/bin/env python3
"""MCK-484: student MC and numeric bars reuse the teacher graph.

The tally shows after a student submits, updates as more answers arrive,
and the correct bar follows Show answer / Hide answer. Unpublished
questions stay off the student payload.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)

from app import create_app  # noqa: E402
from paths import DEFAULT_IT_EMAIL  # noqa: E402


class StudentResultsGraphTests(unittest.TestCase):
    """Live tallies and a key that follows the teacher Show answer toggle."""

    def setUp(self) -> None:
        """One live class and two roster seats."""

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff(DEFAULT_IT_EMAIL)
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["P1", "P2"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.session_id = int(
            self.school.start_live_class_session(
                self.class_id, int(teacher["id"])
            )["id"]
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_id="round_1",
            page_number=4,
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        self.ids = [int(row["id"]) for row in rows]
        for row in rows:
            self.school.join_live_class_session(
                self.session_id,
                int(row["id"]),
                codename=str(row["codename"]),
            )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp directory."""

        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add(self, **kwargs: Any) -> dict[str, Any]:
        """Add one question on the current page and return its lifecycle row."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            page_number=4,
            stage="round",
            **kwargs,
        )
        token = str(placed.get("item_id") or "")
        return next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == token
        )

    def _student_item(self, student_id: int, live_item_id: int) -> dict[str, Any] | None:
        """Return one student's copy of a lifecycle item, if it is visible."""

        payload = self.school.student_live_items_payload(self.session_id, student_id)
        for bucket in ("active_questions", "closed_results", "live_items"):
            for row in payload.get(bucket) or []:
                if int(row.get("id") or 0) == int(live_item_id):
                    return row
        return None

    def _counts(self, results: dict[str, Any] | None) -> dict[str, int]:
        """Map a tally's labels to counts."""

        return {
            str(choice.get("label")): int(choice.get("count") or 0)
            for choice in (results or {}).get("choices") or []
            if isinstance(choice, dict)
        }

    def test_node_student_graph_has_no_key_until_shown(self) -> None:
        """The shared renderer, called the way the phone calls it."""

        node = LMS_DIR / "static" / "student_results_graph.test.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_student_page_reuses_the_teacher_graph(self) -> None:
        """Phone cards call the teacher graph with the Show answer flag."""

        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn(
            'import { mcResultsGraphHtml, numericResultsGraphHtml } from "/static/mc_results_graph.js"',
            portal,
        )
        graph = portal.split("function lifecycleResultsHtml(")[1].split(
            "function lifecycleClassConsensusHtml("
        )[0]
        self.assertIn("mcResultsGraphHtml(", graph)
        self.assertIn("numericResultsGraphHtml(", graph)
        self.assertIn("controls: false", graph)
        self.assertIn("graphOpts.showKey", graph)
        self.assertIn("graphOpts.correctValue = results.correct_value", graph)
        paint = portal.split("const results =")[1].split("const dockKey")[0]
        self.assertIn("item.show_answer", paint)
        self.assertIn("item.results_order", paint)
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        pref = staff.split("function mcGraphPref(")[1].split("function liveItemIdFromGraphKey(")[0]
        self.assertIn("show_answer", pref)
        self.assertIn("results_order", pref)
        click = staff.split('closest("[data-mc-order], [data-mc-key]")')[1].split(
            "const publish"
        )[0]
        self.assertIn("persistMcGraphPref(", click)
        persist = staff.split("async function persistMcGraphPref(")[1].split(
            "function mcOptionLabel("
        )[0]
        self.assertIn("show_answer", persist)
        self.assertIn("results_order", persist)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn("body.student-portal .mc-results-graph {", css)
        self.assertIn("body.student-portal .mc-results-row.is-correct .mc-results-fill {", css)

    def test_tally_waits_for_submit_then_follows_show_answer(self) -> None:
        """Unpublished stays hidden. Counts move live. The key follows the toggle."""

        held = self._add(
            question_type="mc",
            text="Hold this",
            options=["kite", "moth", "wren", "lark"],
            correct_index=0,
        )
        mc = self._add(
            question_type="mc",
            text="Pick one",
            options=["kite", "moth", "wren", "lark"],
            correct_index=0,
        )
        numeric = self._add(
            question_type="numeric",
            text="A number",
            correct_answer="3.14159",
            tolerance=0.01,
        )
        seat = self.ids[0]
        self.assertIsNone(self._student_item(seat, int(held["id"])))
        self.assertIsNone(self._student_item(seat, int(mc["id"])))

        published = self.school.publish_live_session_item(
            self.session_id, int(mc["id"]), publish_mode="individual"
        )
        self.school.publish_live_session_item(
            self.session_id, int(numeric["id"]), publish_mode="individual"
        )
        self.assertIsNone(self._student_item(seat, int(held["id"])))
        before = self._student_item(seat, int(mc["id"]))
        self.assertIsNotNone(before)
        assert before is not None
        self.assertIsNone(before.get("results"))
        self.assertNotIn('"correct"', json.dumps(before.get("results")))

        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        assert prompt is not None
        stamp_before = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, seat
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), seat, {"choice": "A"}
        )
        stamp_one = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, seat
        )
        self.assertNotEqual(stamp_before, stamp_one)
        one = self._student_item(seat, int(mc["id"]))
        assert one is not None
        self.assertEqual(self._counts(one.get("results")).get("kite"), 1)
        self.assertNotIn('"correct"', json.dumps(one.get("results")))
        self.assertFalse(one.get("show_answer"))

        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[1], {"choice": "B"}
        )
        two = self._student_item(seat, int(mc["id"]))
        assert two is not None
        counts = self._counts(two.get("results"))
        self.assertEqual(counts.get("kite"), 1)
        self.assertEqual(counts.get("moth"), 1)
        teacher = self.school.live_session_item_results(self.session_id, int(mc["id"]))
        teacher_counts = self._counts((teacher.get("tally") or {}))
        self.assertEqual(teacher_counts.get("kite"), 1)
        self.assertEqual(teacher_counts.get("moth"), 1)
        self.assertTrue(any(row.get("correct") for row in (teacher.get("tally") or {}).get("choices") or []))

        self.school.close_live_session_item(self.session_id, int(mc["id"]))
        closed = self._student_item(seat, int(mc["id"]))
        assert closed is not None
        self.assertNotIn('"correct"', json.dumps(closed.get("results")))

        self.school.update_live_session_item_settings(
            self.session_id, int(mc["id"]), show_answer=True, results_order="ranked"
        )
        shown = self._student_item(seat, int(mc["id"]))
        assert shown is not None
        self.assertTrue(shown.get("show_answer"))
        self.assertEqual(shown.get("results_order"), "ranked")
        marked = [row for row in shown["results"]["choices"] if row.get("correct")]
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0].get("label"), "kite")

        self.school.update_live_session_item_settings(
            self.session_id, int(mc["id"]), show_answer=False
        )
        hidden = self._student_item(seat, int(mc["id"]))
        assert hidden is not None
        self.assertFalse(hidden.get("show_answer"))
        self.assertNotIn('"correct"', json.dumps(hidden.get("results")))

        number_row = self.school.get_live_session_item(self.session_id, int(numeric["id"]))
        number_prompt = self.school._prompt_for_live_item(number_row)
        assert number_prompt is not None
        self.school.submit_live_prompt_response(
            int(number_prompt["id"]), seat, {"value": 3.14}
        )
        self.school.submit_live_prompt_response(
            int(number_prompt["id"]), self.ids[1], {"value": 3.2}
        )
        open_numbers = self._student_item(seat, int(numeric["id"]))
        assert open_numbers is not None
        blob = json.dumps(open_numbers.get("results"))
        self.assertNotIn("3.14159", blob)
        self.assertNotIn('"correct"', blob)
        self.assertEqual(sorted(self._counts(open_numbers.get("results"))), ["3.14", "3.2"])
        staff_numbers = self.school.live_session_item_results(
            self.session_id, int(numeric["id"])
        )
        self.assertIn("3.14159", self._counts(staff_numbers.get("tally")))

        stamp_hidden = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, seat
        )
        self.school.update_live_session_item_settings(
            self.session_id, int(numeric["id"]), show_answer=True
        )
        stamp_shown = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, seat
        )
        self.assertNotEqual(stamp_hidden, stamp_shown)
        revealed = self._student_item(seat, int(numeric["id"]))
        assert revealed is not None
        revealed_counts = self._counts(revealed.get("results"))
        self.assertIn("3.14159", revealed_counts)
        self.assertEqual(str((revealed.get("results") or {}).get("correct_value")), "3.14159")
        self.assertTrue(any(row.get("correct") for row in revealed["results"]["choices"]))
        self.school.update_live_session_item_settings(
            self.session_id, int(numeric["id"]), show_answer=False
        )
        concealed = self._student_item(seat, int(numeric["id"]))
        assert concealed is not None
        concealed_blob = json.dumps(concealed.get("results"))
        self.assertNotIn("3.14159", concealed_blob)
        self.assertNotIn('"correct"', concealed_blob)


if __name__ == "__main__":
    unittest.main()
