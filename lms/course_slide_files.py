"""Turn an uploaded PowerPoint into page images on the media volume.

The upload and the rendered pages stay files under the class image
folder. SQLite stores a path, never the pptx or the PNG bytes. Conversion
runs LibreOffice headless, then the same PDF-to-PNG path as a Google
Slides export.

External and linked relationships are removed before LibreOffice runs.
The process is started in its own session so a timeout kills ``soffice``
and ``soffice.bin`` together, and it is placed in a network namespace
with ``/data`` hidden when ``unshare`` can do that.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# The sample deck is 21.7 MB. 64 MiB leaves room above that without
# accepting an unbounded body. Flask's global limit is the IMSCC cap,
# so the upload handler has to enforce this itself.
COURSE_SLIDE_UPLOAD_MAX_BYTES = 64 * 1024 * 1024
# Multipart framing around a 64 MiB file. The header check uses this
# slack so a real deck is not refused before its bytes are read.
UPLOAD_FRAMING_SLACK = 1024 * 1024
# Soffice plus PNG render. The import job keeps the whole attempt
# under about 90 seconds, inside the worker timeout.
_CONVERT_TIMEOUT_S = 80
_SOFFICE_LIMIT_S = 70
_READ_CHUNK = 65536
_RELS_MAX_BYTES = 1_000_000
_MEMBER_MAX_BYTES = 80 * 1024 * 1024
_PACKAGE_MAX_BYTES = 256 * 1024 * 1024
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_EXTERNAL_TARGET = re.compile(r"^(?:file|https?):", re.IGNORECASE)
_sandbox_lock = threading.Lock()
_sandbox_prefix: list[str] | None = None


def upload_content_length_allowed(content_length: int | None) -> bool:
    """False when the declared body is past the slide cap plus framing.

    Uses the Content-Length header only. Callers must run this before
    reading the body. A missing length falls through to the capped reader.

    Args:
        content_length: ``Content-Length`` in bytes, or None.
    """
    if content_length is None:
        return True
    try:
        size = int(content_length)
    except (TypeError, ValueError):
        return True
    return size <= COURSE_SLIDE_UPLOAD_MAX_BYTES + UPLOAD_FRAMING_SLACK


def read_capped_upload(uploaded: object, limit: int) -> bytes:
    """Read an upload and stop once it passes ``limit``.

    Args:
        uploaded: File-like object with ``read``.
        limit: Maximum accepted size in bytes.

    Returns:
        The full body when it fits.

    Raises:
        ValueError: The body is empty or larger than ``limit``.
    """
    read = getattr(uploaded, "read", None)
    if not callable(read):
        raise ValueError("Choose a PowerPoint file.")
    chunks: list[bytes] = []
    total = 0
    cap = max(0, int(limit))
    while True:
        block = read(_READ_CHUNK)
        if not block:
            break
        if not isinstance(block, (bytes, bytearray)):
            raise ValueError("Choose a PowerPoint file.")
        total += len(block)
        if total > cap:
            raise ValueError("That file is too large to attach.")
        chunks.append(bytes(block))
    if total < 1:
        raise ValueError("Choose a PowerPoint file.")
    return b"".join(chunks)


def write_capped_upload(uploaded: object, limit: int, dest: object) -> None:
    """Stream an upload to ``dest`` and stop once it passes ``limit``.

    The overflowing chunk is not written. Callers delete ``dest`` when
    the import finishes. The bytes are not kept on the job row.

    Args:
        uploaded: File-like object with ``read``.
        limit: Maximum accepted size in bytes.
        dest: Binary file opened for writing.

    Raises:
        ValueError: The body is empty or larger than ``limit``.
    """
    read = getattr(uploaded, "read", None)
    write = getattr(dest, "write", None)
    if not callable(read) or not callable(write):
        raise ValueError("Choose a PowerPoint file.")
    total = 0
    cap = max(0, int(limit))
    while True:
        block = read(_READ_CHUNK)
        if not block:
            break
        if not isinstance(block, (bytes, bytearray)):
            raise ValueError("Choose a PowerPoint file.")
        total += len(block)
        if total > cap:
            raise ValueError("That file is too large to attach.")
        write(bytes(block))
    if total < 1:
        raise ValueError("Choose a PowerPoint file.")


def office_suffix(filename: str) -> str:
    """Return ``.ppt`` or ``.pptx`` for an upload name.

    Args:
        filename: Original client filename. It is not passed to a shell.

    Raises:
        ValueError: The name is not a PowerPoint file.
    """
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in {".ppt", ".pptx"}:
        raise ValueError("Upload a .ppt or .pptx file.")
    return suffix


def office_bytes_ok(raw: bytes, suffix: str) -> None:
    """Reject a body that is not a PowerPoint package.

    Args:
        raw: Upload bytes.
        suffix: ``.ppt`` or ``.pptx``.

    Raises:
        ValueError: The bytes do not match that package.
    """
    if suffix == ".pptx":
        if not raw.startswith(b"PK"):
            raise ValueError("That file is not a PowerPoint deck.")
        return
    if not raw.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("That file is not a PowerPoint deck.")


def slide_deck_title(raw: str, *, fallback: str) -> str:
    """One short deck name for the Set Class summary.

    Args:
        raw: Filename stem or other label.
        fallback: Used when ``raw`` is blank.

    Returns:
        A single line, at most 120 characters.
    """
    text = " ".join(str(raw or "").replace("\\", " ").replace("/", " ").split())
    if not text:
        text = fallback
    return text[:120]


def soffice_bin() -> str:
    """Path to headless LibreOffice, or empty when it is not installed."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def strip_external_relationships(raw: bytes) -> bytes:
    """Drop external and linked relationships from a pptx package.

    LibreOffice fetches ``TargetMode=External`` targets, including
    ``http://127.0.0.1`` and ``file:`` pictures, while converting. Those
    relationships are removed. Internal slide parts stay. Members are
    not fully loaded except ``.rels`` parts, and a member whose
    uncompressed size is huge is refused before it is read.

    Args:
        raw: pptx bytes that already start with ``PK``.

    Returns:
        A pptx package with those relationships removed.

    Raises:
        ValueError: The package is not a readable pptx, or a member is
            too large.
    """
    try:
        source = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a PowerPoint deck.") from exc
    infos = source.infolist()
    total = 0
    for info in infos:
        _reject_zip_member(info)
        total += int(info.file_size)
        if total > _PACKAGE_MAX_BYTES:
            raise ValueError("That file is too large to attach.")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dest:
        for info in infos:
            name = info.filename
            if name.endswith("/"):
                continue
            if name.lower().endswith(".rels"):
                if info.file_size > _RELS_MAX_BYTES:
                    raise ValueError("That file is not a PowerPoint deck.")
                data = source.read(info)
                if len(data) > _RELS_MAX_BYTES:
                    raise ValueError("That file is not a PowerPoint deck.")
                dest.writestr(name, _strip_rels_xml(data))
                continue
            with source.open(info, "r") as src, dest.open(name, "w") as copied:
                shutil.copyfileobj(src, copied, length=_READ_CHUNK)
    return out.getvalue()


def _reject_zip_member(info: zipfile.ZipInfo) -> None:
    """Refuse a zip member that could escape the package or expand hugely.

    Args:
        info: One entry from the pptx.

    Raises:
        ValueError: The name or the uncompressed size is not safe to copy.
    """
    name = str(info.filename or "")
    parts = Path(name).parts
    if (
        not name
        or name.startswith("/")
        or name.startswith("\\")
        or ".." in parts
        or "\\" in name
    ):
        raise ValueError("That file is not a PowerPoint deck.")
    if info.flag_bits & 0x1:
        raise ValueError("That file is not a PowerPoint deck.")
    if int(info.file_size) > _MEMBER_MAX_BYTES:
        raise ValueError("That file is too large to attach.")


def _relationship_is_external(element: ET.Element) -> bool:
    """True when a relationship points outside the package.

    Args:
        element: One ``Relationship`` element.
    """
    mode = str(element.get("TargetMode") or "").strip().lower()
    target = str(element.get("Target") or "").strip()
    if mode == "external":
        return True
    if _EXTERNAL_TARGET.match(target):
        return True
    lowered = target.lower()
    return (
        lowered.startswith("file:")
        or lowered.startswith("http:")
        or lowered.startswith("https:")
    )


def _strip_rels_xml(data: bytes) -> bytes:
    """Return ``.rels`` XML with external relationships removed.

    Args:
        data: One ``.rels`` part, already size-capped.

    Raises:
        ValueError: The part is not relationship XML.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("That file is not a PowerPoint deck.") from exc
    removed = False
    for child in list(root):
        if not str(child.tag).endswith("Relationship"):
            continue
        if _relationship_is_external(child):
            root.remove(child)
            removed = True
    if not removed:
        return data
    ET.register_namespace("", _REL_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def pages_from_office(
    raw: bytes, suffix: str, *, timeout: float | None = None
) -> tuple[list[bytes], bool]:
    """Render a PowerPoint upload to one PNG per page.

    Args:
        raw: ppt or pptx bytes. Already size-checked.
        suffix: ``.ppt`` or ``.pptx``.
        timeout: Seconds for conversion and rendering together. The
            default stays under the import job budget.

    Returns:
        ``(png_pages, truncated)``. ``truncated`` is true past the page cap.

    Raises:
        ValueError: LibreOffice is missing, the file is not a deck, or
            conversion did not finish.
    """
    office_bytes_ok(raw, suffix)
    if suffix == ".pptx":
        raw = strip_external_relationships(raw)
    binary = soffice_bin()
    if not binary:
        raise ValueError("PowerPoint import is not available on this server.")
    from google_slides_import import render_pdf_pages

    budget = _CONVERT_TIMEOUT_S if timeout is None else max(1.0, float(timeout))
    started = time.monotonic()
    pdf = _office_to_pdf(raw, suffix, binary, timeout=min(_SOFFICE_LIMIT_S, budget))
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise ValueError("That deck took too long to attach.")
    return render_pdf_pages(pdf, deadline=time.monotonic() + remaining)


def sandbox_prefix() -> list[str]:
    """``unshare`` argv that drops the network and hides ``/data``.

    Empty when ``unshare`` is missing or the probe cannot start a user
    and mount namespace. Callers still strip external relationships.

    Returns:
        Argv to place in front of the LibreOffice command, or ``[]``.
    """
    global _sandbox_prefix
    with _sandbox_lock:
        if _sandbox_prefix is None:
            _sandbox_prefix = _probe_sandbox()
        return list(_sandbox_prefix)


def _probe_sandbox() -> list[str]:
    """Try one network-isolated mount namespace.

    Returns:
        The ``unshare`` prefix, or an empty list.
    """
    unshare = shutil.which("unshare")
    if not unshare:
        return []
    hide = (
        "if [ -d /data ]; then "
        "mount -t tmpfs -o ro,size=64k,mode=000 tmpfs /data || exit 97; "
        "fi"
    )
    try:
        probe = subprocess.run(
            [unshare, "--net", "--map-root-user", "--mount", "sh", "-c", hide],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if probe.returncode != 0:
        return []
    return [
        unshare,
        "--net",
        "--map-root-user",
        "--mount",
        "sh",
        "-c",
        hide + '; exec "$@"',
        "sh",
    ]


def run_grouped(
    command: list[str],
    *,
    timeout: float,
    env: dict[str, str],
    cwd: str,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command in its own process group.

    ``subprocess.run`` kills only the direct child on timeout. LibreOffice
    then leaves ``soffice.bin`` behind, and that process keeps the temp
    directory open. A timeout or a failed exit kills the whole group.

    Args:
        command: Argv. Not passed through a shell.
        timeout: Seconds before the group is killed.
        env: Environment for the child. ``HOME`` should be the temp dir.
        cwd: Working directory inside the temp dir.

    Returns:
        The completed process. A non-zero exit already killed the group.

    Raises:
        subprocess.TimeoutExpired: The group was killed after ``timeout``.
    """
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise
    if proc.returncode not in (0, None):
        _kill_process_group(proc)
    return subprocess.CompletedProcess(command, int(proc.returncode or 0), out, err)


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the child's process group, then wait for the direct child.

    Args:
        proc: A process started with ``start_new_session``.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _office_to_pdf(raw: bytes, suffix: str, binary: str, *, timeout: float) -> bytes:
    """Convert one deck to PDF in a private LibreOffice profile.

    The upload name is never placed on the command line. The process
    gets a fixed ``deck.ppt`` or ``deck.pptx`` inside a temp directory
    that is also its ``HOME``. The directory is removed after the
    process group exits, including on timeout.

    Args:
        raw: Deck bytes. pptx bytes are already stripped.
        suffix: ``.ppt`` or ``.pptx``.
        binary: Absolute ``soffice`` path, or a test double.
        timeout: Seconds before the process group is killed.

    Returns:
        PDF bytes.

    Raises:
        ValueError: Conversion failed or timed out.
    """
    root = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    try:
        source = root / f"deck{suffix}"
        outdir = root / "out"
        profile = root / "profile"
        outdir.mkdir()
        source.write_bytes(raw)
        command = [
            binary,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nologo",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(source),
        ]
        command = [*sandbox_prefix(), *command]
        env = os.environ.copy()
        env["HOME"] = str(root)
        env["TMPDIR"] = str(root)
        try:
            completed = run_grouped(command, timeout=timeout, env=env, cwd=str(root))
        except subprocess.TimeoutExpired as exc:
            raise ValueError("That deck took too long to attach.") from exc
        pdfs = sorted(outdir.glob("*.pdf"))
        if completed.returncode != 0 or not pdfs:
            raise ValueError("That PowerPoint could not be read.")
        data = pdfs[0].read_bytes()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if not data.startswith(b"%PDF"):
        raise ValueError("That PowerPoint could not be read.")
    return data
