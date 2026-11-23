#!/usr/bin/env python3
"""MCK-495: full-width student questions, push-order labels, bar colours, minimize lock."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent


class StudentQuestionUxTests(unittest.TestCase):
    """The four student question UX changes stay in CSS and the card renderer."""

    def test_push_order_labels_follow_published_at_then_id(self) -> None:
        """Q numbers use published_at and the live item id, with no new column."""

        node = LMS_DIR / "static" / "student_question_order.test.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_question_cards_fill_the_main_panel(self) -> None:
        """The live stack and the question card drop the 22rem right-edge cap."""

        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        stack = css.split(".live-question-stack {")[1].split("}")[0]
        self.assertIn("left: 0.7rem", stack)
        self.assertIn("right: 0.7rem", stack)
        self.assertIn("width: auto", stack)
        self.assertNotIn("min(22rem", stack)
        card = css.split(".student-live-card.student-floating-pane {")[1].split("}")[0]
        self.assertIn("width: auto", card)
        self.assertNotIn("min(22rem", card)
        self.assertIn(".student-live-card:not(.is-closed) .student-live-dismiss", css)

    def test_cards_number_and_dock_in_push_order(self) -> None:
        """The header, the right-hand dock, and teacher-minimized bars use Q labels."""

        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn(
            'import { compareQuestionPushOrder, questionPushLabel } from "/static/student_question_order.js"',
            portal,
        )
        self.assertIn(".sort(compareQuestionPushOrder)", portal)
        paint = portal.split("function paintLifecycleQuestionStack(")[1].split(
            "function lifecycleAnswerFromCard("
        )[0]
        self.assertIn("questionPushLabel(item, all)", paint)
        self.assertIn('class="student-live-qnum"', paint)
        self.assertIn("data-q-label=", paint)
        self.assertIn('status === "closed"', paint)
        minimize = paint.split("const minimizeControl =")[1].split("return `<article")[0]
        self.assertIn('aria-label="Minimize"', minimize)
        self.assertIn('status === "closed"', minimize)
        dock = portal.split("function paintQuestionDock(")[1].split(
            "function promptAsLifecycleItem("
        )[0]
        self.assertIn(".sort(compareQuestionPushOrder)", dock)
        self.assertIn("questionPushLabel(item, session)", dock)
        bars = portal.split("function paintStudentMinimizedBars(")[1].split(
            "function paintLifecycleQuestionStack("
        )[0]
        self.assertIn(".sort(compareQuestionPushOrder)", bars)
        self.assertIn("students_minimized", paint)
        click = portal.split('closest("[data-dismiss-live-card]")')[1].split(
            "const parentPick"
        )[0]
        self.assertIn('dataset.liveCardStatus !== "closed"', click)

    def test_bars_are_blue_until_the_correct_one(self) -> None:
        """Teacher and student graphs share the blue fill and the green correct bar."""

        shared = (LMS_DIR / "static" / "mc_results_graph.css").read_text(encoding="utf-8")
        fill = shared.split(".mc-results-fill {")[1].split("}")[0]
        self.assertIn("background: #93c5fd", fill)
        self.assertNotIn("#9fd9cc", fill)
        correct = shared.split(".mc-results-row.is-correct .mc-results-fill {")[1].split("}")[0]
        self.assertIn("background: #86d39a", correct)
        self.assertIn("color: #14532d", shared)
        for name in ("staff-shell.css", "student-portal.css"):
            sheet = (LMS_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn('@import url("mc_results_graph.css");', sheet)
            self.assertNotIn(".mc-results-fill {", sheet)


if __name__ == "__main__":
    unittest.main()
