#!/usr/bin/env python3
"""Run feedback-spec scenario_tests against local checkers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from completing_the_square_checker import run_scenario as run_cts_scenario  # noqa: E402
from vertex_form_checker import run_scenario as run_vertex_scenario  # noqa: E402

DEFAULT_SPECS = (
    ROOT / "lessons/MCF3M/M4-L1-vertex-form/feedback-spec.json",
    ROOT / "lessons/MCF3M/M4-L2-completing-the-square/feedback-spec.json",
)


def _runner_for(spec: dict):
    """Pick the checker that owns this feedback-spec."""
    if spec.get("lesson_id") == "M4-L2-completing-the-square":
        return run_cts_scenario
    return run_vertex_scenario


def run_spec(spec_path: Path) -> list[str]:
    """Return failure strings for one feedback-spec file."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    run_scenario = _runner_for(spec)
    failures: list[str] = []
    for row in spec.get("scenario_tests") or []:
        got = run_scenario(row["task_id"], row["input"])
        expect = row["expect_condition"]
        if got != expect:
            failures.append(
                f"{spec_path.name} {row['task_id']} {row['input']} → {got} (want {expect})"
            )
    return failures


def main() -> int:
    """Load scenario_tests and print PASS or FAIL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lesson",
        type=Path,
        help="Lesson directory containing feedback-spec.json. Default: L1 and L2.",
    )
    args = parser.parse_args()
    if args.lesson:
        paths = [args.lesson / "feedback-spec.json" if args.lesson.is_dir() else args.lesson]
    else:
        paths = [path for path in DEFAULT_SPECS if path.is_file()]
    failures: list[str] = []
    total = 0
    for path in paths:
        spec = json.loads(path.read_text(encoding="utf-8"))
        total += len(spec.get("scenario_tests") or [])
        failures.extend(run_spec(path))
    if failures:
        print("FAIL")
        for item in failures:
            print("-", item)
        return 1
    print("PASS", total, "scenario tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
