"""Question-pool coverage, FTS filters, expansion identities, Nelson leak gate."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from question_pool import (  # noqa: E402
    accepted,
    coverage_report,
    load_json,
    near_duplicates,
    rebuild_index,
    search_pool,
    validate_pool,
)
from vertex_form_checker import expand_standard  # noqa: E402

POOL = ROOT / "catalogue" / "pools" / "MCF3M" / "M4-L1-vertex-form.json"
NELSON = ("bungee", "cigarettes sold", "nelson functions 11", "libfile_")


def test_accepted_count_and_families() -> None:
    doc = load_json(POOL)
    validate_pool(doc)
    report = coverage_report(doc)
    assert 24 <= report["accepted"] <= 36
    assert len(report["distinct_families"]) >= 8
    assert near_duplicates(doc) == []


def test_accepted_prompts_are_student_safe() -> None:
    doc = load_json(POOL)
    for item in accepted(doc):
        blob = item["student_facing"].lower()
        for needle in NELSON:
            assert needle not in blob, item["id"]
        assert item["verification"]["verified"] is True
        assert item.get("student_export") is not False


def test_expand_a_h_k_identities() -> None:
    assert expand_standard(3, 1, 4) == {"a": 3, "b": -6, "c": 7}
    assert expand_standard(2, -3, 1) == {"a": 2, "b": 12, "c": 19}
    assert expand_standard(0.5, -2, -3) == {"a": 0.5, "b": 2.0, "c": -1.0}
    assert expand_standard(-0.5, 2, 4) == {"a": -0.5, "b": 2.0, "c": 2.0}


def test_fts_filters(tmp_path: Path) -> None:
    db = tmp_path / "pool.sqlite"
    rebuild_index(db, root=ROOT)
    hits = search_pool("vertex", lesson_id="M4-L1-vertex-form", bloom="evaluate", db_path=db)
    assert hits
    assert all(h["bloom"]["primary"] == "evaluate" for h in hits)
    families = search_pool("", lesson_id="M4-L1-vertex-form", family_id="form-affordances", db_path=db)
    assert families
    assert all(h["family_id"] == "form-affordances" for h in families)
    excluded = search_pool("", lesson_id="M4-L1-vertex-form", status="excluded", db_path=db)
    assert excluded
    assert all(h["status"] == "excluded" for h in excluded)
