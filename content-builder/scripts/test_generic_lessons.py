"""Generic HTML gates for the three scale-proof lessons."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from student_authoring import load_json, validate_student_content  # noqa: E402
from verify_lesson import generic_html_gates  # noqa: E402

PROOF = (
    ("M4-L1-vertex-form", True),
    ("M5-L1-trig-ratios", False),
    ("M7-L3-exponential-functions", False),
)


def test_proof_lessons_validate_and_compile() -> None:
    for lesson_id, vertex in PROOF:
        lesson = ROOT / "lessons" / "MCF3M" / lesson_id
        doc = load_json(lesson / "student-content.json")
        validate_student_content(doc)
        html_path = ROOT / "build" / "MCF3M" / lesson_id / "index.html"
        assert html_path.is_file(), f"rebuild {lesson_id} before asserting export"
        html = html_path.read_text(encoding="utf-8")
        report = generic_html_gates(html, lesson_id=lesson_id)
        assert report["ok"], report["failures"]
        assert report["vertex_tasks"] is vertex
        html = html_path.read_text(encoding="utf-8")
        if vertex:
            assert "jsxgraphcore.js" in html
            assert "card-block" in html
            assert "data-feedback-state" in html
            assert "Sketch the parabola on paper" in html
        else:
            assert "jsxgraphcore.js" not in html
            assert "The Vertex Form" not in html
