#!/usr/bin/env python3
"""Re-import a LLOVES content library from its stored Common Cartridge.

Component rows (pages, assignments, quizzes, banks, questions, outlines) are
written once, the first time a class opens Modules, and the expanded cartridge
tree is then deleted to save volume space. When the ingest parser improves,
existing databases keep the old rows. This script re-unpacks the library's
archived ``.imscc`` into a temporary directory and forces a fresh ingest, which
upserts on ``(library_id, import_key)`` so nothing is duplicated and questions
that no longer exist are pruned.

Usage::

    lms/.venv/bin/python scripts/reingest_library.py --db lms/data/lloves.sqlite
    lms/.venv/bin/python scripts/reingest_library.py --library 1
    lms/.venv/bin/python scripts/reingest_library.py --library 1 --dry-run

Safe to run while the dev server is up: sqlite WAL allows a concurrent writer
plus readers, and the database file itself is never replaced.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from components import ensure_ingested  # noqa: E402
from modules import ensure_unpacked, resolve_module_pack  # noqa: E402
from school_db import SchoolDB  # noqa: E402

COUNT_TABLES = (
    "pages",
    "assignments",
    "quizzes",
    "question_banks",
    "module_outlines",
)


def library_counts(school: Any, library_id: int) -> dict[str, int]:
    """Count every component table owned by one library.

    Args:
        school: Open :class:`SchoolDB`.
        library_id: ``content_libraries.id``.

    Returns:
        Table name -> row count, including ``questions`` counted through banks.
    """
    out: dict[str, int] = {}
    for table in COUNT_TABLES:
        row = school.conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE library_id = ?",
            (int(library_id),),
        ).fetchone()
        out[table] = int(row["n"])
    row = school.conn.execute(
        """
        SELECT COUNT(*) AS n FROM questions q
        JOIN question_banks b ON b.id = q.bank_id
        WHERE b.library_id = ?
        """,
        (int(library_id),),
    ).fetchone()
    out["questions"] = int(row["n"])
    return out


def find_cartridge(school: Any, library: dict[str, Any]) -> Path | None:
    """Locate the ``.imscc`` archive of record for a library.

    Args:
        school: Open :class:`SchoolDB`.
        library: ``content_libraries`` row.

    Returns:
        Existing cartridge path, or None when the archive is not on this host.
    """
    source = library.get("source_path")
    if source and Path(str(source)).is_file():
        return Path(str(source))
    pack = resolve_module_pack(
        str(library.get("ontario_code") or ""),
        None,
        data_dir=school.data_dir,
        library_id=int(library["id"]),
        library_source=str(source) if source else None,
    )
    if pack.imscc is not None and pack.imscc.is_file():
        return pack.imscc
    return None


def reingest(school: Any, library: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Re-unpack and re-ingest one library, reporting before/after counts.

    Args:
        school: Open :class:`SchoolDB`.
        library: ``content_libraries`` row.
        dry_run: Report the plan without writing component rows.

    Returns:
        ``{"library_id", "code", "before", "after", "summary", "error"}``.
    """
    library_id = int(library["id"])
    report: dict[str, Any] = {
        "library_id": library_id,
        "code": str(library.get("ontario_code") or ""),
        "before": library_counts(school, library_id),
        "after": {},
        "summary": None,
        "error": None,
    }
    cartridge = find_cartridge(school, library)
    if cartridge is None:
        report["error"] = "cartridge archive not found on this machine"
        return report
    report["cartridge"] = str(cartridge)
    if dry_run:
        report["after"] = report["before"]
        return report

    with tempfile.TemporaryDirectory(prefix="lloves-reingest-") as tmp:
        unpacked = Path(tmp) / "unpacked"
        status = ensure_unpacked(cartridge, unpacked, force=True)
        if not status.get("ok"):
            report["error"] = str(status.get("error") or "unpack failed")
            return report
        report["summary"] = ensure_ingested(
            school, school.data_dir, library_id, unpacked, force=True
        )
    report["after"] = library_counts(school, library_id)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv``).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=str(LMS_DIR / "data" / "lloves.sqlite"),
        help="Path to the LLOVES sqlite database.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="LMS data volume (defaults to the database's folder).",
    )
    parser.add_argument(
        "--library",
        type=int,
        action="append",
        default=None,
        help="Library id to re-ingest (repeatable; default: all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show current counts and the resolved cartridge without writing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Re-ingest the requested libraries and print a before/after table.

    Args:
        argv: Argument list (defaults to ``sys.argv``).

    Returns:
        Process exit code; 1 when any library could not be re-ingested.
    """
    args = parse_args(argv)
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"No database at {db_path}", file=sys.stderr)
        return 1
    school = SchoolDB(db_path, Path(args.data_dir or db_path.parent))
    try:
        rows = [
            dict(row)
            for row in school.conn.execute(
                "SELECT * FROM content_libraries ORDER BY id"
            )
        ]
        wanted = set(args.library or [])
        if wanted:
            rows = [row for row in rows if int(row["id"]) in wanted]
        if not rows:
            print("No matching libraries.", file=sys.stderr)
            return 1
        failures = 0
        for row in rows:
            report = reingest(school, row, dry_run=bool(args.dry_run))
            label = f"library {report['library_id']} ({report['code']})"
            if report["error"]:
                failures += 1
                print(f"{label}: SKIPPED - {report['error']}")
                continue
            print(f"{label}: {report.get('cartridge', '')}")
            for key in (*COUNT_TABLES, "questions"):
                before = report["before"].get(key, 0)
                after = report["after"].get(key, 0)
                arrow = "" if before == after else "  <-- changed"
                print(f"  {key:<16} {before:>6} -> {after:>6}{arrow}")
        return 1 if failures else 0
    finally:
        school.close()


if __name__ == "__main__":
    raise SystemExit(main())
