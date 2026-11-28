#!/usr/bin/env python3
"""MCK-542: correct End Class attendance and participation credit columns.

Dry run is the default and prints counts only (never student names, ids,
codes, or emails). The dry run works on a throwaway copy of the database
(a sqlite backup taken read-only), so the live file is not written.

Writing needs ``--apply`` and ``--backup-confirmed`` together, runs in one
transaction, and logs every change in ``mck542_recompute_log``. Undo a run
with ``--revert RUN_ID`` (same flags as ``--apply``).

Production (``/data``, Fly, ``FLASK_ENV=production``) is refused unless
``--production --confirm MCK-542`` is passed. Run it when no class is live.

Usage::

    python3 scripts/mck542_recompute_attendance.py --db /tmp/local.sqlite
    python3 scripts/mck542_recompute_attendance.py --db /tmp/local.sqlite --apply --backup-confirmed
    python3 scripts/mck542_recompute_attendance.py --production --confirm MCK-542
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lms"))
sys.path.insert(0, str(REPO_ROOT))

CONFIRM_TOKEN = "MCK-542"
EXIT_REFUSED = 2


def production_reason(db_path: Path) -> str | None:
    """Why this target looks like production, or ``None`` for a local file."""
    if str(os.getenv("FLY_APP_NAME") or "").strip():
        return "FLY_APP_NAME is set"
    if (os.getenv("FLASK_ENV") or "").lower() == "production":
        return "FLASK_ENV=production"
    try:
        text = str(db_path.resolve())
    except OSError:
        text = str(db_path)
    if text == "/data" or text.startswith("/data/"):
        return f"database is on the production volume ({text})"
    return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", type=Path, default=None, help="Sqlite path (default: LLOVES_DB or the LMS default).")
    p.add_argument("--since", default="2026-10-21", help="First day of sessions to scan.")
    p.add_argument("--apply", action="store_true", help="Write the correction (needs --backup-confirmed).")
    p.add_argument("--backup-confirmed", action="store_true", help="Confirm a fresh backup of the database exists.")
    p.add_argument("--revert", metavar="RUN_ID", default=None, help="Undo a previous --apply run.")
    p.add_argument("--production", action="store_true", help="Allow a production target (with --confirm).")
    p.add_argument("--confirm", default=None, help=f"Must be {CONFIRM_TOKEN} with --production.")
    return p.parse_args(argv)


def gate(args: argparse.Namespace, reason: str | None) -> str | None:
    """Return a refusal message, or ``None`` when the run may continue."""
    if bool(args.production) != (args.confirm is not None):
        return f"Refusing: --production and --confirm {CONFIRM_TOKEN} must be passed together."
    if args.confirm is not None and args.confirm != CONFIRM_TOKEN:
        return f"Refusing: --confirm must be exactly {CONFIRM_TOKEN}."
    if reason and not args.production:
        return (
            f"Refusing to open production ({reason}). Nothing was opened. "
            f"Pass --production --confirm {CONFIRM_TOKEN} (dry run unless --apply)."
        )
    if (args.apply or args.revert) and not args.backup_confirmed:
        return "Refusing: writing needs --backup-confirmed (take a backup first)."
    if args.apply and args.revert:
        return "Refusing: pass --apply or --revert, not both."
    return None


def main(argv: list[str] | None = None) -> int:
    """Run the dry run, the write, or the revert.

    Returns:
        ``0`` on success, ``2`` when refused.
    """
    args = _parse_args(argv)
    if args.db is not None:
        db_path = args.db
    elif os.getenv("LLOVES_DB"):
        db_path = Path(os.environ["LLOVES_DB"])
    else:
        from paths import DEFAULT_DB_PATH

        db_path = Path(DEFAULT_DB_PATH)
    reason = production_reason(db_path)
    refusal = gate(args, reason)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    from mck542_attendance_recompute import (
        apply_recompute,
        format_report,
        plan_recompute,
        revert_run,
    )
    from school_db import SchoolDB

    write = bool(args.apply or args.revert)
    print(f"target: {'PRODUCTION (' + reason + ')' if reason else 'local'}")
    print(f"mode: {'APPLY' if args.apply else 'REVERT' if args.revert else 'DRY RUN (works on a copy; nothing is written)'}")
    with tempfile.TemporaryDirectory(prefix="mck542-") as tmp:
        if write:
            path, data_dir = db_path, db_path.parent
        else:
            path, data_dir = Path(tmp) / "snapshot.sqlite", Path(tmp)
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            dst = sqlite3.connect(path)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        school = SchoolDB(path, data_dir, live_database_url="")
        try:
            if args.revert:
                print(f"reverted: {revert_run(school, args.revert)}")
                return 0
            plans = plan_recompute(school, args.since)
            if args.apply:
                run_id = apply_recompute(school, plans)
                print(f"run_id: {run_id} (undo with --revert {run_id})")
            for line in format_report(plans, applied=bool(args.apply)):
                print(line)
        finally:
            school.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
