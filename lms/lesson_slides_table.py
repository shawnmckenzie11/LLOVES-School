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
    latest_by_slot = {
        (str(r["live_module"]).upper(), str(r["live_slot"]).upper()): r for r in latest_rows
    }
    test_ids = set(school.test_student_ids(cid))

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
            test_student_ids=test_ids,
        )
        summary = _summary_counts(questions)
        run_key = ""
        if latest and str(latest.get("run_key") or "").strip():
            run_key = str(latest["run_key"])
        elif (
            catchup
            and str(catchup.get("run_key") or "").strip()
            and bool(catchup.get("is_real", True))
            and not bool(catchup.get("discarded"))
        ):
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
        rows.append(
            {
                "module": module,
                "slot": slot,
                "label": f"{module}{slot}",
                "last_run_at": str(latest.get("ended_at") or "") if latest else "",
                "join_code": (
                    str(catchup.get("portal_join_code") or catchup.get("token") or "").strip()
                    if catchup and has_run
                    else ""
                ),
                "has_run": has_run,
                "question_summary": _question_summary_text(summary) if questions else "—",
                "summary": summary,
                "questions": questions,
                "deck_url": deck_url,
                "catchup_url": catchup_url,
                "catchup_enabled": has_run and bool(catchup_url),
                "run_key": run_key,
            }
        )
    rows.sort(key=lambda r: (int(_MODULE_RE.match(r["module"]).group(1)) if _MODULE_RE.match(r["module"]) else 0,
                            int(_SLOT_RE.match(r["slot"]).group(1)) if _SLOT_RE.match(r["slot"]) else 0))
    return {"ok": True, "course": course, "rows": rows}


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
