#!/usr/bin/env python3
"""Build a slim Common Cartridge for IT course assignment.

Reads a Canvas ``.imscc`` and writes a new archive that keeps the manifest,
module metadata, QTI banks, quiz metadata, wiki pages, and referenced images
(including ``Uploaded Media`` paths and images with no extension).
Learning outcomes, rubrics, assignment groups, PDFs, documents, and video
are dropped. Members are stored at the zip root.

Usage::

    python3 scripts/slim_imscc.py --imscc course.imscc --out course-slim.imscc
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))

from slim_pack import build_slim_imscc  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for a slim cartridge build.

    Args:
        argv: Argument list, or None to read the process arguments.
    """
    parser = argparse.ArgumentParser(
        description="Write a slim .imscc with banks, outline, and referenced images."
    )
    parser.add_argument("--imscc", type=Path, required=True, help="Source .imscc or .zip")
    parser.add_argument("--out", type=Path, required=True, help="Destination .imscc path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build a slim cartridge and print kept/dropped counts.

    Args:
        argv: Argument list, or None to read the process arguments.

    Returns:
        Process exit code. 0 on success.
    """
    args = parse_args(argv)
    try:
        result = build_slim_imscc(args.imscc, args.out)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Slim pack: kept {len(result.kept)} files, dropped {result.dropped_members}")
    print(f"  from: {args.imscc}")
    print(f"  to:   {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
