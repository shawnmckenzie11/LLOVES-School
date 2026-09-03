#!/usr/bin/env python3
"""Parse a Canvas gradebook CSV into a Math Game Show roster.

Keeps only ``Student`` and ``ID``. Skips header/meta rows and blank IDs.
"""

from __future__ import annotations

import csv
import io
from typing import Any


def parse_student_name(raw: str) -> tuple[str, str]:
    """Split a Canvas ``Student`` cell into last-display and first name.

    ``"Last, First"`` uses the comma (trimmed; handles ``"Sxxxxxx , Joy "``).
    With no comma (``"Zxx Hafsa"``), the last token after the final space is
    the first name and the rest is the last-name display.

    Args:
        raw: Student column value from Canvas.

    Returns:
        ``(last_display, first_name)``.
    """
    text = (raw or "").strip().strip('"').strip()
    if "," in text:
        last, first = text.split(",", 1)
        return last.strip(), first.strip()
    parts = text.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]).strip(), parts[-1].strip()


def _header_index(header: list[str], name: str) -> int:
    """Return the column index for a header name (case-insensitive).

    Args:
        header: Header row cells.
        name: Expected column title.

    Returns:
        Zero-based index.

    Raises:
        ValueError: If the column is missing.
    """
    lowered = [h.strip().lower() for h in header]
    want = name.strip().lower()
    try:
        return lowered.index(want)
    except ValueError as exc:
        raise ValueError(f"CSV missing required column: {name}") from exc


def _is_meta_student(student: str) -> bool:
    """Return True if this row is a Canvas meta row, not a person.

    Args:
        student: Student cell text.
    """
    label = student.strip().lower()
    if not label:
        return True
    if "points possible" in label:
        return True
    if label in {"student", "name"}:
        return True
    return False


def parse_canvas_grades_csv(text: str) -> list[dict[str, str]]:
    """Parse a Canvas gradebook export into roster dicts.

    Expected shape: header row, posting-method row, ``Points Possible`` row,
    then student rows. Extra assignment/score columns are ignored.

    Args:
        text: Entire CSV file contents.

    Returns:
        List of ``{canvas_id, last_display, first_name, student_raw}``.

    Raises:
        ValueError: If the file has no Student/ID columns or no students.
    """
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV is empty") from exc

    student_i = _header_index(header, "Student")
    id_i = _header_index(header, "ID")

    rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in reader:
        if not raw:
            continue
        student = raw[student_i] if student_i < len(raw) else ""
        canvas_id = (raw[id_i] if id_i < len(raw) else "").strip()
        if _is_meta_student(student) or not canvas_id:
            continue
        last_display, first_name = parse_student_name(student)
        if not last_display and not first_name:
            continue
        if canvas_id in seen_ids:
            continue
        seen_ids.add(canvas_id)
        rows.append(
            {
                "canvas_id": canvas_id,
                "last_display": last_display,
                "first_name": first_name,
                "student_raw": student.strip(),
            }
        )
    if not rows:
        raise ValueError("No students found in CSV (check Student and ID columns)")
    return rows


def roster_summary(students: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a short import summary for the wizard UI.

    Args:
        students: Output of :func:`parse_canvas_grades_csv`.

    Returns:
        Count plus a few parsed name previews.
    """
    preview = [
        {
            "canvas_id": s["canvas_id"],
            "last_display": s["last_display"],
            "first_name": s["first_name"],
        }
        for s in students[:5]
    ]
    return {"count": len(students), "preview": preview}
