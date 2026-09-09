#!/usr/bin/env python3
"""Bridge module banks → lesson bank-sourced-candidates.json for practice-designer.

Reads catalogue/banks/{course}/M{module}/module.json + items/*.json and emits
lessons/{course}/{lesson_id}/bank-sourced-candidates.json.

Default eligibility: student_html_allowed and review_status in {approved, locked}.
Pass --include-selected to also take review_status selected (dry-run before the
human approve gate). Always excludes Nelson, permitted_use_status excluded, and
student_html_allowed false — never writes commercial stems into student fields.

practice-designer should prefer bank-sourced-candidates.json when present so
hand-authored question-candidates.json is not clobbered.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANKS = ROOT / "catalogue" / "banks"
LESSONS = ROOT / "lessons"

DEFAULT_STATUSES = frozenset({"approved", "locked"})
SELECTED_STATUS = "selected"
ALWAYS_EXCLUDE_SOURCES = frozenset({"nelson_functions_11"})

# Map bank placement_hint → question-candidates-style role (closest fit).
PLACEMENT_TO_ROLE = {
    "worked_example": "guided_example",
    "guided_practice": "diagnostic",
    "core_practice": "practice",
    "additional_practice": "practice",
    "extension": "transfer",
    "unassigned": "practice",
}


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _module_dir(course: str, module: int) -> Path:
    return BANKS / course / f"M{module}"


def _item_id(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        return entry["id"]
    return None


def _is_always_excluded(item: dict) -> tuple[bool, str | None]:
    source = item.get("source")
    if source in ALWAYS_EXCLUDE_SOURCES or source == "nelson_functions_11":
        return True, "nelson_or_excluded_source"
    if item.get("permitted_use_status") == "excluded":
        return True, "permitted_use_status_excluded"
    if item.get("student_html_allowed") is not True:
        return True, "student_html_not_allowed"
    return False, None


def _eligible_statuses(include_selected: bool) -> frozenset[str]:
    if include_selected:
        return DEFAULT_STATUSES | {SELECTED_STATUS}
    return DEFAULT_STATUSES


def _candidate_from_item(item: dict, rank: int) -> dict:
    placement = item.get("placement_hint") or "unassigned"
    role = PLACEMENT_TO_ROLE.get(placement, "practice")
    process_tags = list(item.get("process_tags") or [])
    primary_process = process_tags[0] if process_tags else None
    stem_student = item.get("stem_student")
    # Defence in depth: never emit null/empty student stem from excluded paths.
    if not isinstance(stem_student, str) or not stem_student.strip():
        raise ValueError(
            f"eligible item {item.get('id')!r} missing stem_student "
            "(student-facing field required)"
        )
    purpose_bits = []
    if process_tags:
        purpose_bits.append("process_tags=" + ",".join(process_tags))
    if item.get("provenance_notes"):
        purpose_bits.append(str(item["provenance_notes"]))
    purpose = "; ".join(purpose_bits) if purpose_bits else (
        f"Bank item {item.get('id')} ({placement})"
    )
    return {
        "bank_id": item["id"],
        "role": role,
        "rank": rank,
        "title": item.get("title") or item["id"],
        "student_facing": stem_student,
        "source_stem": item.get("stem_teacher") or stem_student,
        "answer": item.get("answer"),
        "answer_verification": item.get("answer_verification"),
        "checker_ref": item.get("checker_ref"),
        "placement": placement,
        "process_tags": process_tags,
        "process_id": primary_process,
        "expectation_codes": list(item.get("expectation_codes") or []),
        "review_status": item.get("review_status"),
        "student_html_allowed": True,
        "provenance": {
            "source": item.get("source"),
            "source_id": item.get("source_id"),
            "source_url": item.get("source_url"),
            "licence": item.get("licence"),
            "attribution": item.get("attribution"),
            "permitted_use_status": item.get("permitted_use_status"),
            "bank_id": item["id"],
        },
        "instructional_roles": process_tags,
        "reason": purpose,
        "purpose": purpose,
        "visible_by_default": item.get("review_status") in DEFAULT_STATUSES,
    }


def build_candidates(
    course: str,
    module: int,
    lesson_id: str,
    include_selected: bool,
) -> tuple[dict, dict]:
    mod_path = _module_dir(course, module) / "module.json"
    if not mod_path.is_file():
        raise FileNotFoundError(f"module bank not found: {mod_path}")
    module_data = _load_json(mod_path)
    if not isinstance(module_data, dict):
        raise ValueError(f"{mod_path}: not an object")

    items_dir = mod_path.parent / "items"
    declared = module_data.get("items") or []
    statuses_ok = _eligible_statuses(include_selected)

    loaded: list[dict] = []
    missing: list[str] = []
    for entry in declared:
        item_id = _item_id(entry)
        if not item_id:
            continue
        item_path = items_dir / f"{item_id}.json"
        if not item_path.is_file():
            missing.append(item_id)
            continue
        item = _load_json(item_path)
        if not isinstance(item, dict):
            raise ValueError(f"{item_path}: not an object")
        loaded.append(item)

    excluded_counts: Counter[str] = Counter()
    status_skipped: Counter[str] = Counter()
    eligible: list[dict] = []
    for item in loaded:
        always, reason = _is_always_excluded(item)
        if always:
            excluded_counts[reason or "always_excluded"] += 1
            continue
        status = item.get("review_status")
        if status not in statuses_ok:
            status_skipped[str(status)] += 1
            continue
        eligible.append(item)

    # Stable order: placement then id
    placement_order = {
        "worked_example": 0,
        "guided_practice": 1,
        "core_practice": 2,
        "additional_practice": 3,
        "extension": 4,
        "unassigned": 5,
    }
    eligible.sort(
        key=lambda i: (
            placement_order.get(i.get("placement_hint") or "unassigned", 99),
            i.get("id") or "",
        )
    )

    rank_by_role: Counter[str] = Counter()
    candidates: list[dict] = []
    for item in eligible:
        placement = item.get("placement_hint") or "unassigned"
        role = PLACEMENT_TO_ROLE.get(placement, "practice")
        rank_by_role[role] += 1
        candidates.append(_candidate_from_item(item, rank_by_role[role]))

    by_process: Counter[str] = Counter()
    by_placement: Counter[str] = Counter()
    by_review: Counter[str] = Counter()
    for c in candidates:
        by_review[str(c.get("review_status"))] += 1
        by_placement[str(c.get("placement"))] += 1
        for tag in c.get("process_tags") or []:
            by_process[tag] += 1
        if not c.get("process_tags"):
            by_process["(none)"] += 1

    doc = {
        "schema_version": "bank-sourced-candidates.v1",
        "lesson_id": lesson_id,
        "course_code": course,
        "module": module,
        "source_bank": str(mod_path.relative_to(ROOT)).replace("\\", "/"),
        "eligibility": {
            "student_html_allowed": True,
            "review_status": sorted(statuses_ok),
            "include_selected": include_selected,
            "always_exclude": [
                "source nelson_functions_11",
                "permitted_use_status excluded",
                "student_html_allowed false",
            ],
        },
        "notes_for_practice_designer": (
            "Prefer this file over hand-authored question-candidates.json when "
            "present. Do not flip review_status; Shawn’s human gate owns "
            "selected→approved. Nelson / excluded items are omitted from "
            "student-facing fields."
        ),
        "candidates": candidates,
        "summary": {
            "module_items_declared": len(declared),
            "module_items_loaded": len(loaded),
            "missing_item_files": missing,
            "always_excluded": dict(excluded_counts),
            "skipped_by_review_status": dict(status_skipped),
            "emitted": len(candidates),
            "by_review_status": dict(by_review),
            "by_placement": dict(by_placement),
            "by_process_tag": dict(by_process),
        },
    }
    stats = {
        "loaded": len(loaded),
        "emitted": len(candidates),
        "excluded": dict(excluded_counts),
        "skipped_status": dict(status_skipped),
        "missing": missing,
        "by_review": dict(by_review),
        "by_placement": dict(by_placement),
        "by_process": dict(by_process),
    }
    return doc, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit bank-sourced-candidates.json from a module bank."
    )
    parser.add_argument("--course", default="MCF3M")
    parser.add_argument("--module", type=int, default=4)
    parser.add_argument("--lesson-id", default="M4-L1-vertex-form")
    parser.add_argument(
        "--include-selected",
        action="store_true",
        help="Also include review_status=selected (dry-run before human approve).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary only; do not write the output file.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Override output path (default: lessons/{course}/{lesson_id}/"
        "bank-sourced-candidates.json)",
    )
    args = parser.parse_args(argv)

    try:
        doc, stats = build_candidates(
            course=args.course,
            module=args.module,
            lesson_id=args.lesson_id,
            include_selected=args.include_selected,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out = args.out
    if out is None:
        out = LESSONS / args.course / args.lesson_id / "bank-sourced-candidates.json"

    print(f"course={args.course} module=M{args.module} lesson={args.lesson_id}")
    print(f"include_selected={args.include_selected}")
    print(f"loaded={stats['loaded']} emitted={stats['emitted']}")
    print(f"always_excluded={stats['excluded']}")
    print(f"skipped_by_review_status={stats['skipped_status']}")
    if stats["missing"]:
        print(f"missing_item_files={stats['missing']}")
    print(f"by_review_status={stats['by_review']}")
    print(f"by_placement={stats['by_placement']}")
    print(f"by_process_tag={stats['by_process']}")

    if args.dry_run:
        print(f"dry-run: would write {out}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
