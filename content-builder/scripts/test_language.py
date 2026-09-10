"""Terminology lint on M4-L1 student copy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from language_lint import lint_lesson  # noqa: E402

LESSON = ROOT / "lessons" / "MCF3M" / "M4-L1-vertex-form"


def test_m4_l1_language_lint_clean() -> None:
    hits = lint_lesson(LESSON)
    assert hits == [], hits
    feedback = (LESSON / "student-feedback.json").read_text(encoding="utf-8").lower()
    assert "under the graph" not in feedback
    assert "the writing" not in feedback
