#!/usr/bin/env python3
"""Unpack a Canvas Common Cartridge (.imscc) into a working tree.

IMSCC files are ZIP archives. Pass an explicit ``--imscc`` path; packs are
not stored in this school repo (Admin uploads to the Fly volume).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMSCC = None  # pass --imscc; packs are not in this repo
DEFAULT_OUT = ROOT / "lms" / "data" / "imscc-unpacked"


def unpack_imscc(imscc_path: Path, out_dir: Path, *, clean: bool = False) -> int:
    """Extract an IMSCC/ZIP into ``out_dir``.

    Args:
        imscc_path: Path to the ``.imscc`` (or ``.zip``) archive.
        out_dir: Destination directory for the working tree.
        clean: If True, delete ``out_dir`` before extracting.

    Returns:
        Number of members extracted.

    Raises:
        FileNotFoundError: If the archive does not exist.
        zipfile.BadZipFile: If the archive is not a valid ZIP.
    """
    if not imscc_path.is_file():
        raise FileNotFoundError(f"IMSCC not found: {imscc_path}")

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(imscc_path, "r") as zf:
        members = zf.namelist()
        zf.extractall(out_dir)
    return len(members)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for unpacking an IMSCC archive."""
    parser = argparse.ArgumentParser(
        description="Unpack a Canvas .imscc export into a working directory."
    )
    parser.add_argument(
        "--imscc",
        type=Path,
        required=True,
        help="Path to .imscc archive (required; packs are not in git)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Unpack destination (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the destination directory before unpacking.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: unpack IMSCC and print destination summary."""
    args = parse_args(argv)
    try:
        count = unpack_imscc(args.imscc, args.out, clean=args.clean)
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Unpacked {count} members")
    print(f"  from: {args.imscc}")
    print(f"  to:   {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
