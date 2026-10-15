"""MCK-230: shared Participation tab exclusions (test runs, Test Student).

Test runs (`participation_runs.is_real = 0` or `discarded = 1`) are omitted from
the Participation tab grid, SUBTOTAL, and TOTAL. Test Student roster rows are
excluded when deciding which live answers count toward denominators.
"""

from __future__ import annotations

from typing import Any

from live_class_run import parse_ended_at

_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def sql_exclude_non_counted_participation_run(
    class_id_expr: str,
    run_key_expr: str,
    *,
    alias: str = "dr",
) -> str:
    """SQL fragment: skip test/discarded ``participation_runs`` rows.

    Args:
        class_id_expr: SQL expression for ``participation_runs.class_id``.
        run_key_expr: SQL expression for ``participation_runs.run_key``.
        alias: Subquery table alias.

    Returns:
        ``AND NOT EXISTS (...)`` suitable to append to a ``WHERE`` clause.
    """
    return (
        f" AND NOT EXISTS (SELECT 1 FROM participation_runs {alias} "
        f"WHERE {alias}.class_id = {class_id_expr} AND {alias}.run_key = {run_key_expr} "
        f"AND ({alias}.discarded = 1 OR {alias}.is_real = 0))"
    )


def participation_run_counts_in_tab(kind: dict[str, Any] | None) -> bool:
    """True when a run should appear in Participation tab columns and rollups."""
    if kind is None:
        return True
    return bool(kind.get("is_real")) and not bool(kind.get("discarded"))


def format_participation_session_header(
    live_module: str,
    live_slot: str,
    ended_at: str | None,
) -> str:
    """Column label: lesson slot plus Toronto calendar date (naive UTC stored)."""
    mod = str(live_module or "M1").strip().upper()
    slot = str(live_slot or "C1").strip().upper()
    slot_label = f"{mod}{slot}"
    text = str(ended_at or "").strip()
    if not text:
        return slot_label
    when = parse_ended_at(text)
    abbr = _WEEKDAY_ABBR[when.weekday()]
    return f"{slot_label} · {abbr} {when.month}/{when.day}/{when.year % 100:02d}"


def toronto_date_label(ended_at: str | None) -> str:
    """Short Toronto date for tests and display helpers."""
    text = str(ended_at or "").strip()
    if not text:
        return ""
    when = parse_ended_at(text)
    return f"{when.month}/{when.day}/{when.year % 100:02d}"
