"""Per-teacher course instance trees under the LMS data volume.

Git ``courses/<CODE>/`` stays a shared read-only template. Instances are
thin (``manifest.json`` + ``syllabus/``). Shared cartridges live in
``content_libraries`` and ``{data_dir}/libraries/<id>/``.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from paths import REPO_ROOT, SEMESTER_JSON, SYLLABUS_DATA_DIR
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.paths import REPO_ROOT, SEMESTER_JSON, SYLLABUS_DATA_DIR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemplatePackPaths:
    """Read-only IMSCC / unpacked / inventory under a git ``content_root``."""

    content_root: Path | None
    imscc: Path | None
    unpacked: Path
    inventory: Path


def year_term_from_label(semester_label: str) -> tuple[str, str]:
    """Turn ``2026-2027 S1`` into ``(2026-2027, S1)`` path segments.

    Uses the semester **label**, not ``year_display`` (``2026/27``), which is
    unsafe as a directory name.

    Args:
        semester_label: Value of ``semester.json``'s ``semester`` field.

    Returns:
        ``(year, term)`` with slashes replaced by hyphens.
    """
    text = (semester_label or "").strip()
    parts = text.split()
    year = (parts[0] if parts else "unknown").replace("/", "-")
    term = (parts[1] if len(parts) > 1 else "S1").replace("/", "-")
    return year or "unknown", term or "S1"


def teacher_slug(teacher_user_id: int, section_index: int = 1) -> str:
    """Return a stable instance folder name for one section a staff user holds.

    Args:
        teacher_user_id: ``users.id``.
        section_index: 1-based section for this (teacher, code, term).

    Returns:
        ``t{user_id}`` for the first section (unchanged for every instance
        that existed before sections), ``t{user_id}-{n}`` for later ones.
    """
    base = f"t{int(teacher_user_id)}"
    try:
        index = int(section_index or 1)
    except (TypeError, ValueError):
        index = 1
    return base if index <= 1 else f"{base}-{index}"


def instance_relpath(
    code: str, year: str, term: str, teacher_id: int, section_index: int = 1
) -> str:
    """Return a volume-relative instance path.

    Args:
        code: Ontario course code.
        year: Label year segment (``2026-2027``).
        term: Label term segment (``S1``).
        teacher_id: Staff ``users.id``.
        section_index: 1-based section number for this teacher and code.

    Returns:
        POSIX relative path such as ``instances/MCF3M/2026-2027/S1/t12``.
    """
    key = (code or "").strip().upper() or "UNKNOWN"
    return f"instances/{key}/{year}/{term}/{teacher_slug(teacher_id, section_index)}"


def instance_root(
    data_dir: Path,
    code: str,
    year: str,
    term: str,
    teacher_id: int,
    section_index: int = 1,
) -> Path:
    """Absolute instance directory under ``data_dir``.

    Args:
        data_dir: LMS data volume (``/data`` on Fly, ``lms/data`` locally).
        code: Ontario course code.
        year: Label year segment.
        term: Label term segment.
        teacher_id: Staff ``users.id``.
        section_index: 1-based section number for this teacher and code.
    """
    return Path(data_dir) / instance_relpath(
        code, year, term, teacher_id, section_index
    )


def instance_root_from_relpath(data_dir: Path, relpath: str) -> Path:
    """Resolve a stored ``instance_relpath`` against the data volume.

    Args:
        data_dir: LMS data volume.
        relpath: Value of ``course_offerings.instance_relpath``.
    """
    return Path(data_dir) / str(relpath)


def instance_pack_dir(root: Path) -> Path:
    """Return the leftover ``pack/`` folder under an instance root.

    New assigns do not create this folder. Existing Fly forks may still
    have one; resolvers treat it as a backward-compatible fallback.

    Args:
        root: Instance directory.
    """
    return Path(root) / "pack"


def library_relpath(library_id: int) -> str:
    """Return a volume-relative shared-library folder.

    Args:
        library_id: ``content_libraries.id``.

    Returns:
        POSIX relative path such as ``libraries/3``.
    """
    return f"libraries/{int(library_id)}"


def library_root(data_dir: Path, library_id: int) -> Path:
    """Absolute shared-library directory under ``data_dir``.

    One folder per extracted/uploaded pack, shared by every offering that
    points at the same ``library_id``. Phase 2 stores blobs here / under
    ``blobs/``; phase 1 uses it for uploaded IMSCC + one unpack.

    Args:
        data_dir: LMS data volume.
        library_id: ``content_libraries.id``.
    """
    return Path(data_dir) / library_relpath(library_id)


def instance_syllabus_dir(root: Path) -> Path:
    """Return the ``syllabus/`` folder under an instance root.

    Args:
        root: Instance directory.
    """
    return Path(root) / "syllabus"


def legacy_module_pack_root(data_dir: Path, offering_id: int) -> Path:
    """Pre-instance upload folder ``module_packs/<offering_id>/``.

    Args:
        data_dir: LMS data volume.
        offering_id: ``course_offerings.id``.
    """
    return Path(data_dir) / "module_packs" / str(int(offering_id))


def pack_dir_for_offering(data_dir: Path, offering: dict[str, Any]) -> Path:
    """Preferred live pack folder for an offering.

    Args:
        data_dir: LMS data volume.
        offering: ``course_offerings`` row (needs ``id``; ``instance_relpath`` optional).
    """
    rel = offering.get("instance_relpath")
    if rel:
        return instance_pack_dir(instance_root_from_relpath(data_dir, str(rel)))
    return legacy_module_pack_root(data_dir, int(offering["id"]))


def template_pack_paths(
    code: str, content_root: str | None = None
) -> TemplatePackPaths:
    """Resolve the git template cartridge for a catalog course (read-only).

    Uses ``ontario_courses.content_root`` when it points at an existing folder.
    This school repo keeps ``content_root`` NULL; packs come from Admin upload.
    Never creates ``courses/<CODE>/``.

    Args:
        code: Ontario course code.
        content_root: Catalog ``content_root`` (repo-relative or absolute).

    Returns:
        Template paths; ``imscc`` is None when this code has no git pack.
    """
    key = (code or "").strip().upper()
    root: Path | None = None
    raw = (content_root or "").strip()
    if raw:
        cand = Path(raw)
        root = cand if cand.is_absolute() else (REPO_ROOT / cand)
        if not root.is_dir():
            root = None
    if root is None and key:
        builtin = REPO_ROOT / "courses" / key
        if builtin.is_dir():
            root = builtin
    if root is None:
        missing = Path("/nonexistent-template")
        return TemplatePackPaths(None, None, missing / "unpacked", missing / "inventory.json")

    sources = root / "sources"
    imscc: Path | None = None
    if sources.is_dir():
        found = sorted(p for p in sources.glob("*.imscc") if p.is_file())
        imscc = found[0] if found else None
    return TemplatePackPaths(
        root,
        imscc,
        root / "canvas" / "unpacked",
        root / "canvas" / "inventory.json",
    )


def is_template_imscc(path: Path | None) -> bool:
    """True when ``path`` is a git template cartridge, not an instance copy.

    Args:
        path: Stored ``imscc_path`` or a resolved file.
    """
    if path is None:
        return False
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    try:
        courses = (REPO_ROOT / "courses").resolve()
        rel = resolved.relative_to(courses)
    except ValueError:
        return False
    parts = rel.parts
    return (
        len(parts) >= 3
        and parts[1] == "sources"
        and resolved.suffix.lower() == ".imscc"
    )


def imscc_in_pack(pack: Path, stored: str | None = None) -> Path | None:
    """Return a readable ``.imscc`` inside ``pack/``, if any.

    Args:
        pack: Instance or legacy pack directory.
        stored: Offering ``imscc_path`` to prefer when it already lives here.
    """
    if stored:
        raw = Path(stored)
        if raw.is_file() and not is_template_imscc(raw):
            try:
                raw.resolve().relative_to(Path(pack).resolve())
                return raw
            except (ValueError, OSError):
                if raw.is_file():
                    return raw
    course = Path(pack) / "course.imscc"
    if course.is_file():
        return course
    found = sorted(p for p in Path(pack).glob("*.imscc") if p.is_file())
    return found[0] if found else None


def pack_looks_present(pack: Path) -> bool:
    """True when a pack folder has a cartridge, inventory, or unpacked tree.

    Args:
        pack: ``pack/`` or legacy ``module_packs/<id>/`` directory.
    """
    root = Path(pack)
    if not root.exists():
        return False
    if imscc_in_pack(root) is not None:
        return True
    if (root / "inventory.json").is_file():
        return True
    if (root / "unpacked" / "imsmanifest.xml").is_file():
        return True
    return False


def offering_has_pack(data_dir: Path, offering: dict[str, Any]) -> bool:
    """True when this offering already has a forked or legacy module pack.

    Args:
        data_dir: LMS data volume.
        offering: Offering row.
    """
    if pack_looks_present(pack_dir_for_offering(data_dir, offering)):
        return True
    rel = offering.get("instance_relpath")
    if rel and pack_looks_present(instance_pack_dir(instance_root_from_relpath(data_dir, str(rel)))):
        return True
    oid = offering.get("id")
    if oid is not None and pack_looks_present(legacy_module_pack_root(data_dir, int(oid))):
        return True
    stored = offering.get("imscc_path")
    if stored and Path(stored).is_file():
        return True
    return False


def _copy_file(src: Path, dest: Path) -> None:
    """Copy one file, preferring copy-on-write when the OS supports it.

    Args:
        src: Source file.
        dest: Destination file path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _copy_tree(src: Path, dest: Path) -> None:
    """Copy a directory tree into ``dest``.

    Args:
        src: Source directory.
        dest: Destination directory.
    """
    if not src.is_dir():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True, copy_function=shutil.copy2)


def _move_tree(src: Path, dest: Path) -> None:
    """Move ``src`` into ``dest``, merging if ``dest`` already exists.

    Args:
        src: Existing directory to consume.
        dest: Destination directory.
    """
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.move(str(src), str(dest))
        return
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            _move_tree(item, target)
        elif target.exists():
            target.unlink()
            shutil.move(str(item), str(target))
        else:
            shutil.move(str(item), str(target))
    try:
        src.rmdir()
    except OSError:
        shutil.rmtree(src, ignore_errors=True)


def copy_pack_dir(src_pack: Path, dest_pack: Path) -> None:
    """Copy IMSCC + unpacked + inventory from one pack folder to another.

    Args:
        src_pack: Source ``pack/`` directory.
        dest_pack: Destination ``pack/`` directory.
    """
    if not src_pack.exists():
        return
    dest_pack.mkdir(parents=True, exist_ok=True)
    for item in src_pack.iterdir():
        target = dest_pack / item.name
        if item.is_dir():
            _copy_tree(item, target)
        elif item.is_file():
            _copy_file(item, target)


def copy_template_into_pack(template: TemplatePackPaths, dest_pack: Path) -> Path | None:
    """Copy a git template cartridge into an instance ``pack/`` (never writes git).

    Args:
        template: Read-only template paths.
        dest_pack: Instance pack directory.

    Returns:
        Path to the copied ``course.imscc``, or None if the template has no IMSCC.
    """
    dest_pack.mkdir(parents=True, exist_ok=True)
    copied: Path | None = None
    if template.imscc is not None and template.imscc.is_file():
        copied = dest_pack / "course.imscc"
        _copy_file(template.imscc, copied)
    if template.inventory.is_file():
        _copy_file(template.inventory, dest_pack / "inventory.json")
    if template.unpacked.is_dir():
        _copy_tree(template.unpacked, dest_pack / "unpacked")
    return copied


def copy_syllabus_structure(
    src_syllabus: Path,
    dest_syllabus: Path,
    *,
    new_semester_label: str | None = None,
) -> None:
    """Copy included/excluded lesson ids; skip dated HTML/CSV.

    Args:
        src_syllabus: Base instance ``syllabus/`` folder.
        dest_syllabus: New instance ``syllabus/`` folder.
        new_semester_label: If set, also write ``{slug}.answers.json`` for this term.
    """
    if not src_syllabus.is_dir():
        return
    dest_syllabus.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for path in src_syllabus.iterdir():
        if not path.is_file():
            continue
        if path.name in {"_active_semester.json"}:
            continue
        if not path.name.endswith(".answers.json"):
            continue
        dest = dest_syllabus / path.name
        _copy_file(path, dest)
        copied.append(dest)
    if new_semester_label and copied:
        slug = new_semester_label.replace(" ", "-")
        named = dest_syllabus / f"{slug}.answers.json"
        if not named.is_file():
            payload = json.loads(copied[0].read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload["semester"] = new_semester_label
                named.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    """Write ``manifest.json`` for an instance tree.

    Args:
        root: Instance directory.
        payload: Manifest fields from the plan (code, year, term, teacher, …).

    Returns:
        Path to ``manifest.json``.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def manifest_payload(
    *,
    ontario_code: str,
    year: str,
    term: str,
    teacher_user_id: int,
    teacher_name: str,
    offering_id: int,
    base_offering_id: int | None,
    calendar: Any,
    library_id: int | None = None,
    section_index: int = 1,
) -> dict[str, Any]:
    """Build the instance ``manifest.json`` object.

    Args:
        ontario_code: Course code.
        year: Path year segment.
        term: Path term segment.
        teacher_user_id: Staff user id.
        teacher_name: Display name at assign time (informational).
        offering_id: Sqlite offering id.
        base_offering_id: Offering this syllabus was copied from, if any.
        calendar: ``frameworks/semester.json`` payload (or semester ``raw_json``).
        library_id: Shared ``content_libraries.id`` pointer, if attached.
        section_index: 1-based section this teacher holds of the code.
    """
    return {
        "ontario_code": ontario_code,
        "section_index": int(section_index or 1),
        "year": year,
        "term": term,
        "teacher_user_id": int(teacher_user_id),
        "teacher_name": teacher_name,
        "offering_id": int(offering_id),
        "base_offering_id": int(base_offering_id) if base_offering_id else None,
        "library_id": int(library_id) if library_id else None,
        "calendar": calendar,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _calendar_from_semester(semester_label: str, calendar: Any) -> Any:
    """Prefer the provided semester payload; else load ``frameworks/semester.json``.

    Args:
        semester_label: Offering semester label (unused when ``calendar`` is set).
        calendar: Already-parsed semester JSON.
    """
    if calendar is not None:
        return calendar
    if SEMESTER_JSON.is_file():
        try:
            return json.loads(SEMESTER_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read %s", SEMESTER_JSON)
    return {"semester": semester_label}


def materialize_instance(
    data_dir: Path,
    offering: dict[str, Any],
    *,
    semester_label: str,
    teacher_name: str = "",
    content_root: str | None = None,
    base_offering: dict[str, Any] | None = None,
    calendar: Any = None,
    library_id: int | None = None,
) -> dict[str, Any]:
    """Create a thin instance directory (manifest + syllabus only).

    A second teacher gets a new directory even when they share the same
    ``library_id``. Never writes into ``courses/``. Never copies IMSCC /
    unpacked / inventory into the instance.

    Args:
        data_dir: LMS data volume.
        offering: Newly inserted offering row.
        semester_label: ``2026-2027 S1``.
        teacher_name: Informational label for the manifest.
        content_root: Catalog template pointer (unused for files; kept so
            callers do not need a signature break).
        base_offering: Prior offering to copy syllabus answers JSON from.
        calendar: Semester JSON payload stored on the manifest.
        library_id: Shared content-library pointer stored on the manifest.

    Returns:
        Dict with ``instance_relpath``, ``root``, ``pack``, ``syllabus``,
        ``imscc_path`` (always None — the library holds the cartridge).
    """
    del content_root  # template files stay in the shared library, not here
    code = str(offering["ontario_code"]).strip().upper()
    year, term = year_term_from_label(semester_label)
    teacher_id = int(offering["teacher_user_id"])
    rel = instance_relpath(
        code, year, term, teacher_id, int(offering.get("section_index") or 1)
    )
    root = Path(data_dir) / rel
    pack = instance_pack_dir(root)
    syllabus = instance_syllabus_dir(root)
    root.mkdir(parents=True, exist_ok=True)
    syllabus.mkdir(parents=True, exist_ok=True)
    base_id = None
    if base_offering:
        base_id = int(base_offering["id"])
        base_rel = base_offering.get("instance_relpath")
        if base_rel:
            src_root = instance_root_from_relpath(data_dir, str(base_rel))
            copy_syllabus_structure(
                instance_syllabus_dir(src_root),
                syllabus,
                new_semester_label=semester_label,
            )
    write_manifest(
        root,
        manifest_payload(
            ontario_code=code,
            year=year,
            term=term,
            teacher_user_id=teacher_id,
            teacher_name=teacher_name,
            offering_id=int(offering["id"]),
            base_offering_id=base_id,
            calendar=_calendar_from_semester(semester_label, calendar),
            library_id=library_id,
            section_index=int(offering.get("section_index") or 1),
        ),
    )
    return {
        "instance_relpath": rel,
        "root": root,
        "pack": pack,
        "syllabus": syllabus,
        "imscc_path": None,
        "library_id": int(library_id) if library_id else None,
    }


def leftover_pack_imscc(
    data_dir: Path, offering: dict[str, Any]
) -> Path | None:
    """Return a leftover forked IMSCC without moving or deleting it.

    Checks instance ``pack/`` then ``module_packs/<id>/``. Used to attach a
    ``content_libraries`` pointer so new assigns do not create more forks.

    Args:
        data_dir: LMS data volume.
        offering: Offering row.

    Returns:
        Path to an existing cartridge, or None.
    """
    rel = offering.get("instance_relpath")
    if rel:
        pack = instance_pack_dir(instance_root_from_relpath(data_dir, str(rel)))
        found = imscc_in_pack(pack, offering.get("imscc_path"))
        if found is not None and found.is_file() and not is_template_imscc(found):
            return found
    oid = offering.get("id")
    if oid is not None:
        legacy = legacy_module_pack_root(data_dir, int(oid))
        found = imscc_in_pack(legacy, offering.get("imscc_path"))
        if found is not None and found.is_file() and not is_template_imscc(found):
            return found
    stored_raw = offering.get("imscc_path")
    if stored_raw:
        stored = Path(stored_raw)
        if stored.is_file() and not is_template_imscc(stored):
            return stored
    return None


def migrate_legacy_pack(
    data_dir: Path,
    offering: dict[str, Any],
    *,
    semester_label: str,
    teacher_name: str = "",
    content_root: str | None = None,
    calendar: Any = None,
    library_id: int | None = None,
) -> dict[str, Any]:
    """Ensure a thin instance exists; leave leftover packs in place.

    Does not copy a git template into ``pack/`` and does not delete
    ``module_packs/<id>/`` or an existing instance ``pack/``.

    Args:
        data_dir: LMS data volume.
        offering: Existing offering that may still point at a leftover pack.
        semester_label: Semester label for the path slug.
        teacher_name: Manifest label.
        content_root: Unused (library attach happens in the school DB).
        calendar: Semester JSON for the manifest.
        library_id: Shared library pointer to store on the manifest.

    Returns:
        Same shape as ``materialize_instance``, plus ``leftover_imscc``.
    """
    del content_root
    code = str(offering["ontario_code"]).strip().upper()
    year, term = year_term_from_label(semester_label)
    teacher_id = int(offering["teacher_user_id"])
    rel = offering.get("instance_relpath") or instance_relpath(
        code, year, term, teacher_id, int(offering.get("section_index") or 1)
    )
    root = Path(data_dir) / rel
    pack = instance_pack_dir(root)
    syllabus = instance_syllabus_dir(root)
    root.mkdir(parents=True, exist_ok=True)
    syllabus.mkdir(parents=True, exist_ok=True)
    leftover = leftover_pack_imscc(data_dir, {**offering, "instance_relpath": rel})

    if not (root / "manifest.json").is_file():
        write_manifest(
            root,
            manifest_payload(
                ontario_code=code,
                year=year,
                term=term,
                teacher_user_id=teacher_id,
                teacher_name=teacher_name,
                offering_id=int(offering["id"]),
                base_offering_id=(
                    int(offering["copied_from_offering_id"])
                    if offering.get("copied_from_offering_id")
                    else None
                ),
                calendar=_calendar_from_semester(semester_label, calendar),
                library_id=library_id,
                section_index=int(offering.get("section_index") or 1),
            ),
        )
    return {
        "instance_relpath": rel,
        "root": root,
        "pack": pack,
        "syllabus": syllabus,
        "imscc_path": str(leftover) if leftover else None,
        "leftover_imscc": leftover,
        "library_id": int(library_id) if library_id else None,
    }


def legacy_syllabus_dirs(
    semester_label: str,
    ontario_code: str,
    data_dir: Path,
) -> list[Path]:
    """Candidate pre-instance syllabus folders (volume + hardcoded LMS path).

    Args:
        semester_label: ``2026-2027 S1``.
        ontario_code: Course code.
        data_dir: LMS data volume (``/data`` on Fly).
    """
    slug = (semester_label or "").replace(" ", "-")
    code = (ontario_code or "").strip().upper()
    seen: set[Path] = set()
    out: list[Path] = []
    for parent in (
        Path(data_dir) / "syllabus",
        SYLLABUS_DATA_DIR,
        Path("/data/syllabus"),
    ):
        try:
            key = parent.resolve()
        except OSError:
            key = parent
        if key in seen:
            continue
        seen.add(key)
        out.append(parent / slug / code)
    return out


def migrate_legacy_syllabus(
    data_dir: Path,
    offering: dict[str, Any],
    *,
    semester_label: str,
    peer_count: int,
    all_peers_have_instance: bool,
) -> None:
    """Move or copy leftover ``data/syllabus/<slug>/<CODE>/`` into the instance.

    When several teachers shared that folder, copy until each has an instance,
    then delete the shared leftover.

    Args:
        data_dir: LMS data volume.
        offering: Offering with ``instance_relpath`` already set.
        semester_label: Semester label used by the old slug path.
        peer_count: Offerings for this (semester, code) pair.
        all_peers_have_instance: True when every peer already has a relpath.
    """
    rel = offering.get("instance_relpath")
    if not rel:
        return
    dest = instance_syllabus_dir(instance_root_from_relpath(data_dir, str(rel)))
    dest.mkdir(parents=True, exist_ok=True)
    dest_has_work = any(
        p.is_file() and p.name != "_active_semester.json" for p in dest.iterdir()
    )
    src: Path | None = None
    for cand in legacy_syllabus_dirs(
        semester_label, str(offering["ontario_code"]), data_dir
    ):
        if cand.is_dir() and any(cand.iterdir()):
            src = cand
            break
    if src is None:
        return
    if dest_has_work:
        if all_peers_have_instance:
            shutil.rmtree(src, ignore_errors=True)
        return
    dest.mkdir(parents=True, exist_ok=True)
    if peer_count <= 1:
        _move_tree(src, dest)
    else:
        _copy_tree(src, dest)
        if all_peers_have_instance:
            shutil.rmtree(src, ignore_errors=True)
