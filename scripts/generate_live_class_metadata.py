#!/usr/bin/env python3
"""Generate thin schema-v2 live-class playlists for every Ontario math module."""

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

# Authored decks keep their extra items, media, and team-challenge copy.
AUTHORED_PATHS = {
    ("MCF3M", "M1", "C1"),
    ("MCF3M", "M1", "C2"),
    ("MCF3M", "M1", "C3"),
    ("MCF3M", "M1", "C4"),
    ("MCR3U", "M1", "C1"),
    ("MCR3U", "M1", "C2"),
    ("MCR3U", "M1", "C3"),
}

DEFAULT_PAGES = [
    {"id": "join", "name": "Join", "stage": "join"},
    {"id": "welcome", "name": "Welcome", "stage": "teams"},
    {"id": "meet", "name": "Meet", "stage": "meet"},
    {"id": "round_1", "name": "Round 1", "stage": "round"},
    {"id": "round_2", "name": "Round 2", "stage": "play"},
    {"id": "round_3", "name": "Round 3", "stage": "round_3"},
    {"id": "summary", "name": "Summary", "stage": "summary"},
]


def inactive_placement(
    ref: str,
    *,
    stage: str,
    page_number: int,
    order: int,
    publish_modes: list[str],
    response_mode: str = "individual",
) -> dict[str, Any]:
    """Return one thin v2 playlist row.

    Args:
        ref: Catalogue item ref.
        stage: Live-lesson stage.
        page_number: 1-based named page.
        order: Order on that page.
        publish_modes: Allowed publish modes.
        response_mode: Student response mode.
    """

    return {
        "ref": ref,
        "stage": stage,
        "page_number": page_number,
        "order": order,
        "default_status": "inactive",
        "publish_modes": publish_modes,
        "response_mode": response_mode,
    }


def metadata_document(course: str, module: str, live_class: str) -> dict[str, Any]:
    """Build one thin schema-v2 playlist for an unauthored live class."""

    return {
        "schema_version": 2,
        "course": course,
        "module": module,
        "live_class": live_class,
        "items": [
            inactive_placement(
                "universal/question/teams-spark",
                stage="teams",
                page_number=2,
                order=1,
                publish_modes=["individual", "group_consensus"],
            ),
            inactive_placement(
                "universal/question/meet-team",
                stage="meet",
                page_number=3,
                order=1,
                publish_modes=["individual"],
            ),
        ],
        "pages": DEFAULT_PAGES,
    }


def render_metadata_files(root: Path, *, check: bool = False) -> list[Path]:
    """Write deterministic metadata files and return paths that differ."""

    changed: list[Path] = []
    for course in COURSES:
        for module in MODULES:
            for live_class in LIVE_CLASSES:
                if (course, module, live_class) in AUTHORED_PATHS:
                    continue
                path = root / course / module / f"{live_class}.json"
                content = (
                    json.dumps(
                        metadata_document(course, module, live_class),
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                existing = path.read_text(encoding="utf-8") if path.exists() else ""
                if existing == content:
                    continue
                changed.append(path)
                if not check:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
    return changed


def main() -> int:
    """Generate metadata or verify committed generic files are current."""

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
