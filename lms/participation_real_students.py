"""Shared helpers for excluding Test Student from participation answer counts."""

from __future__ import annotations

from typing import Any, Protocol


class _ParticipationSchool(Protocol):
    """Minimal school DB surface for participation answer exclusion."""

    conn: Any

    def test_student_ids(self, class_id: int) -> list[int]: ...


def exclude_test_student_ids(class_id: int, school: _ParticipationSchool) -> set[int]:
    """Return Test Student ids for one class (shared exclusion list)."""
    return set(school.test_student_ids(int(class_id)))


def exclude_participation_answer_student_ids(
    class_id: int, school: _ParticipationSchool
) -> set[int]:
    """Return roster ids excluded from participation / attendance answer gates."""
    return exclude_test_student_ids(int(class_id), school)


def run_has_real_student_answer(
    school: _ParticipationSchool,
    class_id: int,
    run_key: str,
    *,
    excluded_student_ids: set[int] | None = None,
) -> bool:
    """True when at least one non-excluded student row exists in ``participation_answers``."""
    cid = int(class_id)
    key = str(run_key or "").strip()
    if not key:
        return False
    exclude_ids = (
        excluded_student_ids
        if excluded_student_ids is not None
        else exclude_participation_answer_student_ids(cid, school)
    )
    params: list[Any] = [cid, key]
    exclude_sql = ""
    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        exclude_sql = f" AND student_id NOT IN ({placeholders})"
        params.extend(sorted(exclude_ids))
    row = school.conn.execute(
        f"""
        SELECT 1 FROM participation_answers
        WHERE class_id = ? AND run_key = ? AND student_id IS NOT NULL
        {exclude_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row is not None
