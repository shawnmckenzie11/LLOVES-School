"""Factory lock: pooled lessons assemble from accepted pool ids.

Lessons without a pool (MCF3M M5-L1-trig-ratios, M7-L3-exponential-functions)
are skipped — do not invent pedagogy or require assembly.json until a pool exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from assembly import (  # noqa: E402
    COMPILE_INPUTS,
    NELSON_LEAKS,
    accepted_ids,
    assembled_ids,
    compile_reads_question_candidates,
    lessons_with_pool,
    lessons_without_pool,
    live_practice_set_ids,
    load_json,
    nelson_leak_hits,
    pool_path,
    validate_assembly,
)
from question_pool import validate_pool  # noqa: E402

BUILD = ROOT / "build"
BUILD_LESSON = ROOT / "scripts" / "build_lesson.py"
SKIP_NO_POOL = {
    "M5-L1-trig-ratios",
    "M7-L3-exponential-functions",
}


def test_lessons_without_pool_skip_assembly() -> None:
    """Generic compile lessons may exist before a bank. Do not require assembly.json."""
    skipped = {lesson_id for _course, lesson_id, _dir in lessons_without_pool()}
    assert SKIP_NO_POOL <= skipped, skipped
    for _course, lesson_id, lesson_dir in lessons_without_pool():
        assert not (lesson_dir / "assembly.json").is_file(), (
            f"{lesson_id} has assembly.json without a pool; remove it or add a pool"
        )


def test_pooled_lessons_have_valid_assembly() -> None:
    pooled = lessons_with_pool()
    assert pooled, "expected M4-L1-vertex-form to have a pool"
    for course, lesson_id, lesson_dir in pooled:
        assembly_file = lesson_dir / "assembly.json"
        assert assembly_file.is_file(), (
            f"{course}/{lesson_id} has a pool but no assembly.json"
        )
        pool = load_json(pool_path(course, lesson_id))
        validate_pool(pool)
        assembly = load_json(assembly_file)
        validate_assembly(assembly, pool)
        assert assembly["page_prints_all_accepted"] is False
        ok = accepted_ids(pool)
        assembled = assembled_ids(assembly)
        assert assembled <= ok
        assert assembled < ok
        richness = assembly["richness_must_assemble"]
        by_family = {row["id"]: row for row in assembly["families"]}
        for name in richness:
            row = by_family[name]
            assert row["disposition"] == "assemble"
            assert row["assembled_ids"]
        by_recipe = {row["recipe"]: row for row in assembly["expansion_recipes"]}
        for name in richness:
            rec = by_recipe[name]
            assert rec["disposition"] == "assemble"
            assert rec["assembled_ids"]


def test_needed_families_assembled_or_reasoned() -> None:
    for course, lesson_id, lesson_dir in lessons_with_pool():
        pool = load_json(pool_path(course, lesson_id))
        assembly = load_json(lesson_dir / "assembly.json")
        by_family = {row["id"]: row for row in assembly["families"]}
        for meta in (pool.get("coverage") or {}).get("families") or []:
            row = by_family[meta["id"]]
            if meta.get("needed"):
                assembled = bool(row.get("assembled_ids"))
                reasoned = bool((row.get("reason") or "").strip())
                assert row["disposition"] == "assemble" or reasoned, meta["id"]
                if row["disposition"] == "assemble":
                    assert assembled, meta["id"]
            else:
                assert row["disposition"] == "excluded"
                assert (row.get("reason") or "").strip()


def test_practice_set_ids_follow_assembly_lock() -> None:
    """Compiled practice-set item ids must be accepted pool ids once copywriter locks.

    While practice_sets_status is pending_copywriter, live ids must match the
    documented leftovers so the gap cannot drift silently.
    """
    for course, lesson_id, lesson_dir in lessons_with_pool():
        pool = load_json(pool_path(course, lesson_id))
        assembly = load_json(lesson_dir / "assembly.json")
        ok = accepted_ids(pool)
        live = live_practice_set_ids(lesson_dir)
        status = assembly["practice_sets_status"]
        if status == "pool-locked":
            assert live, f"{lesson_id} pool-locked practice-sets are empty"
            extra = set(live) - ok
            assert not extra, f"{lesson_id} practice-set ids not in accepted pool: {sorted(extra)}"
        elif status == "pending_copywriter":
            expected = assembly.get("practice_sets_current_ids") or []
            assert live == expected, (
                f"{lesson_id} leftover practice-set ids drifted; "
                f"got {live}, assembly records {expected}"
            )
            assert not (set(live) & ok)
        else:
            raise AssertionError(f"unknown practice_sets_status {status}")
        for cid in assembled_ids(assembly):
            assert cid in ok


def test_compiled_html_renders_all_practice_set_items() -> None:
    """Compiled practice-sets must include every assembled pool item, not two leftover prompts."""
    leftover = (
        "Determine the domain and range of f(x) = −2(x + 3)² − 6.",
        "On the same axes, graph y = x²",
    )
    for course, lesson_id, lesson_dir in lessons_with_pool():
        html_path = BUILD / course / lesson_id / "index.html"
        if not html_path.is_file():
            continue
        html = html_path.read_text(encoding="utf-8")
        live = live_practice_set_ids(lesson_dir)
        for iid in live:
            assert f'data-item-id="{iid}"' in html, f"{lesson_id} missing practice item {iid}"
        assert html.count('class="practice-item"') >= len(live)
        for needle in leftover:
            assert needle not in html, f"{lesson_id} still has leftover OpenStax prompt"


def test_student_html_has_no_nelson_leak() -> None:
    for course, lesson_id, _lesson_dir in lessons_with_pool():
        html_path = BUILD / course / lesson_id / "index.html"
        if not html_path.is_file():
            continue
        hits = nelson_leak_hits(html_path.read_text(encoding="utf-8"))
        assert hits == [], f"{lesson_id} HTML leaks {hits}"
    student = ROOT / "lessons" / "MCF3M" / "M4-L1-vertex-form" / "student-content.json"
    hits = nelson_leak_hits(student.read_text(encoding="utf-8"))
    assert hits == []
    for needle in NELSON_LEAKS:
        assert needle


def test_compile_does_not_read_question_candidates() -> None:
    """Student HTML compiles from student-content plus optional practice-sets, not the old 12-item file."""
    assert BUILD_LESSON.is_file()
    assert compile_reads_question_candidates(BUILD_LESSON) is False
    text = BUILD_LESSON.read_text(encoding="utf-8")
    for name in COMPILE_INPUTS:
        if name == "student-content.json":
            assert "student-content.json" in text
    assert "student-practice-sets.json" in text
    review = (ROOT / "review" / "index.html").read_text(encoding="utf-8")
    assert "/api/pool" in review
    assert "question-candidates.json" in review
    serve = (ROOT / "scripts" / "serve_review.py").read_text(encoding="utf-8")
    assert "question-candidates" not in serve
