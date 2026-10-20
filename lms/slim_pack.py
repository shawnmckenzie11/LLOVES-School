"""Slim Common Cartridge builder and lean streaming materializer.

IT course assignment only needs the module outline, quiz metadata, QTI banks,
wiki pages, and the images those files actually reference. A full Canvas
export also carries video and unused ``web_resources``. Copying that tree
peaks at several times the pack size on a 1 GB machine.

:func:`build_slim_imscc` writes a new ``.imscc`` at the zip root with the
extra files removed. :func:`materialize_import_tree` is what install uses:
it streams the members ingest reads (including assignments and referenced
PDFs) and leaves unreferenced media inside the archive.
"""

from __future__ import annotations

import os
import re
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

# Extensions the importer treats as pictures embedded in stems and pages.
IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}
)
_TEXT_SUFFIXES = frozenset({".xml", ".html", ".htm", ".qti", ".txt"})
_BANK_SUFFIXES = frozenset({".xml", ".qti", ".html", ".htm"})
_CHUNK = 1024 * 1024

# ``$IMS-CC-FILEBASE$`` is the cartridge root for web_resources. Canvas also
# percent-encodes the token, and pages link with ``../web_resources/...``.
_MEDIA_REF_RE = re.compile(
    r"""(?ix)
    (?:\$IMS-CC-FILEBASE\$|%24IMS-CC-FILEBASE%24)/+
    (?P<filebase>[^\s"'<>?#]+)
    |
    (?:(?:\.\./)+)?
    (?P<web>web_resources/[^\s"'<>?#]+)
    """
)
_ENTITY_TAIL_RE = re.compile(r"&(?:gt|lt|quot|amp|nbsp);?$", re.I)


@dataclass(frozen=True)
class SlimPackResult:
    """What a slim-pack build kept and how large the archives are."""

    kept: tuple[str, ...]
    dropped_members: int
    source_bytes: int
    output_bytes: int
    output_path: Path


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag name."""
    return tag.rsplit("}", 1)[-1]


def _safe_rel(name: str) -> str | None:
    """Return a zip member as a relative path, or None if it is unsafe.

    Args:
        name: Raw zip member name.
    """
    rel = name.replace("\\", "/").lstrip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel.endswith("/"):
        return None
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return rel


def _root_prefix(rels: list[str]) -> str:
    """Return a single wrapper directory to strip, including the slash.

    Canvas sometimes nests the cartridge one folder down. Install and the
    slim archive both expect ``imsmanifest.xml`` at the root.

    Args:
        rels: Safe relative member names.
    """
    if not rels:
        return ""
    tops = {rel.split("/", 1)[0] for rel in rels}
    if len(tops) != 1:
        return ""
    top = next(iter(tops))
    if f"{top}/imsmanifest.xml" not in rels:
        return ""
    return f"{top}/"


def _clean_ref(raw: str) -> str:
    """Normalize one media path found in HTML or XML.

    Args:
        raw: Path text from a ``src`` or ``href``.
    """
    text = unquote(raw or "").replace("\\", "/").split("?", 1)[0].strip()
    while text.startswith("./"):
        text = text[2:]
    text = _ENTITY_TAIL_RE.sub("", text).rstrip(").,;")
    return text


def referenced_media_paths(text: str) -> set[str]:
    """Return cartridge-relative media paths mentioned in HTML or XML.

    ``$IMS-CC-FILEBASE$/diagram.png`` is rooted at ``web_resources/``.
    Paths that already start with ``web_resources/`` are kept as written.

    Args:
        text: Wiki HTML, QTI, or quiz metadata.
    """
    found: set[str] = set()
    for match in _MEDIA_REF_RE.finditer(text or ""):
        web = match.group("web")
        filebase = match.group("filebase")
        if web:
            path = _clean_ref(web)
        elif filebase:
            path = _clean_ref(filebase)
            if path.startswith("web_resources/"):
                pass
            elif path:
                path = f"web_resources/{path.lstrip('/')}"
            else:
                continue
        else:
            continue
        if not path or ".." in path.split("/"):
            continue
        found.add(path)
    return found


def _is_image(rel: str) -> bool:
    """Return True when ``rel`` has an image suffix."""
    return Path(rel).suffix.lower() in IMAGE_SUFFIXES


def _is_scannable(rel: str) -> bool:
    """Return True when a member is text the reference scan should read."""
    suffixes = "".join(Path(rel).suffixes).lower()
    if suffixes.endswith(".xml.qti") or suffixes.endswith(".qti"):
        return True
    return Path(rel).suffix.lower() in _TEXT_SUFFIXES


def _quiz_dirs(rels: list[str]) -> set[str]:
    """Return directories that contain ``assessment_meta.xml``.

    Args:
        rels: Cartridge-relative member names.
    """
    found: set[str] = set()
    for rel in rels:
        if rel.endswith("/assessment_meta.xml"):
            found.add(rel.rsplit("/", 1)[0])
    return found


def _assignment_dirs(rels: list[str], quiz_dirs: set[str]) -> set[str]:
    """Return directories that contain ``assignment_settings.xml``.

    Args:
        rels: Cartridge-relative member names.
        quiz_dirs: Quiz directories, which are not treated as assignments.
    """
    found: set[str] = set()
    for rel in rels:
        if not rel.endswith("/assignment_settings.xml"):
            continue
        parent = rel.rsplit("/", 1)[0]
        if parent not in quiz_dirs:
            found.add(parent)
    return found


def _structural_member(
    rel: str,
    *,
    quiz_dirs: set[str],
    assignment_dirs: set[str],
    include_assignments: bool,
) -> bool:
    """Return True when ingest needs this member regardless of media refs.

    Args:
        rel: Cartridge-relative path.
        quiz_dirs: Folders that hold ``assessment_meta.xml``.
        assignment_dirs: Folders that hold ``assignment_settings.xml``.
        include_assignments: Full import keeps assignment bodies. The slim
            pack does not.
    """
    if rel in {
        "imsmanifest.xml",
        "course_settings/module_meta.xml",
        "course_settings/course_settings.xml",
    }:
        return True
    if rel.startswith("non_cc_assessments/"):
        suffixes = "".join(Path(rel).suffixes).lower()
        if suffixes.endswith(".xml.qti") or suffixes.endswith(".qti"):
            return True
        return Path(rel).suffix.lower() in _BANK_SUFFIXES
    if rel.startswith("wiki_content/") and Path(rel).suffix.lower() in {".html", ".htm"}:
        # ``LibraryIngestor._ingest_wiki_pages`` reads these HTML files.
        return True
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    base = rel.rsplit("/", 1)[-1].lower()
    if parent in quiz_dirs:
        if base in {"assessment_meta.xml", "assessment_qti.xml"}:
            return True
        # Per-question metadata lives beside assessment_meta.xml.
        suffixes = "".join(Path(rel).suffixes).lower()
        if suffixes.endswith(".xml.qti") or base.endswith(".qti"):
            return True
        return Path(rel).suffix.lower() == ".xml"
    if include_assignments and parent in assignment_dirs:
        return base == "assignment_settings.xml" or base.endswith(".html")
    return False


def _scan_member(archive: zipfile.ZipFile, zip_name: str) -> set[str]:
    """Stream one text member and collect media paths it mentions.

    Args:
        archive: Open cartridge.
        zip_name: Member name inside ``archive``.
    """
    found: set[str] = set()
    tail = ""
    with archive.open(zip_name, "r") as src:
        while True:
            raw = src.read(256 * 1024)
            if not raw:
                break
            text = tail + raw.decode("utf-8", errors="replace")
            found |= referenced_media_paths(text)
            tail = text[-1024:]
    return found


def _manifest_media_refs(manifest_xml: str, module_xml: str) -> set[str]:
    """Return ``web_resources`` hrefs that module items reach via the manifest.

    A PDF attached as a module item is not always mentioned in HTML. The
    item ``identifierref`` points at a manifest resource whose href is the
    file. Those paths are referenced media.

    Args:
        manifest_xml: ``imsmanifest.xml`` text.
        module_xml: ``course_settings/module_meta.xml`` text, possibly empty.
    """
    resources: dict[str, list[str]] = {}
    try:
        root = ET.fromstring(manifest_xml)
    except ET.ParseError:
        return set()
    for element in root.iter():
        if _local(element.tag) != "resource":
            continue
        rid = element.get("identifier")
        if not rid:
            continue
        paths: list[str] = []
        href = element.get("href")
        if href:
            paths.append(_safe_rel(href) or "")
        for child in element:
            if _local(child.tag) == "file" and child.get("href"):
                paths.append(_safe_rel(child.get("href") or "") or "")
        resources[rid] = [path for path in paths if path]
    if not module_xml:
        return set()
    try:
        meta = ET.fromstring(module_xml)
    except ET.ParseError:
        return set()
    wanted: set[str] = set()
    for element in meta.iter():
        if _local(element.tag) != "identifierref":
            continue
        ref = (element.text or "").strip()
        for path in resources.get(ref, []):
            if path.startswith("web_resources/"):
                wanted.add(path)
    return wanted


def _read_text_member(archive: zipfile.ZipFile, zip_name: str) -> str:
    """Read one zip member as UTF-8 text, replacing bad bytes.

    Args:
        archive: Open cartridge.
        zip_name: Member to read.
    """
    with archive.open(zip_name, "r") as src:
        return src.read().decode("utf-8", errors="replace")


def _rewrite_manifest(manifest_xml: str, kept: set[str]) -> bytes:
    """Drop manifest resources and file entries that are not in ``kept``.

    Args:
        manifest_xml: Original ``imsmanifest.xml`` text.
        kept: Cartridge-relative paths the slim archive contains.
    """
    try:
        root = ET.fromstring(manifest_xml)
    except ET.ParseError:
        return manifest_xml.encode("utf-8")
    original_ids: set[str] = set()
    for element in root.iter():
        if _local(element.tag) == "resource" and element.get("identifier"):
            original_ids.add(element.get("identifier") or "")
    for parent in root.iter():
        for child in list(parent):
            if _local(child.tag) != "resource":
                continue
            href = _safe_rel(child.get("href") or "") or ""
            for file_node in list(child):
                if _local(file_node.tag) != "file":
                    continue
                file_href = _safe_rel(file_node.get("href") or "") or ""
                if file_href not in kept:
                    child.remove(file_node)
            kept_files = [
                node
                for node in child
                if _local(node.tag) == "file" and node.get("href")
            ]
            if href in kept or kept_files:
                continue
            parent.remove(child)
    kept_ids = {
        element.get("identifier") or ""
        for element in root.iter()
        if _local(element.tag) == "resource" and element.get("identifier")
    }
    removed = original_ids - kept_ids

    def prune(parent: ET.Element) -> None:
        """Remove organization items whose resource was dropped."""
        for child in list(parent):
            if _local(child.tag) == "item":
                ref = child.get("identifierref")
                if ref and ref in removed:
                    parent.remove(child)
                    continue
            prune(child)

    prune(root)
    ET.register_namespace("", "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


@dataclass
class _Plan:
    """Members to write, keyed by cartridge-relative path."""

    rel_to_zip: dict[str, str]
    keep: list[str]
    manifest_xml: bytes | None
    dropped_members: int


def _plan_archive(archive: zipfile.ZipFile, *, slim: bool) -> _Plan:
    """Choose zip members for a slim archive or a lean import tree.

    Args:
        archive: Open ``.imscc``.
        slim: When True, drop assignments and non-image media and rewrite
            the manifest. When False, keep every file ingest reads.
    """
    rel_to_zip: dict[str, str] = {}
    for info in archive.infolist():
        rel = _safe_rel(info.filename)
        if rel is None:
            continue
        rel_to_zip[rel] = info.filename
    prefix = _root_prefix(list(rel_to_zip))
    if prefix:
        stripped: dict[str, str] = {}
        for rel, zip_name in rel_to_zip.items():
            if rel.startswith(prefix):
                stripped[rel[len(prefix) :]] = zip_name
        rel_to_zip = stripped
    if "imsmanifest.xml" not in rel_to_zip:
        raise ValueError(
            "That archive is missing imsmanifest.xml (not a Common Cartridge)."
        )
    rels = list(rel_to_zip)
    quiz_dirs = _quiz_dirs(rels)
    assignment_dirs = _assignment_dirs(rels, quiz_dirs)
    structural = [
        rel
        for rel in rels
        if _structural_member(
            rel,
            quiz_dirs=quiz_dirs,
            assignment_dirs=assignment_dirs,
            include_assignments=not slim,
        )
    ]
    referenced: set[str] = set()
    for rel in structural:
        # The manifest lists every exported file. That index is not a
        # content reference — only module items and kept HTML/QTI are.
        if rel == "imsmanifest.xml":
            continue
        if _is_scannable(rel):
            referenced |= _scan_member(archive, rel_to_zip[rel])
    manifest_xml = _read_text_member(archive, rel_to_zip["imsmanifest.xml"])
    module_rel = "course_settings/module_meta.xml"
    module_xml = ""
    if module_rel in rel_to_zip:
        module_xml = _read_text_member(archive, rel_to_zip[module_rel])
    referenced |= _manifest_media_refs(manifest_xml, module_xml)
    media: list[str] = []
    for rel in rels:
        if rel not in referenced:
            continue
        if slim and not _is_image(rel):
            continue
        media.append(rel)
    keep = list(dict.fromkeys([*structural, *media]))
    rewritten: bytes | None = None
    if slim:
        rewritten = _rewrite_manifest(manifest_xml, set(keep))
    dropped = len(rels) - len(keep)
    return _Plan(
        rel_to_zip=rel_to_zip,
        keep=sorted(keep),
        manifest_xml=rewritten,
        dropped_members=max(dropped, 0),
    )


def _stream_member(archive: zipfile.ZipFile, zip_name: str, target: Path) -> None:
    """Copy one zip member to ``target`` in chunks.

    Args:
        archive: Open cartridge.
        zip_name: Member to read.
        target: Destination file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        with archive.open(zip_name, "r") as src, partial.open("wb") as out:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(partial, target)
    except (zipfile.BadZipFile, zlib.error, RuntimeError, OSError):
        if partial.exists():
            partial.unlink()
        raise
    finally:
        if partial.exists():
            partial.unlink()


def _contained(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``root``, rejecting paths that escape it.

    Args:
        root: Directory that must contain the result.
        rel: Cartridge-relative path.
    """
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def materialize_import_tree(
    imscc_path: Path,
    out_dir: Path,
    *,
    clean: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Stream the members a full import needs into ``out_dir``.

    Unreferenced ``web_resources`` stay in the zip and are not written to
    disk. Assignments, quizzes, banks, wiki pages, and referenced files
    (including a PDF a module item points at) are written so ingest matches
    a full unpack for everything it reads.

    Args:
        imscc_path: Stored ``.imscc`` archive.
        out_dir: Destination working tree.
        clean: When True, replace an existing tree first.
        progress: Optional ``(done, total, member_name)`` callback.

    Returns:
        Number of members written.

    Raises:
        FileNotFoundError: The archive is missing.
        zipfile.BadZipFile: The archive or a member cannot be read.
        ValueError: The archive has no ``imsmanifest.xml``.
    """
    import shutil

    imscc_path = Path(imscc_path)
    out_dir = Path(out_dir)
    if not imscc_path.is_file():
        raise FileNotFoundError(f"IMSCC not found: {imscc_path}")
    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(imscc_path, "r") as archive:
        plan = _plan_archive(archive, slim=False)
        total = len(plan.keep)
        for index, rel in enumerate(plan.keep, start=1):
            target = _contained(out_dir, rel)
            if target is None:
                continue
            try:
                _stream_member(archive, plan.rel_to_zip[rel], target)
            except (zipfile.BadZipFile, zlib.error, RuntimeError, OSError) as exc:
                raise zipfile.BadZipFile(
                    f"Could not decompress '{rel}' in the cartridge: {exc}"
                ) from exc
            written += 1
            if progress is not None:
                progress(index, total, rel)
    return written


def build_slim_imscc(source: Path, dest: Path) -> SlimPackResult:
    """Write a slim ``.imscc`` that keeps only what IT assignment imports.

    Kept at the zip root: ``imsmanifest.xml`` (resources and file entries
    limited to kept paths), ``course_settings/module_meta.xml``,
    ``course_settings/course_settings.xml``, ``non_cc_assessments`` QTI
    banks, quiz folders (``assessment_meta.xml``, ``assessment_qti.xml``,
    and other per-question XML metadata), wiki HTML (the importer reads
    it), and images those files reference.

    Dropped: learning outcomes, ``assignment_groups.xml``, ``rubrics.xml``,
    assignment folders, and every other ``web_resources`` file.

    Args:
        source: Full Canvas ``.imscc`` or ``.zip``.
        dest: Where to write the slim archive.

    Returns:
        Counts and paths for the build.

    Raises:
        FileNotFoundError: ``source`` does not exist.
        ValueError: ``source`` is not a Common Cartridge.
        zipfile.BadZipFile: ``source`` is not a readable zip.
    """
    source = Path(source)
    dest = Path(dest)
    if not source.is_file():
        raise FileNotFoundError(f"IMSCC not found: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(
            partial, "w", compression=zipfile.ZIP_DEFLATED
        ) as out:
            plan = _plan_archive(archive, slim=True)
            for rel in plan.keep:
                if rel == "imsmanifest.xml":
                    continue
                with archive.open(plan.rel_to_zip[rel], "r") as src, out.open(
                    rel, "w"
                ) as target:
                    while True:
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        target.write(chunk)
            manifest = plan.manifest_xml
            if manifest is None:
                raise ValueError("Slim pack is missing a rewritten manifest.")
            out.writestr("imsmanifest.xml", manifest)
            kept = tuple(plan.keep)
            dropped = plan.dropped_members
        os.replace(partial, dest)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise
    return SlimPackResult(
        kept=kept,
        dropped_members=dropped,
        source_bytes=source.stat().st_size,
        output_bytes=dest.stat().st_size,
        output_path=dest,
    )
