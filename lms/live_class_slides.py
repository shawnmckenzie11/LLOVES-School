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


def strand_from_module(title: str | None, module_number: int | None) -> str:
    """Guess MCF3M strand letter from a module title or number.

    Args:
        title: Module outline title.
        module_number: 1-based module index when known.

    Returns:
        ``A``, ``B``, or ``C`` (default ``A``).
    """
    blob = f"{title or ''} {module_number or ''}".lower()
    if any(word in blob for word in ("exponential", "finance", "interest", "strand b")):
        return "B"
    if any(word in blob for word in ("sine", "trig", "periodic", "strand c")):
        return "C"
    if any(word in blob for word in ("quadratic", "strand a")):
        return "A"
    if module_number is not None:
        if module_number <= 3:
            return "A"
        if module_number <= 6:
            return "B"
        return "C"
    return "A"


def build_timeline(
    *,
    meeting: date,
    placements: dict[str, dict[str, Any]],
    module_titles: dict[int, str],
) -> dict[str, Any]:
    """Compute Live Class Y of N and so-far / by-the-end copy.

    Args:
        meeting: Class date.
        placements: ISO date → ``{module, live, assessment_kind, lesson}``.
        module_titles: Module number → title.

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
        "strand": strand_from_module(module_title, module_num or None),
    }


def select_live_problems(
    problems: list[dict[str, Any]],
    *,
    strand: str,
    warmup_fallback: str = "",
) -> dict[str, Any]:
    """Pick warmup, contest, and 3–5 standard items for one live class.

    Args:
        problems: Active bank rows (with ``processes`` lists).
        strand: ``A`` / ``B`` / ``C`` (matched to ``module_hint``).
        warmup_fallback: Page title used when the bank has no warmup.

    Returns:
        Selected problem ids plus process keys for contest/consolidation.
    """
    strand_key = (strand or "A").upper()[:1]

    def _hint_match(row: dict[str, Any]) -> bool:
        hint = str(row.get("module_hint") or "").upper()
        return strand_key in hint or hint.startswith(strand_key)

    def _kind(kind: str) -> list[dict[str, Any]]:
        return [
            row
            for row in problems
            if str(row.get("kind") or "") == kind and int(row.get("active") or 1)
        ]

    warmups = [row for row in _kind("warmup") if _hint_match(row)] or _kind("warmup")
    contests = [
        row
        for row in _kind("contest")
        if _hint_match(row) and "problem_solving" in (row.get("processes") or [])
    ] or [row for row in _kind("contest") if _hint_match(row)] or _kind("contest")
    standards = [row for row in _kind("standard") if _hint_match(row)] or _kind("standard")
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
        std_lines.append(f"• {row.get('title') or 'Problem'} ({tags})")
    consol_tags = ", ".join(
        process_labels.get(k, k)
        for k in (selection.get("consolidation_process_keys") or [])
    )
    roles = (
        "Roles: recorder · explainer · skeptic · reporter. "
        "Reminders: " + (consol_tags or "problem solving, communicating")
    )
    return [
        {
            "title": "Intro",
            "body": (
                f"{timeline.get('headline')}\n{so_far_line}\n{by_end}\n\n"
                f"Open question / warmup:\n{warmup_text}"
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
                f"{contest_stem}\n{contest_task}\n\nRelated practice:\n"
                + ("\n".join(std_lines) or "• Use today's standard items")
                + f"\n\n{roles}"
            ),
        },
        {
            "title": "Consolidation",
            "body": (
                f"Share one strategy and one representation.\n"
                f"Process focus: {consol_tags or 'Communicating'}\n\n"
                "Which approach would you reuse? What still feels unfinished?"
            ),
        },
    ]


def _plain_from_html(raw: str) -> str:
    """Strip a few HTML tags from a problem stem.

    Args:
        raw: HTML or plain text.
    """
    text = raw.replace("<p>", "").replace("</p>", "\n").replace("<em>", "").replace("</em>", "")
    text = text.replace("<br>", "\n").replace("<br/>", "\n")
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
    """Create or reuse a four-slide deck for one class meeting.

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
    )
    bank = school.list_live_problems(ontario_code=ontario, active_only=True)
    fallback = ""
    if module_titles:
        fallback = next(iter(module_titles.values()), "")
    selection = select_live_problems(
        bank,
        strand=str(timeline.get("strand") or "A"),
        warmup_fallback=fallback,
    )
    process_labels = load_process_labels()
    slides = _slide_bodies(timeline, selection, process_labels)
    title = (
        f"{cls.get('section_code') or ontario} · {meeting_date.isoformat()} · "
        f"{timeline.get('headline')}"
    )
    existing = school.get_live_session_for_class_date(class_id, meeting_date.isoformat())
    live_session = school.get_active_live_session_for_class(class_id) or existing
    if (
        existing
        and existing.get("presentation_id")
        and not force_regenerate
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
    token_row = school.get_google_api_token(int(user["id"]))
    use_mock = mock_slides_enabled() or (
        token_row and token_row.get("refresh_token") == MOCK_SLIDES_REFRESH_TOKEN
    )
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
        if old_id:
            client.archive_presentation(old_id, meeting_date)
        created = client.create_four_slide_deck(title, slides)
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
    """Thin REST client for presentations + drive.file sharing."""

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
            json={"name": name},
            timeout=15,
        )

    def create_four_slide_deck(
        self, title: str, slides: list[dict[str, str]]
    ) -> dict[str, str]:
        """Create a presentation, fill four slides, and share view-with-link.

        Args:
            title: Presentation title.
            slides: Four title/body pairs.

        Returns:
            ``presentation_id`` and ``presentation_url``.
        """
        created = self.http.post(
            "https://slides.googleapis.com/v1/presentations",
            headers=self._headers(),
            json={"title": title},
            timeout=20,
        )
        created.raise_for_status()
        body = created.json()
        pres_id = str(body.get("presentationId") or "")
        if not pres_id:
            raise RuntimeError("Slides API did not return presentationId")
        default_slides = body.get("slides") or []
        first_id = (default_slides[0] or {}).get("objectId") if default_slides else None
        requests_body: list[dict[str, Any]] = []
        object_ids = []
        if first_id:
            object_ids.append(str(first_id))
        for index in range(max(0, 4 - len(object_ids))):
            oid = f"liveSlide{index + 2}"
            object_ids.append(oid)
            requests_body.append(
                {
                    "createSlide": {
                        "objectId": oid,
                        "insertionIndex": len(object_ids) - 1,
                        "slideLayoutReference": {"predefinedLayout": "BLANK"},
                    }
                }
            )
        for index, slide in enumerate(slides[:4]):
            slide_id = object_ids[index] if index < len(object_ids) else f"liveSlide{index}"
            box_id = f"box{index + 1}"
            requests_body.append(
                {
                    "createShape": {
                        "objectId": box_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {
                                "width": {"magnitude": 6000000, "unit": "EMU"},
                                "height": {"magnitude": 4000000, "unit": "EMU"},
                            },
                            "transform": {
                                "scaleX": 1,
                                "scaleY": 1,
                                "translateX": 200000,
                                "translateY": 200000,
                                "unit": "EMU",
                            },
                        },
                    }
                }
            )
            requests_body.append(
                {
                    "insertText": {
                        "objectId": box_id,
                        "text": f"{slide['title']}\n\n{slide['body']}",
                    }
                }
            )
        if requests_body:
            batch = self.http.post(
                f"https://slides.googleapis.com/v1/presentations/{pres_id}:batchUpdate",
                headers=self._headers(),
                json={"requests": requests_body},
                timeout=20,
            )
            batch.raise_for_status()
        self.http.post(
            f"https://www.googleapis.com/drive/v3/files/{pres_id}/permissions",
            headers=self._headers(),
            json={"role": "reader", "type": "anyone"},
            timeout=15,
        )
        meta = self.http.get(
            f"https://www.googleapis.com/drive/v3/files/{pres_id}",
            headers=self._headers(),
            params={"fields": "webViewLink,id"},
            timeout=15,
        )
        url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
        if meta.ok:
            url = str(meta.json().get("webViewLink") or url)
        return {"presentation_id": pres_id, "presentation_url": url}


def slides_oauth_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """Build the incremental Google consent URL for Slides + Drive file.

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
