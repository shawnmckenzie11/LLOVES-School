"""Assemble Portfolio Marking card rows from Drive/local files + observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from portfolio.drive_docs import (
    ensure_portfolio_folder,
    export_google_doc_text,
    list_folder_files,
)
from portfolio.lookfors import LOOKFOR_IDS, LOOKFOR_LABELS, empty_tally
from portfolio.store import local_submissions_dir
from portfolio.suggest import submission_text_from_html, suggest_levels
from slides_template import drive_year_semester


def list_local_submissions(course_code: str, module_number: int) -> list[dict[str, str]]:
    """List HTML/PDF files in the local fallback folder.

    Args:
        course_code: Ontario code.
        module_number: Module index.
    """
    folder = local_submissions_dir(course_code, module_number)
    if not folder.is_dir():
        return []
    out: list[dict[str, str]] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".htm", ".pdf", ".txt", ".doc", ".docx"}:
            continue
        out.append(
            {
                "id": str(path),
                "name": path.name,
                "mimeType": path.suffix.lower(),
                "url": "",
                "source": "local",
            }
        )
    return out


def read_local_text(path: Path) -> str:
    """Read a local submission for the classifier."""
    if path.suffix.lower() == ".pdf":
        try:
            from portfolio.useful_words import _extract_pdf_text

            return _extract_pdf_text(path)
        except Exception:  # noqa: BLE001
            return ""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if path.suffix.lower() in {".html", ".htm"}:
        return submission_text_from_html(raw)
    return raw


def match_submission_for_student(
    student: dict[str, Any], files: list[dict[str, str]]
) -> dict[str, str] | None:
    """Best-effort filename match on codename.

    Args:
        student: Roster row.
        files: Drive + local listings.
    """
    needles = [
        str(student.get("last_display") or "").strip().lower(),
        str(student.get("first_name") or "").strip().lower(),
        str(student.get("codename") or "").strip().lower(),
    ]
    needles = [n for n in needles if n]
    for row in files:
        name = str(row.get("name") or "").lower()
        if any(n and n in name for n in needles):
            return row
    return None


def drive_path_label(ontario_code: str, module_number: int, semester_label: str) -> str:
    """Human path ``ALC/{year}/{term}/{CODE}/M{n}``."""
    year, term = drive_year_semester(semester_label)
    return f"ALC/{year}/{term}/{str(ontario_code).upper()}/M{int(module_number)}"
