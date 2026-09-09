#!/usr/bin/env python3
"""Search the rebuildable catalogue index (not LMS sqlite)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalogue import rebuild_index, search  # noqa: E402


def main() -> int:
    """CLI for keyword + structured filters."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--source", default=None)
    parser.add_argument("--type", dest="resource_type", default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.rebuild:
        rebuild_index()
    hits = search(
        args.query,
        source=args.source,
        resource_type=args.resource_type,
        limit=args.limit,
    )
    print(json.dumps([{"id": h["id"], "title": h.get("title"), "source": h.get("source")} for h in hits], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
