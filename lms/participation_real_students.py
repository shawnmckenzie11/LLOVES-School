"""Shared helpers for excluding the class Test Student from participation counts."""

from __future__ import annotations

from typing import Any, Protocol


class _ParticipationSchool(Protocol):
    """Minimal school DB surface for test-student exclusion."""

    conn: Any

    def test_student_ids(self, class_id: int) -> list[int]: ...


def exclude_test_student_ids(class_id: int, school: _ParticipationSchool) -> set[int]:
    """Return Test Student ids for one class (shared exclusion list)."""
    return set(school.test_student_ids(int(class_id)))


def run_has_real_student_answer(
    school: _ParticipationSchool,
    class_id: int,
    run_key: str,
    *,
    test_student_ids: set[int] | None = None,
) -> bool:
    """True when at least one non–Test Student row exists in ``participation_answers``."""
    cid = int(class_id)
    key = str(run_key or "").strip()
    if not key:
        return False
    test_ids = test_student_ids if test_student_ids is not None else exclude_test_student_ids(cid, school)
    params: list[Any] = [cid, key]
    exclude_sql = ""
    if test_ids:
        placeholders = ",".join("?" * len(test_ids))
        exclude_sql = f" AND student_id NOT IN ({placeholders})"
        params.extend(sorted(test_ids))
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
