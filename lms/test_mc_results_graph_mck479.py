#!/usr/bin/env python3
"""MCK-479 / MCK-478: MC results graph and a single options list."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent


class McResultsGraphTests(unittest.TestCase):
    """Toggles, counts, and a single MC results graph on the teacher card."""

    def test_node_harness(self) -> None:
        """Order, answer key, counts, and no second options list."""
        node = LMS_DIR / "static" / "mc_results_graph.test.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_teacher_mc_card_is_the_graph_only(self) -> None:
        """Multiple choice skips the options list. Other types still render it."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        paint = js.split("function paintLiveQuestionCards()")[1].split(
            "function focusedGroupConfirmButton("
        )[0]
        self.assertEqual(paint.count("live-question-card-options"), 1)
        mc_gate = paint.split("const mcCard =")[1].split("const answerKey")[0]
        self.assertIn('mcCard\n          ? ""', mc_gate)
        self.assertIn("stripDuplicateOptionTable(", paint)
        self.assertIn("mcGraphSlot(", paint)
        self.assertIn("mcResultsGraphHtml(", paint)
        self.assertNotIn("live-item-chart", paint)
        chart = js.split("function individualLifecycleResultsHtml(")[1].split(
            "function groupAnswerLabel("
        )[0]
        self.assertIn("mcResultsGraphHtml(", chart)
        self.assertIn("numericResultsGraphHtml(", chart)
        self.assertNotIn("answerMarkHtml(", chart)
        self.assertNotIn("live-item-chart", chart)
        student = (LMS_DIR / "static" / "live_question_card.js").read_text(encoding="utf-8")
        self.assertIn("stripDuplicateOptionTable(", student)
        css = (LMS_DIR / "static" / "mc_results_graph.css").read_text(encoding="utf-8")
        self.assertIn(".mc-results-graph {", css)
        self.assertIn("color: #12202e", css)
        bars = css.split(".mc-results-bars,")[1].split("}")[0]
        self.assertIn("flex-direction: column", bars)
        self.assertNotIn("grid-template-columns:", bars)
        self.assertIn(".mc-results-face {", css)
        self.assertIn("padding: 6px 8px", css)
        self.assertIn("transition: width", css)
        self.assertIn("background: #e8eef4", css)
        self.assertIn("background: #9fd9cc", css)
        self.assertIn("background: #86d39a", css)
        self.assertIn("color: #166534", css)
        self.assertNotIn("text-overflow: ellipsis", css)
        self.assertNotIn("@media (max-width: 600px)", css)
        for name in ("staff-shell.css", "student-portal.css"):
            sheet = (LMS_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn('@import url("mc_results_graph.css");', sheet)


if __name__ == "__main__":
    unittest.main()
