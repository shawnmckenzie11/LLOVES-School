"""Public student-celebration board for ``alc.mckenzian.com#celebrations``.

Cards:

* **Awards** — teacher-picked Codename (school setting)
* **Most Engaged** — most presents, then participation points
* **Most Improved** — biggest recent-vs-prior climb (needs four scored classes)
* **Quietly Cooking** — high attendance, not the points leader
"""

from __future__ import annotations

import json
from typing import Any

SETTING_FEATURED_AWARD = "celebration_featured_award"
MIN_SESSIONS_FOR_IMPROVED = 4
MIN_PRESENTS_FOR_COOKING = 2


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
    """Per-student attendance and points across active-semester classes."""
    stats: list[dict[str, Any]] = []
    game = school.game
    for cls in _active_class_rows(school):
        class_id = int(cls["class_id"])
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
                    SELECT id, starts_at FROM sessions
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
                    WHERE se.class_id = ?
                    """,
                    (class_id,),
                )
            ]
        if not students:
            continue
        scored_ids = []
        seen = set()
        for session in sessions:
            sid = int(session["id"])
            if any(int(r["session_id"]) == sid for r in score_rows):
                if sid not in seen:
                    scored_ids.append(sid)
                    seen.add(sid)
        half = len(scored_ids) // 2
        prior_ids = set(scored_ids[:half])
        recent_ids = set(scored_ids[half:])
        by_student: dict[int, list[dict[str, Any]]] = {}
        for row in score_rows:
            by_student.setdefault(int(row["student_id"]), []).append(row)
        for student in students:
            stid = int(student["id"])
            rows = by_student.get(stid, [])
            present = 0
            points = 0.0
            prior_score = 0.0
            recent_score = 0.0
            for row in rows:
                sess_id = int(row["session_id"])
                here_present = 1 if row.get("present") else 0
                here_points = float(row.get("points") or 0)
                present += here_present
                points += here_points
                climb = here_points + here_present
                if sess_id in prior_ids:
                    prior_score += climb
                elif sess_id in recent_ids:
                    recent_score += climb
            stats.append(
                {
                    "class_id": class_id,
                    "student_id": stid,
                    "name": student_public_name(student),
                    "course": cls["course"],
                    "present": present,
                    "session_count": len(scored_ids),
                    "points": points,
                    "delta": recent_score - prior_score,
                    "present_rate": (
                        present / len(scored_ids) if scored_ids else 0.0
                    ),
                }
            )
    return stats


def _pick_unique(
    ranked: list[dict[str, Any]],
    taken: set[tuple[int, int]],
) -> dict[str, Any] | None:
    """First ranked student not already on another computed card."""
    for item in ranked:
        key = (int(item["class_id"]), int(item["student_id"]))
        if key not in taken:
            return item
    return ranked[0] if ranked else None


def _card_from_stat(
    key: str,
    title: str,
    kicker: str,
    waiting: str,
    stat: dict[str, Any] | None,
    detail: str,
) -> dict[str, Any]:
    """Fill a card from a student stat row."""
    card = _empty_card(key, title, kicker, waiting)
    if not stat:
        return card
    card["name"] = stat["name"]
    card["course"] = stat.get("course") or ""
    card["detail"] = detail
    card["class_id"] = stat["class_id"]
    card["student_id"] = stat["student_id"]
    return card


def _featured_card(school: Any) -> dict[str, Any]:
    """Resolve the teacher-picked Award against the live roster."""
    card = _empty_card(
        "award",
        "Awards",
        "Celebrating a student",
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
    """Assemble the four public celebration cards.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        ``{cards: [...]}`` in display order.
    """
    stats = _gather_stats(school)
    engaged_pool = [s for s in stats if s["present"] > 0 or s["points"] > 0]
    engaged_ranked = sorted(
        engaged_pool,
        key=lambda s: (-s["present"], -s["points"], s["name"].lower()),
    )
    improved_pool = [
        s
        for s in stats
        if s["session_count"] >= MIN_SESSIONS_FOR_IMPROVED and s["delta"] > 0
    ]
    improved_ranked = sorted(
        improved_pool,
        key=lambda s: (-s["delta"], s["name"].lower()),
    )
    cooking_pool = [
        s for s in stats if s["present"] >= MIN_PRESENTS_FOR_COOKING
    ]
    cooking_ranked = sorted(
        cooking_pool,
        key=lambda s: (-s["present_rate"], s["points"], s["name"].lower()),
    )

    taken: set[tuple[int, int]] = set()
    engaged = _pick_unique(engaged_ranked, taken)
    if engaged:
        taken.add((int(engaged["class_id"]), int(engaged["student_id"])))
    improved = _pick_unique(improved_ranked, taken)
    if improved:
        taken.add((int(improved["class_id"]), int(improved["student_id"])))
    cooking = _pick_unique(cooking_ranked, taken)

    engaged_detail = ""
    if engaged:
        pts = engaged["points"]
        engaged_detail = (
            f"{engaged['present']} class"
            f"{'es' if engaged['present'] != 1 else ''} present"
        )
        if pts:
            engaged_detail += f" · {pts:g} pts"
    improved_detail = ""
    if improved:
        improved_detail = f"+{improved['delta']:g} vs earlier classes"
    cooking_detail = ""
    if cooking:
        rate = int(round(cooking["present_rate"] * 100))
        cooking_detail = f"{rate}% attendance · keeping it steady"

    cards = [
        _featured_card(school),
        _card_from_stat(
            "engaged",
            "Most Engaged",
            "Showed up and jumped in",
            "Waiting on the first attendance.",
            engaged,
            engaged_detail,
        ),
        _card_from_stat(
            "improved",
            "Most Improved",
            "Biggest climb lately",
            "Needs a few more live classes.",
            improved,
            improved_detail,
        ),
        _card_from_stat(
            "cooking",
            "Quietly Cooking",
            "Steady work, no spotlight needed",
            "Waiting on a quiet streak.",
            cooking,
            cooking_detail,
        ),
    ]
    return {"cards": cards}


def celebration_candidates(school: Any, teacher_user_id: int) -> list[dict[str, Any]]:
    """Codenames this teacher can feature on the Awards card.

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
    """Save or clear the teacher-picked Awards student.

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
