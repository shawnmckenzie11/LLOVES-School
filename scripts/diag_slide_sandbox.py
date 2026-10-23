#!/usr/bin/env python3
"""Print pass or fail for each PowerPoint sandbox probe step.

Run this on the Fly machine before and after a deploy:

    fly ssh console -a lloves-lms -C "python3 /app/scripts/diag_slide_sandbox.py"

Each step runs on its own. The process does not change the app database.
A temporary directory from the full probe is deleted before the script
exits. Exit status is 1 when any step fails. A failing full probe is the
same refusal the app uses for a PowerPoint import. The host-data step
runs ``ls /data`` inside the sandbox and prints that result. It passes
when the list fails. On a root worker the list runs as nobody. A visible
host data directory fails the step. The script keeps extra descriptors
open so the sandbox wait pipe is not fd 3. A fresh process would hide
that failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent.parent / "lms"
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

import course_slide_files  # noqa: E402


def _hold_low_descriptors(count: int = 20) -> list[int]:
    """Open descriptors so the sandbox wait pipe cannot be fd 3.

    A fresh interpreter often receives that pipe as fd 3, which hides
    the handoff bug a worker with a full table hits. The descriptors
    stay open until the process exits.

    Args:
        count: How many descriptors to open.

    Returns:
        The open descriptor numbers.
    """
    return [os.open(os.devnull, os.O_RDONLY) for _ in range(count)]


def main() -> int:
    """Print one ``name: pass|fail`` line per probe step.

    Returns:
        0 when every step passed, otherwise 1.
    """
    held = _hold_low_descriptors()
    span_ok = len(held) >= 20 and held[-1] - held[0] >= 19
    state = "pass" if span_ok else "fail"
    print(f"fd-hold: {state} (n={len(held)} first={held[0]} last={held[-1]})")
    failed = not span_ok
    for name, ok, detail in course_slide_files.sandbox_diagnostic_steps():
        state = "pass" if ok else "fail"
        print(f"{name}: {state} ({detail})")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
