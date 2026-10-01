"""CSV export of saved End-of-class answers (MCK-46).

Pure formatting: callers read rows per run with
``SchoolDB.live_result_snapshot_rows(class_id, run_key=...)``.
"""

from __future__ import annotations

import csv
import json
import re
from io import StringIO
from typing import Any, Iterable

COLUMNS = [
    "run_date",
    "run_key",
    "session_code",
    "module",
    "live_class",
    "question_key",
    "question",
    "source",
    "student_id",
    "name",
    "team_id",
    "answer",
    "answer_key",
    "awarded_points",
    "answered_at",
]

_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_ANSWER_KEYS = ("choice", "value", "answer", "text", "response", "choices")


def safe_cell(value: Any) -> str:
    """Return a CSV cell that a spreadsheet will not run as a formula.

    Text starting with ``= + - @``, a tab or a carriage return gets a
    leading apostrophe. Plain numbers such as ``-3`` are left alone.
    """
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in "=+-@\t\r" and not _NUMBER.match(text):
        return "'" + text
    return text


def answer_text(answer: Any) -> str:
    """Flatten a saved answer dict to one readable cell."""
    if not isinstance(answer, dict):
        return "" if answer is None else str(answer)
    for key in _ANSWER_KEYS:
        if key in answer and answer[key] not in (None, ""):
            value = answer[key]
            if isinstance(value, (list, tuple)):
                return "; ".join(str(item) for item in value)
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            return str(value)
    rest = {k: v for k, v in answer.items() if k != "question_view"}
    return json.dumps(rest, ensure_ascii=False, sort_keys=True) if rest else ""


def question_text(question: Any) -> str:
    """Pick the question's stem from its saved JSON."""
    if not isinstance(question, dict):
        return ""
    for key in ("text", "prompt", "label", "title"):
        if question.get(key):
            return str(question[key])
    return ""


def live_results_csv(
    runs: Iterable[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> str:
    """Build the CSV text for ``(run, rows)`` pairs, in the order given.

    Args:
        runs: Each run from ``live_result_snapshot_runs`` with its rows from
            ``live_result_snapshot_rows``.

    Returns:
        UTF-8 CSV text with a BOM so Excel reads accented names.
    """
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for run, rows in runs:
        for row in rows:
            question = row.get("question") or {}
            key = question.get("correct_answer") if isinstance(question, dict) else None
            writer.writerow(
                [
                    safe_cell(row.get("snapshot_at") or run.get("snapshot_at")),
                    safe_cell(row.get("run_key")),
                    safe_cell(row.get("session_code")),
                    safe_cell(row.get("live_module")),
                    safe_cell(row.get("live_slot")),
                    safe_cell(row.get("question_key")),
                    safe_cell(question_text(question)),
                    safe_cell(row.get("source")),
                    safe_cell(row.get("student_id")),
                    safe_cell(row.get("codename")),
                    safe_cell(row.get("team_id")),
                    safe_cell(answer_text(row.get("answer"))),
                    safe_cell(
                        "; ".join(map(str, key)) if isinstance(key, list) else key
                    ),
                    safe_cell(row.get("awarded_points")),
                    safe_cell(row.get("answered_at")),
                ]
            )
    return "\ufeff" + buf.getvalue()
