#!/usr/bin/env python3
"""Ensure the STMTG staff-meeting tester seat.

Dry-run is the default. Nothing is written unless ``--apply`` is passed.
``--dry-run`` wins over ``--apply``. Production (Fly, ``FLASK_ENV=production``,
or a database on ``/data``) is refused unless ``--production`` and
``--confirm MCK-241`` are both passed. The script never prints a PIN.

Usage::

    python3 scripts/seed_stmtg_student_tester.py --teacher-email teacher@school.ca
    python3 scripts/seed_stmtg_student_tester.py --apply --teacher-email teacher@school.ca --db /tmp/local.sqlite
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

CONFIRM_TOKEN = "MCK-241"
EXIT_REFUSED = 2


def refusal_reason(db_path: Path | None) -> str | None:
    """Why this process must not open the database.

    Args:
        db_path: Sqlite path the operator passed, or None for the default.

    Returns:
        A short reason, or None when the target is a local file.
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
    """
    parser = argparse.ArgumentParser(
        description=(
            "Ensure the STMTG student tester seat. Dry-run by default. "
            "Production needs --production --confirm MCK-241."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan. This is the default.")
    parser.add_argument("--apply", action="store_true", help="Write the seat. Ignored when --dry-run is also set.")
    parser.add_argument("--db", type=Path, default=None, help="Sqlite path. Default is the LMS database.")
    parser.add_argument("--teacher-email", required=True, help="Existing staff user who owns STMTG.")
    parser.add_argument("--production", action="store_true", help="Allow a production target. Needs --confirm MCK-241 too.")
    parser.add_argument("--confirm", default=None, help="Must be MCK-241 together with --production.")
    return parser.parse_args(argv)


def production_gate(args: argparse.Namespace, reason: str | None) -> str | None:
    """Return a refusal message, or None when the run may continue.

    Args:
        args: Parsed CLI arguments.
        reason: Output of :func:`refusal_reason`.
    """
    has_flag = bool(args.production)
    has_confirm = args.confirm is not None
    if has_flag != has_confirm:
        return "Refusing: --production and --confirm MCK-241 must be passed together."
    if has_confirm and str(args.confirm) != CONFIRM_TOKEN:
        return f"Refusing: --confirm must be exactly {CONFIRM_TOKEN}."
    if reason and not has_flag:
        return (
            f"Refusing to run against production ({reason}). "
            "Nothing was opened or written. To run on production, pass "
            f"--production --confirm {CONFIRM_TOKEN} (dry-run unless --apply)."
        )
    return None


def format_plan(plan: dict) -> list[str]:
    """Lines for the dry-run plan. No PIN and no roster names."""
    return [
        "plan:",
        f"  course={plan['course']}",
        f"  teacher_found={plan['teacher_found']}",
        f"  semester_found={plan['semester_found']}",
        f"  catalog_exists={plan['catalog_exists']}",
        f"  offering_exists={plan['offering_exists']}",
        f"  class_exists={plan['class_exists']}",
        f"  tester_flagged={plan['tester_flagged']}",
        "  pin=stored for the teacher and school IT only; not printed",
    ]


def main(argv: list[str] | None = None) -> int:
    """Plan or apply the STMTG tester seat.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        ``0`` on success, ``2`` when refused.
    """
    args = _parse_args(argv)
    reason = refusal_reason(args.db)
    refusal = production_gate(args, reason)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    from app import create_app
    from stmtg_tester import apply_stmtg_tester, plan_stmtg_tester

    kwargs: dict = {"testing": True}
    if args.db is not None:
        kwargs["db_path"] = args.db
        kwargs["data_dir"] = args.db.parent
    app = create_app(**kwargs)
    school = app.config["SCHOOL_DB"]
    write = bool(args.apply) and not bool(args.dry_run)
    try:
        print(f"target: {'PRODUCTION (' + reason + ')' if reason else 'local'}")
        print(f"mode: {'APPLY' if write else 'DRY RUN (no writes; pass --apply to write)'}")
        before = plan_stmtg_tester(school, args.teacher_email)
        for line in format_plan(before):
            print(line)
        if write:
            summary = apply_stmtg_tester(
                school, args.teacher_email, str(app.secret_key or "")
            )
            print(
                "summary: "
                f"course={summary['course']} "
                f"offering_id={summary['offering_id']} "
                f"class_id={summary['class_id']} "
                f"student_id={summary['student_id']} "
                f"created_class={summary['created_class']} "
                f"pin_stored={summary['pin_stored']} "
                "APPLIED"
            )
        else:
            print("summary: DRY RUN, nothing written")
    finally:
        school.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
