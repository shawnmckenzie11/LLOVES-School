"""Set Class defaults and open-office labels (MCK-250).

Open office only relabels the slot. The first open office in a module is
``O1`` (shown as ``M3 O1``), then ``O2``. Those rows stay on Lesson Slides
and are left out when the course's live-class template is copied.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

_OPEN_OFFICE_RE = re.compile(r"^O([1-9]\d*)$")
_TEMPLATE_SLOT_RE = re.compile(r"^C\d+$")
_TORONTO = ZoneInfo("America/Toronto")


def is_open_office_slot(raw: object) -> bool:
    """Return True when ``raw`` is an open-office slot such as ``O1``.

    Args:
        raw: Slot token from Set Class or a stored live session.
    """

    return _OPEN_OFFICE_RE.fullmatch(str(raw or "").strip().upper()) is not None


def next_open_office_slot(existing: Iterable[object]) -> str:
    """Return the next open-office number for one module.

    Live-class ``C`` slots are ignored. ``O1`` is first; after ``O1`` the
    next stored label is ``O2``.

    Args:
        existing: Slot tokens already used in this module.
    """

    highest = 0
    for raw in existing:
        match = _OPEN_OFFICE_RE.fullmatch(str(raw or "").strip().upper())
        if match is None:
            continue
        highest = max(highest, int(match.group(1)))
    return f"O{highest + 1}"


def open_office_label(module: object, slot: object) -> str:
    """Return the stored open-office label, for example ``M3 O1``.

    Args:
        module: Module token such as ``M3``.
        slot: Open-office slot such as ``O1``.
    """

    return f"{str(module or '').strip().upper()} {str(slot or '').strip().upper()}"


def format_next_class_label(module: object, slot: object) -> str:
    """Return the Set Class helper label, for example ``M3 · C2``.

    Args:
        module: Module token such as ``M3``.
        slot: Live-class slot such as ``C2``.
    """

    return f"{str(module or '').strip().upper()} · {str(slot or '').strip().upper()}"


def chapters_for_module(
    module: object,
    catalogue: list[tuple[str, str]],
    plan: list[tuple[str, list[str]]] | None = None,
) -> list[str]:
    """Class slots for one module, from the section plan, then the pack.

    Args:
        module: Section module such as ``M2``.
        catalogue: Published ``(module, slot)`` pairs.
        plan: Section plan ``(module, slots)``. Used when the pack has
            not published this module.

    Returns:
        Chapter tokens in teaching order. ``["C1"]`` when neither source
        lists the module.
    """

    token = str(module or "").strip().upper()
    if plan:
        for planned, slots in plan:
            if str(planned).strip().upper() != token:
                continue
            found = [
                str(slot).strip().upper()
                for slot in slots
                if str(slot).strip().upper().startswith("C")
            ]
            if found:
                return found
    found = [
        str(slot).strip().upper()
        for mod, slot in catalogue
        if str(mod).strip().upper() == token and str(slot).strip().upper().startswith("C")
    ]
    return found or ["C1"]


def default_setup_class(
    catalogue: list[tuple[str, str]],
    last_run: tuple[str, str] | None,
    *,
    current_module: str = "",
    run_in_current: tuple[str, str] | None = None,
    current_chapters: list[str] | None = None,
) -> tuple[str, str] | None:
    """Default module and chapter for one section's Set Class.

    An empty ``current_module`` keeps the template class after
    ``last_run``. A stored module stays selected. Its first chapter is
    the default until a real class in that module has been run; the next
    chapter in that module is the default after that. A class run in
    some other module does not move this default. This function does
    not write the stored module.

    Args:
        catalogue: Ordered published ``(module, slot)`` pairs.
        last_run: Latest real template class in the section, or None.
        current_module: ``classes.current_live_module``, or empty.
        run_in_current: Latest real class inside ``current_module``.
        current_chapters: Class slots for ``current_module``, in order.

    Returns:
        ``(module, slot)``, or None when the catalogue is empty and no
        current module is stored.
    """

    module = str(current_module or "").strip().upper()
    if not module:
        return next_class_after_last_run(catalogue, last_run)
    chapters = [
        str(slot).strip().upper()
        for slot in (current_chapters or [])
        if str(slot).strip().upper().startswith("C")
    ]
    if not chapters:
        chapters = [
            str(slot).strip().upper()
            for mod, slot in catalogue
            if str(mod).strip().upper() == module and str(slot).strip().upper().startswith("C")
        ]
    if not chapters:
        chapters = ["C1"]
    scoped = [(module, chapter) for chapter in chapters]
    if run_in_current is None:
        return scoped[0]
    nxt = next_class_after_last_run(scoped, run_in_current)
    return nxt or scoped[0]


def registry_with_setup_module(registry: dict, module: object, chapters: Iterable[object]) -> dict:
    """Copy a pack registry so Set Class can select the section module.

    Published packs sometimes stop before the section's current module.
    The copy adds that module and its chapters for the page. The stored
    pack and the section pointer are left as they are.

    Args:
        registry: ``live_class_registry`` result for the page.
        module: Module Set Class should select.
        chapters: Class slots for that module.

    Returns:
        ``registry`` when it already lists the module, otherwise a copy.
    """

    token = str(module or "").strip().upper()
    slots = [
        str(slot).strip().upper()
        for slot in chapters
        if str(slot or "").strip().upper().startswith("C")
    ]
    if not token:
        return registry
    modules = [str(item).strip().upper() for item in (registry.get("modules") or [])]
    slots_by = {
        str(key).strip().upper(): [str(slot).strip().upper() for slot in (value or [])]
        for key, value in (registry.get("slots_by_module") or {}).items()
    }
    changed = False
    if token not in modules:
        modules.append(token)
        changed = True
    if token not in slots_by:
        slots_by[token] = slots or ["C1"]
        changed = True
    if not changed:
        return registry
    copied = dict(registry)
    copied["modules"] = modules
    copied["slots_by_module"] = slots_by
    return copied


def next_class_after_last_run(
    catalogue: list[tuple[str, str]],
    last_run: tuple[str, str] | None,
) -> tuple[str, str] | None:
    """Return the template class after the last one that was run.

    ``catalogue`` is live-class slots in teaching order (module, then
    ``C1``, ``C2``, …). Open-office slots are not part of this sequence.
    With no run yet, the first class is the default. After the last
    template class has already run, that class stays selected.

    Args:
        catalogue: Ordered ``(module, slot)`` pairs from the course template.
        last_run: The most recently run template class, or None.
    """

    ordered = [
        (str(module).strip().upper(), str(slot).strip().upper())
        for module, slot in catalogue
        if str(slot).strip().upper().startswith("C")
    ]
    if not ordered:
        return None
    if last_run is None:
        return ordered[0]
    key = (str(last_run[0]).strip().upper(), str(last_run[1]).strip().upper())
    if key not in ordered:
        return ordered[0]
    index = ordered.index(key)
    if index + 1 < len(ordered):
        return ordered[index + 1]
    return ordered[index]


def format_slide_day(iso: object) -> str:
    """Format a stored run time as ``Mon Oct 12`` in America/Toronto.

    Naive timestamps are treated as UTC, matching Lesson Slides display.

    Args:
        iso: Stored ``ended_at`` value.
    """

    text = str(iso or "").strip()
    if not text:
        return ""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif "T" in text and re.search(r"[+-]\d{2}:\d{2}$", text) is None:
        text = text + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(_TORONTO)
    return f"{local.strftime('%a %b')} {local.day}"


def copy_course_live_template(src: Path, dest: Path) -> list[str]:
    """Copy a course live-class template, skipping open-office files.

    Template files are ``<module>/C*.json``. Open office is stored beside
    them as ``O1.json`` and the like, but those files are not part of the
    template and are not copied.

    Args:
        src: Source course directory (``live_classes/<code>``).
        dest: Destination course directory.

    Returns:
        Relative paths that were copied, in sorted order.
    """

    copied: list[str] = []
    if not src.is_dir():
        return copied
    for path in sorted(src.glob("M*/*.json")):
        if not path.is_file():
            continue
        slot = path.stem.upper()
        if is_open_office_slot(slot) or _TEMPLATE_SLOT_RE.fullmatch(slot) is None:
            continue
        module = path.parent.name
        target = dest / module / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(f"{module}/{path.name}")
    return copied
