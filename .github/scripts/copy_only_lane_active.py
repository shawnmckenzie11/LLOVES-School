#!/usr/bin/env python3
"""Set GitHub Actions output ``active`` when the PR has the copy-only label."""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    """Read PR_LABELS_JSON from the environment and write lane output."""
    raw = os.environ.get("PR_LABELS_JSON", "[]")
    try:
        labels = json.loads(raw)
    except json.JSONDecodeError:
        print(f"invalid PR_LABELS_JSON: {raw}", file=sys.stderr)
        return 1
    active = "copy-only" in labels
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    line = f"active={'true' if active else 'false'}\n"
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
