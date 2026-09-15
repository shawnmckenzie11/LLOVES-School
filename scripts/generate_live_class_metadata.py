#!/usr/bin/env python3
"""Generate four live-class metadata files for every Ontario math module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

COURSES = (
    "MCR3U",
    "MCF3M",
    "MBF3C",
    "MEL3E",
    "MHF4U",
    "MCV4U",
    "MDM4U",
    "MCT4C",
    "MAP4C",
    "MEL4E",
)
MODULES = tuple(f"M{index}" for index in range(1, 9))
LIVE_CLASSES = tuple(f"C{index}" for index in range(1, 5))

MCF3M_M1_MINDS_ON: dict[str, dict[str, Any]] = {
    "C1": {
        "text": (
            "A straight-line graph has a **constant rate of change**. "
            "Which statement best matches that?"
        ),
        "options": [
            "Every equal step across adds the same amount up (or down)",
            "The graph curves",
            "Second differences in a table are constant",
        ],
        "correct_answer": "A",
    },
    "C2": {
        "text": "Looking at y = x^2, which claim must be true from the graph?",
        "options": [
            "a > 0 (it opens upward)",
            "a < 0 (it opens downward)",
            "The graph is a straight line",
        ],
        "correct_answer": "A",
    },
    "C3": {
        "text": (
            "A graph of y = x^2 has been moved so it still passes through a "
            "marked point. Which claim is safest?"
        ),
        "options": [
            "Every parameter a, h, and k is locked by the point alone",
            "The point links the parameters — some stay free",
            "Domain and range are always all real numbers",
        ],
        "correct_answer": "B",
    },
}

MEDIA_FILES = {
    ("MCF3M", "M1", "C1"): {
        "file": "/static/live-media/m1c1-c1-real-slice.html",
        "title": "Real Slice",
        "shared_across_rounds": True,
    },
    ("MCR3U", "M1", "C1"): {
        "file": "/static/live-media/mcr3u-m1c1-sqrt.html",
        "title": "Square-root transformation",
        "shared_across_rounds": True,
    },
}


def metadata_questions(course: str, module: str, live_class: str) -> list[dict[str, Any]]:
    """Return universal questions plus any verified course-specific Minds-On."""

    questions: list[dict[str, Any]] = []
    minds_on = (
        MCF3M_M1_MINDS_ON.get(live_class)
        if course == "MCF3M" and module == "M1"
        else None
    )
    if minds_on:
        questions.append(
            {
                "id": "minds_on",
                "stage": "join",
                "type": "mc",
                **minds_on,
                "page_number": 1,
                "order": 1,
                "default_visibility": True,
            }
        )
    questions.append(
        {
            "id": "teams-spark",
            "stage": "join",
            "type": "numeric",
            "text": "Type the integer you think most students will answer",
            "options": [],
            "correct_answer": None,
            "page_number": 2,
            "order": 2 if minds_on else 1,
            "default_visibility": not bool(minds_on),
        }
    )
    questions.append(
        {
            "id": "meet-team",
            "stage": "meet",
            "type": "poll",
            "text": "Today I’m the teammate who…",
            "options": [
                "notices details",
                "asks the good question",
                "keeps us kind",
                "wants to try being team leader",
                "Not sure",
            ],
            "correct_answer": None,
            "page_number": 1,
            "order": 1,
            "default_visibility": True,
        }
    )
    return questions


def metadata_document(course: str, module: str, live_class: str) -> dict[str, Any]:
    """Build one complete live-class metadata document."""

    return {
        "schema_version": 1,
        "course": course,
        "module": module,
        "live_class": live_class,
        "media": MEDIA_FILES.get((course, module, live_class)),
        "slides": {"deck_ref": None, "page_numbers": []},
        "questions": metadata_questions(course, module, live_class),
        "round_defaults": {
            stage: {"timer": False, "timer_minutes": 3, "teams": False}
            for stage in ("join", "teams", "meet", "round", "play")
        },
    }


def render_metadata_files(root: Path, *, check: bool = False) -> list[Path]:
    """Write deterministic metadata files and return paths that differ."""

    changed: list[Path] = []
    for course in COURSES:
        for module in MODULES:
            for live_class in LIVE_CLASSES:
                path = root / course / module / f"{live_class}.json"
                content = json.dumps(
                    metadata_document(course, module, live_class),
                    indent=2,
                    ensure_ascii=False,
                ) + "\n"
                existing = path.read_text(encoding="utf-8") if path.exists() else ""
                if existing == content:
                    continue
                changed.append(path)
                if not check:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
    return changed


def main() -> int:
    """Generate metadata or verify committed files are current."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "lms" / "seeds" / "live_classes"
    changed = render_metadata_files(root, check=args.check)
    if args.check and changed:
        print(f"{len(changed)} live-class metadata files need regeneration.")
        return 1
    print(f"{'Would update' if args.check else 'Updated'} {len(changed)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
