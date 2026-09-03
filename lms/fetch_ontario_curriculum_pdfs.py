#!/usr/bin/env python3
"""Download the Science 11–12 and HPE 9–12 curriculum PDFs if they are missing.

Seed never fetches the network. Run this script locally or during the Docker
build so ``seed_curriculum`` can extract course codes from disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

LMS_DIR = Path(__file__).resolve().parent
DEST_DIR = LMS_DIR / "sources" / "ontario-curriculum"

# Only the two extra documents requested for this catalog round.
DOCUMENTS: tuple[tuple[str, str], ...] = (
    (
        "science-11-12.pdf",
        "https://www.edu.gov.on.ca/eng/curriculum/secondary/2009science11_12.pdf",
    ),
    (
        "health-pe-9-12.pdf",
        "https://www.edu.gov.on.ca/eng/curriculum/secondary/health9to12.pdf",
    ),
)


def fetch_one(filename: str, url: str, dest_dir: Path) -> Path:
    """Download one Ministry PDF when the local file is missing or not a PDF.

    Args:
        filename: Destination basename under ``dest_dir``.
        url: Official Ministry URL.
        dest_dir: ``lms/sources/ontario-curriculum``.

    Returns:
        Path to the local PDF.

    Raises:
        RuntimeError: HTTP or content is not a PDF.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / filename
    if path.is_file() and path.stat().st_size > 10_000:
        with path.open("rb") as handle:
            if handle.read(5).startswith(b"%PDF"):
                return path
    response = requests.get(
        url,
        timeout=90,
        headers={"User-Agent": "LLOVES-LMS/1.0 (curriculum catalog)"},
        stream=True,
    )
    response.raise_for_status()
    tmp = path.with_suffix(path.suffix + ".part")
    with tmp.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                handle.write(chunk)
    with tmp.open("rb") as handle:
        header = handle.read(5)
    if not header.startswith(b"%PDF"):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{filename} from {url} is not a PDF (got {header!r})")
    tmp.replace(path)
    return path


def main() -> int:
    """Fetch missing Science and HPE PDFs; print one line per file."""
    for filename, url in DOCUMENTS:
        path = fetch_one(filename, url, DEST_DIR)
        print(f"{filename}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
