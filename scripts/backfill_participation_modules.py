#!/usr/bin/env python3
"""Finish past modules for current live courses (MCK-251).

Groups earlier live-class columns into modules. Each class and module
that is written gets one ``Finish Module N`` subtotal (through that
module's last session) and one participation mark. Modules that already
have either are skipped, so a second run does not create
``Finish Module 1 2``.

Dry-run is the default: the plan and the before/after counts are
printed, and nothing is written. ``--apply`` writes to a local database.
``--dry-run`` is the same as the default.

This script refuses production (Fly, ``FLASK_ENV=production``, a data
dir on ``/data``, or a database on the ``/data`` volume), including a
dry-run, before it opens the database. Shawn approves a production run
separately, after a backup. There is no override flag here.

Usage::

    python3 scripts/backfill_participation_modules.py
    python3 scripts/backfill_participation_modules.py --db lms/data/lloves.sqlite
    python3 scripts/backfill_participation_modules.py --apply --db /tmp/local.sqlite
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))


def refusal_reason(db_path: Path | None) -> str | None:
    """Why this process must not open the database.

    Args:
        db_path: Sqlite path the operator passed, or ``None`` for the
            default local file (``LLOVES_DB`` or the LMS default).

    Returns:
        A short reason, or ``None`` when the target is a local file.
    """
    if str(os.getenv("FLY_APP_NAME") or "").strip():
        return "FLY_APP_NAME is set"
    if (os.getenv("FLASK_ENV") or "").lower() == "production":
        return "FLASK_ENV=production"
    data_dir = str(os.getenv("LLOVES_DATA_DIR") or os.getenv("DATA_DIR") or "").strip()
    if data_dir == "/data" or data_dir.startswith("/data/"):
        return "LLOVES data dir is the production volume"
    candidates: list[Path] = []
    if db_path is not None:
        candidates.append(db_path)
    else:
        raw = str(os.getenv("LLOVES_DB") or "").strip()
        if raw:
            candidates.append(Path(raw))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        text = str(resolved)
        if text == "/data" or text.startswith("/data/"):
            return f"database is on the production volume ({text})"
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Namespace with ``apply``, ``dry_run``, and ``db``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Finish past participation modules for current live courses. "
            "Dry-run by default. Refuses production."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and before/after counts. This is the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the plan to the local database. Ignored when --dry-run is also set.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Sqlite path. Default is the local LMS database.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the backfill, or refuse when the target looks like production.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        ``0`` after a local dry-run or apply, ``2`` when production is refused.
    """
    args = _parse_args(argv)
    reason = refusal_reason(args.db)
    if reason:
        print(
            "Refusing to run against production "
            f"({reason}). Shawn approves the production run separately after a backup.",
            file=sys.stderr,
        )
        return 2
    from app import create_app
    from participation_module_backfill import (
        apply_participation_module_backfill,
        backfill_counts,
        format_backfill_report,
        plan_participation_module_backfill,
    )

    kwargs: dict = {"testing": True}
    if args.db is not None:
        kwargs["db_path"] = args.db
        kwargs["data_dir"] = args.db.parent
    app = create_app(**kwargs)
    school = app.config["SCHOOL_DB"]
    try:
        database = str(school.game.db_path)
        before = backfill_counts(school)
        plan = plan_participation_module_backfill(school)
        write = bool(args.apply) and not bool(args.dry_run)
        if write:
            apply_participation_module_backfill(school, plan)
        after = backfill_counts(school)
        for line in format_backfill_report(
            plan,
            dry_run=not write,
            database=database,
            before=before,
            after=after,
        ):
            print(line)
    finally:
        school.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
