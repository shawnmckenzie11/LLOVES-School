"""IMSCC unpack, inventory nav, and wiki HTML rewriting for the Modules tab."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from paths import (
    REPO_ROOT,
    SCRIPTS_DIR,
)

try:
    from instances import (
        is_template_imscc,
        imscc_in_pack,
        legacy_module_pack_root,
        library_root,
        pack_looks_present,
        template_pack_paths,
    )
except ImportError:  # package import
    from lms.instances import (
        is_template_imscc,
        imscc_in_pack,
        legacy_module_pack_root,
        library_root,
        pack_looks_present,
        template_pack_paths,
    )

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

logger = logging.getLogger(__name__)

# Canvas MCF3M export is ~189MB; production packs can exceed 600MB.
IMSCC_MAX_BYTES = 800 * 1024 * 1024
PACK_STATUS_NAME = "install_status.json"
PACK_BUSY_STAGES = frozenset({"saving", "validating", "unpacking", "inventory"})

PLACEHOLDER_TYPES = {
    "Quizzes::Quiz",
    "Assignment",
    "DiscussionTopic",
    "ContextModuleSubHeader",
}

_FILEBASE_RE = re.compile(r"\$IMS-CC-FILEBASE\$", re.I)
_CANVAS_REF_RE = re.compile(r"\$CANVAS_COURSE_REFERENCE\$[^\"'\s]*", re.I)
_WEB_RES_RE = re.compile(
    r"""((?:src|href)\s*=\s*["'])(?:\.\./)*web_resources/""",
    re.I,
)


@dataclass(frozen=True)
class ModulePackPaths:
    """Resolved cartridge, unpacked tree, and inventory for one offering."""

    imscc: Path | None
    unpacked: Path
    inventory: Path
    preloaded: bool


def module_pack_root(
    data_dir: Path,
    offering_id: int,
    *,
    instance_relpath: str | None = None,
) -> Path:
    """Volume folder for this offering's Common Cartridge.

    Prefers ``{data_dir}/{instance_relpath}/pack`` when the offering has an
    instance tree. Falls back to ``module_packs/<id>`` so leftover Fly uploads
    still resolve during migration.

    Args:
        data_dir: LMS data directory (``/data`` on Fly, ``lms/data`` locally).
        offering_id: ``course_offerings.id``.
        instance_relpath: Stored instance folder, if any.
    """
    if instance_relpath:
        return Path(data_dir) / str(instance_relpath) / "pack"
    return legacy_module_pack_root(data_dir, int(offering_id))


def pack_status_path(dest_root: Path) -> Path:
    """Return the install-status JSON path for an offering pack folder.

    Args:
        dest_root: Offering pack folder (instance ``pack/`` or legacy ``module_packs/<id>/``).
    """
    return Path(dest_root) / PACK_STATUS_NAME


def write_pack_status(
    dest_root: Path,
    *,
    stage: str,
    detail: str,
    error: str | None = None,
) -> None:
    """Atomically write pack install progress for the staff upload UI to poll.

    Args:
        dest_root: Offering pack folder (instance ``pack/`` or legacy ``module_packs/<id>/``).
        stage: Machine-readable step (``saving``, ``unpacking``, ``done``, …).
        detail: Short teacher-facing sentence.
        error: Failure message when ``stage`` is ``error``.
    """
    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": stage not in {"error"},
        "stage": stage,
        "detail": detail,
        "error": error,
        "busy": stage in PACK_BUSY_STAGES,
        "updated_at": time.time(),
    }
    path = pack_status_path(dest_root)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.exception("Could not write module-pack status")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_pack_status(dest_root: Path) -> dict[str, Any]:
    """Load install status, or an idle payload when none has been written.

    Args:
        dest_root: Offering pack folder (instance ``pack/`` or legacy ``module_packs/<id>/``).
    """
    idle = {
        "ok": True,
        "stage": "idle",
        "detail": "",
        "error": None,
        "busy": False,
        "updated_at": None,
    }
    path = pack_status_path(Path(dest_root))
    if not path.is_file():
        return idle
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return idle
    if not isinstance(data, dict):
        return idle
    stage = str(data.get("stage") or "idle")
    return {
        "ok": bool(data.get("ok", stage != "error")),
        "stage": stage,
        "detail": str(data.get("detail") or ""),
        "error": data.get("error"),
        "busy": stage in PACK_BUSY_STAGES,
        "updated_at": data.get("updated_at"),
    }


def resolve_module_pack(
    ontario_code: str,
    offering_imscc: str | None,
    *,
    data_dir: Path | None = None,
    offering_id: int | None = None,
    instance_relpath: str | None = None,
    library_id: int | None = None,
    library_source: str | None = None,
) -> ModulePackPaths:
    """Resolve IMSCC / unpacked / inventory for one offering.

    Order: leftover instance ``pack/`` if present → leftover
    ``module_packs/<id>`` → shared ``libraries/<id>/`` (unpack lands here,
    never in git ``courses/``). New assigns have no instance pack.

    Args:
        ontario_code: Assigned Ontario course code.
        offering_imscc: Path stored on the offering, if any.
        data_dir: LMS data directory.
        offering_id: Offering primary key (legacy pack fallback).
        instance_relpath: Volume-relative instance folder.
        library_id: Shared ``content_libraries.id``.
        library_source: Library ``source_path`` (template IMSCC or upload).
    """
    code = (ontario_code or "").strip().upper()
    stored: Path | None = None
    if offering_imscc:
        raw = Path(offering_imscc)
        stored = raw if raw.is_absolute() else (REPO_ROOT / raw)
        if stored and is_template_imscc(stored):
            stored = None

    if data_dir is not None and instance_relpath:
        root = Path(data_dir) / str(instance_relpath) / "pack"
        if pack_looks_present(root):
            imscc = imscc_in_pack(root, str(stored) if stored else offering_imscc)
            if imscc is None and stored is not None and stored.is_file():
                imscc = stored
            return ModulePackPaths(
                imscc, root / "unpacked", root / "inventory.json", False
            )

    if stored is not None and stored.is_file() and data_dir is None:
        root = stored.parent
        return ModulePackPaths(
            stored, root / "unpacked", root / "inventory.json", False
        )

    if data_dir is not None and offering_id is not None:
        legacy = module_pack_root(data_dir, int(offering_id))
        if pack_looks_present(legacy):
            imscc = imscc_in_pack(legacy, offering_imscc)
            return ModulePackPaths(
                imscc, legacy / "unpacked", legacy / "inventory.json", False
            )

    if data_dir is not None and library_id:
        lib_root = library_root(data_dir, int(library_id))
        imscc: Path | None = None
        if library_source:
            src = Path(library_source)
            if src.is_file():
                imscc = src
        if imscc is None:
            imscc = imscc_in_pack(lib_root, offering_imscc)
        return ModulePackPaths(
            imscc, lib_root / "unpacked", lib_root / "inventory.json", False
        )

    empty = Path("/nonexistent-instance-pack") / (code or "none")
    return ModulePackPaths(None, empty / "unpacked", empty / "inventory.json", False)


def imscc_path_for_code(
    ontario_code: str,
    offering_imscc: str | None,
    *,
    data_dir: Path | None = None,
    offering_id: int | None = None,
    instance_relpath: str | None = None,
    library_id: int | None = None,
    library_source: str | None = None,
) -> Path | None:
    """Resolve a cartridge path from the instance pack (not the git template).

    Args:
        ontario_code: Assigned course code.
        offering_imscc: Relative or absolute path stored on the offering.
        data_dir: LMS data volume.
        offering_id: Offering primary key.
        instance_relpath: Stored instance folder.

    Returns:
        Path if the file exists, else None.
    """
    pack = resolve_module_pack(
        ontario_code,
        offering_imscc,
        data_dir=data_dir,
        offering_id=offering_id,
        instance_relpath=instance_relpath,
        library_id=library_id,
        library_source=library_source,
    )
    return pack.imscc if pack.imscc is not None and pack.imscc.is_file() else None


def offering_has_imscc(
    ontario_code: str,
    offering_imscc: str | None,
    *,
    data_dir: Path | None = None,
    offering_id: int | None = None,
    instance_relpath: str | None = None,
    library_id: int | None = None,
    library_source: str | None = None,
) -> bool:
    """Return True when Modules/Syllabus already have a readable .imscc."""
    pack = resolve_module_pack(
        ontario_code,
        offering_imscc,
        data_dir=data_dir,
        offering_id=offering_id,
        instance_relpath=instance_relpath,
        library_id=library_id,
        library_source=library_source,
    )
    return pack.imscc is not None and pack.imscc.is_file()


def unpacked_dir_for_code(
    ontario_code: str, *, content_root: str | None = None
) -> Path:
    """Template unpacked tree (read-only). Not a live write target.

    Args:
        ontario_code: Course code.
        content_root: Catalog ``content_root`` when known.
    """
    return template_pack_paths(ontario_code, content_root).unpacked


def inventory_path_for_code(
    ontario_code: str, *, content_root: str | None = None
) -> Path:
    """Template ``inventory.json`` (read-only).

    Args:
        ontario_code: Course code.
        content_root: Catalog ``content_root`` when known.
    """
    return template_pack_paths(ontario_code, content_root).inventory


def ensure_unpacked(imscc: Path, out_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Unpack the cartridge when the working tree is missing.

    Args:
        imscc: ``.imscc`` archive.
        out_dir: Destination (gitignored / volume).
        force: If True, replace an existing unpack (used after staff upload).

    Returns:
        Status dict: ``ok``, ``unpacked``, ``error``.
    """
    wiki = out_dir / "wiki_content"
    if wiki.is_dir() and not force:
        return {"ok": True, "unpacked": False, "error": None}
    if not imscc.is_file():
        return {"ok": False, "unpacked": False, "error": "IMSCC archive is not on this machine."}
    try:
        import canvas_unpack
    except ImportError:
        logger.exception("canvas_unpack import failed")
        return {"ok": False, "unpacked": False, "error": "Unpack tool is missing."}
    try:
        canvas_unpack.unpack_imscc(imscc, out_dir, clean=force)
    except Exception as exc:  # noqa: BLE001 — surface to the Modules empty state
        logger.exception("IMSCC unpack failed")
        return {"ok": False, "unpacked": False, "error": str(exc)}
    return {"ok": True, "unpacked": True, "error": None}


def write_pack_inventory(imscc: Path, unpacked: Path, inventory_path: Path) -> Path:
    """Write ``inventory.json`` used by the Modules left nav.

    Args:
        imscc: Cartridge file.
        unpacked: Unpacked working tree (preferred when ``imsmanifest.xml`` exists).
        inventory_path: Output JSON path.

    Returns:
        ``inventory_path``.
    """
    import canvas_inventory

    root = unpacked if (unpacked / "imsmanifest.xml").is_file() else None
    archive = imscc if imscc.is_file() else None
    with canvas_inventory.CourseSource(root=root, archive=archive) as source:
        inv = canvas_inventory.build_inventory(
            source,
            source_label="uploaded-imscc" if root is None else "unpacked",
            imscc_path=str(imscc) if archive else None,
            unpacked_path=str(unpacked) if root else None,
        )
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
    return inventory_path


def install_uploaded_module_pack(imscc: Path, dest_root: Path) -> dict[str, Any]:
    """Unpack a stored cartridge and write inventory so Modules and Syllabus load.

    Args:
        imscc: Stored ``.imscc`` file (usually ``dest_root/course.imscc``).
        dest_root: Offering folder on the data volume.

    Returns:
        Status dict with ``ok``, ``unpacked``, ``inventory``, ``error``.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    unpacked = dest_root / "unpacked"
    inventory_path = dest_root / "inventory.json"
    write_pack_status(
        dest_root,
        stage="unpacking",
        detail="Unpacking Common Cartridge… this can take a few minutes",
    )
    status = ensure_unpacked(imscc, unpacked, force=True)
    if not status.get("ok"):
        err = str(status.get("error") or "Could not unpack that module pack.")
        write_pack_status(dest_root, stage="error", detail=err, error=err)
        return {**status, "inventory": None}
    write_pack_status(dest_root, stage="inventory", detail="Writing module inventory…")
    try:
        write_pack_inventory(imscc, unpacked, inventory_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("inventory after IMSCC upload failed")
        write_pack_status(dest_root, stage="error", detail=str(exc), error=str(exc))
        return {
            "ok": False,
            "unpacked": True,
            "error": str(exc),
            "inventory": None,
        }
    return {
        "ok": True,
        "unpacked": True,
        "error": None,
        "inventory": str(inventory_path),
    }


def validate_imscc_upload(filename: str, path: Path, *, max_bytes: int = IMSCC_MAX_BYTES) -> None:
    """Raise ``ValueError`` if ``path`` is not a reasonable Common Cartridge.

    Args:
        filename: Original upload name (extension check).
        path: Saved file on disk.
        max_bytes: Size ceiling.

    Raises:
        ValueError: Type, size, ZIP, or missing ``imsmanifest.xml``.
    """
    name = (filename or "").lower().strip()
    if not (name.endswith(".imscc") or name.endswith(".zip")):
        raise ValueError("Module pack must be a .imscc (or .zip) Common Cartridge.")
    if not path.is_file():
        raise ValueError("Upload did not land on disk.")
    size = path.stat().st_size
    if size == 0:
        raise ValueError("That file is empty.")
    if size > max_bytes:
        raise ValueError(
            f"Module pack is too large (max {max_bytes // (1024 * 1024)} MB)."
        )
    with path.open("rb") as handle:
        header = handle.read(4)
    if header[:2] != b"PK":
        raise ValueError("That file is not a ZIP/IMSCC archive.")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a readable ZIP/IMSCC archive.") from exc
    if not any(n.rstrip("/").endswith("imsmanifest.xml") for n in names):
        raise ValueError(
            "That archive is missing imsmanifest.xml (not a Common Cartridge)."
        )


def store_uploaded_module_pack(
    file_storage: Any,
    dest_root: Path,
    *,
    max_bytes: int = IMSCC_MAX_BYTES,
) -> Path:
    """Validate and store an IMSCC upload without unpacking it.

    Args:
        file_storage: Werkzeug ``FileStorage`` from the upload form.
        dest_root: Offering folder (``module_packs/<id>/``).
        max_bytes: Size ceiling.

    Returns:
        Path to the stored ``course.imscc``.

    Raises:
        ValueError: Invalid type, size, or cartridge contents.
    """
    if file_storage is None or not getattr(file_storage, "filename", None):
        raise ValueError("Choose a .imscc module pack to upload.")
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / "course.imscc"
    tmp = dest.with_suffix(".imscc.part")
    if tmp.exists():
        tmp.unlink()
    write_pack_status(dest_root, stage="saving", detail="Saving upload to disk…")
    try:
        file_storage.save(tmp)
        write_pack_status(
            dest_root, stage="validating", detail="Checking Common Cartridge…"
        )
        validate_imscc_upload(str(file_storage.filename), tmp, max_bytes=max_bytes)
        os.replace(tmp, dest)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        message = str(exc) if isinstance(exc, ValueError) else "Could not store that module pack."
        write_pack_status(dest_root, stage="error", detail=message, error=message)
        raise
    return dest


def save_uploaded_module_pack(
    file_storage: Any,
    dest_root: Path,
    *,
    max_bytes: int = IMSCC_MAX_BYTES,
) -> Path:
    """Validate, store, unpack, and inventory a staff IMSCC upload.

    Args:
        file_storage: Werkzeug ``FileStorage`` from the upload form.
        dest_root: Offering folder (``module_packs/<id>/``).
        max_bytes: Size ceiling.

    Returns:
        Path to the stored ``course.imscc``.

    Raises:
        ValueError: Invalid type, size, or cartridge contents.
    """
    dest = store_uploaded_module_pack(
        file_storage, dest_root, max_bytes=max_bytes
    )
    status = install_uploaded_module_pack(dest, dest_root)
    if not status.get("ok"):
        raise ValueError(status.get("error") or "Could not unpack that module pack.")
    write_pack_status(dest_root, stage="done", detail="Module pack installed.")
    return dest


def load_inventory_file(path: Path) -> dict[str, Any] | None:
    """Load a module-nav ``inventory.json`` from disk.

    Args:
        path: Inventory JSON path.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_inventory(ontario_code: str) -> dict[str, Any] | None:
    """Load the git template ``inventory.json`` (not the live instance pack).

    Args:
        ontario_code: Course code.
    """
    return load_inventory_file(inventory_path_for_code(ontario_code))


def module_nav(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten inventory modules into left-nav entries.

    Args:
        inventory: Parsed ``inventory.json``.
    """
    nav = []
    for module in inventory.get("modules") or []:
        items = []
        for item in module.get("items") or []:
            content_type = str(item.get("content_type") or "")
            href = item.get("href")
            kind = "page"
            if content_type in {"Quizzes::Quiz", "Assignment", "DiscussionTopic"}:
                kind = "placeholder"
            elif content_type == "ContextModuleSubHeader":
                kind = "header"
            elif content_type == "Attachment" or (href and str(href).startswith("web_resources/")):
                kind = "file"
            items.append(
                {
                    "title": item.get("title") or "Untitled",
                    "content_type": content_type,
                    "href": href,
                    "kind": kind,
                    "identifier": item.get("identifier"),
                }
            )
        nav.append(
            {
                "title": module.get("title") or "Module",
                "identifier": module.get("identifier"),
                "items": items,
            }
        )
    return nav


def rewrite_wiki_html(
    raw: str, ontario_code: str, *, files_root: str | None = None
) -> str:
    """Rewrite Canvas IMSCC tokens and relative asset URLs for LLOVES.

    Args:
        raw: Unpacked wiki HTML.
        ontario_code: Course code used in default file URLs.
        files_root: Prefix for ``web_resources`` (class-scoped staff route).
    """
    root = files_root or f"/lms/modules/{ontario_code}/files/web_resources"
    html_out = _FILEBASE_RE.sub(root, raw)
    html_out = _CANVAS_REF_RE.sub("#", html_out)
    html_out = _WEB_RES_RE.sub(rf"\1{root}/", html_out)
    return html_out


def wrap_page(title: str, inner_html: str) -> str:
    """Minimal chrome around unpacked wiki HTML (no Canvas RCE).

    Args:
        title: Document title.
        inner_html: Rewritten page HTML (may include its own html/body).
    """
    # Prefer the original body when present so we do not nest documents.
    body_match = re.search(r"<body[^>]*>(.*)</body>", inner_html, re.I | re.S)
    body = body_match.group(1) if body_match else inner_html
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{safe_title}</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; color: #1e293b;
         margin: 0; padding: 1rem 1.25rem 2rem; background: #fff; }}
  img, video {{ max-width: 100%; height: auto; }}
  a {{ color: #0f766e; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def placeholder_html(title: str, kind: str) -> str:
    """Quiz/assignment/discussion stand-in for LLOVES v1.

    Args:
        title: Inventory item title.
        kind: Canvas content type.
    """
    label = {
        "Quizzes::Quiz": "native quiz not in LLOVES v1",
        "Assignment": "native assignment not in LLOVES v1",
        "DiscussionTopic": "native discussion not in LLOVES v1",
    }.get(kind, "not in LLOVES v1")
    return wrap_page(
        title,
        f"<h1>{html.escape(title)}</h1><p>{html.escape(title)} — {html.escape(label)}.</p>",
    )


def safe_unpacked_file(unpacked: Path, rel: str) -> Path | None:
    """Resolve a relative path inside the unpacked tree, or None if unsafe.

    Args:
        unpacked: Unpacked IMSCC root.
        rel: URL path after the files prefix.
    """
    raw = unquote(rel or "").lstrip("/")
    candidate = (unpacked / raw).resolve()
    try:
        candidate.relative_to(unpacked.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None
