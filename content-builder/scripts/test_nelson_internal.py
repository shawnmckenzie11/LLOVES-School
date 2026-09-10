"""Nelson 4.1 records stay internal and do not leak into student export."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NELSON = ROOT / "catalogue" / "sources" / "internal" / "nelson-mcf3m-4-1"
STUDENT = ROOT / "lessons" / "MCF3M" / "M4-L1-vertex-form" / "student-content.json"
BUILD = ROOT / "build" / "MCF3M" / "M4-L1-vertex-form" / "index.html"

BANNED_SOURCE_SNIPPETS = (
    "bungee",
    "cigarettes sold",
    "printed_pages",
    "nelson-mcf3m-4-1",
    "libfile_e2d03712cde48191abf78770941d36c9",
)


def test_nelson_internal_rights_gate() -> None:
    manifest = json.loads((NELSON / "manifest.json").read_text(encoding="utf-8"))
    source_map = json.loads((NELSON / "source-map.json").read_text(encoding="utf-8"))
    baseline = json.loads((NELSON / "question-baseline.json").read_text(encoding="utf-8"))
    assert manifest["rights_status"] == "restricted_pending_review"
    assert source_map["source"]["rights_status"] == "restricted_pending_review"
    assert source_map["source"]["student_export_policy"].startswith("Do not export")
    assert baseline["rights_and_verification"]["source_extracts"] == "restricted_pending_review"
    assert not list(NELSON.glob("crops/**"))


def test_nelson_wording_not_in_student_export() -> None:
    student = STUDENT.read_text(encoding="utf-8").lower()
    built = BUILD.read_text(encoding="utf-8").lower() if BUILD.is_file() else ""
    for needle in BANNED_SOURCE_SNIPPETS:
        assert needle.lower() not in student
        assert needle.lower() not in built
