"""Lesson Slides staff table: one row per live class (MCK-229)."""

from __future__ import annotations

import json
import re
from typing import Any

from bank_question_types import TYPE_LABELS, bank_question_type
from participation_weight import count_participation_opportunities, item_participation_weight

_GROUP_PUBLISH = frozenset({"group_consensus", "group_shared", "group"})


def _publish_mode_label(publish_mode: str) -> str:
    """Map live publish_mode to individual vs group for filters."""
    pub = str(publish_mode or "individual").strip().lower()
    return "group" if pub in _GROUP_PUBLISH else "individual"


_SLOT_RE = re.compile(r"^C(\d+)$", re.I)
_MODULE_RE = re.compile(r"^M(\d+)$", re.I)

_TYPE_LABELS = {
    **TYPE_LABELS,
    "board": "Board",
    "team_rank": "Team rank",
    "rank": "Rank",
    "whiteboard": "Whiteboard",
}


def _type_label(item: dict[str, Any]) -> str:
    """Human type label for one live question."""
    try:
        token = bank_question_type(
            item,
            item_type=str(item.get("item_type") or ""),
            payload=item,
        )
    except Exception:  # noqa: BLE001
        token = str(item.get("type") or "open").strip().lower()
    return _TYPE_LABELS.get(token, token.replace("_", " ").title() or "Question")


def _stem(item: dict[str, Any]) -> str:
    """First line of stem text for display."""
    for key in ("text", "prompt", "title", "stem"):
        raw = str(item.get(key) or "").strip()
        if raw:
            return raw
    return str(item.get("id") or item.get("placement_key") or "").strip()


def _bank_question_id(item: dict[str, Any]) -> int | None:
    """Resolve a bank question id from a playlist item when possible."""
    for key in ("question_id", "bank_question_id"):
        try:
            val = int(item.get(key) or 0)
        except (TypeError, ValueError):
            val = 0
        if val > 0:
            return val
    ref = str(item.get("ref") or item.get("item_ref") or item.get("id") or "")
    match = re.search(r"(?:bank|question)[/:](\d+)", ref, re.I)
    if match:
        return int(match.group(1))
    return None


def _summary_counts(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate mode/type counts and total weight for the summary strip."""
    by_mode = {"individual": 0, "group": 0}
    by_type: dict[str, int] = {}
    for row in questions:
        mode = str(row.get("mode") or "individual").lower()
        if mode == "group":
            by_mode["group"] += 1
        else:
            by_mode["individual"] += 1
        label = str(row.get("type_label") or row.get("type") or "Question")
        by_type[label] = by_type.get(label, 0) + 1
    return {
        "by_mode": by_mode,
        "by_type": by_type,
        "total_weight": sum(int(q.get("weight") or 0) for q in questions),
        "total_questions": len(questions),
    }


def _question_summary_text(summary: dict[str, Any]) -> str:
    """One-line question summary for the table row."""
    parts = [
        f"{summary['by_mode']['individual']} individual",
        f"{summary['by_mode']['group']} group",
    ]
    mc = summary["by_type"].get("Multiple choice") or summary["by_type"].get("mc") or 0
    if mc:
        parts.append(f"{mc} MC")
    parts.append(f"weight {summary['total_weight']}")
    return " · ".join(parts)


from lesson_slides_run_policy import display_ended_at_from_latest, effective_run_is_real
from participation_real_students import exclude_participation_answer_student_ids
from set_class import (
    format_slide_day,
    is_open_office_slot,
    next_class_after_last_run,
    open_office_label,
)


def catchup_run_visible_for_staff(
    school: Any, class_id: int, catchup: dict[str, Any] | None
) -> bool:
    """Staff Lesson Slides: only surface catch-up runs that are real and not discarded."""
    if not catchup:
        return False
    if bool(catchup.get("discarded")):
        return False
    run_key = str(catchup.get("run_key") or "").strip()
    if not run_key:
        return False
    return effective_run_is_real(school, int(class_id), run_key)



def build_lesson_slides_table(school: Any, class_id: int) -> dict[str, Any]:
    """Build the Lesson Slides table payload for one class.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.

    Returns:
        ``{ok, rows, course}`` where each row is one live class slot.
    """
    try:
        from live_class_metadata import list_live_lesson_summaries, parse_live_lesson_code
    except ImportError:
        from lms.live_class_metadata import list_live_lesson_summaries, parse_live_lesson_code

    cid = int(class_id)
    cls = school.enrich_class(school.game.get_class(cid))
    offering = school.get_offering(int(cls["offering_id"])) if cls.get("offering_id") else None
    course = str((offering or {}).get("ontario_code") or cls.get("course_code") or "MCF3M").upper()

    def merged_lesson(module: str, slot: str) -> dict | None:
        try:
            return school.live_class_metadata_for_class_lesson(cid, module, slot, fresh=True)
        except KeyError:
            return None

    lessons = list_live_lesson_summaries(course, metadata_for=merged_lesson)
    decks = {
        (int(d["module_number"]), int(d["live_index"])): d
        for d in school.list_lesson_slide_decks(cid)
    }
    catchup_by_slot = {
        (str(l.get("live_module") or "").upper(), str(l.get("live_slot") or "").upper()): l
        for l in school.list_catchup_links(cid)
    }
    latest_rows = school.list_lesson_slides_latest(cid)
    real_latest = [row for row in latest_rows if not int(row.get("is_test") or 0)]
    test_latest = [row for row in latest_rows if int(row.get("is_test") or 0)]
    latest_by_slot = {
        (str(r["live_module"]).upper(), str(r["live_slot"]).upper()): r
        for r in real_latest
    }
    test_ids = exclude_participation_answer_student_ids(cid, school)

    rows: list[dict[str, Any]] = []
    for lesson in lessons:
        module = str(lesson.get("module") or "").upper()
        slot = str(lesson.get("live_class") or "").upper()
        mod_match = _MODULE_RE.match(module)
        slot_match = _SLOT_RE.match(slot)
        deck = decks.get((int(mod_match.group(1)), int(slot_match.group(1)))) if (
            mod_match and slot_match
        ) else None
        latest = latest_by_slot.get((module, slot))
        catchup = catchup_by_slot.get((module, slot))
        questions = school.lesson_slides_question_rows(
            cid,
            module,
            slot,
            latest=latest,
            excluded_student_ids=test_ids,
        )
        summary = _summary_counts(questions)
        run_key = ""
        if latest and str(latest.get("run_key") or "").strip():
            run_key = str(latest["run_key"])
        elif catchup_run_visible_for_staff(school, cid, catchup):
            run_key = str(catchup["run_key"])
        has_run = bool(run_key)
        catchup_url = ""
        if catchup and has_run:
            catchup_url = school.catchup_public_url(cid, catchup)
        deck_url = ""
        if latest and str(latest.get("presentation_url") or "").strip():
            deck_url = str(latest["presentation_url"])
        elif deck and str(deck.get("presentation_url") or "").strip():
            deck_url = str(deck["presentation_url"])
        portal_code = ""
        if catchup and has_run:
            portal_code = str(
                catchup.get("portal_join_code") or catchup.get("token") or ""
            ).strip()
        session_code = (
            str(latest.get("session_code") or "").strip() if latest else ""
        )
        join_code = portal_code or session_code
        last_run_at = display_ended_at_from_latest(latest) if latest else ""
        if not last_run_at and has_run and run_key:
            last_run_at = school.ended_at_for_participation_run(cid, run_key)
        rows.append(
            {
                "module": module,
                "slot": slot,
                "label": f"{module}{slot}",
                "line_label": f"{module} {slot}",
                "office_code": "",
                "not_in_template": False,
                "last_run_at": last_run_at,
                "join_code": join_code if has_run else "",
                "has_run": has_run,
                "question_summary": _question_summary_text(summary) if questions else "—",
                "summary": summary,
                "questions": questions,
                "deck_url": deck_url,
                "catchup_url": catchup_url,
                "catchup_enabled": has_run and bool(catchup_url),
                "run_key": run_key,
                "is_test": False,
                "live_session_id": None,
            }
        )
    seen = {(str(row["module"]), str(row["slot"])) for row in rows}
    for (module, slot), latest in latest_by_slot.items():
        if not is_open_office_slot(slot) or (module, slot) in seen:
            continue
        questions = school.lesson_slides_question_rows(
            cid,
            module,
            slot,
            latest=latest,
            excluded_student_ids=test_ids,
        )
        summary = _summary_counts(questions)
        run_key = str(latest.get("run_key") or "").strip()
        has_run = bool(run_key)
        last_run_at = display_ended_at_from_latest(latest)
        deck_url = str(latest.get("presentation_url") or "").strip()
        rows.append(
            {
                "module": module,
                "slot": slot,
                "label": f"{module}{slot}",
                "line_label": f"{open_office_label(module, slot)} · Open office",
                "office_code": open_office_label(module, slot),
                "not_in_template": True,
                "last_run_at": last_run_at,
                "join_code": str(latest.get("session_code") or "").strip() if has_run else "",
                "has_run": has_run,
                "question_summary": _question_summary_text(summary) if questions else "—",
                "summary": summary,
                "questions": questions,
                "deck_url": deck_url,
                "catchup_url": "",
                "catchup_enabled": False,
                "run_key": run_key,
                "is_test": False,
                "live_session_id": None,
            }
        )
    try:
        from live_class_test_labels import (
            format_test_lesson_slides_label,
            test_same_day_indices as same_day_test_indices,
        )
    except ImportError:
        from lms.live_class_test_labels import (
            format_test_lesson_slides_label,
            test_same_day_indices as same_day_test_indices,
        )
    test_index_rows = [
        {
            "live_session_id": latest.get("live_session_id"),
            "ended_at": latest.get("ended_at"),
        }
        for latest in test_latest
    ]
    test_day_index = same_day_test_indices(test_index_rows)
    for latest in test_latest:
        module = str(latest.get("live_module") or "").upper()
        slot = str(latest.get("live_slot") or "").upper()
        run_key = str(latest.get("run_key") or "").strip()
        link = _test_catchup_for_run(school, cid, run_key, latest.get("live_session_id"))
        join_code = ""
        catchup_url = ""
        if link:
            join_code = str(
                link.get("portal_join_code") or link.get("token") or ""
            ).strip()
            catchup_url = school.catchup_public_url(cid, link)
        if not join_code:
            join_code = str(latest.get("session_code") or "").strip()
        questions = school.lesson_slides_question_rows(
            cid,
            module,
            slot,
            latest=latest,
            excluded_student_ids=test_ids,
        )
        summary = _summary_counts(questions)
        live_sid = latest.get("live_session_id")
        day_index = int(test_day_index.get(int(live_sid or 0), 1))
        ended_for_label = str(latest.get("ended_at") or "")
        test_line = format_test_lesson_slides_label(
            ended_for_label, same_day_index=day_index
        )
        rows.append(
            {
                "module": module,
                "slot": slot,
                "label": test_line,
                "line_label": test_line,
                "office_code": "",
                "not_in_template": True,
                "last_run_at": display_ended_at_from_latest(latest),
                "join_code": join_code,
                "has_run": True,
                "question_summary": _question_summary_text(summary) if questions else "—",
                "summary": summary,
                "questions": questions,
                "deck_url": str(latest.get("presentation_url") or "").strip(),
                "catchup_url": catchup_url,
                "catchup_enabled": bool(catchup_url),
                "run_key": run_key,
                "is_test": True,
                "live_session_id": latest.get("live_session_id"),
                "status": "test",
                "status_label": "TEST",
            }
        )
    rows.sort(key=_lesson_row_sort_key)
    _stamp_lesson_slide_status(rows)
    return {"ok": True, "course": course, "rows": rows}


def _test_catchup_for_run(
    school: Any, class_id: int, run_key: str, live_session_id: Any
) -> dict[str, Any] | None:
    """Return the test catch-up row for one run, without logging its code.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        run_key: Participation run key.
        live_session_id: ``live_class_sessions.id`` when the row stored one.
    """
    key = str(run_key or "").strip()
    sid = int(live_session_id) if live_session_id not in (None, "") else 0
    with school._lock:
        if sid:
            row = school.conn.execute(
                """
                SELECT * FROM catchup_links
                WHERE live_session_id = ? AND COALESCE(is_test, 0) = 1
                LIMIT 1
                """,
                (sid,),
            ).fetchone()
            if row is not None:
                return dict(row)
        if not key:
            return None
        row = school.conn.execute(
            """
            SELECT * FROM catchup_links
            WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 1
            LIMIT 1
            """,
            (int(class_id), key),
        ).fetchone()
    return dict(row) if row is not None else None


def _lesson_row_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    """Order template classes before open office inside each module.

    Args:
        row: One Lesson Slides table row.
    """

    module = str(row.get("module") or "")
    slot = str(row.get("slot") or "").upper()
    module_number = int(_MODULE_RE.match(module).group(1)) if _MODULE_RE.match(module) else 0
    if slot.startswith("C") and slot[1:].isdigit():
        return (module_number, 0, int(slot[1:]))
    if slot.startswith("O") and slot[1:].isdigit():
        return (module_number, 1, int(slot[1:]))
    return (module_number, 2, 0)


def _stamp_lesson_slide_status(rows: list[dict[str, Any]]) -> None:
    """Mark Delivered, Next, and Not in template on Lesson Slides rows.

    Args:
        rows: Table rows for one class. Updated in place.
    """

    catalogue = [
        (str(row["module"]), str(row["slot"]))
        for row in rows
        if not row.get("not_in_template")
    ]
    last_pair: tuple[str, str] | None = None
    last_at = ""
    for row in rows:
        if row.get("is_test") or row.get("not_in_template") or not row.get("has_run"):
            continue
        ended = str(row.get("last_run_at") or "")
        if ended and ended >= last_at:
            last_at = ended
            last_pair = (str(row["module"]), str(row["slot"]))
    upcoming = next_class_after_last_run(catalogue, last_pair if last_at else None)
    for row in rows:
        row["day_label"] = format_slide_day(row.get("last_run_at") or "")
        if row.get("is_test"):
            row["status"] = "test"
            row["status_label"] = "TEST"
            continue
        if row.get("not_in_template"):
            row["status"] = "not_in_template"
            row["status_label"] = "Not in template"
            continue
        if row.get("has_run"):
            row["status"] = "delivered"
            row["status_label"] = "Delivered"
            continue
        if upcoming and (str(row["module"]), str(row["slot"])) == upcoming:
            row["status"] = "next"
            row["status_label"] = "Next"
            continue
        row["status"] = ""
        row["status_label"] = ""


def filter_questions(
    questions: list[dict[str, Any]],
    filter_mode: str,
) -> list[dict[str, Any]]:
    """Apply All / individual / group / weighted filter to question rows."""
    token = str(filter_mode or "all").strip().lower()
    if token in {"", "all"}:
        return list(questions)
    if token == "individual":
        return [q for q in questions if str(q.get("mode") or "").lower() == "individual"]
    if token == "group":
        return [q for q in questions if str(q.get("mode") or "").lower() == "group"]
    if token in {"weighted", "weighted_only", "weight"}:
        return [q for q in questions if int(q.get("weight") or 0) > 0]
    return list(questions)
