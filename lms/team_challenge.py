"""Resolve the Run Live Class Action / team-challenge stem.

MCF3M M1C1 keeps the C1 Real-slice parabola. Every other course and live
slot uses the Lesson Slides pick, then the live-problem contest, then the
preview default for that Ontario code.
"""

from __future__ import annotations

from typing import Any

try:
    from live_class_constants import (
        M1C1_TEAM_CHALLENGE_QUESTION,
        MCR3U_M1C1_TEAM_CHALLENGE_QUESTION,
    )
    from live_class_slides import _plain_from_html, select_live_problems, strand_from_module
    from live_media import DEFAULT_LIVE_MEDIA_STEM, DEFAULT_LIVE_MEDIA_URL
    from live_teacher_state import normalize_live_module, normalize_live_slot
    from slide_builder import default_team_challenge
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.live_class_constants import (
        M1C1_TEAM_CHALLENGE_QUESTION,
        MCR3U_M1C1_TEAM_CHALLENGE_QUESTION,
    )
    from lms.live_class_slides import (
        _plain_from_html,
        select_live_problems,
        strand_from_module,
    )
    from lms.live_media import DEFAULT_LIVE_MEDIA_STEM, DEFAULT_LIVE_MEDIA_URL
    from lms.live_teacher_state import normalize_live_module, normalize_live_slot
    from lms.slide_builder import default_team_challenge

TEAM_CHALLENGE_ITEM_ID = "team-challenge"
TEAM_CHALLENGE_PACK = "team-challenge"
TEAM_CHALLENGE_RIDE = "action"
TEAM_CHALLENGE_KIND = "share"
TEAM_CHALLENGE_SLIDE_INDEX = 400
MCR3U_M1C1_MEDIA_URL = "/static/live-media/mcr3u-m1c1-sqrt.html"


def uses_c1_real_slice(
    ontario_code: str,
    live_module: str = "M1",
    live_slot: str = "C1",
) -> bool:
    """True when Play/Action should keep the MCF3M M1C1 parabola media.

    Args:
        ontario_code: Course code.
        live_module: ``M1`` / ``M2`` / …
        live_slot: ``C1`` / ``C2`` / ``C3``.
    """
    code = str(ontario_code or "").strip().upper()
    module = normalize_live_module(live_module)
    slot = normalize_live_slot(live_slot)
    return code == "MCF3M" and module == "M1" and slot == "C1"


def team_challenge_media_url(
    ontario_code: str,
    live_module: str = "M1",
    live_slot: str = "C1",
) -> str:
    """Return associated Play/Action media for one course + live class.

    Args:
        ontario_code: Course code.
        live_module: ``M1`` / ``M2`` / …
        live_slot: ``C1`` / ``C2`` / ``C3``.
    """
    code = str(ontario_code or "").strip().upper()
    module = normalize_live_module(live_module)
    slot = normalize_live_slot(live_slot)
    if uses_c1_real_slice(code, module, slot):
        return DEFAULT_LIVE_MEDIA_URL
    if code == "MCR3U" and module == "M1" and slot == "C1":
        return MCR3U_M1C1_MEDIA_URL
    return ""


def live_class_seed_media(
    ontario_code: str,
    live_module: str = "M1",
    live_slot: str = "C1",
) -> dict[str, str] | None:
    """Return Join/Play seed media for one course + live class, if any.

    Args:
        ontario_code: Course code.
        live_module: ``M1`` / ``M2`` / …
        live_slot: ``C1`` / ``C2`` / ``C3``.
    """
    url = team_challenge_media_url(ontario_code, live_module, live_slot)
    if not url:
        return None
    if uses_c1_real_slice(ontario_code, live_module, live_slot):
        return {
            "url": url,
            "title": DEFAULT_LIVE_MEDIA_STEM,
            "stem": DEFAULT_LIVE_MEDIA_STEM,
        }
    if url == MCR3U_M1C1_MEDIA_URL:
        return {
            "url": url,
            "title": "Nested Square-Root Range",
            "stem": MCR3U_M1C1_TEAM_CHALLENGE_QUESTION,
        }
    return {"url": url, "title": "", "stem": ""}


def lesson_key_for(live_module: str, live_slot: str) -> str:
    """Return ``M1C1`` from module + slot tokens.

    Args:
        live_module: ``M1`` / ``M2`` / …
        live_slot: ``C1`` / ``C2`` / ``C3``.
    """
    return f"{normalize_live_module(live_module)}{normalize_live_slot(live_slot)}"


def live_index_for(live_slot: str) -> int:
    """1-based live-class index from ``C1`` / ``C2`` / ``C3``.

    Args:
        live_slot: Live slot token.
    """
    slot = normalize_live_slot(live_slot)
    try:
        return int(slot[1:])
    except (TypeError, ValueError):
        return 1


def module_number_for(live_module: str) -> int:
    """1-based module number from ``M1`` / ``M2`` / …

    Args:
        live_module: Catalogue module token.
    """
    module = normalize_live_module(live_module)
    try:
        return int(module[1:])
    except (TypeError, ValueError):
        return 1


def is_team_challenge_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the Action team-challenge stem.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    ride = str(payload.get("ride") or "").strip().lower()
    return (
        item_id == TEAM_CHALLENGE_ITEM_ID
        or pack == TEAM_CHALLENGE_PACK
        or ride == TEAM_CHALLENGE_RIDE
    )


def compose_team_challenge_prompt(*, context: str = "", question: str = "") -> str:
    """Join context and question for the student Question frame.

    Args:
        context: Optional setup sentence(s).
        question: The ask.
    """
    ctx = str(context or "").strip()
    ask = str(question or "").strip()
    if ctx and ask and ctx != ask:
        return f"{ctx}\n\n{ask}"
    return ask or ctx


def staff_team_challenge_prompt_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Build the live-prompt payload for Play / Action.

    Args:
        row: ``resolve_team_challenge`` result.
    """
    prompt = compose_team_challenge_prompt(
        context=str(row.get("context") or ""),
        question=str(row.get("question") or ""),
    )
    return {
        "item_id": TEAM_CHALLENGE_ITEM_ID,
        "pack": TEAM_CHALLENGE_PACK,
        "ride": TEAM_CHALLENGE_RIDE,
        "title": str(row.get("title") or "Team Challenge"),
        "label": str(row.get("title") or "Team Challenge"),
        "prompt": prompt,
        "ontario_code": str(row.get("ontario_code") or ""),
        "lesson_key": str(row.get("lesson_key") or ""),
        "use_real_slice": bool(row.get("use_real_slice")),
        "media_url": str(row.get("media_url") or ""),
    }


def _stored_lesson_slides_challenge(
    school: Any,
    *,
    class_id: int,
    module_number: int,
    live_index: int,
) -> dict[str, str] | None:
    """Return a teacher-saved Lesson Slides team-challenge, if any.

    Args:
        school: ``SchoolDB``.
        class_id: Game-show class id.
        module_number: 1-based module.
        live_index: 1-based live class.
    """
    deck = school.get_lesson_slide_deck(class_id, module_number, live_index)
    if not deck:
        return None
    fill = deck.get("fill_json")
    blob = fill.get("team_challenge") if isinstance(fill, dict) else None
    if not isinstance(blob, dict):
        preview = deck.get("preview_json")
        blob = preview.get("team_challenge") if isinstance(preview, dict) else None
    if not isinstance(blob, dict):
        return None
    question = str(blob.get("question") or "").strip()
    if not question:
        return None
    return {
        "title": str(blob.get("title") or "Team Challenge"),
        "context": str(blob.get("context") or "").strip(),
        "question": question,
    }


def _contest_challenge(
    school: Any,
    *,
    ontario_code: str,
    module_number: int,
    lesson_key: str,
) -> dict[str, str] | None:
    """Return the bank contest selected for this course + live class.

    Args:
        school: ``SchoolDB``.
        ontario_code: Course code.
        module_number: 1-based module.
        lesson_key: ``M1C1``.
    """
    bank = school.list_live_problems(ontario_code=ontario_code, active_only=True)
    strand = strand_from_module("", module_number, ontario_code)
    picked = select_live_problems(bank, strand=strand or "A", lesson_key=lesson_key)
    contest = picked.get("contest")
    if not isinstance(contest, dict):
        return None
    stem = _plain_from_html(str(contest.get("stem_html") or ""))
    task = _plain_from_html(str(contest.get("task_html") or ""))
    title = str(contest.get("title") or "Team Challenge").strip()
    if not (stem or task or title):
        return None
    return {
        "title": title,
        "context": stem,
        "question": task or stem or title,
    }


def resolve_team_challenge(
    school: Any,
    *,
    class_id: int,
    ontario_code: str,
    live_module: str = "M1",
    live_slot: str = "C1",
) -> dict[str, Any]:
    """Return the Action-round team-challenge for one live class.

    Args:
        school: ``SchoolDB``.
        class_id: Game-show class id.
        ontario_code: Course code.
        live_module: ``M1`` / ``M2`` / …
        live_slot: ``C1`` / ``C2`` / ``C3``.
    """
    code = str(ontario_code or "MCF3M").strip().upper()
    module = normalize_live_module(live_module)
    slot = normalize_live_slot(live_slot)
    lesson_key = lesson_key_for(module, slot)
    module_n = module_number_for(module)
    live_i = live_index_for(slot)
    base = {
        "ontario_code": code,
        "live_module": module,
        "live_slot": slot,
        "lesson_key": lesson_key,
        "title": "Team Challenge",
        "context": "",
        "question": "",
        "use_real_slice": False,
        "media_url": "",
        "source": "empty",
    }
    if uses_c1_real_slice(code, module, slot):
        base.update(
            {
                "title": "Team Challenge",
                "context": "",
                "question": DEFAULT_LIVE_MEDIA_STEM,
                "use_real_slice": True,
                "media_url": DEFAULT_LIVE_MEDIA_URL,
                "source": "c1_real_slice",
            }
        )
        return base
    stored = _stored_lesson_slides_challenge(
        school,
        class_id=int(class_id),
        module_number=module_n,
        live_index=live_i,
    )
    if stored:
        base.update(stored)
        base["source"] = "lesson_slides"
        return _with_slot_media(base)
    contest = _contest_challenge(
        school,
        ontario_code=code,
        module_number=module_n,
        lesson_key=lesson_key,
    )
    if contest:
        base.update(contest)
        base["source"] = "live_problem"
        return _with_slot_media(base)
    defaults = default_team_challenge(module_n, live_i, code)
    question = str(defaults.get("question") or "").strip()
    context = str(defaults.get("context") or "").strip()
    if question or context:
        base["context"] = context
        base["question"] = question or context
        base["source"] = "default"
        if code == "MCR3U" and question == MCR3U_M1C1_TEAM_CHALLENGE_QUESTION:
            base["title"] = "Nested Square-Root Range"
        elif question == M1C1_TEAM_CHALLENGE_QUESTION:
            base["title"] = "Jigsaw-able class"
    return _with_slot_media(base)


def _with_slot_media(row: dict[str, Any]) -> dict[str, Any]:
    """Attach catalogue media for this course + live class.

    Args:
        row: Resolver payload.
    """
    url = team_challenge_media_url(
        str(row.get("ontario_code") or ""),
        str(row.get("live_module") or "M1"),
        str(row.get("live_slot") or "C1"),
    )
    if url:
        row["media_url"] = url
        row["use_real_slice"] = uses_c1_real_slice(
            str(row.get("ontario_code") or ""),
            str(row.get("live_module") or "M1"),
            str(row.get("live_slot") or "C1"),
        )
    return row
