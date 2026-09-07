"""Generate a four-slide live-class deck from syllabus + live problem bank.

Login OAuth stays identity-only. Deck creation uses a stored Slides/Drive
token (or a localhost HTML mock when ``LOCAL_DEV_LOGIN`` / tests).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from live_class_constants import (
    MOCK_SLIDES_REFRESH_TOKEN,
    SLIDES_SCOPES,
    is_slides_operator_email,
)
from paths import MCF3M_EXPECTATIONS

SLIDE_TITLES = ("Intro", "Open Question", "Team Breakout", "Consolidation")


def is_mock_presentation(
    presentation_id: str | None, presentation_url: str | None = None
) -> bool:
    """True when the stored deck is the local HTML mock, not a Google file.

    Args:
        presentation_id: Stored id (``mock-…`` for HTML).
        presentation_url: Stored open URL.
    """
    pid = str(presentation_id or "")
    url = str(presentation_url or "")
    return pid.startswith("mock-") or url.endswith(".html")


def should_reuse_stored_deck(
    *,
    presentation_id: str | None,
    presentation_url: str | None = None,
    force_regenerate: bool,
    use_mock: bool,
) -> bool:
    """Whether Create Lesson Slides should skip Drive copy and reopen the file.

    Mock HTML ids are not reused when this process is talking to Google, so a
    leftover localhost preview cannot block the first real copy.

    Args:
        presentation_id: Id on ``live_class_sessions``.
        presentation_url: Open URL on that session.
        force_regenerate: Staff clicked Regenerating slides.
        use_mock: Tests / mock refresh token — keep HTML decks.
    """
    if force_regenerate or not str(presentation_id or "").strip():
        return False
    if use_mock:
        return True
    return not is_mock_presentation(presentation_id, presentation_url)


def mock_slides_enabled() -> bool:
    """True when decks should be local HTML instead of Google Slides.

    Flask tests stay offline. ``LOCAL_DEV_LOGIN`` still creates a real Google
    deck when a non-mock refresh token is stored.
    """
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("TESTING"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _ontario_course_code(course_code: str | None) -> str:
    """Normalize an Ontario course code for cache paths.

    Args:
        course_code: Raw offering code (e.g. ``MCF3M``).
    """
    return "".join(ch for ch in (course_code or "").upper() if ch.isalnum())


def _strand_letter(value: Any) -> str:
    """Return a single A–E letter from a map field, or empty.

    Args:
        value: Strand letter or longer label starting with that letter.
    """
    letter = str(value or "").strip().upper()[:1]
    return letter if letter.isalpha() else ""


def _load_module_strand_map(
    course_code: str,
    cache_root: Path | None = None,
) -> dict[str, Any] | None:
    """Read ``{CODE}/module-strand-map.json`` from the local curriculum cache.

    Args:
        course_code: Normalized Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).
    """
    if not course_code:
        return None
    try:
        from math_expectations_pdf import default_local_dest
    except ImportError:
        from lms.math_expectations_pdf import default_local_dest
    root = cache_root if cache_root is not None else default_local_dest()
    path = Path(root) / course_code / "module-strand-map.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _strand_from_module_map(
    payload: dict[str, Any],
    module_number: int,
) -> str:
    """Resolve ``modules[].module_number`` → ``strand`` (and dict / specifics).

    Args:
        payload: ``module-strand-map.json`` object.
        module_number: 1-based ELC module outline position.
    """
    modules = payload.get("modules")
    if isinstance(modules, list):
        for row in modules:
            if not isinstance(row, dict):
                continue
            num = int(row.get("module_number") or row.get("module") or 0)
            if num != module_number:
                continue
            letter = _strand_letter(row.get("strand"))
            if letter:
                return letter
    elif isinstance(modules, dict):
        for key in (str(module_number), f"M{module_number}", module_number):
            if key not in modules:
                continue
            val = modules[key]
            if isinstance(val, dict):
                letter = _strand_letter(val.get("strand"))
            else:
                letter = _strand_letter(val)
            if letter:
                return letter
    letters: set[str] = set()
    for field in ("specifics", "items"):
        rows = payload.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_mods = row.get("module_numbers") or row.get("modules") or []
            if isinstance(raw_mods, int):
                raw_mods = [raw_mods]
            if not isinstance(raw_mods, list):
                continue
            nums: set[int] = set()
            for n in raw_mods:
                try:
                    nums.add(int(n))
                except (TypeError, ValueError):
                    continue
            if module_number not in nums:
                continue
            letter = _strand_letter(row.get("strand") or str(row.get("code") or ""))
            if letter:
                letters.add(letter)
    if len(letters) == 1:
        return next(iter(letters))
    return ""


def _elc_pack_strand(course_code: str, module_number: int) -> str:
    """Documented numeric fallback when title keywords and the map are silent.

    ELC MCF3M (Functions and Applications) packing — not Ministry strand order:

    * M1–M4 quadratic → ``A``
    * M5–M6 trig / sinusoidal → ``C``
    * M7–M8 exponential / finance → ``B``

    MCR3U uses the same ``module-strand-map.json`` contract. Without a map or
    title keywords, MCR3U (and other codes) default to ``A`` so we do not
    invent a packing.

    Args:
        course_code: Normalized Ontario course code (empty treated as MCF3M).
        module_number: 1-based module outline position.
    """
    code = course_code or "MCF3M"
    if code == "MCF3M":
        if module_number <= 4:
            return "A"
        if module_number <= 6:
            return "C"
        return "B"
    return "A"


def strand_from_module(
    title: str | None,
    module_number: int | None,
    course_code: str | None = None,
    *,
    cache_root: Path | None = None,
) -> str:
    """Guess strand letter from title keywords, then the local module map.

    Order: distinctive title keywords; then
    ``.local-data/curriculum/{CODE}/module-strand-map.json``
    (``modules[].module_number`` → ``strand``) for MCF3M and MCR3U; then the
    documented ELC numeric fallback. Does not invent expectation wording.

    Args:
        title: Module outline title.
        module_number: 1-based module index when known.
        course_code: Ontario course code (``MCF3M``, ``MCR3U``, …).
        cache_root: Optional ``.local-data/curriculum`` override (tests).

    Returns:
        ``A``, ``B``, or ``C`` (default ``A``).
    """
    blob = str(title or "").lower()
    if any(word in blob for word in ("exponential", "finance", "interest", "strand b")):
        return "B"
    if any(
        word in blob
        for word in ("sine", "trig", "periodic", "sinusoidal", "strand c")
    ):
        return "C"
    if any(word in blob for word in ("quadratic", "strand a")):
        return "A"
    code = _ontario_course_code(course_code)
    if module_number is not None:
        mapped = ""
        payload = _load_module_strand_map(code, cache_root=cache_root)
        if payload is not None:
            mapped = _strand_from_module_map(payload, int(module_number))
        if mapped:
            return mapped
        return _elc_pack_strand(code, int(module_number))
    return "A"


def build_timeline(
    *,
    meeting: date,
    placements: dict[str, dict[str, Any]],
    module_titles: dict[int, str],
    course_code: str | None = None,
) -> dict[str, Any]:
    """Compute Live Class Y of N and so-far / by-the-end copy.

    Args:
        meeting: Class date.
        placements: ISO date → ``{module, live, assessment_kind, lesson}``.
        module_titles: Module number → title.
        course_code: Ontario course code for strand map / ELC pack fallback.

    Returns:
        Timeline snapshot used on the intro slide and stored in ``slides_json``.
    """
    iso = meeting.isoformat()
    today = placements.get(iso) or {}
    module_num = int(today.get("module") or today.get("module_number") or 0)
    live_in_module: list[str] = []
    for day_iso, slot in sorted(placements.items()):
        if int(slot.get("module") or slot.get("module_number") or 0) != module_num:
            continue
        if slot.get("live") or str(slot.get("lesson_id") or "") == "__live_class__":
            live_in_module.append(day_iso)
    if iso not in live_in_module:
        live_in_module.append(iso)
        live_in_module.sort()
    y = live_in_module.index(iso) + 1 if iso in live_in_module else 1
    n = max(len(live_in_module), 1)
    prior_titles: list[str] = []
    seen: set[int] = set()
    for day_iso, slot in sorted(placements.items()):
        if day_iso >= iso:
            break
        num = int(slot.get("module") or slot.get("module_number") or 0)
        if num and num != module_num and num not in seen:
            seen.add(num)
            prior_titles.append(module_titles.get(num) or f"Module {num}")
    remaining_live = [d for d in live_in_module if d > iso]
    next_assessment = ""
    for day_iso, slot in sorted(placements.items()):
        if day_iso < iso:
            continue
        kind = slot.get("assessment_kind") or slot.get("assessmentKind")
        if kind:
            title = slot.get("assessment_title") or slot.get("assessmentTitle") or kind
            next_assessment = f"{kind}: {title} ({day_iso})"
            break
    module_title = module_titles.get(module_num) or (
        f"Module {module_num}" if module_num else "this module"
    )
    return {
        "meeting_date": iso,
        "module_number": module_num,
        "module_title": module_title,
        "live_index": y,
        "live_count": n,
        "headline": f"Live Class {y} of {n} in {module_title}",
        "so_far": prior_titles,
        "remaining_live_dates": remaining_live,
        "next_assessment": next_assessment,
        "strand": strand_from_module(
            module_title, module_num or None, course_code=course_code
        ),
    }


def lesson_key_from_timeline(timeline: dict[str, Any]) -> str:
    """Return ``M{module}C{live_index}`` from a timeline snapshot.

    Args:
        timeline: ``build_timeline`` result (needs ``module_number``, ``live_index``).

    Returns:
        Lesson key such as ``M1C1``, or ``""`` when module or live index is missing.
    """
    module_num = int(timeline.get("module_number") or 0)
    live_index = int(timeline.get("live_index") or 0)
    if module_num <= 0 or live_index <= 0:
        return ""
    return f"M{module_num}C{live_index}"


def _module_hint_parts(row: dict[str, Any]) -> list[str]:
    """Split ``module_hint`` on ``/`` for lesson-key and strand matching.

    Args:
        row: Live-problem bank row.
    """
    hint = str(row.get("module_hint") or "").replace(" ", "").upper()
    return [part for part in hint.split("/") if part]


def _hint_has_lesson_key(row: dict[str, Any], lesson_key: str) -> bool:
    """True when ``module_hint`` encodes this lesson (e.g. ``A/M1C1``).

    Args:
        row: Live-problem bank row.
        lesson_key: ``M1C1``-style key.
    """
    key = (lesson_key or "").replace(" ", "").upper()
    if not key:
        return False
    return key in _module_hint_parts(row)


def _hint_has_strand(row: dict[str, Any], strand_key: str) -> bool:
    """True when the hint is this strand or starts with it (legacy ``A`` hints).

    Args:
        row: Live-problem bank row.
        strand_key: ``A`` / ``B`` / ``C``.
    """
    parts = _module_hint_parts(row)
    if not parts:
        return False
    return strand_key in parts or parts[0].startswith(strand_key)


def select_live_problems(
    problems: list[dict[str, Any]],
    *,
    strand: str,
    warmup_fallback: str = "",
    lesson_key: str = "",
) -> dict[str, Any]:
    """Pick warmup, contest, and 3–5 standard items for one live class.

    Prefers rows whose ``module_hint`` contains ``lesson_key`` (e.g. ``A/M1C1``),
    then falls back to strand letter as before.

    Args:
        problems: Active bank rows (with ``processes`` lists).
        strand: ``A`` / ``B`` / ``C`` (matched to ``module_hint``).
        warmup_fallback: Page title used when the bank has no warmup.
        lesson_key: Optional ``M1C1``-style key from the syllabus timeline.

    Returns:
        Selected problem ids plus process keys for contest/consolidation.
    """
    strand_key = (strand or "A").upper()[:1]
    key = (lesson_key or "").replace(" ", "").upper()

    def _kind(kind: str) -> list[dict[str, Any]]:
        return [
            row
            for row in problems
            if str(row.get("kind") or "") == kind and int(row.get("active") or 1)
        ]

    def _prefer(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keyed = [row for row in rows if _hint_has_lesson_key(row, key)] if key else []
        stranded = [row for row in rows if _hint_has_strand(row, strand_key)]
        return keyed or stranded or rows

    warmups = _prefer(_kind("warmup"))
    contests = _prefer(
        [
            row
            for row in _kind("contest")
            if "problem_solving" in (row.get("processes") or [])
        ]
    ) or _prefer(_kind("contest"))
    standards = _prefer(_kind("standard"))
    warmup = warmups[0] if warmups else None
    contest = contests[0] if contests else None
    contest_processes = list(contest.get("processes") or []) if contest else ["problem_solving"]
    tagged = [
        row
        for row in standards
        if set(row.get("processes") or []) & set(contest_processes)
    ] or standards
    selected_standards = tagged[:5]
    if len(selected_standards) < 3:
        extras = [row for row in standards if row not in selected_standards]
        selected_standards.extend(extras[: max(0, 3 - len(selected_standards))])
    return {
        "warmup": warmup,
        "contest": contest,
        "standards": selected_standards[:5],
        "warmup_fallback": warmup_fallback if warmup is None else "",
        "contest_process_keys": contest_processes,
        "consolidation_process_keys": list(
            dict.fromkeys(
                contest_processes
                + [p for row in selected_standards[:3] for p in (row.get("processes") or [])]
            )
        )[:4],
    }


def _problem_ids(selection: dict[str, Any]) -> list[int]:
    """Collect selected problem primary keys.

    Args:
        selection: Output of ``select_live_problems``.
    """
    ids: list[int] = []
    for key in ("warmup", "contest"):
        row = selection.get(key)
        if row and row.get("id"):
            ids.append(int(row["id"]))
    for row in selection.get("standards") or []:
        if row.get("id"):
            ids.append(int(row["id"]))
    return ids


def load_syllabus_placements(
    *,
    data_dir: Path,
    instance_relpath: str | None,
    semester_label: str,
    ontario_code: str,
) -> dict[str, dict[str, Any]]:
    """Load saved editor placements JSON when present.

    Args:
        data_dir: LMS data volume.
        instance_relpath: Offering instance folder.
        semester_label: Active semester label.
        ontario_code: Course code.

    Returns:
        ISO date → placement dict (may be empty).
    """
    from syllabus import offering_output_dir

    out_dir = offering_output_dir(
        semester_label,
        ontario_code,
        data_dir=data_dir,
        instance_relpath=instance_relpath,
    )
    path = out_dir / f"{semester_label.replace(' ', '-')}.placements.json"
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = blob.get("placements") if isinstance(blob, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def load_process_labels() -> dict[str, str]:
    """Return process_key → Ministry statement snippet for slide reminders.

    Returns:
        Short labels keyed by process.
    """
    from live_class_constants import process_key_from_name

    path = MCF3M_EXPECTATIONS
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for proc in (payload.get("mathematical_processes") or {}).get("processes") or []:
        key = process_key_from_name(str(proc.get("name") or ""))
        if key:
            out[key] = str(proc.get("name") or key)
    return out


def _slide_bodies(
    timeline: dict[str, Any],
    selection: dict[str, Any],
    process_labels: dict[str, str],
) -> list[dict[str, str]]:
    """Build title/body text for the four slides.

    Args:
        timeline: ``build_timeline`` result.
        selection: ``select_live_problems`` result.
        process_labels: Display names for process keys.
    """
    so_far = timeline.get("so_far") or []
    so_far_line = (
        "So far: " + "; ".join(so_far) if so_far else "So far: course introduction."
    )
    end_bits = []
    remaining = timeline.get("remaining_live_dates") or []
    if remaining:
        end_bits.append(f"{len(remaining)} live class(es) left in this module")
    if timeline.get("next_assessment"):
        end_bits.append(str(timeline["next_assessment"]))
    by_end = "By the end: " + ("; ".join(end_bits) if end_bits else "wrap this module.")
    live_index = int(timeline.get("live_index") or 0)
    module_title = str(timeline.get("module_title") or "this module")
    if live_index == 1:
        today_line = f"Today: first {module_title} live problem-solving."
    else:
        today_line = f"Today: {timeline.get('headline')}."
    warmup = selection.get("warmup")
    warmup_text = (
        _plain_from_html(warmup.get("stem_html") or warmup.get("title") or "")
        if warmup
        else selection.get("warmup_fallback") or "Warm-up from today's module page."
    )
    contest = selection.get("contest")
    contest_title = contest.get("title") if contest else "Team problem"
    contest_stem = _plain_from_html((contest or {}).get("stem_html") or "")
    contest_task = _plain_from_html((contest or {}).get("task_html") or "")
    contest_tags = ", ".join(
        process_labels.get(k, k)
        for k in (selection.get("contest_process_keys") or [])
    )
    standards = selection.get("standards") or []
    std_lines = []
    for row in standards:
        tags = ", ".join(process_labels.get(k, k) for k in (row.get("processes") or [])[:2])
        stem = _plain_from_html(row.get("stem_html") or "")
        line = f"• {row.get('title') or 'Problem'}"
        if tags:
            line += f" ({tags})"
        if stem:
            line += f"\n  {stem}"
        task = _plain_from_html(row.get("task_html") or "")
        if task:
            line += f"\n  Your task: {task}"
        std_lines.append(line)
    consol_tags = ", ".join(
        process_labels.get(k, k)
        for k in (selection.get("consolidation_process_keys") or [])
    )
    roles = (
        "Roles: recorder · explainer · skeptic · reporter. "
        "Reminders: " + (contest_tags or "problem solving, communicating")
    )
    return [
        {
            "title": "Intro",
            "body": (
                f"{module_title}\n{timeline.get('headline')}\n"
                f"{so_far_line}\n{today_line}\n{by_end}"
            ),
        },
        {
            "title": "Open Question",
            "body": (
                f"{warmup_text}\n\nThink-pair: What do you notice? What do you wonder?"
            ),
        },
        {
            "title": "Team Breakout",
            "body": (
                f"{contest_title}\nProcesses: {contest_tags or 'Problem Solving'}\n\n"
                f"{contest_stem}\nYour task: {contest_task}\n\n{roles}"
            ),
        },
        {
            "title": "Consolidation",
            "body": (
                f"Share one strategy and one representation.\n"
                f"Process focus: {consol_tags or 'Communicating'}\n\n"
                + ("\n".join(std_lines) or "• Use today's standard items")
                + "\n\nWhich approach would you reuse? What still feels unfinished?"
            ),
        },
    ]


def _plain_from_html(raw: str) -> str:
    """Strip a few HTML tags from a problem stem.

    Args:
        raw: HTML or plain text.
    """
    text = raw.replace("<p>", "").replace("</p>", "\n")
    for tag in ("em", "strong", "sup", "sub"):
        text = text.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return " ".join(text.split())


def render_mock_deck_html(
    *,
    title: str,
    slides: list[dict[str, str]],
) -> str:
    """Return a four-slide HTML preview for localhost / tests.

    Args:
        title: Deck title.
        slides: Title/body pairs.
    """
    cards = []
    for index, slide in enumerate(slides, start=1):
        cards.append(
            "<section class=\"slide\">"
            f"<p class=\"kicker\">Slide {index} · {html_escape(slide['title'])}</p>"
            f"<h2>{html_escape(slide['title'])}</h2>"
            f"<pre>{html_escape(slide['body'])}</pre>"
            "</section>"
        )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html_escape(title)}</title>"
        "<style>body{font-family:sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem}"
        ".slide{border:1px solid #ccc;padding:1rem 1.25rem;margin:1rem 0;border-radius:8px}"
        "pre{white-space:pre-wrap}</style></head><body>"
        f"<h1>{html_escape(title)}</h1>"
        + "".join(cards)
        + "</body></html>"
    )


class SlidesConnectRequired(Exception):
    """Raised when an allowlisted operator has not connected Slides yet."""

    def __init__(self, message: str = "Connect Google Slides to create a deck.") -> None:
        super().__init__(message)


class SlidesOperatorDenied(Exception):
    """Raised when the signed-in user is not on the Slides allowlist."""


def generate_live_class_slides(
    school: Any,
    *,
    class_id: int,
    meeting_date: date,
    user: dict[str, Any],
    force_regenerate: bool = False,
    placements: dict[str, dict[str, Any]] | None = None,
    http: Any | None = None,
) -> dict[str, Any]:
    """Create or reuse a live-class deck for one class meeting.

    Mock/tests write four-slide HTML. Real Google copies the 7-layout
    theme template into the ALC Drive tree and fills named layouts.

    Args:
        school: ``SchoolDB``.
        class_id: MGS ``classes.id``.
        meeting_date: Confirmed Class Date.
        user: Signed-in staff/IT row.
        force_regenerate: Archive the old file and make a new deck.
        placements: Optional syllabus placements (tests / CLI override).
        http: Optional ``requests``-like session (tests).

    Returns:
        Dict with ``presentation_id``, ``presentation_url``, ``slides_json``,
        ``timeline``, and ``live_session``.

    Raises:
        SlidesOperatorDenied: Wrong Google account for deck creation.
        SlidesConnectRequired: Allowlisted but no stored token (and not mock).
        KeyError: Class or offering missing.
    """
    email = str(user.get("email") or "")
    if not is_slides_operator_email(email):
        raise SlidesOperatorDenied(
            "Google Slides connect is limited to solutions@ and Shawn's Gmail."
        )
    cls = school.enrich_class(school.game.get_class(class_id))
    offering_id = cls.get("offering_id")
    if not offering_id:
        raise KeyError(f"class {class_id} has no offering")
    offering = school.ensure_offering_instance(school.get_offering(int(offering_id)))
    ontario = str(offering.get("ontario_code") or cls.get("ontario_code") or "MCF3M")
    semester = school.get_active_semester() or {}
    semester_label = str(semester.get("label") or "")
    module_titles = _module_titles(school, offering)
    place = placements
    if place is None:
        place = load_syllabus_placements(
            data_dir=Path(school.data_dir),
            instance_relpath=offering.get("instance_relpath"),
            semester_label=semester_label,
            ontario_code=ontario,
        )
    timeline = build_timeline(
        meeting=meeting_date,
        placements=place,
        module_titles=module_titles,
        course_code=ontario,
    )
    bank = school.list_live_problems(ontario_code=ontario, active_only=True)
    fallback = ""
    if module_titles:
        fallback = next(iter(module_titles.values()), "")
    selection = select_live_problems(
        bank,
        strand=str(timeline.get("strand") or "A"),
        warmup_fallback=fallback,
        lesson_key=lesson_key_from_timeline(timeline),
    )
    process_labels = load_process_labels()
    slides = _slide_bodies(timeline, selection, process_labels)
    title = (
        f"{cls.get('section_code') or ontario} · {meeting_date.isoformat()} · "
        f"{timeline.get('headline')}"
    )
    existing = school.get_live_session_for_class_date(class_id, meeting_date.isoformat())
    live_session = school.get_active_live_session_for_class(class_id) or existing
    token_row = school.get_google_api_token(int(user["id"]))
    use_mock = mock_slides_enabled() or (
        token_row and token_row.get("refresh_token") == MOCK_SLIDES_REFRESH_TOKEN
    )
    if should_reuse_stored_deck(
        presentation_id=existing.get("presentation_id") if existing else None,
        presentation_url=existing.get("presentation_url") if existing else None,
        force_regenerate=force_regenerate,
        use_mock=bool(use_mock),
    ):
        school.set_live_session_slides(
            int((live_session or existing)["id"]),
            meeting_date=meeting_date.isoformat(),
            presentation_id=str(existing["presentation_id"]),
            presentation_url=str(existing.get("presentation_url") or ""),
            slides_json=existing.get("slides_json"),
        )
        row = school.get_live_session(int((live_session or existing)["id"]))
        return {
            "ok": True,
            "reused": True,
            "presentation_id": existing["presentation_id"],
            "presentation_url": existing.get("presentation_url"),
            "live_session": row,
            "timeline": timeline,
            "needs_slides_connect": False,
        }

    old_id = str((existing or {}).get("presentation_id") or "") if force_regenerate else ""
    if not use_mock and existing and is_mock_presentation(
        existing.get("presentation_id"), existing.get("presentation_url")
    ):
        old_id = ""
    if not use_mock and not token_row:
        raise SlidesConnectRequired()

    if use_mock:
        pres_id, pres_url = _write_mock_deck(
            school,
            offering=offering,
            meeting_date=meeting_date,
            title=title,
            slides=slides,
        )
    else:
        client = GoogleSlidesClient(school, user=user, http=http)
        join_code = str((live_session or existing or {}).get("session_code") or "")
        lesson_key = lesson_key_from_timeline(timeline)
        from slides_template import deck_drive_name

        drive_title = deck_drive_name(ontario, lesson_key, meeting_date.isoformat())
        team_names: list[str] = []
        try:
            for team in (school.game.dashboard(class_id) or {}).get("teams") or []:
                label = team.get("name") or team.get("label")
                if label:
                    team_names.append(str(label))
        except Exception:  # noqa: BLE001
            team_names = []
        overwrite_id = old_id if old_id and not old_id.startswith("mock-") else ""
        created = client.copy_template_and_fill(
            offering=offering,
            meeting_date=meeting_date,
            drive_title=drive_title,
            selection=selection,
            join_code=join_code,
            timeline=timeline,
            team_names=team_names,
            overwrite_presentation_id=overwrite_id,
        )
        pres_id = created["presentation_id"]
        pres_url = created["presentation_url"]

    snapshot = {
        "timeline": timeline,
        "problem_ids": _problem_ids(selection),
        "contest_process_keys": selection.get("contest_process_keys") or [],
        "consolidation_process_keys": selection.get("consolidation_process_keys") or [],
        "slide_titles": [s["title"] for s in slides],
        "mock": bool(use_mock),
    }
    if live_session is None:
        live_session = school.start_live_class_session(class_id, int(user["id"]))
    updated = school.set_live_session_slides(
        int(live_session["id"]),
        meeting_date=meeting_date.isoformat(),
        presentation_id=pres_id,
        presentation_url=pres_url,
        slides_json=snapshot,
    )
    phrase_ids = school.preselect_quick_phrases_for_processes(
        list(
            dict.fromkeys(
                (selection.get("contest_process_keys") or [])
                + (selection.get("consolidation_process_keys") or [])
            )
        )
    )
    snapshot["preselected_phrase_ids"] = phrase_ids
    updated = school.set_live_session_slides(
        int(live_session["id"]),
        meeting_date=meeting_date.isoformat(),
        presentation_id=pres_id,
        presentation_url=pres_url,
        slides_json=snapshot,
    )
    return {
        "ok": True,
        "reused": False,
        "presentation_id": pres_id,
        "presentation_url": pres_url,
        "live_session": updated,
        "timeline": timeline,
        "needs_slides_connect": False,
        "preselected_phrase_ids": phrase_ids,
    }


def _module_titles(school: Any, offering: dict[str, Any]) -> dict[int, str]:
    """Map 1-based module index to outline title.

    Args:
        school: ``SchoolDB``.
        offering: Offering row (may include ``library_id``).
    """
    library_id = offering.get("library_id")
    if not library_id:
        lib = school.latest_library_for_code(str(offering.get("ontario_code") or ""))
        library_id = lib["id"] if lib else None
    if not library_id:
        return {}
    rows = school.list_module_outlines(int(library_id))
    return {index: str(row.get("title") or f"Module {index}") for index, row in enumerate(rows, start=1)}


def _write_mock_deck(
    school: Any,
    *,
    offering: dict[str, Any],
    meeting_date: date,
    title: str,
    slides: list[dict[str, str]],
) -> tuple[str, str]:
    """Write a four-slide HTML preview under the offering instance.

    Args:
        school: ``SchoolDB``.
        offering: Offering with ``instance_relpath``.
        meeting_date: Class date.
        title: Deck title.
        slides: Slide bodies.

    Returns:
        ``(presentation_id, file_url)``.
    """
    rel = offering.get("instance_relpath") or f"instances/{offering['id']}"
    dest_dir = Path(school.data_dir) / str(rel) / "slides"
    dest_dir.mkdir(parents=True, exist_ok=True)
    pres_id = f"mock-{offering['id']}-{meeting_date.isoformat()}"
    path = dest_dir / f"{meeting_date.isoformat()}.html"
    path.write_text(render_mock_deck_html(title=title, slides=slides), encoding="utf-8")
    url = f"/staff/offerings/{int(offering['id'])}/slides/{meeting_date.isoformat()}.html"
    return pres_id, url


class GoogleSlidesClient:
    """Thin REST client for presentations + Drive copy into the ALC tree."""

    def __init__(self, school: Any, *, user: dict[str, Any], http: Any | None = None) -> None:
        """Store school DB, operator user, and optional HTTP session.

        Args:
            school: ``SchoolDB`` for token refresh persistence.
            user: Allowlisted operator.
            http: ``requests`` module or mock.
        """
        self.school = school
        self.user = user
        self.http = http or requests

    def _access_token(self) -> str:
        """Return a fresh access token from the stored refresh token.

        Returns:
            Bearer token string.

        Raises:
            SlidesConnectRequired: No refresh token on file.
            RuntimeError: Google token endpoint failed.
        """
        row = self.school.get_google_api_token(int(self.user["id"]))
        if not row or not row.get("refresh_token"):
            raise SlidesConnectRequired()
        expires = str(row.get("access_token_expires_at") or "")
        if row.get("access_token") and expires:
            try:
                if datetime.fromisoformat(expires) > datetime.now():
                    return str(row["access_token"])
            except ValueError:
                pass
        from auth import google_client_id, google_client_secret

        resp = self.http.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": google_client_id(),
                "client_secret": google_client_secret(),
                "refresh_token": row["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        access = payload.get("access_token")
        if not access:
            raise RuntimeError("Google did not return an access token for Slides.")
        self.school.store_google_api_token(
            int(self.user["id"]),
            refresh_token=str(row["refresh_token"]),
            scopes=str(row.get("scopes") or " ".join(SLIDES_SCOPES)),
            access_token=access,
            expires_in=int(payload.get("expires_in") or 3500),
        )
        return str(access)

    def _headers(self) -> dict[str, str]:
        """Authorization header for Slides/Drive REST."""
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _drive_params(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Query params so copies work in My Drive and shared drives.

        Args:
            extra: Additional Drive query fields.
        """
        params = {
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if extra:
            params.update(extra)
        return params

    def _escape_drive_name(self, name: str) -> str:
        """Escape a folder/file name for Drive ``q`` syntax."""
        return str(name).replace("\\", "\\\\").replace("'", "\\'")

    def find_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return a child folder id, creating it under ``parent_id`` if missing.

        Args:
            name: Folder title.
            parent_id: Parent Drive folder id.

        Returns:
            Folder file id.
        """
        query = (
            f"name = '{self._escape_drive_name(name)}' and "
            f"'{parent_id}' in parents and "
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        listed = self.http.get(
            "https://www.googleapis.com/drive/v3/files",
            headers=self._headers(),
            params=self._drive_params(
                {"q": query, "fields": "files(id,name)", "pageSize": "5"}
            ),
            timeout=20,
        )
        listed.raise_for_status()
        files = (listed.json() or {}).get("files") or []
        if files:
            return str(files[0]["id"])
        created = self.http.post(
            "https://www.googleapis.com/drive/v3/files",
            headers=self._headers(),
            params=self._drive_params(),
            json={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            timeout=20,
        )
        created.raise_for_status()
        folder_id = str((created.json() or {}).get("id") or "")
        if not folder_id:
            raise RuntimeError(f"Drive did not return an id for folder {name}")
        return folder_id

    def ensure_module_folder(
        self,
        *,
        ontario_code: str,
        module_number: int,
        semester_label: str = "",
    ) -> str:
        """Create ``ALC / year / semester / course / Module n`` as needed.

        Year and semester come from ``frameworks/semester.json`` via
        ``drive_year_semester``, not ``year_display``.

        Args:
            ontario_code: Course code folder name.
            module_number: 1-based module index.
            semester_label: Optional label override for tests.

        Returns:
            Module folder id.
        """
        from live_class_constants import (
            ALC_DRIVE_FOLDER_ID,
            SETTING_ALC_DRIVE_FOLDER_ID,
        )
        from slides_template import drive_year_semester

        alc = self.school.get_school_setting(
            SETTING_ALC_DRIVE_FOLDER_ID, ALC_DRIVE_FOLDER_ID
        ) or ALC_DRIVE_FOLDER_ID
        year, term = drive_year_semester(semester_label)
        module_name = f"Module {max(int(module_number or 1), 1)}"
        year_id = self.find_or_create_folder(year, alc)
        term_id = self.find_or_create_folder(term, year_id)
        course_id = self.find_or_create_folder(str(ontario_code).upper(), term_id)
        return self.find_or_create_folder(module_name, course_id)

    def archive_presentation(self, presentation_id: str, meeting: date) -> None:
        """Rename an existing Drive file as archived.

        Args:
            presentation_id: Google file / presentation id.
            meeting: Meeting date used in the archive prefix.
        """
        stamp = datetime.now().date().isoformat()
        name = f"[archived {stamp}] {meeting.isoformat()} live class"
        self.http.patch(
            f"https://www.googleapis.com/drive/v3/files/{presentation_id}",
            headers=self._headers(),
            params=self._drive_params(),
            json={"name": name},
            timeout=15,
        )

    def copy_template_and_fill(
        self,
        *,
        offering: dict[str, Any],
        meeting_date: date,
        drive_title: str,
        selection: dict[str, Any],
        join_code: str,
        timeline: dict[str, Any],
        team_names: list[str] | None = None,
        overwrite_presentation_id: str = "",
    ) -> dict[str, str]:
        """Copy Lesson Theme Template #1 into the ALC tree and fill layouts.

        Regenerating with ``overwrite_presentation_id`` re-fills that file
        instead of minting a second deck for the same class date.

        Args:
            offering: Offering row (template id + Ontario code).
            meeting_date: Class date.
            drive_title: Drive file name (``MCF3M M1C1 2026-09-11``).
            selection: Live-problem pick.
            join_code: Session join code for ``TITLE_CLASS``.
            timeline: ``build_timeline`` snapshot (module number).
            team_names: Optional names for ``TEAM_INTRO``.
            overwrite_presentation_id: Existing Google id to fill in place.

        Returns:
            ``presentation_id`` and ``presentation_url``.
        """
        from slides_template import layout_fill_values, resolved_template_id

        ontario = str(offering.get("ontario_code") or "MCF3M")
        lesson_key = lesson_key_from_timeline(timeline)
        values = layout_fill_values(
            ontario_code=ontario,
            lesson_key=lesson_key,
            join_code=join_code,
            selection=selection,
            team_names=team_names,
        )
        pres_id = (overwrite_presentation_id or "").strip()
        if pres_id:
            got = self.http.get(
                f"https://slides.googleapis.com/v1/presentations/{pres_id}",
                headers=self._headers(),
                timeout=20,
            )
            if got.status_code == 404:
                pres_id = ""
            else:
                got.raise_for_status()
                self._apply_fill(pres_id, got.json(), values)
                self.http.patch(
                    f"https://www.googleapis.com/drive/v3/files/{pres_id}",
                    headers=self._headers(),
                    params=self._drive_params(),
                    json={"name": drive_title},
                    timeout=15,
                )
                return {
                    "presentation_id": pres_id,
                    "presentation_url": (
                        f"https://docs.google.com/presentation/d/{pres_id}/edit"
                    ),
                }
        template_id = resolved_template_id(offering, self.school)
        semester = self.school.get_active_semester() or {}
        folder_id = self.ensure_module_folder(
            ontario_code=ontario,
            module_number=int(timeline.get("module_number") or 1),
            semester_label=str(semester.get("label") or ""),
        )
        copied = self.http.post(
            f"https://www.googleapis.com/drive/v3/files/{template_id}/copy",
            headers=self._headers(),
            params=self._drive_params(),
            json={"name": drive_title, "parents": [folder_id]},
            timeout=30,
        )
        copied.raise_for_status()
        pres_id = str((copied.json() or {}).get("id") or "")
        if not pres_id:
            raise RuntimeError("Drive copy did not return a presentation id")
        got = self.http.get(
            f"https://slides.googleapis.com/v1/presentations/{pres_id}",
            headers=self._headers(),
            timeout=20,
        )
        got.raise_for_status()
        self._apply_fill(pres_id, got.json(), values)
        self.http.post(
            f"https://www.googleapis.com/drive/v3/files/{pres_id}/permissions",
            headers=self._headers(),
            params=self._drive_params(),
            json={"role": "reader", "type": "anyone"},
            timeout=15,
        )
        meta = self.http.get(
            f"https://www.googleapis.com/drive/v3/files/{pres_id}",
            headers=self._headers(),
            params=self._drive_params({"fields": "webViewLink,id"}),
            timeout=15,
        )
        url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
        if getattr(meta, "ok", False):
            url = str((meta.json() or {}).get("webViewLink") or url)
        return {"presentation_id": pres_id, "presentation_url": url}

    def _apply_fill(
        self,
        presentation_id: str,
        presentation: dict[str, Any],
        values: dict[str, str],
    ) -> None:
        """POST ``batchUpdate`` fill requests when any were built.

        Args:
            presentation_id: Google presentation id.
            presentation: GET presentations JSON.
            values: Placeholder map from ``layout_fill_values``.
        """
        from slides_template import build_template_fill_requests

        requests_body = build_template_fill_requests(presentation, values)
        if not requests_body:
            return
        batch = self.http.post(
            f"https://slides.googleapis.com/v1/presentations/{presentation_id}:batchUpdate",
            headers=self._headers(),
            json={"requests": requests_body},
            timeout=20,
        )
        batch.raise_for_status()

    def upload_public_png(self, *, folder_id: str, name: str, png_path: Path) -> str:
        """Upload a PNG into the module folder and return a public image URL.

        Args:
            folder_id: Drive folder id.
            name: File title.
            png_path: Local PNG path.

        Returns:
            URL usable with Slides ``createImage``.
        """
        metadata = {
            "name": name,
            "parents": [folder_id],
            "mimeType": "image/png",
        }
        with png_path.open("rb") as handle:
            payload = handle.read()
        boundary = "lesson-slide-png"
        body = (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--".encode("utf-8")
        uploaded = self.http.post(
            "https://www.googleapis.com/upload/drive/v3/files",
            headers={
                **self._headers(),
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            params=self._drive_params({"uploadType": "multipart"}),
            data=body,
            timeout=30,
        )
        uploaded.raise_for_status()
        file_id = str((uploaded.json() or {}).get("id") or "")
        if not file_id:
            raise RuntimeError("Drive did not return an id for a question PNG")
        self.http.post(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
            headers=self._headers(),
            params=self._drive_params(),
            json={"role": "reader", "type": "anyone"},
            timeout=15,
        )
        return f"https://drive.google.com/uc?id={file_id}&export=download"

    def copy_template_and_fill_by_index(
        self,
        *,
        offering: dict[str, Any],
        drive_title: str,
        module_number: int,
        values_by_index: dict[int, dict[str, str]],
        png_paths: list[Path] | None = None,
        overwrite_presentation_id: str = "",
    ) -> dict[str, Any]:
        """Copy Lesson Theme Template #1 and fill by slide order.

        Args:
            offering: Offering row (template + Ontario code).
            drive_title: Drive filename such as ``MCF3M M1C1``.
            module_number: Folder ``Module n``.
            values_by_index: Index fill map from ``lesson_fill_values``.
            png_paths: Optional consolidation PNGs for slide 7.
            overwrite_presentation_id: Existing Google id to refill.

        Returns:
            ``presentation_id``, ``presentation_url``, ``image_mode``,
            ``request_count``.
        """
        from slide_builder import build_index_fill_requests
        from slides_template import resolved_template_id

        ontario = str(offering.get("ontario_code") or "MCF3M")
        semester = self.school.get_active_semester() or {}
        folder_id = self.ensure_module_folder(
            ontario_code=ontario,
            module_number=int(module_number or 1),
            semester_label=str(semester.get("label") or ""),
        )
        pres_id = (overwrite_presentation_id or "").strip()
        if pres_id:
            got = self.http.get(
                f"https://slides.googleapis.com/v1/presentations/{pres_id}",
                headers=self._headers(),
                timeout=20,
            )
            if got.status_code == 404:
                pres_id = ""
            else:
                got.raise_for_status()
        if not pres_id:
            template_id = resolved_template_id(offering, self.school)
            copied = self.http.post(
                f"https://www.googleapis.com/drive/v3/files/{template_id}/copy",
                headers=self._headers(),
                params=self._drive_params(),
                json={"name": drive_title, "parents": [folder_id]},
                timeout=30,
            )
            copied.raise_for_status()
            pres_id = str((copied.json() or {}).get("id") or "")
            if not pres_id:
                raise RuntimeError("Drive copy did not return a presentation id")
            got = self.http.get(
                f"https://slides.googleapis.com/v1/presentations/{pres_id}",
                headers=self._headers(),
                timeout=20,
            )
            got.raise_for_status()
            self.http.post(
                f"https://www.googleapis.com/drive/v3/files/{pres_id}/permissions",
                headers=self._headers(),
                params=self._drive_params(),
                json={"role": "reader", "type": "anyone"},
                timeout=15,
            )
        else:
            self.http.patch(
                f"https://www.googleapis.com/drive/v3/files/{pres_id}",
                headers=self._headers(),
                params=self._drive_params(),
                json={"name": drive_title},
                timeout=15,
            )
        presentation = got.json()
        requests_body = build_index_fill_requests(presentation, values_by_index)
        image_mode = "text"
        slides = list(presentation.get("slides") or [])
        if png_paths and len(slides) >= 7:
            consol = slides[6]
            page_id = str(consol.get("objectId") or "")
            try:
                urls: list[str] = []
                for index, png in enumerate(png_paths, start=1):
                    urls.append(
                        self.upload_public_png(
                            folder_id=folder_id,
                            name=f"{drive_title} Q{index}.png",
                            png_path=png,
                        )
                    )
                for index, url in enumerate(urls):
                    requests_body.append(
                        {
                            "createImage": {
                                "url": url,
                                "elementProperties": {
                                    "pageObjectId": page_id,
                                    "size": {
                                        "width": {"magnitude": 2500000, "unit": "EMU"},
                                        "height": {"magnitude": 1400000, "unit": "EMU"},
                                    },
                                    "transform": {
                                        "scaleX": 1,
                                        "scaleY": 1,
                                        "translateX": 400000,
                                        "translateY": 400000 + index * 1500000,
                                        "unit": "EMU",
                                    },
                                },
                            }
                        }
                    )
                image_mode = "png"
            except Exception:  # noqa: BLE001 — keep text fill if Drive image fails
                image_mode = "text"
        if requests_body:
            batch = self.http.post(
                f"https://slides.googleapis.com/v1/presentations/{pres_id}:batchUpdate",
                headers=self._headers(),
                json={"requests": requests_body},
                timeout=30,
            )
            batch.raise_for_status()
        url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
        meta = self.http.get(
            f"https://www.googleapis.com/drive/v3/files/{pres_id}",
            headers=self._headers(),
            params=self._drive_params({"fields": "webViewLink,id"}),
            timeout=15,
        )
        if getattr(meta, "ok", False):
            url = str((meta.json() or {}).get("webViewLink") or url)
        return {
            "presentation_id": pres_id,
            "presentation_url": url,
            "image_mode": image_mode,
            "request_count": len(requests_body),
        }


def slides_oauth_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """Build the incremental Google consent URL for Slides + Drive.

    Args:
        client_id: Web OAuth client id.
        redirect_uri: ``/auth/google/slides/callback``.
        state: CSRF token.

    Returns:
        Full accounts.google.com URL.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SLIDES_SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
