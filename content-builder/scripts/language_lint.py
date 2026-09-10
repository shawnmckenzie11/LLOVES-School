#!/usr/bin/env python3
"""Contextual lint for student-content against the terminology registry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "catalogue" / "terminology" / "mcf3m-a2-quadratic-forms.json"

from student_authoring import iter_blocks, load_json  # noqa: E402

EDIT_FIELDS = ("text", "prompt", "alt", "label")


def lint_text(blob: str, *, block_id: str, registry: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Lint a single student-visible string."""
    registry = registry or load_json(DEFAULT_REGISTRY)
    hits = []
    for rule in registry.get("lint") or []:
        pat = rule.get("pattern")
        if not pat:
            continue
        if re.search(pat, blob or "", flags=re.IGNORECASE):
            hits.append(
                {
                    "block_id": block_id,
                    "rule_id": rule["id"],
                    "direction": rule.get("direction") or "",
                    "excerpt": (blob or "")[:180],
                }
            )
    return hits


def lint_student_content(
    doc: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return lint hits with block id, rule, and direction."""
    registry = registry or load_json(DEFAULT_REGISTRY)
    hits = []
    for block in iter_blocks(doc):
        blob = " ".join(str(block.get(field) or "") for field in EDIT_FIELDS)
        hits.extend(lint_text(blob, block_id=str(block.get("id") or ""), registry=registry))
    return hits


def lint_lesson(lesson_dir: Path) -> list[dict[str, str]]:
    """Lint student-content.json and student-visible feedback messages."""
    doc = load_json(lesson_dir / "student-content.json")
    hits = lint_student_content(doc)
    feedback_path = lesson_dir / "student-feedback.json"
    if feedback_path.is_file():
        messages = (load_json(feedback_path).get("messages") or {})
        for key, text in messages.items():
            hits.extend(lint_text(str(text), block_id=f"feedback:{key}"))
    return hits


def main() -> int:
    """CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lesson",
        type=Path,
        default=ROOT / "lessons" / "MCF3M" / "M4-L1-vertex-form",
    )
    args = parser.parse_args()
    hits = lint_lesson(args.lesson)
    print(json.dumps(hits, indent=2, ensure_ascii=False))
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
