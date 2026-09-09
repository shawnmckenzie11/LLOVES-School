#!/usr/bin/env python3
"""Run feedback-spec scenario_tests against local checkers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vertex_form_checker import run_scenario  # noqa: E402


def main() -> int:
    """Load scenario_tests and print PASS or FAIL."""
    spec_path = ROOT / "lessons/MCF3M/M4-L1-vertex-form/feedback-spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for row in spec.get("scenario_tests") or []:
        got = run_scenario(row["task_id"], row["input"])
        expect = row["expect_condition"]
        if got != expect:
            failures.append(f"{row['task_id']} {row['input']} → {got} (want {expect})")
    if failures:
        print("FAIL")
        for item in failures:
            print("-", item)
        return 1
    print("PASS", len(spec.get("scenario_tests") or []), "scenario tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
