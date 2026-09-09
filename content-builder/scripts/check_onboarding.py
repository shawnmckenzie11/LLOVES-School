#!/usr/bin/env python3
"""Verify MCF3M onboarding integration (section 7 of the implementation plan)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from onboarding_lib import (  # noqa: E402
    BuilderPaths,
    apply_import,
    classify_rule_effect,
    load_json,
    load_store,
    operational_fingerprint,
    preview_import,
    resolve_lesson_context,
    sha256_bytes,
    validate_input_package,
)
from student_fields import assert_student_content  # noqa: E402


def _fail(failures: list[str], cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def main() -> int:
    """Check identities, text fidelity, pilot preservation, and re-import."""
    root = Path(__file__).resolve().parents[1]
    paths = BuilderPaths(root)
    package = paths.package()
    failures: list[str] = []
    validation = validate_input_package(package)
    _fail(failures, validation.get("status") == "passed", f"package validation {validation}")
    store = load_store(paths, "MCF3M")
    identities = store.get("identities") or []
    counts = [sum(1 for i in identities if i["module_number"] == n) for n in range(1, 9)]
    _fail(failures, len(identities) == 39, f"identities {len(identities)}")
    _fail(failures, counts == [7, 6, 5, 5, 5, 3, 3, 5], f"module counts {counts}")
    _fail(
        failures,
        any(i["internal_id"] == "M4-L1-vertex-form" and i["stable_key"] == "MCF3M-M4L1" for i in identities),
        "pilot not matched to MCF3M-M4L1",
    )
    curriculum = store.get("curriculum") or []
    pack_cur = load_json(package / "data" / "curriculum.json")
    by_code = {r["code"]: r for r in curriculum if r.get("expectation_type") == "specific"}
    for item in pack_cur["specific_expectations"]:
        rec = by_code.get(item["code"])
        _fail(failures, rec is not None, f"missing {item['code']}")
        if rec:
            _fail(failures, rec["text"] == item["text"], f"text changed {item['code']}")
            _fail(
                failures,
                rec.get("sample_problem") == item.get("sample_problem"),
                f"sample problem mixed into text {item['code']}",
            )
    processes = [r for r in curriculum if r.get("expectation_type") == "process"]
    _fail(failures, len(processes) == 7, f"processes {len(processes)}")
    student = paths.lessons / "MCF3M" / "M4-L1-vertex-form" / "student-content.json"
    baseline = paths.store("MCF3M") / "baselines" / "M4-L1-vertex-form.json"
    _fail(failures, student.is_file() and baseline.is_file(), "missing student copy or baseline")
    if student.is_file() and baseline.is_file():
        expected = load_json(baseline)["files"].get("student-content.json")
        actual = sha256_bytes(student.read_bytes())
        _fail(failures, expected == actual, "student-content.json changed during onboarding")
        assert_student_content(load_json(student))
    resources_before = {p.name for p in paths.resources.glob("*.json")}
    _fail(failures, "jsxgraph-vertex-form-board.json" in resources_before, "existing JSXGraph resource missing")

    preview = preview_import(paths, package, "MCF3M")
    _fail(failures, preview.get("noop") is True, f"re-preview not noop: {preview.get('noop')}")
    second = apply_import(paths, package, "MCF3M")
    _fail(failures, second.get("batch", {}).get("outcome") == "noop", f"re-import {second.get('batch')}")
    after_ids = {i["stable_key"] for i in load_store(paths, "MCF3M")["identities"]}
    _fail(failures, len(after_ids) == 39, "re-import duplicated identities")

    ctx = resolve_lesson_context(paths, "MCF3M", "M4-L1-vertex-form", "lesson-director")
    _fail(failures, ctx.get("context_revision"), "missing context revision")
    _fail(failures, "student-content.json" not in json.dumps(ctx.get("scope_notes")), "ok")
    live = classify_rule_effect("rule.live-slides")
    _fail(failures, live == ["live_slides"], f"live-slide effect {live}")
    lang_effect = classify_rule_effect("rule.student-language")
    _fail(failures, "student_prose" in lang_effect, f"language effect {lang_effect}")

    report = {
        "status": "passed" if not failures else "failed",
        "validation": validation,
        "module_counts": counts,
        "identities": len(identities),
        "unresolved_conflicts": len(
            [c for c in load_store(paths, "MCF3M").get("conflicts") or [] if c.get("status") == "unresolved"]
        ),
        "fingerprint": operational_fingerprint(load_store(paths, "MCF3M")),
        "failures": failures,
        "repeat_import": "content-builder/.venv/bin/python content-builder/scripts/import_mcf3m_package.py --apply",
        "rollback": "content-builder/.venv/bin/python content-builder/scripts/import_mcf3m_package.py --rollback BATCH_ID",
        "rollback_boundary": "Restores only files the named latest batch wrote. Later teacher edits in those same files would also revert; do not rollback after subsequent lesson edits.",
    }
    dest = paths.store("MCF3M") / "integration-report.json"
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
