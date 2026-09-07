"""Google file-id parsing and named-layout fill requests for live-class decks.

Runtime copy/fill still goes through ``GoogleSlidesClient`` REST calls.
This module is the offline contract used by tests and by that client.
"""

from __future__ import annotations

import json
import re
from typing import Any

from live_class_constants import DEFAULT_SLIDES_TEMPLATE_ID

TEMPLATE_LAYOUT_NAMES: tuple[str, ...] = (
    "TITLE_CLASS",
    "TEAM_INTRO",
    "OPEN_QUESTIONS_ROUND",
    "TEAM_CHALLENGE_ROUND_CONTEXT",
    "TEAM_CHALLENGE_ROUND_QUESTION",
    "TEAM_CHALLENGE_REFLECTION",
    "CONSOLIDATION_ROUND",
)

STOCK_REFLECTION = (
    "What strategy did your team try first?\n"
    "What would you try next time?\n"
    "What still feels unfinished?"
)

_FILE_ID_IN_URL = re.compile(
    r"(?:/d/|/presentation/d/|/document/d/|/file/d/|id=)([A-Za-z0-9_-]{20,})"
)
_BARE_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def parse_google_file_id(raw: str) -> str:
    """Extract a Google file/presentation id from a URL or pasted id.

    Args:
        raw: Docs/Drive URL, or a bare file id.

    Returns:
        The file id.

    Raises:
        ValueError: When the value is non-empty but not a Google id/URL.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if _BARE_FILE_ID.match(text) and "/" not in text and " " not in text:
        return text
    match = _FILE_ID_IN_URL.search(text)
    if match:
        return match.group(1)
    raise ValueError("Paste a Google Slides/Drive URL or file id.")


def drive_year_semester(semester_label: str | None = None) -> tuple[str, str]:
    """Return ``(2026-2027, S1)`` from ``frameworks/semester.json`` (or a label).

    Args:
        semester_label: Optional ``2026-2027 S1`` override (tests).

    Returns:
        Academic year folder name and semester code.
    """
    label = (semester_label or "").strip()
    if not label:
        try:
            from paths import SEMESTER_JSON
        except ImportError:
            from lms.paths import SEMESTER_JSON
        payload = json.loads(SEMESTER_JSON.read_text(encoding="utf-8"))
        label = str(payload.get("semester") or "")
    try:
        from instances import year_term_from_label
    except ImportError:
        from lms.instances import year_term_from_label
    return year_term_from_label(label)


def deck_drive_name(ontario_code: str, lesson_key: str, meeting_iso: str) -> str:
    """Drive filename such as ``MCF3M M1C1 2026-09-11``.

    Args:
        ontario_code: Course code.
        lesson_key: ``M1C1`` or empty.
        meeting_iso: Class date.
    """
    code = (ontario_code or "Course").strip().upper()
    key = (lesson_key or "").strip().upper()
    day = (meeting_iso or "").strip()
    parts = [code]
    if key:
        parts.append(key)
    if day:
        parts.append(day)
    return " ".join(parts)


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


def layout_fill_values(
    *,
    ontario_code: str,
    lesson_key: str,
    join_code: str,
    selection: dict[str, Any],
    team_names: list[str] | None = None,
) -> dict[str, str]:
    """Placeholder strings written into the copied 7-layout template.

    Open Question working canvas is intentionally omitted so the teacher
    annotates live. Contest math stays on the challenge question layout.

    Args:
        ontario_code: Course code.
        lesson_key: ``M1C1``-style key.
        join_code: Live-session join code.
        selection: ``select_live_problems`` result.
        team_names: Optional team labels for ``TEAM_INTRO``.
    """
    course_lesson = f"{ontario_code}: {lesson_key}" if lesson_key else str(ontario_code)
    contest = selection.get("contest") or {}
    contest_title = str(contest.get("title") or "Team challenge")
    contest_stem = _plain_from_html(str(contest.get("stem_html") or ""))
    contest_task = _plain_from_html(str(contest.get("task_html") or ""))
    question = contest_stem
    if contest_task:
        question = f"{contest_stem}\nYour task: {contest_task}".strip()
    context = (
        f"{contest_title}\n"
        "Work as a team. Roles: recorder · explainer · skeptic · reporter.\n"
        "Read the challenge on the next slide. Do not race — show your process."
    )
    std_lines: list[str] = []
    for row in selection.get("standards") or []:
        title = str(row.get("title") or "Problem")
        stem = _plain_from_html(str(row.get("stem_html") or ""))
        line = f"• {title}"
        if stem:
            line += f"\n  {stem}"
        std_lines.append(line)
    consolidation = "\n".join(std_lines) or "Connect today's work back to the lesson."
    teams = ", ".join(n for n in (team_names or []) if n)
    return {
        "{{COURSE_LESSON}}": course_lesson,
        "{{JOIN_CODE}}": join_code or "",
        "{{TEAM_NAMES}}": teams,
        "{{CONTEST_TITLE}}": contest_title,
        "{{CONTEST_CONTEXT}}": context,
        "{{CONTEST_QUESTION}}": question or contest_title,
        "{{REFLECTION_PROMPTS}}": STOCK_REFLECTION,
        "{{CONSOLIDATION_ITEMS}}": consolidation,
        "Course Code + Lesson Code": course_lesson,
        "COURSE CODE + LESSON CODE": course_lesson,
    }


def _element_text(element: dict[str, Any]) -> str:
    """Concatenate textRun contents from a page element."""
    chunks: list[str] = []
    shape = element.get("shape") or {}
    for te in (shape.get("text") or {}).get("textElements") or []:
        run = te.get("textRun") or {}
        chunks.append(str(run.get("content") or ""))
    notes = element.get("shape") or shape
    return "".join(chunks) or str(notes.get("placeholder") or "")


def _walk_text(page: dict[str, Any]) -> str:
    """All visible text on a slide or notes page."""
    bits = [_element_text(el) for el in page.get("pageElements") or []]
    return "\n".join(bits)


def _notes_text(slide: dict[str, Any]) -> str:
    """Speaker notes blob for layout-name fallback."""
    notes = (slide.get("slideProperties") or {}).get("notesPage") or {}
    return _walk_text(notes)


def _layout_maps(presentation: dict[str, Any]) -> dict[str, str]:
    """Map layout objectId → layout name."""
    out: dict[str, str] = {}
    for layout in presentation.get("layouts") or []:
        oid = str(layout.get("objectId") or "")
        props = layout.get("layoutProperties") or {}
        name = str(props.get("name") or props.get("displayName") or "")
        if oid and name:
            out[oid] = name
    return out


def layout_name_for_slide(slide: dict[str, Any], layouts_by_id: dict[str, str]) -> str:
    """Resolve a template layout name for one slide.

    Args:
        slide: Slides API slide resource.
        layouts_by_id: ``layoutObjectId`` → name.
    """
    layout_oid = str((slide.get("slideProperties") or {}).get("layoutObjectId") or "")
    named = layouts_by_id.get(layout_oid) or ""
    if named in TEMPLATE_LAYOUT_NAMES:
        return named
    blob = f"{named}\n{_notes_text(slide)}\n{_walk_text(slide)}"
    for name in TEMPLATE_LAYOUT_NAMES:
        if name in blob:
            return name
    return named


def _set_text_requests(object_id: str, text: str, current: str) -> list[dict[str, Any]]:
    """Delete existing text (when present) then insert ``text``."""
    reqs: list[dict[str, Any]] = []
    if (current or "").strip():
        reqs.append(
            {
                "deleteText": {
                    "objectId": object_id,
                    "textRange": {"type": "ALL"},
                }
            }
        )
    reqs.append(
        {
            "insertText": {
                "objectId": object_id,
                "text": text,
                "insertionIndex": 0,
            }
        }
    )
    return reqs


def _role_boxes(slide: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Pick title/body text-box object ids from placeholder types or order."""
    boxes: list[tuple[str, str, str]] = []
    for el in slide.get("pageElements") or []:
        oid = str(el.get("objectId") or "")
        if not oid:
            continue
        shape = el.get("shape") or {}
        if not shape:
            continue
        ph = str((shape.get("placeholder") or {}).get("type") or "")
        current = _element_text(el)
        boxes.append((oid, ph, current))
    title = next((b for b in boxes if b[1] in {"TITLE", "CENTERED_TITLE"}), None)
    body = next((b for b in boxes if b[1] in {"BODY", "SUBTITLE"}), None)
    others = [b for b in boxes if b not in {title, body}]
    if title is None and boxes:
        title = boxes[0]
        others = boxes[1:]
    if body is None and others:
        body = others[0]
    out: dict[str, tuple[str, str]] = {}
    if title:
        out["title"] = (title[0], title[2])
    if body:
        out["body"] = (body[0], body[2])
    return out


def build_template_fill_requests(
    presentation: dict[str, Any],
    values: dict[str, str],
) -> list[dict[str, Any]]:
    """Build Slides ``batchUpdate`` requests for named layouts.

    Prefers ``replaceAllText`` for ``{{TOKEN}}`` placeholders, then targeted
    insert on title/body boxes for each style-guide layout. Leaves
    ``OPEN_QUESTIONS_ROUND`` working area empty.

    Args:
        presentation: GET presentations payload (slides + layouts).
        values: ``layout_fill_values`` map.

    Returns:
        Request list for ``presentations.batchUpdate``.
    """
    requests: list[dict[str, Any]] = []
    blob = json.dumps(presentation)
    for token, replacement in values.items():
        if not token or replacement is None:
            continue
        if token.startswith("{{") and token not in blob:
            continue
        if not token.startswith("{{") and token not in blob:
            continue
        requests.append(
            {
                "replaceAllText": {
                    "containsText": {"text": token, "matchCase": True},
                    "replaceText": replacement,
                }
            }
        )
    layouts_by_id = _layout_maps(presentation)
    course_lesson = values.get("{{COURSE_LESSON}}") or ""
    join_code = values.get("{{JOIN_CODE}}") or ""
    layout_body = {
        "TITLE_CLASS": (
            f"Join code: {join_code}" if join_code else course_lesson
        ),
        "TEAM_INTRO": values.get("{{TEAM_NAMES}}") or "",
        "TEAM_CHALLENGE_ROUND_CONTEXT": values.get("{{CONTEST_CONTEXT}}") or "",
        "TEAM_CHALLENGE_ROUND_QUESTION": values.get("{{CONTEST_QUESTION}}") or "",
        "TEAM_CHALLENGE_REFLECTION": values.get("{{REFLECTION_PROMPTS}}") or "",
        "CONSOLIDATION_ROUND": values.get("{{CONSOLIDATION_ITEMS}}") or "",
    }
    for slide in presentation.get("slides") or []:
        name = layout_name_for_slide(slide, layouts_by_id)
        if name == "OPEN_QUESTIONS_ROUND":
            continue
        boxes = _role_boxes(slide)
        if name == "TITLE_CLASS":
            title_box = boxes.get("title")
            if title_box and course_lesson and "{{COURSE_LESSON}}" not in title_box[1]:
                if title_box[1].strip() in {"", name, "Course Code + Lesson Code"}:
                    requests.extend(_set_text_requests(title_box[0], course_lesson, title_box[1]))
            body_box = boxes.get("body")
            body_text = layout_body["TITLE_CLASS"]
            if body_box and join_code and "{{JOIN_CODE}}" not in body_box[1]:
                if body_box[1].strip() in {"", name, "JOIN_CODE", "Join code"}:
                    requests.extend(_set_text_requests(body_box[0], body_text, body_box[1]))
            continue
        if name == "TEAM_INTRO" and not layout_body["TEAM_INTRO"]:
            continue
        fill = layout_body.get(name) or ""
        if not fill:
            continue
        body_box = boxes.get("body") or boxes.get("title")
        if not body_box:
            continue
        current = body_box[1]
        if "{{" in current:
            continue
        if current.strip() in {"", name} or name in current:
            requests.extend(_set_text_requests(body_box[0], fill, current))
    return requests


def resolved_template_id(offering: dict[str, Any] | None, school: Any) -> str:
    """Offering template, else school default, else Lesson Theme Template #1.

    Args:
        offering: ``course_offerings`` row.
        school: ``SchoolDB``.
    """
    from live_class_constants import SETTING_DEFAULT_SLIDES_TEMPLATE_ID

    oid = str((offering or {}).get("slides_template_id") or "").strip()
    if oid:
        return oid
    stored = str(school.get_school_setting(SETTING_DEFAULT_SLIDES_TEMPLATE_ID, "") or "").strip()
    return stored or DEFAULT_SLIDES_TEMPLATE_ID
