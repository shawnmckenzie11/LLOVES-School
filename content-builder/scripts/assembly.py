#!/usr/bin/env python3
"""Load and validate lesson assembly.json against a question pool.

Lessons without a pool file are skipped (M5/M7 today). A pooled lesson must
have assembly.json. question-candidates.json is not a compile input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "catalogue" / "contracts" / "lesson-assembly.schema.json"
FADE_SLOTS = ("worked", "variation", "partial", "independent", "retrieval")
RICHNESS_DEFAULT = (
    "error-diagnosis",
    "claim-testing",
    "constraint-construction",
    "method-choice",
)
NELSON_LEAKS = (
    "bungee",
    "cigarettes sold",
    "nelson functions 11",
    "libfile_",
)
COMPILE_INPUTS = (
    "student-content.json",
    "interaction-spec.json",
    "student-feedback.json",
    "student-practice-sets.json",
)

from student_authoring import load_json  # noqa: E402


def load_schema() -> dict[str, Any]:
    """Load the lesson-assembly schema."""
    return load_json(SCHEMA_PATH)


def assembly_path(course: str, lesson_id: str, *, root: Path | None = None) -> Path:
    """Return the lesson-local assembly.json path."""
    return (root or ROOT) / "lessons" / course / lesson_id / "assembly.json"


def pool_path(course: str, lesson_id: str, *, root: Path | None = None) -> Path:
    """Return the committed pool JSON path."""
    return (root or ROOT) / "catalogue" / "pools" / course / f"{lesson_id}.json"


def iter_student_lessons(root: Path | None = None) -> list[tuple[str, str, Path]]:
    """Yield (course, lesson_id, lesson_dir) for lessons with student-content.json."""
    lessons_root = (root or ROOT) / "lessons"
    rows: list[tuple[str, str, Path]] = []
    if not lessons_root.is_dir():
        return rows
    for course_dir in sorted(lessons_root.iterdir()):
        if not course_dir.is_dir():
            continue
        for lesson_dir in sorted(course_dir.iterdir()):
            if (lesson_dir / "student-content.json").is_file():
                rows.append((course_dir.name, lesson_dir.name, lesson_dir))
    return rows


def lessons_with_pool(root: Path | None = None) -> list[tuple[str, str, Path]]:
    """Student lessons that have a committed pool file."""
    base = root or ROOT
    return [
        (course, lesson_id, lesson_dir)
        for course, lesson_id, lesson_dir in iter_student_lessons(base)
        if pool_path(course, lesson_id, root=base).is_file()
    ]


def lessons_without_pool(root: Path | None = None) -> list[tuple[str, str, Path]]:
    """Student lessons with no pool yet — assembly.json is not required.

    Documented skip: MCF3M M5-L1-trig-ratios and M7-L3-exponential-functions
    prove generic compile but have no bank. Do not invent pedagogy for them.
    """
    base = root or ROOT
    return [
        (course, lesson_id, lesson_dir)
        for course, lesson_id, lesson_dir in iter_student_lessons(base)
        if not pool_path(course, lesson_id, root=base).is_file()
    ]


def accepted_ids(pool: dict[str, Any]) -> set[str]:
    """Accepted candidate ids."""
    return {c["id"] for c in pool.get("candidates") or [] if c.get("status") == "accepted"}


def excluded_ids(pool: dict[str, Any]) -> set[str]:
    """Excluded candidate ids."""
    return {c["id"] for c in pool.get("candidates") or [] if c.get("status") == "excluded"}


def pool_by_id(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index pool candidates by id."""
    return {c["id"]: c for c in pool.get("candidates") or [] if c.get("id")}


def assembled_ids(assembly: dict[str, Any]) -> set[str]:
    """Union of family assembled_ids (the student-page subset)."""
    ids: set[str] = set()
    for family in assembly.get("families") or []:
        ids.update(family.get("assembled_ids") or [])
    return ids


def drawer_ids(assembly: dict[str, Any]) -> set[str]:
    """Union of family drawer_ids (pool-only / review drawer)."""
    ids: set[str] = set()
    for family in assembly.get("families") or []:
        ids.update(family.get("drawer_ids") or [])
    return ids


def live_practice_set_ids(lesson_dir: Path) -> list[str]:
    """Item ids from student-practice-sets.json (compile input for practice-set blocks)."""
    path = lesson_dir / "student-practice-sets.json"
    if not path.is_file():
        return []
    doc = load_json(path)
    ids: list[str] = []
    for group in doc.get("sets") or []:
        for item in group.get("items") or []:
            iid = item.get("id")
            if iid:
                ids.append(str(iid))
    return ids


def _xor_fade_cell(cell: dict[str, Any], *, process_id: str, slot: str) -> None:
    """Require a pool id or an explicit exception, not both empty and not both set."""
    pool_id = cell.get("pool_id") or None
    exception = cell.get("exception") or None
    if bool(pool_id) == bool(exception):
        raise ValueError(
            f"{process_id}.{slot} needs exactly one of pool_id or exception, got {cell}"
        )
    if exception and not (cell.get("reason") or "").strip():
        raise ValueError(f"{process_id}.{slot} exception needs a reason")


def validate_assembly(assembly: dict[str, Any], pool: dict[str, Any]) -> None:
    """Raise if the matrix fails schema, coverage, or pool-id gates."""
    jsonschema.validate(assembly, load_schema())
    if assembly.get("lesson_id") != pool.get("lesson_id"):
        raise ValueError(
            f"assembly lesson_id {assembly.get('lesson_id')} != pool {pool.get('lesson_id')}"
        )
    ok = accepted_ids(pool)
    excluded = excluded_ids(pool)
    by_id = pool_by_id(pool)
    family_rows = assembly.get("families") or []
    assembled = assembled_ids(assembly)
    drawer = drawer_ids(assembly)
    if assembled & drawer:
        raise ValueError(f"ids both assembled and drawer: {sorted(assembled & drawer)}")
    if assembled | drawer != ok:
        missing = ok - (assembled | drawer)
        extra = (assembled | drawer) - ok
        raise ValueError(f"family assemble+drawer must equal accepted ids; missing={sorted(missing)} extra={sorted(extra)}")
    listed_excluded = {row["id"] for row in assembly.get("excluded") or []}
    if listed_excluded != excluded:
        raise ValueError(
            f"excluded mismatch: assembly={sorted(listed_excluded)} pool={sorted(excluded)}"
        )
    richness = tuple(assembly.get("richness_must_assemble") or RICHNESS_DEFAULT)
    pool_needed = {f["id"]: f for f in (pool.get("coverage") or {}).get("families") or []}
    seen_family: set[str] = set()
    for family in family_rows:
        fid = family["id"]
        if fid in seen_family:
            raise ValueError(f"duplicate family {fid}")
        seen_family.add(fid)
        ids = set(family.get("pool_ids") or [])
        assembled_f = set(family.get("assembled_ids") or [])
        drawer_f = set(family.get("drawer_ids") or [])
        if assembled_f & drawer_f:
            raise ValueError(f"{fid}: overlapping assembled/drawer")
        if assembled_f | drawer_f != ids:
            raise ValueError(f"{fid}: pool_ids must equal assembled_ids ∪ drawer_ids")
        for cid in ids:
            item = by_id.get(cid)
            if not item:
                raise ValueError(f"{fid}: unknown pool id {cid}")
            if item.get("family_id") != fid and item.get("status") == "accepted":
                raise ValueError(f"{fid}: {cid} has family_id {item.get('family_id')}")
            if cid in assembled_f and item.get("status") != "accepted":
                raise ValueError(f"{fid}: assembled {cid} is not accepted")
        if fid in pool_needed and family["needed"] != pool_needed[fid]["needed"]:
            raise ValueError(f"{fid}: needed flag disagrees with pool coverage")
        needed = family["needed"]
        if needed and family["disposition"] != "assemble":
            if not (family.get("reason") or "").strip():
                raise ValueError(f"needed family {fid} must be assembled or list a reason")
        if family["disposition"] == "assemble" and not assembled_f:
            raise ValueError(f"assemble family {fid} has no assembled_ids")
        if fid in richness:
            if family["disposition"] != "assemble" or not assembled_f:
                raise ValueError(f"richness family {fid} must be assemble (not drawer-only)")
        if family["disposition"] == "excluded" and not (family.get("reason") or "").strip():
            raise ValueError(f"excluded family {fid} needs a reason")
    for fid in pool_needed:
        if fid not in seen_family:
            raise ValueError(f"pool coverage family {fid} missing from assembly.json")
    for recipe in assembly.get("expansion_recipes") or []:
        name = recipe["recipe"]
        rec_assembled = set(recipe.get("assembled_ids") or [])
        rec_drawer = set(recipe.get("drawer_ids") or [])
        rec_pool = set(recipe.get("pool_ids") or [])
        if rec_assembled | rec_drawer != rec_pool:
            raise ValueError(f"recipe {name}: pool_ids must equal assembled ∪ drawer")
        for cid in rec_pool:
            item = by_id.get(cid)
            if not item:
                raise ValueError(f"recipe {name}: unknown pool id {cid}")
            if item.get("expansion_recipe") != name:
                raise ValueError(
                    f"recipe {name}: {cid} has expansion_recipe {item.get('expansion_recipe')}"
                )
            if cid in rec_assembled and item.get("status") != "accepted":
                raise ValueError(f"recipe {name}: assembled {cid} is not accepted")
        if name in richness:
            if recipe["disposition"] != "assemble" or not rec_assembled:
                raise ValueError(f"richness recipe {name} must be assemble (not drawer-only)")
    for process in assembly.get("fade") or []:
        pid = process["process_id"]
        cells = process.get("cells") or {}
        for slot in FADE_SLOTS:
            if slot not in cells:
                raise ValueError(f"{pid} missing fade cell {slot}")
            cell = cells[slot]
            _xor_fade_cell(cell, process_id=pid, slot=slot)
            pool_id = cell.get("pool_id") or None
            if pool_id:
                if pool_id not in ok:
                    raise ValueError(f"{pid}.{slot} {pool_id} is not an accepted pool id")
                if pool_id not in assembled:
                    raise ValueError(
                        f"{pid}.{slot} {pool_id} is drawer-only; fade cells must be assembled or an exception"
                    )
    if assembly.get("page_prints_all_accepted") is not False:
        raise ValueError("page_prints_all_accepted must be false")
    if len(assembled) >= len(ok):
        raise ValueError("assembled set must be a proper subset of accepted (page is not the whole bank)")
    status = assembly.get("practice_sets_status")
    if status not in ("pending_copywriter", "pool-locked"):
        raise ValueError(f"unknown practice_sets_status {status}")
    seed = assembly.get("practice_sequence_seed") or []
    if seed:
        seed_ids = [row["id"] for row in seed]
        if len(seed_ids) != len(set(seed_ids)):
            raise ValueError("duplicate practice_sequence_seed id")
        if set(seed_ids) != ok | excluded:
            raise ValueError("practice_sequence_seed must dispose every accepted and excluded candidate")


def nelson_leak_hits(text: str) -> list[str]:
    """Return banned Nelson leak strings found in text."""
    blob = (text or "").lower()
    return [needle for needle in NELSON_LEAKS if needle in blob]


def compile_reads_question_candidates(script: Path) -> bool:
    """True if a compile script opens the retired ranking file as a path."""
    text = script.read_text(encoding="utf-8")
    return bool(re.search(r'["\']question-candidates\.json["\']', text))
