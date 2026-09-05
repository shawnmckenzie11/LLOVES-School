#!/usr/bin/env python3
"""Generate a four-slide live-class deck without using the staff UI.

Usage:
    python3 scripts/generate_live_class_slides.py --class-id 1 --date 2026-09-10
    python3 scripts/generate_live_class_slides.py --class-id 1 --date 2026-09-10 --force
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LMS = REPO / "lms"
sys.path.insert(0, str(LMS))
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv

load_dotenv(LMS / ".env")
load_dotenv(REPO / ".env")

from live_class_slides import generate_live_class_slides  # noqa: E402
from school_db import SchoolDB  # noqa: E402
from paths import DEFAULT_DB_PATH, DEFAULT_IT_EMAIL  # noqa: E402


def main() -> int:
    """CLI entry: generate or reuse a live-class deck.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Generate live-class Google Slides (or a mock HTML deck).")
    parser.add_argument("--class-id", type=int, required=True, help="MGS classes.id")
    parser.add_argument("--date", required=True, help="Meeting date YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Archive the previous deck and regenerate")
    parser.add_argument("--email", default=DEFAULT_IT_EMAIL, help="Operator email (must be allowlisted)")
    args = parser.parse_args()
    db_file = Path(os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    store = Path(os.getenv("LLOVES_DATA_DIR") or db_file.parent)
    school = SchoolDB(db_file, store)
    user = school.get_user_by_email(args.email)
    if user is None:
        print(f"error: no user {args.email}", file=sys.stderr)
        return 2
    result = generate_live_class_slides(
        school,
        class_id=int(args.class_id),
        meeting_date=date.fromisoformat(args.date),
        user=user,
        force_regenerate=bool(args.force),
    )
    print(result.get("presentation_url") or result.get("presentation_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
