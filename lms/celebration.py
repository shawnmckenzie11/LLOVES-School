"""Staff and public celebration helpers for ``alc.mckenzian.com#celebrations``.

Ranking and the teacher-picked Shoutout setting live here. The public page
asks ``public_celebration_board`` for filled cards only.

Cards:

* **Shoutout** — teacher-picked Codename (school setting)
* **Most Engaged** — one card per class in ``ENGAGED_COURSES``: most
  distinct class days present, then participation points; ties list every
  tied student
"""

from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any

try:
    from gradebook import session_meeting_date
except ImportError:  # ``lms`` package import
    from lms.gradebook import session_meeting_date

SETTING_FEATURED_AWARD = "celebration_featured_award"
# Section codes (``section_code``) that each get their own Most Engaged card.
ENGAGED_COURSES: tuple[str, ...] = ("MCR3U", "MCR3U-2", "MCF3M")
PUBLIC_BOARD_TTL_SECONDS = 60.0

# Every user-visible celebrations string. Wonder replaces these placeholders.
WONDER_COPY: dict[str, str] = {
    "page_title": "Celebrations",  # Wonder copy slot
    # MCK-78 Wonder copy for the three keys #178 kept. Copy pending Shawn.
    "empty_board": "No shout-outs yet. The first one's up for grabs.",  # Wonder copy slot
    "coming_soon_line": "Shout-outs are on their way.",  # Wonder copy slot
    "coming_soon_sub": "Good work deserves a spotlight. We're still setting up the lights.",  # Wonder copy slot
    "award_title": "Shoutout",  # Wonder copy slot
    "engaged_title": "Most Engaged",  # Wonder copy slot
}

_public_board_lock = Lock()
_public_board_memo: dict[str, Any] = {
    "school_id": None,
    "at": 0.0,
    "payload": None,
}


def student_public_name(row: dict[str, Any]) -> str:
    """Codename first; never a legal last name on the public board.

    Args:
        row: Game-show ``students`` row.

    Returns:
        Display string for the celebration cards.
    """
    for key in ("codename", "first_name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "Student"


def public_student_label(row: dict[str, Any], course: str) -> str:
    """Public card name: Codename, else first name, else a course placeholder.

    Matches ``student_public_name`` when a Codename or first name is set.
    A blank Codename and a blank first name become ``A student in <course>``.
    Last names are never read.

    Args:
        row: Game-show ``students`` row.
        course: Section code used only for the blank-name placeholder.

    Returns:
        Name safe to show on the public board.
    """
    if str(row.get("codename") or "").strip() or str(row.get("first_name") or "").strip():
        return student_public_name(row)
    course_code = str(course or "").strip()
    if course_code:
        return f"A student in {course_code}"
    return "A student"


def _empty_card(key: str, title: str, kicker: str, waiting: str) -> dict[str, Any]:
    """Return a card payload with no winner yet."""
    return {
        "key": key,
        "title": title,
        "kicker": kicker,
        "waiting": waiting,
        "name": "",
        "detail": "",
        "course": "",
    }


def _active_class_rows(school: Any) -> list[dict[str, Any]]:
    """Populated class ids in the active semester."""
    semester = school.get_active_semester()
    if not semester:
        return []
    offerings = school.list_offerings(
        semester_id=int(semester["id"]),
        include_archived=False,
    )
    rows: list[dict[str, Any]] = []
    for offering in offerings:
        course = str(
            offering.get("section_code") or offering.get("ontario_code") or ""
        ).strip()
        for cls in offering.get("classes") or []:
            rows.append({"class_id": int(cls["id"]), "course": course})
    return rows


def _gather_stats(school: Any) -> list[dict[str, Any]]:
    """Per-student attendance and points, scoped to one class at a time.

    ``session_scores`` holds one row per (session, student), but a class can
    log several sessions on the same meeting day (a second attendance pass,
    an extra game ``_2``/``_3``). Attendance therefore counts distinct
    meeting days with ``present`` set, matching the staff Attendance grid
    (``gradebook.build_attendance_week_grid``): template columns are
    skipped. Points sum every scored row, matching the gradebook TOTAL.

    Students are ``students`` rows owned by one ``class_id``, so a learner
    rostered in two sections gets two separate entries, never a merged one.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        One stats row per (class, student) in the active semester.
    """
    stats: list[dict[str, Any]] = []
    game = school.game
    seen_classes: set[int] = set()
    for cls in _active_class_rows(school):
        class_id = int(cls["class_id"])
        if class_id in seen_classes:
            continue
        seen_classes.add(class_id)
        with game._lock:
            students = [
                dict(row)
                for row in game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ?",
                    (class_id,),
                )
            ]
            sessions = [
                dict(row)
                for row in game.conn.execute(
                    """
                    SELECT id, starts_at, status FROM sessions
                    WHERE class_id = ?
                    ORDER BY starts_at ASC, id ASC
                    """,
                    (class_id,),
                )
            ]
            score_rows = [
                dict(row)
                for row in game.conn.execute(
                    """
                    SELECT ss.session_id, ss.student_id, ss.present, ss.points
                    FROM session_scores ss
                    JOIN sessions se ON se.id = ss.session_id
                    JOIN students st ON st.id = ss.student_id
                    WHERE se.class_id = ? AND st.class_id = se.class_id
                    """,
                    (class_id,),
                )
            ]
        if not students:
            continue
        # Meeting day per non-template session in this class.
        session_days: dict[int, str] = {}
        for session in sessions:
            if str(session.get("status") or "") == "template":
                continue
            meeting = session_meeting_date(session.get("starts_at"))
            if meeting is None:
                continue
            session_days[int(session["id"])] = meeting.isoformat()
        present_days: dict[int, set[str]] = {}
        points_by_student: dict[int, float] = {}
        for row in score_rows:
            stid = int(row["student_id"])
            points_by_student[stid] = points_by_student.get(stid, 0.0) + float(
                row.get("points") or 0
            )
            day = session_days.get(int(row["session_id"]))
            if day is not None and int(row.get("present") or 0) == 1:
                present_days.setdefault(stid, set()).add(day)
        held_days = set(session_days.values())
        for student in students:
            stid = int(student["id"])
            stats.append(
                {
                    "class_id": class_id,
                    "student_id": stid,
                    "name": student_public_name(student),
                    "course": cls["course"],
                    "present": len(present_days.get(stid, set())),
                    "session_count": len(held_days),
                    "points": round(points_by_student.get(stid, 0.0), 1),
                }
            )
    return stats


def _top_tied(stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every student tied for most presents, then most points.

    Args:
        stats: ``_gather_stats`` rows for one class.

    Returns:
        Tied leaders sorted by name, or an empty list when nobody has
        attended or scored yet.
    """
    pool = [s for s in stats if s["present"] > 0 or s["points"] > 0]
    if not pool:
        return []
    best = max((s["present"], s["points"]) for s in pool)
    leaders = [s for s in pool if (s["present"], s["points"]) == best]
    return sorted(leaders, key=lambda s: s["name"].lower())


def _engaged_card(course: str, leaders: list[dict[str, Any]]) -> dict[str, Any]:
    """Most Engaged card for one class, listing every tied leader.

    Args:
        course: Section code shown on the card (``MCR3U-2``).
        leaders: ``_top_tied`` rows; empty leaves the card waiting.

    Returns:
        Card payload with ``students`` ids for the public name lookup.
    """
    card = _empty_card(
        "engaged",
        WONDER_COPY["engaged_title"],
        "",
        "Waiting on the first attendance.",
    )
    card["course"] = course
    if not leaders:
        return card
    top = leaders[0]
    detail = f"{top['present']} class{'es' if top['present'] != 1 else ''} present"
    if top["points"]:
        detail += f" · {top['points']:g} pts"
    card["name"] = ", ".join(s["name"] for s in leaders)
    card["detail"] = detail
    card["students"] = [
        {"class_id": int(s["class_id"]), "student_id": int(s["student_id"])}
        for s in leaders
    ]
    return card


def _featured_card(school: Any) -> dict[str, Any]:
    """Resolve the teacher-picked Shoutout against the live roster."""
    card = _empty_card(
        "award",
        WONDER_COPY["award_title"],
        "",
        "A teacher will feature someone here.",
    )
    raw = school.get_school_setting(SETTING_FEATURED_AWARD, "")
    if not raw.strip():
        return card
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return card
    if not isinstance(payload, dict):
        return card
    try:
        class_id = int(payload.get("class_id"))
        student_id = int(payload.get("student_id"))
    except (TypeError, ValueError):
        return card
    blurb = str(payload.get("blurb") or "").strip()
    try:
        student = school.game.get_student(class_id, student_id)
    except (KeyError, TypeError):
        student = None
    if not student:
        return card
    if int(student.get("class_id") or 0) != class_id:
        return card
    course = ""
    try:
        cls = school.game.get_class(class_id)
        offering_id = cls.get("offering_id")
        if offering_id:
            offering = school.get_offering(int(offering_id))
            course = str(
                offering.get("section_code") or offering.get("ontario_code") or ""
            )
    except (KeyError, TypeError):
        course = ""
    card["name"] = student_public_name(student)
    card["course"] = course
    card["detail"] = blurb or "Featured by their teacher."
    card["class_id"] = class_id
    card["student_id"] = student_id
    return card


def build_celebration_board(school: Any) -> dict[str, Any]:
    """Assemble the Shoutout card and one Most Engaged card per class.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{cards: [...]}`` in display order.
    """
    stats = _gather_stats(school)
    cards = [_featured_card(school)]
    for course in ENGAGED_COURSES:
        in_course = [
            s for s in stats if str(s.get("course") or "").upper() == course
        ]
        cards.append(_engaged_card(course, _top_tied(in_course)))
    return {"cards": cards}


def _public_card(school: Any, card: dict[str, Any]) -> dict[str, Any] | None:
    """One public card, or None when the card has no winner.

    Args:
        school: ``SchoolDB`` instance.
        card: Internal board card, including roster ids.

    Returns:
        ``name``, ``course``, ``detail``, ``title``, ``kicker``, and ``key``,
        or None when ``name`` is empty.
    """
    if not str(card.get("name") or "").strip():
        return None
    course = str(card.get("course") or "").strip()
    name = str(card.get("name") or "").strip()
    if card.get("students"):
        labels = []
        for ids in card["students"]:
            try:
                student = school.game.get_student(
                    int(ids["class_id"]), int(ids["student_id"])
                )
            except (KeyError, TypeError, ValueError):
                student = None
            if student is not None:
                labels.append(public_student_label(student, course))
        name = ", ".join(labels) if labels else name
    class_id = card.get("class_id")
    student_id = card.get("student_id")
    if class_id is not None and student_id is not None:
        try:
            student = school.game.get_student(int(class_id), int(student_id))
        except (KeyError, TypeError, ValueError):
            student = None
        if student is not None:
            name = public_student_label(student, course)
    if not name:
        return None
    return {
        "key": str(card.get("key") or ""),
        "name": name,
        "course": course,
        "detail": str(card.get("detail") or ""),
        "title": str(card.get("title") or ""),
        "kicker": str(card.get("kicker") or ""),
    }


def _assemble_public_board(school: Any) -> dict[str, Any]:
    """Build the public card list from the live ranking board.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{cards: [...]}`` with ids removed and empty cards dropped.
    """
    cards = []
    for card in build_celebration_board(school).get("cards") or []:
        public = _public_card(school, card)
        if public is not None:
            cards.append(public)
    return {"cards": cards}


def clear_public_celebration_memo() -> None:
    """Drop the in-process public board memo.

    Tests call this when a new ``SchoolDB`` replaces the previous one so a
    reused ``id()`` cannot serve another school's cards.
    """
    with _public_board_lock:
        _public_board_memo["school_id"] = None
        _public_board_memo["at"] = 0.0
        _public_board_memo["payload"] = None


def _copy_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a detached copy so callers cannot mutate the memo.

    Args:
        payload: Cached ``{cards: [...]}``.

    Returns:
        Shallow copy of the payload and each card.
    """
    return {"cards": [dict(card) for card in payload.get("cards") or []]}


def public_celebration_board(
    school: Any,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Public celebration cards, memoized for about 60 seconds.

    Only cards with a winner are returned. ``class_id`` and ``student_id``
    are stripped. Names follow ``public_student_label``: Codename, else
    first name, else ``A student in <course>``.

    Args:
        school: ``SchoolDB`` instance.
        now: Monotonic timestamp for tests. Production omits it.

    Returns:
        ``{cards: [...]}`` safe to send to the public landing page.
    """
    stamp = time.monotonic() if now is None else float(now)
    school_id = id(school)
    with _public_board_lock:
        cached = _public_board_memo
        payload = cached.get("payload")
        if (
            cached.get("school_id") == school_id
            and isinstance(payload, dict)
            and stamp - float(cached.get("at") or 0.0) < PUBLIC_BOARD_TTL_SECONDS
        ):
            return _copy_public_payload(payload)
    fresh = _assemble_public_board(school)
    with _public_board_lock:
        _public_board_memo["school_id"] = school_id
        _public_board_memo["at"] = stamp
        _public_board_memo["payload"] = fresh
    return _copy_public_payload(fresh)


def celebration_candidates(school: Any, teacher_user_id: int) -> list[dict[str, Any]]:
    """Codenames this teacher can feature on the Shoutout card.

    Args:
        school: ``SchoolDB`` instance.
        teacher_user_id: Staff user id.

    Returns:
        Sorted ``{class_id, student_id, name, course}`` rows.
    """
    out: list[dict[str, Any]] = []
    for cls in school.list_staff_classes(int(teacher_user_id)):
        class_id = int(cls["id"])
        course = str(cls.get("section_code") or cls.get("course_code") or "")
        with school.game._lock:
            students = [
                dict(row)
                for row in school.game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ? ORDER BY first_name",
                    (class_id,),
                )
            ]
        for student in students:
            out.append(
                {
                    "class_id": class_id,
                    "student_id": int(student["id"]),
                    "name": student_public_name(student),
                    "course": course,
                }
            )
    out.sort(key=lambda row: (row["course"], row["name"].lower()))
    return out


def set_featured_award(
    school: Any,
    *,
    teacher_user_id: int,
    class_id: int | None,
    student_id: int | None,
    blurb: str = "",
) -> dict[str, Any]:
    """Save or clear the teacher-picked Shoutout student.

    Args:
        school: ``SchoolDB`` instance.
        teacher_user_id: Acting staff user.
        class_id: Class of the featured student, or None to clear.
        student_id: Featured student, or None to clear.
        blurb: Optional one-line celebration.

    Returns:
        Updated featured card.

    Raises:
        ValueError: Unknown student or class the teacher does not own.
    """
    if class_id is None or student_id is None:
        school.set_school_setting(SETTING_FEATURED_AWARD, "")
        return _featured_card(school)
    if not school.teacher_owns_class(int(teacher_user_id), int(class_id)):
        raise ValueError("That class is not yours to celebrate.")
    with school.game._lock:
        row = school.game.conn.execute(
            "SELECT id FROM students WHERE id = ? AND class_id = ?",
            (int(student_id), int(class_id)),
        ).fetchone()
    if row is None:
        raise ValueError("That Codename is not on this roster.")
    payload = {
        "class_id": int(class_id),
        "student_id": int(student_id),
        "blurb": str(blurb or "").strip()[:240],
    }
    school.set_school_setting(SETTING_FEATURED_AWARD, json.dumps(payload))
    return _featured_card(school)
