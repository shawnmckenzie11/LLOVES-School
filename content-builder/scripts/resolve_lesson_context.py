#!/usr/bin/env python3
"""Resolve grounded lesson context for one revision-plan role."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from onboarding_lib import BuilderPaths, resolve_lesson_context, write_resolved_context  # noqa: E402


def main() -> int:
    """Print or write a role-filtered context document."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", default="MCF3M")
    parser.add_argument("--lesson", default="M4-L1-vertex-form")
    parser.add_argument("--role", default="lesson-director")
    parser.add_argument("--write", action="store_true", help="Write resolved-context.{role}.json")
    args = parser.parse_args()
    paths = BuilderPaths(Path(__file__).resolve().parents[1])
    if args.write:
        dest = write_resolved_context(paths, args.course, args.lesson, args.role)
        print(dest)
        return 0
    print(json.dumps(resolve_lesson_context(paths, args.course, args.lesson, args.role), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
