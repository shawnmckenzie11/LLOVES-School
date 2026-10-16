"""Create the STMTG staff-meeting section and its one tester seat.

The seat is a roster row with ``is_test_student`` set, so attendance,
participation, celebrations, and student class lists skip it. It is never
added to MCR3U, MCR3U-2, or MCF3M. Callers that print a plan must not
print the PIN. The PIN, when stored, is the existing default-PIN
ciphertext the teacher and school IT already see on the roster.
"""

from __future__ import annotations

from typing import Any

from tester_feedback import REAL_SECTION_CODES, STAFF_MEETING_CODE, TESTER_CODENAME

STAFF_MEETING_TITLE = "Staff meeting"
STAFF_MEETING_DAYS = "M/W/F"
STAFF_MEETING_TIME = "3:15pm"


def course_accepts_tester(course_code: str) -> bool:
    """True only for the staff-meeting section.

    Args:
        course_code: Ontario or section code.
    """
    return str(course_code or "").strip().upper() == STAFF_MEETING_CODE


def assert_tester_course(course_code: str) -> None:
    """Refuse to place the tester on a real course section.

    Args:
        course_code: Section the caller is about to write.

    Raises:
        ValueError: The code is not STMTG.
    """
    code = str(course_code or "").strip().upper()
    if code in {item.upper() for item in REAL_SECTION_CODES} or not course_accepts_tester(code):
        raise ValueError("The student tester seat belongs on STMTG only.")


def plan_stmtg_tester(school: Any, teacher_email: str) -> dict[str, Any]:
    """Describe the STMTG tester seat without writing.

    Args:
        school: School DB.
        teacher_email: Staff account that should own the section.

    Returns:
        A plan dict. It has no PIN and no student names from other classes.
    """
    email = str(teacher_email or "").strip().lower()
    teacher = school.get_user_by_email(email) if email else None
    semester = school.get_active_semester()
    course = school.get_course(STAFF_MEETING_CODE)
    offering = None
    class_row = None
    tester_id = None
    if teacher and semester and course:
        offering = school.get_offering_for(
            int(semester["id"]), STAFF_MEETING_CODE, int(teacher["id"])
        )
        if offering:
            classes = school.classes_for_offering(int(offering["id"]))
            class_row = classes[0] if classes else None
            if class_row:
                ids = school.test_student_ids(int(class_row["id"]))
                tester_id = ids[0] if ids else None
    return {
        "course": STAFF_MEETING_CODE,
        "teacher_found": teacher is not None,
        "semester_found": semester is not None,
        "catalog_exists": course is not None,
        "offering_exists": offering is not None,
        "class_exists": class_row is not None,
        "tester_flagged": tester_id is not None,
        "class_id": int(class_row["id"]) if class_row else None,
        "writes": False,
    }


def apply_stmtg_tester(school: Any, teacher_email: str, secret: str) -> dict[str, Any]:
    """Ensure the STMTG section and one flagged tester seat.

    Args:
        school: School DB.
        teacher_email: Existing staff user.
        secret: App secret used to store a default PIN ciphertext.
            The plaintext PIN is not returned.

    Returns:
        Summary with ids only. ``pin_stored`` says whether a ciphertext
        was written. The PIN itself is absent.

    Raises:
        ValueError: The teacher or semester is missing, or the seat would
            land on a real course.
    """
    assert_tester_course(STAFF_MEETING_CODE)
    email = str(teacher_email or "").strip().lower()
    teacher = school.get_user_by_email(email) if email else None
    if teacher is None:
        raise ValueError("That teacher account is not in this database.")
    semester = school.get_active_semester()
    if not semester:
        raise ValueError("No active semester.")
    school.upsert_ontario_course(STAFF_MEETING_CODE, STAFF_MEETING_TITLE)
    offering = school.assign_course(
        semester_id=int(semester["id"]),
        ontario_code=STAFF_MEETING_CODE,
        teacher_user_id=int(teacher["id"]),
    )
    if str(offering.get("ontario_code") or "").upper() != STAFF_MEETING_CODE:
        raise ValueError("The student tester seat belongs on STMTG only.")
    if not str(offering.get("live_days") or "").strip() or not str(
        offering.get("live_time") or ""
    ).strip():
        offering = school.set_offering_schedule(
            int(offering["id"]),
            live_days=STAFF_MEETING_DAYS,
            live_time=STAFF_MEETING_TIME,
        )
    classes = school.classes_for_offering(int(offering["id"]))
    created_class = False
    if classes:
        class_row = classes[0]
    else:
        class_row = school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=STAFF_MEETING_CODE,
            days_preset=str(offering.get("live_days") or STAFF_MEETING_DAYS),
            time_label=str(offering.get("live_time") or STAFF_MEETING_TIME),
            codenames=[TESTER_CODENAME],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        created_class = True
    class_id = int(class_row["id"])
    course_code = str(class_row.get("course_code") or STAFF_MEETING_CODE)
    assert_tester_course(course_code)
    tester_id = _ensure_flagged_tester(school, class_id)
    pin_stored = False
    if str(secret or "").strip():
        assigned = school.ensure_default_pins_for_class(class_id, str(secret))
        pin_stored = int(assigned.get("assigned") or 0) > 0 or _tester_has_pin(
            school, tester_id
        )
    return {
        "course": STAFF_MEETING_CODE,
        "offering_id": int(offering["id"]),
        "class_id": class_id,
        "student_id": tester_id,
        "created_class": created_class,
        "pin_stored": pin_stored,
        "writes": True,
    }


def _ensure_flagged_tester(school: Any, class_id: int) -> int:
    """Return the one ``is_test_student`` row, creating Tester when needed.

    Args:
        school: School DB.
        class_id: STMTG class id.

    Returns:
        ``students.id`` of the flagged seat.
    """
    existing = school.test_student_ids(int(class_id))
    if existing:
        return int(existing[0])
    found = school.game.find_student_by_codename(int(class_id), TESTER_CODENAME)
    if found is None:
        school.game.add_student(int(class_id), codename=TESTER_CODENAME)
        found = school.game.find_student_by_codename(int(class_id), TESTER_CODENAME)
    if found is None:
        raise ValueError("Could not add the tester seat.")
    student_id = int(found["id"])
    with school.game._lock:
        school.game.conn.execute(
            """
            UPDATE students SET is_test_student = 1
            WHERE id = ? AND class_id = ?
            """,
            (student_id, int(class_id)),
        )
        school.game.conn.commit()
    return student_id


def _tester_has_pin(school: Any, student_id: int) -> bool:
    """True when the seat already has a PIN hash. The hash is not returned."""
    with school.game._lock:
        row = school.game.conn.execute(
            "SELECT pin_hash FROM students WHERE id = ?",
            (int(student_id),),
        ).fetchone()
    return bool(row and str(row[0] or "").strip())
