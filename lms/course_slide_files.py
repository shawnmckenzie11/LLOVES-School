"""Turn an uploaded PowerPoint into page images on the media volume.

The upload and the rendered pages stay files under the class image
folder. SQLite stores a path, never the pptx or the PNG bytes. Conversion
runs LibreOffice headless, then the same PDF-to-PNG path as a Google
Slides export.

External relationships and media that point outside the package are
removed before LibreOffice runs. The process is started in its own
session, inside a network and mount namespace that can see only the job
directory plus the system libraries LibreOffice needs. Conversion is
refused when that sandbox cannot be started. A legacy ``.ppt`` is refused
because its links cannot be stripped.
"""

from __future__ import annotations

import ctypes
import io
import logging
import math
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

logger = logging.getLogger(__name__)

# Loaded once so the child after fork can call prctl without allocating.
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True)

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
# Directories older than this belong to a worker that is already gone.
STALE_SLIDE_DIR_SECONDS = 180
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_EXTERNAL_TARGET = re.compile(r"^(?:file|https?|ftp):", re.IGNORECASE)
_HREF_ATTR = re.compile(
    r"""(?P<prefix>\s(?:[\w.]+:)?(?:href|src|poster)\s*=\s*)"""
    r"""(?P<q>["'])(?P<val>[^"']*)(?P=q)""",
    re.IGNORECASE,
)
_SCHEME_IN_TEXT = re.compile(r"(?i)(?:file:|https?:|ftp:)")
_XMLNS_DECL = re.compile(r"""(?i)\sxmlns(?::[\w.-]+)?\s*=\s*["'][^"']*["']""")
_ALLOWED_BIN_PREFIXES = ("/usr/", "/bin/", "/lib/", "/lib64/", "/sbin/")
_SANDBOX_MESSAGE = "PowerPoint import is not available on this server."
_PPT_MESSAGE = "Save that deck as .pptx and attach it again."
_EMPTY_SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg"/>'
)
# 1x1 transparent PNG. Replaces a media part that pointed outside.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
# $1 is the job directory. The rest of the argv is the converter.
# The new root bind-mounts system libraries and that directory only.
_SANDBOX_SCRIPT = r"""
set -eu
work=$1
shift
mount --make-rprivate / || exit 97
newroot="/dev/shm/lloves-root-$(basename "$work")"
rm -rf "$newroot"
mkdir -p "$newroot"
for p in /usr /bin /lib /lib64 /sbin /etc; do
  if [ -e "$p" ]; then
    mkdir -p "$newroot$p"
    mount --bind "$p" "$newroot$p" || exit 97
    mount -o remount,bind,ro "$newroot$p" || true
  fi
done
mkdir -p "$newroot/tmp" "$newroot/dev" "$newroot/proc"
mount -t tmpfs -o nosuid,nodev,mode=755,size=64m tmpfs "$newroot/tmp" || exit 97
mount -t tmpfs -o nosuid,mode=755,size=4m tmpfs "$newroot/dev" || exit 97
for n in null zero urandom random; do
  if [ -e "/dev/$n" ]; then
    touch "$newroot/dev/$n"
    mount --bind "/dev/$n" "$newroot/dev/$n" || exit 97
  fi
done
mkdir -p "$newroot$work"
mount --bind "$work" "$newroot$work" || exit 97
mount -t proc proc "$newroot/proc" || exit 97
cd "$newroot"
exec chroot "$newroot" "$@"
"""
_sandbox_lock = threading.Lock()
# None until the first probe. False means the probe failed.
_sandbox_ready: tuple[str, str] | None | bool = None


def upload_content_length_allowed(content_length: int | None) -> bool:
    """False when the declared body is past the slide cap plus framing.

    Uses the Content-Length header only. Callers must run this before
    reading the body. A missing length is not treated as small: the
    caller caps the stream with :func:`cap_wsgi_input`.

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


def cap_wsgi_input(environ: dict, *, limit: int | None = None) -> None:
    """Stop a body that has no Content-Length at the slide cap.

    The app's global limit is the IMSCC size. A chunked slide upload
    must not read that far. Call this before the form or JSON body is
    parsed. A body past ``limit`` raises ``RequestEntityTooLarge``.

    Args:
        environ: The WSGI environ for this request.
        limit: Byte cap. Defaults to the slide cap plus multipart framing.
    """
    from werkzeug.wsgi import LimitedStream

    stream = environ.get("wsgi.input")
    if stream is None or isinstance(stream, LimitedStream):
        return
    cap = (
        COURSE_SLIDE_UPLOAD_MAX_BYTES + UPLOAD_FRAMING_SLACK
        if limit is None
        else int(limit)
    )
    environ["wsgi.input"] = LimitedStream(stream, max(0, cap), is_max=True)


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
    """Return ``.pptx`` for an upload name.

    A legacy ``.ppt`` is refused. Its OLE package can hide a linked
    picture, and that link cannot be removed reliably.

    Args:
        filename: Original client filename. It is not passed to a shell.

    Raises:
        ValueError: The name is not a ``.pptx``, or it is a ``.ppt``.
    """
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix == ".ppt":
        raise ValueError(_PPT_MESSAGE)
    if suffix != ".pptx":
        raise ValueError("Upload a .pptx file.")
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
    if suffix == ".ppt":
        raise ValueError(_PPT_MESSAGE)
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
    """Drop outside links from a pptx package.

    LibreOffice fetches ``TargetMode=External`` targets, including
    ``http://127.0.0.1`` and ``file:`` pictures, while converting. Those
    relationships are removed. An SVG or other media part that points
    at a ``file:`` or ``http:`` resource is replaced so the reference is
    gone. Internal slide parts stay. Members are not fully loaded except
    relationship and media parts, and a member whose uncompressed size
    is huge is refused before it is read.

    Args:
        raw: pptx bytes that already start with ``PK``.

    Returns:
        A pptx package with those outside references removed.

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
            if _part_may_reference_outside(name):
                if info.file_size > _MEMBER_MAX_BYTES:
                    raise ValueError("That file is too large to attach.")
                data = source.read(info)
                dest.writestr(name, _neutralize_media(name, data))
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
    return _reference_is_external(target)


def _reference_is_external(value: str) -> bool:
    """True when a target or href leaves the package.

    Schema URLs on a ``Type`` attribute are not passed here.

    Args:
        value: ``Target``, ``href``, or ``src``.
    """
    text = str(value or "").strip()
    if _EXTERNAL_TARGET.match(text):
        return True
    lowered = text.lower()
    if lowered.startswith(("file:", "http:", "https:", "ftp:", "//")):
        return True
    return text.startswith("/") or text.startswith("\\")


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


def _part_may_reference_outside(name: str) -> bool:
    """True when this zip member can carry its own outside link.

    Args:
        name: Path inside the pptx.
    """
    lower = str(name or "").lower()
    if lower.endswith((".svg", ".html", ".htm", ".xhtml", ".emf", ".wmf", ".mml")):
        return True
    if "/media/" in f"/{lower}" or "/embeddings/" in f"/{lower}":
        return True
    return lower.endswith(".xml") and not lower.endswith("[content_types].xml")


def _neutralize_media(name: str, data: bytes) -> bytes:
    """Remove outside hrefs from one media or XML part.

    Args:
        name: Path inside the pptx.
        data: Part bytes, already size-capped.

    Returns:
        The part with outside links removed, or a stand-in image when
        the part itself cannot be cleaned.
    """
    lower = str(name or "").lower()
    if lower.endswith((".emf", ".wmf")) or (
        "/media/" in f"/{lower}" and not _looks_like_xml(data)
    ):
        if _binary_has_external_url(data):
            return _TINY_PNG
        return data
    if not _looks_like_xml(data) and not lower.endswith(".svg"):
        if _binary_has_external_url(data):
            return _TINY_PNG
        return data
    text = data.decode("utf-8", "replace")
    blank_outside = lower.endswith(".svg") or "/media/" in f"/{lower}"

    def replacer(match: re.Match[str]) -> str:
        if _reference_is_external(match.group("val")):
            return ""
        return match.group(0)

    cleaned = _HREF_ATTR.sub(replacer, text)
    if blank_outside and _text_has_external_url(cleaned):
        return _EMPTY_SVG
    if cleaned == text:
        return data
    return cleaned.encode("utf-8")


def _looks_like_xml(data: bytes) -> bool:
    """True when the part starts like XML or SVG.

    Args:
        data: Part bytes.
    """
    head = data.lstrip()[:64].lower()
    return head.startswith((b"<?xml", b"<svg", b"<html", b"<a:", b"<p:", b"<"))


def _text_has_external_url(text: str) -> bool:
    """True when text still contains an outside URL after xmlns removal.

    Args:
        text: XML or SVG text.
    """
    stripped = _XMLNS_DECL.sub(" ", text)
    return _SCHEME_IN_TEXT.search(stripped) is not None


def _binary_has_external_url(data: bytes) -> bool:
    """True when binary media contains a file or http URL.

    Args:
        data: Media bytes.
    """
    lowered = data.lower()
    if b"file://" in lowered or b"http://" in lowered or b"https://" in lowered:
        return True
    for token in (b"file://", b"http://", b"https://"):
        if token.decode("ascii").encode("utf-16le") in data:
            return True
    return False


def pages_from_office(
    raw: bytes, suffix: str, *, timeout: float | None = None
) -> tuple[list[bytes], bool]:
    """Render a PowerPoint upload to one PNG per page.

    Args:
        raw: pptx bytes. Already size-checked.
        suffix: ``.ppt`` or ``.pptx``. ``.ppt`` is refused.
        timeout: Seconds for conversion and rendering together. The
            default stays under the import job budget.

    Returns:
        ``(png_pages, truncated)``. ``truncated`` is true past the page cap.

    Raises:
        ValueError: LibreOffice is missing, the sandbox is missing, the
            file is not a pptx, or conversion did not finish.
    """
    if suffix == ".ppt":
        raise ValueError(_PPT_MESSAGE)
    office_bytes_ok(raw, suffix)
    raw = strip_external_relationships(raw)
    sandbox_tools()
    binary = soffice_bin()
    if not binary:
        raise ValueError(_SANDBOX_MESSAGE)
    from google_slides_import import render_pdf_pages

    budget = _CONVERT_TIMEOUT_S if timeout is None else max(1.0, float(timeout))
    started = time.monotonic()
    pdf = _office_to_pdf(raw, suffix, binary, timeout=min(_SOFFICE_LIMIT_S, budget))
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise ValueError("That deck took too long to attach.")
    return render_pdf_pages(pdf, deadline=time.monotonic() + remaining)


def sandbox_tools() -> tuple[str, str]:
    """``unshare`` and ``timeout`` paths, after a successful probe.

    The probe result is cached. A failed probe is logged once and then
    every conversion is refused. Callers do not run LibreOffice unsandboxed.

    Returns:
        ``(unshare_path, timeout_path)``.

    Raises:
        ValueError: The sandbox cannot be started. The message is safe
            to show to the teacher.
    """
    global _sandbox_ready
    with _sandbox_lock:
        if _sandbox_ready is False:
            raise ValueError(_SANDBOX_MESSAGE)
        if isinstance(_sandbox_ready, tuple):
            return _sandbox_ready
        found = _probe_sandbox()
        if found is None:
            _sandbox_ready = False
            logger.error("slide conversion refused: unshare sandbox probe failed")
            raise ValueError(_SANDBOX_MESSAGE)
        _sandbox_ready = found
        return found


def _probe_sandbox() -> tuple[str, str] | None:
    """Try one isolated ``true`` inside the slide sandbox.

    Returns:
        ``(unshare, timeout)`` when the probe exits 0, else None.
    """
    unshare = shutil.which("unshare")
    timeout_bin = shutil.which("timeout")
    if not unshare or not timeout_bin:
        return None
    work = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    try:
        probe = subprocess.run(
            sandbox_argv(work, ["true"], unshare=unshare),
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(
            Path("/dev/shm") / f"lloves-root-{work.name}", ignore_errors=True
        )
    if probe.returncode != 0:
        return None
    return unshare, timeout_bin


def sandbox_argv(
    work: Path, command: list[str], *, unshare: str | None = None
) -> list[str]:
    """Argv that runs ``command`` with only ``work`` exposed.

    Args:
        work: Job directory. It is bind-mounted at the same path.
        command: Converter argv. Not passed through a shell.
        unshare: Absolute ``unshare`` path. Looked up when omitted.

    Returns:
        Argv beginning with ``unshare``.
    """
    binary = unshare or shutil.which("unshare") or "unshare"
    return [
        binary,
        "--net",
        "--mount",
        "--pid",
        "--fork",
        "--kill-child=SIGKILL",
        "--map-root-user",
        "sh",
        "-c",
        _SANDBOX_SCRIPT,
        "sh",
        str(work),
        *command,
    ]


def sweep_stale_slide_dirs(*, max_age_seconds: float = STALE_SLIDE_DIR_SECONDS) -> int:
    """Delete conversion directories left by a worker that already exited.

    A directory newer than ``max_age_seconds`` is left in place so a
    live import in another worker is not removed. ``0`` removes every
    match.

    Args:
        max_age_seconds: Age threshold. Directories younger than this stay.

    Returns:
        How many directories were removed.
    """
    removed = 0
    now = time.time()
    roots = [Path(tempfile.gettempdir()), Path("/dev/shm"), Path("/tmp")]
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for prefix in ("lloves-slides-", "lloves-slide-upload-", "lloves-root-"):
            for path in root.glob(prefix + "*"):
                if path in seen:
                    continue
                seen.add(path)
                try:
                    age = now - path.stat().st_mtime
                except OSError:
                    continue
                if age < float(max_age_seconds):
                    continue
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed += 1
    return removed


def _die_with_parent() -> None:
    """SIGKILL this process when its parent dies.

    Called in the child after fork, before exec. ``prctl`` and
    ``getppid`` are the only calls, and libc is already loaded.
    ``SIGKILL`` cannot be caught, so a dead worker takes ``unshare``
    with it. ``unshare --kill-child`` then takes the converter.
    """
    if _LIBC.prctl(1, signal.SIGKILL) != 0:
        os._exit(127)
    if os.getppid() == 1:
        os._exit(127)


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
    The child also dies when this worker dies.

    Args:
        command: Argv. Not passed through a shell.
        timeout: Seconds before the group is killed.
        env: Environment for the child. Pass :func:`office_env`.
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
        preexec_fn=_die_with_parent,
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


def office_env(root: Path) -> dict[str, str]:
    """Environment for LibreOffice with no application secrets.

    The child gets ``PATH`` and locale, plus a ``HOME`` inside the job
    directory. Secrets that arrived in the worker environment are not
    copied.

    Args:
        root: Job directory. It becomes ``HOME`` and the temp dir.

    Returns:
        A new environment mapping.
    """
    env: dict[str, str] = {
        "PATH": os.environ.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(root),
        "TMPDIR": str(root),
        "TEMP": str(root),
        "TMP": str(root),
        "XDG_CONFIG_HOME": str(root),
        "XDG_CACHE_HOME": str(root),
    }
    for key in ("LC_ALL", "LC_CTYPE", "TZ", "FONTCONFIG_PATH", "LD_LIBRARY_PATH"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _converter_binary(root: Path, binary: str) -> str:
    """Return a converter path that exists inside the sandbox.

    System binaries under ``/usr`` and ``/bin`` stay where they are.
    Anything else is copied into the job directory, because the sandbox
    does not expose the rest of the filesystem.

    Args:
        root: Job directory.
        binary: Host path of ``soffice`` or a test double.

    Returns:
        Path to execute inside the sandbox.
    """
    path = Path(binary)
    resolved = path.resolve()
    for candidate in (path, resolved):
        text = str(candidate)
        if text.startswith(_ALLOWED_BIN_PREFIXES):
            return text
    dest = root / "converter-bin"
    shutil.copy(path, dest)
    dest.chmod(dest.stat().st_mode | 0o755)
    return str(dest)


def _office_to_pdf(raw: bytes, suffix: str, binary: str, *, timeout: float) -> bytes:
    """Convert one deck to PDF in a private LibreOffice profile.

    The upload name is never placed on the command line. The process
    gets a fixed ``deck.pptx`` inside a temp directory that is also its
    ``HOME``. The directory is the only writable tree the sandbox
    exposes. It is removed after the process group exits, including on
    timeout. The converter is wrapped in ``timeout -s KILL`` and dies
    with the worker.

    Args:
        raw: Deck bytes. pptx bytes are already stripped.
        suffix: ``.pptx``.
        binary: Absolute ``soffice`` path, or a test double.
        timeout: Seconds before the process group is killed.

    Returns:
        PDF bytes.

    Raises:
        ValueError: The sandbox is unavailable, or conversion failed
            or timed out.
    """
    if suffix == ".ppt":
        raise ValueError(_PPT_MESSAGE)
    unshare, timeout_bin = sandbox_tools()
    root = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    try:
        source = root / f"deck{suffix}"
        outdir = root / "out"
        profile = root / "profile"
        outdir.mkdir()
        source.write_bytes(raw)
        run_bin = _converter_binary(root, binary)
        limit = max(1, int(math.ceil(timeout)))
        inner = [
            timeout_bin,
            "-s",
            "KILL",
            str(limit),
            run_bin,
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
        # A test double is a script, not LibreOffice. Skip the soffice flags.
        if run_bin.endswith("converter-bin"):
            inner = [timeout_bin, "-s", "KILL", str(limit), run_bin]
        command = sandbox_argv(root, inner, unshare=unshare)
        try:
            completed = run_grouped(
                command,
                timeout=limit + 5,
                env=office_env(root),
                cwd=str(root),
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("That deck took too long to attach.") from exc
        pdfs = sorted(outdir.glob("*.pdf"))
        if completed.returncode in {124, 137} or (
            run_bin.endswith("converter-bin") and completed.returncode != 0
        ):
            raise ValueError("That deck took too long to attach.")
        if completed.returncode != 0 or not pdfs:
            raise ValueError("That PowerPoint could not be read.")
        data = pdfs[0].read_bytes()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(Path("/dev/shm") / f"lloves-root-{root.name}", ignore_errors=True)
    if not data.startswith(b"%PDF"):
        raise ValueError("That PowerPoint could not be read.")
    return data
