"""Interim live-class pack registry from catalogue briefs.

Does not invent a CMS. Discovers ``{CODE}-M*-C*-stem-and-slide.md`` and
``{CODE}-M*-C*-minds-on-student.md`` under
``content-builder/catalogue/challenges/module-briefs/``. Wired runtime
packs today are MCF3M Module 1 · C1/C2/C3 (Minds-On, TC, CONS).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT_LIVE_MODULE = "M1"
DEFAULT_COURSE = "MCF3M"
MODULE_RE = re.compile(r"^M\d+$")
SLOT_RE = re.compile(r"^C\d+$")
STEM_NAME = re.compile(
    r"^(?P<course>[A-Z]{3,}\d[A-Z]?)-(?P<module>M\d+)-(?P<slot>C\d+)-stem-and-slide\.md$"
)
MINDS_ON_NAME = re.compile(
    r"^(?P<course>[A-Z]{3,}\d[A-Z]?)-(?P<module>M\d+)-(?P<slot>C\d+)-minds-on-student\.md$"
)


def _repo_root() -> Path:
    """Return the repository root that holds ``content-builder/``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "content-builder" / "catalogue").is_dir():
            return parent
    return here.parents[1]


def briefs_root() -> Path:
    """Return ``content-builder/catalogue/challenges/module-briefs``."""
    return (
        _repo_root()
        / "content-builder"
        / "catalogue"
        / "challenges"
        / "module-briefs"
    )


def normalize_live_module(raw: Any) -> str:
    """Return ``M1``, ``M2``, … Unknown values fall back to M1.

    Args:
        raw: Posted or stored module id.
    """
    text = str(raw or "").strip().upper()
    if MODULE_RE.fullmatch(text):
        return text
    return DEFAULT_LIVE_MODULE


def normalize_course_code(raw: Any) -> str:
    """Return an Ontario course code, defaulting to MCF3M.

    Args:
        raw: Offering ``ontario_code``.
    """
    text = str(raw or "").strip().upper()
    return text or DEFAULT_COURSE


def minds_on_brief_relpath(course: str, module: str, slot: str) -> str:
    """Catalogue-relative path for one Minds-On student brief.

    Args:
        course: Ontario code, e.g. ``MCF3M``.
        module: ``M1``.
        slot: ``C1``.
    """
    return (
        "catalogue/challenges/module-briefs/minds-on/"
        f"{normalize_course_code(course)}-{normalize_live_module(module)}-"
        f"{str(slot or 'C1').strip().upper()}-minds-on-student.md"
    )


def live_class_registry(course: Any = None) -> dict[str, Any]:
    """Discover modules and live-class slots from catalogue briefs.

    Args:
        course: Ontario code to filter. Empty uses MCF3M.

    Returns:
        ``{course, modules, slots_by_module, packs}``. Interim: MCF3M
        lists M1 → C1/C2/C3 when those briefs exist.
    """
    code = normalize_course_code(course)
    root = briefs_root()
    found: dict[tuple[str, str], dict[str, Any]] = {}
    if root.is_dir():
        for path in root.rglob("*.md"):
            match = STEM_NAME.fullmatch(path.name) or MINDS_ON_NAME.fullmatch(
                path.name
            )
            if match is None:
                continue
            if match.group("course") != code:
                continue
            module = match.group("module")
            slot = match.group("slot")
            key = (module, slot)
            row = found.setdefault(
                key,
                {
                    "course": code,
                    "module": module,
                    "slot": slot,
                    "stem_path": "",
                    "minds_on_path": "",
                },
            )
            rel = str(path.relative_to(_repo_root() / "content-builder"))
            if STEM_NAME.fullmatch(path.name):
                row["stem_path"] = rel
            else:
                row["minds_on_path"] = rel
    if not found and code == DEFAULT_COURSE:
        for slot in ("C1", "C2", "C3"):
            found[("M1", slot)] = {
                "course": code,
                "module": "M1",
                "slot": slot,
                "stem_path": "",
                "minds_on_path": minds_on_brief_relpath(code, "M1", slot),
            }
    modules = sorted({key[0] for key in found}, key=lambda m: int(m[1:] or 0))
    slots_by_module: dict[str, list[str]] = {}
    for module, slot in sorted(found, key=lambda pair: (int(pair[0][1:] or 0), int(pair[1][1:] or 0))):
        slots_by_module.setdefault(module, []).append(slot)
    packs = [found[key] for key in sorted(found, key=lambda pair: (int(pair[0][1:] or 0), int(pair[1][1:] or 0)))]
    return {
        "course": code,
        "modules": modules or [DEFAULT_LIVE_MODULE],
        "slots_by_module": slots_by_module
        or {DEFAULT_LIVE_MODULE: ["C1", "C2", "C3"]},
        "packs": packs,
        "interim": True,
    }
