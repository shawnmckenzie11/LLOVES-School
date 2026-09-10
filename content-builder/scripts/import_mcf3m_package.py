#!/usr/bin/env python3
"""Validate and import the MCF3M builder-input package into catalogue/onboarding."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from onboarding_lib import (  # noqa: E402
    BuilderPaths,
    apply_import,
    preview_import,
    rollback_import,
)


def main() -> int:
    """CLI: preview, apply, or rollback a package import."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package",
        type=Path,
        default=None,
        help="Path to MCF3M-builder-input (default: content-builder/packages/MCF3M-builder-input)",
    )
    parser.add_argument("--course", default="MCF3M")
    parser.add_argument("--preview", action="store_true", help="Reconcile without writing operational records")
    parser.add_argument("--apply", action="store_true", help="Write the onboarding store")
    parser.add_argument("--rollback", metavar="BATCH_ID", help="Restore only this latest import")
    parser.add_argument(
        "--require-preview",
        action="store_true",
        help="Refuse apply if catalogue/onboarding/COURSE/previews/latest.json fingerprint is stale",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = BuilderPaths(root)
    package = args.package or paths.package()
    if args.rollback:
        result = rollback_import(paths, args.course, args.rollback)
        print(json.dumps(result, indent=2))
        return 0
    if args.apply:
        expected = None
        if args.require_preview:
            preview_path = paths.store(args.course) / "previews" / "latest.json"
            if not preview_path.is_file():
                print("missing preview; run --preview first", file=sys.stderr)
                return 1
            expected = json.loads(preview_path.read_text())["fingerprint_before"]
        result = apply_import(paths, package, args.course, expected_fingerprint=expected)
        print(json.dumps({k: result[k] for k in result if k != "unresolved_conflicts"}, indent=2, default=str))
        print("unresolved_conflicts", len(result.get("unresolved_conflicts") or []))
        return 0
    preview = preview_import(paths, package, args.course)
    print(json.dumps(preview, indent=2, default=str))
    return 0 if preview.get("validation", {}).get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
