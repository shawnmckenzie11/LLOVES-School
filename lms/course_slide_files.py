"""Turn an uploaded PowerPoint into page images on the media volume.

The upload and the rendered pages stay files under the class image
folder. SQLite stores a path, never the pptx or the PNG bytes. Conversion
runs LibreOffice headless, then the same PDF-to-PNG path as a Google
Slides export.

External relationships and media that point outside the package are
removed before LibreOffice runs. The process is started in its own
session, inside a network and mount namespace. ``pivot_root`` is the
only root switch. It drops the host filesystem, so the converter sees
the job directory and read-only system libraries only. When the worker
is root, the converter runs as nobody. A root worker prefers a user
namespace. A chroot cannot create one, so that worker still drops to
nobody with setpriv inside the mount namespace. A shared parent mount
makes ``pivot_root`` return EINVAL, and the import is refused.
Conversion is refused when that sandbox cannot be started. A legacy
``.ppt`` is refused because its links cannot be stripped.
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

try:
    from xml_serialize import tostring_with_default_namespace
except ImportError:
    from lms.xml_serialize import tostring_with_default_namespace

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
# Metric clones installed in the image. A deck that names one of these
# Office families gets the clone. Any other family is used when the
# deck embeds it; otherwise the generic sans/serif/mono alias applies.
METRIC_FONT_FAMILIES: tuple[str, ...] = (
    "Liberation Sans",
    "Liberation Serif",
    "Liberation Mono",
    "Carlito",
    "Caladea",
)
_OFFICE_METRICS_CONF = "59-lloves-office-metrics.conf"
# Applied before /etc/fonts/fonts.conf. That file's metric map binds
# Arial to Arimo (and Times to Tinos, Courier to Cousine) with
# binding="same", and a later alias cannot outrank it.
_OFFICE_FACE_EDITS: tuple[tuple[str, str], ...] = (
    ("Arial", "Liberation Sans"),
    ("Helvetica", "Liberation Sans"),
    ("Times New Roman", "Liberation Serif"),
    ("Times", "Liberation Serif"),
    ("Courier New", "Liberation Mono"),
    ("Courier", "Liberation Mono"),
    ("Calibri", "Carlito"),
    ("Cambria", "Caladea"),
)
_EMBEDDED_FONT_MAX_BYTES = 8 * 1024 * 1024
_EMBEDDED_FONT_MAX_FACES = 32
_EOT2TTF_TIMEOUT_S = 10
_SFNT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")
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
# A failed sandbox probe is tried again after this, not held until restart.
SANDBOX_PROBE_RETRY_SECONDS = 180
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
_SANDBOX_INNER = r"""
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
die() {
  printf 'lloves-sandbox-step %s %s\n' "$1" "$2" >&2
  exit 97
}
# The wait pipe and pass_fds=(3,) are only for host root plus a user
# namespace. An unprivileged worker uses --map-root-user and has no pipe.
# /bin/sh can redirect only fds 0-9, so the launcher parks that pipe on
# fd 3 and lists 3 in pass_fds. Close it before the converter starts.
if [ "${LLOVES_SANDBOX_WAIT:-}" = 1 ]; then
  read -r _ <&3 || die unshare $?
  exec 3<&-
fi
work=$1
shift
mount --make-rprivate / || die mount $?
newroot="/dev/shm/lloves-root-$(basename "$work")"
rm -rf "$newroot"
mkdir -p "$newroot" || die mkdir $?
mount --bind "$newroot" "$newroot" || die mount $?
mount --make-rprivate "$newroot" || die mount $?
# System libraries only. Host /etc is not mounted; it can hold secrets.
for p in /usr /bin /lib /lib64 /sbin; do
  if [ -e "$p" ]; then
    mkdir -p "$newroot$p" || die mkdir $?
    mount --bind "$p" "$newroot$p" || die mount $?
    mount -o remount,bind,ro "$newroot$p" || die mount $?
  fi
done
mkdir -p "$newroot/etc" || die mkdir $?
mount -t tmpfs -o nosuid,nodev,mode=755,size=8m tmpfs "$newroot/etc" || die mount $?
printf 'root:x:0:0:root:/:/bin/false\nnobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n' > "$newroot/etc/passwd"
printf 'root:x:0:\nnogroup:x:65534:\n' > "$newroot/etc/group"
printf 'passwd: files\ngroup: files\nhosts: files\n' > "$newroot/etc/nsswitch.conf"
printf '127.0.0.1 localhost\n' > "$newroot/etc/hosts"
if [ -d /etc/fonts ]; then
  mkdir -p "$newroot/etc/fonts"
  cp -R --no-preserve=ownership /etc/fonts/. "$newroot/etc/fonts/" || true
fi
if [ -d /etc/libreoffice ]; then
  mkdir -p "$newroot/etc/libreoffice"
  cp -R --no-preserve=ownership /etc/libreoffice/. "$newroot/etc/libreoffice/" || true
fi
if [ -f /etc/localtime ]; then
  cp -R --no-preserve=ownership /etc/localtime "$newroot/etc/localtime" || true
fi
mkdir -p "$newroot/tmp" "$newroot/dev" "$newroot/proc" "$newroot/oldroot" || die mkdir $?
# 1777: LibreOffice creates its bootstrap pipe in /tmp, as nobody.
mount -t tmpfs -o nosuid,nodev,mode=1777,size=256m tmpfs "$newroot/tmp" || die mount $?
mount -t tmpfs -o nosuid,mode=755,size=4m tmpfs "$newroot/dev" || die mount $?
for n in null zero urandom random; do
  if [ -e "/dev/$n" ]; then
    touch "$newroot/dev/$n"
    mount --bind "/dev/$n" "$newroot/dev/$n" || die mount $?
  fi
done
mkdir -p "$newroot$work" || die mkdir $?
chmod -R a+rwx "$work" || true
mount --bind "$work" "$newroot$work" || die mount $?
mount -o remount,bind,nosuid,nodev "$newroot$work" || die mount $?
mount -t proc proc "$newroot/proc" || die mount $?
cd "$newroot"
pivot_root . oldroot || die pivot_root $?
umount -l /oldroot || die umount $?
rmdir /oldroot 2>/dev/null || true
# nobody is mapped when the worker is host root. A user namespace lists
# 65534 on its own line. The initial namespace lists one wide range, and
# 65534 inside that range is host nobody. An unprivileged namespace root
# is already a host uid, and setuid would fail.
# awk is often a symlink into /etc/alternatives, which this root does not have.
mapped_nobody=0
host=1
while read -r inner outer count; do
  count=${count:-0}
  if [ "$inner" = 65534 ] && [ "$outer" = 65534 ]; then
    mapped_nobody=1
  fi
  if [ "$inner" -le 65534 ] 2>/dev/null && [ $((inner + count)) -gt 65534 ] 2>/dev/null; then
    off=$((65534 - inner))
    if [ $((outer + off)) -eq 65534 ]; then
      mapped_nobody=1
    fi
  fi
  if [ "$inner" = 0 ]; then
    host=$outer
  fi
done < /proc/self/uid_map
cd "$work" || die cd $?
if [ "$mapped_nobody" = 1 ]; then
  command -v setpriv >/dev/null 2>&1 || die setpriv 127
  # Keep the converter's status. die would hide a LibreOffice failure.
  setpriv --reuid=65534 --regid=65534 --clear-groups --inh-caps=-all --bounding-set=-all --no-new-privs -- "$@" || exit $?
  exit 0
fi
if [ "$host" = 0 ]; then
  echo "slide sandbox refused to drop root" >&2
  die setpriv 97
fi
command -v setpriv >/dev/null 2>&1 || die setpriv 127
# An unprivileged namespace root is a host user. Still clear the bounding set.
setpriv --inh-caps=-all --bounding-set=-all --no-new-privs -- "$@" || exit $?
exit 0
"""
_SANDBOX_SCRIPT = _SANDBOX_INNER
_sandbox_lock = threading.Lock()
# None until a probe succeeds. A failure time means the next probe waits.
_sandbox_ready: tuple[str, str] | None = None
_sandbox_failed_at: float | None = None
# Last refusal line. Paths are already removed. Cleared on the next probe.
_sandbox_probe_detail: str | None = None
# None until a root worker has tried ``unshare --user``.
_root_user_ns: bool | None = None
_PROBE_STDERR_LIMIT = 200
_PROBE_STEP_RE = re.compile(r"lloves-sandbox-step\s+([A-Za-z0-9_.+-]+)\s+(-?\d+)")
_PROBE_PATH_RE = re.compile(r"/[^\s\"']+")



def _which_tool(name: str) -> str | None:
    """Resolve a sandbox binary, including sbin when PATH omits it.

    Args:
        name: Executable name, such as ``pivot_root`` or ``unshare``.

    Returns:
        An absolute path, or None when the tool is not installed.
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in ("/usr/sbin", "/sbin", "/usr/bin", "/bin"):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _sanitize_probe_text(text: str) -> str:
    """Drop absolute paths and keep a short single line of probe stderr.

    Args:
        text: Stderr or an exception string. A job path may be in it.

    Returns:
        At most 200 characters, with paths replaced by ``[path]``.
    """
    cleaned = _PROBE_PATH_RE.sub("[path]", str(text).replace("\x00", ""))
    return " ".join(cleaned.split())[:_PROBE_STDERR_LIMIT]


def _format_probe_failure(command: str, exit_code: int | None, stderr: str) -> str:
    """One public line naming the sandbox step that failed.

    Args:
        command: Executable name, such as ``pivot_root`` or ``unshare``.
        exit_code: Wait status, or None when the process did not exit.
        stderr: Raw stderr. Paths are removed.

    Returns:
        The warning and Sentry message, with no absolute paths.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", command or ""):
        command = "unshare"
    code = "none" if exit_code is None else str(int(exit_code))
    detail = _sanitize_probe_text(stderr)
    return (
        "slide conversion refused: unshare sandbox probe failed "
        f"(step={command} exit={code} stderr={detail})"
    )


def _failure_from_output(err: bytes, returncode: int) -> str:
    """Build the public probe line from a sandbox child's stderr.

    Args:
        err: Stderr bytes from the unshare supervisor.
        returncode: The supervisor's wait status.

    Returns:
        The warning line. A ``lloves-sandbox-step`` marker names the command.
    """
    text = err.decode("utf-8", "replace")
    match = _PROBE_STEP_RE.search(text)
    if match:
        command = match.group(1)
        code: int | None = int(match.group(2))
        text = _PROBE_STEP_RE.sub(" ", text)
    else:
        command = "unshare"
        for name in ("pivot_root", "umount", "mount", "setpriv", "mkdir", "unshare"):
            if f"{name}:" in text:
                command = name
                break
        code = int(returncode)
    return _format_probe_failure(command, code, text)


def _report_sandbox_refusal(detail: str) -> None:
    """Log a refused probe and send that same line to Sentry.

    Warning is below the SDK's default event level, so the log line is a
    breadcrumb only. ``capture_message`` is the event whose message is
    ``detail``. Paths are already gone. Import and send failures are ignored.

    Args:
        detail: The public failure line from :func:`_format_probe_failure`.
    """
    logger.warning("%s", detail)
    try:
        import sentry_sdk
    except ImportError:
        return
    try:
        if not sentry_sdk.get_client().is_active():
            return
        sentry_sdk.capture_message(detail, level="warning")
    except Exception:  # noqa: BLE001
        logger.debug("slide sandbox Sentry report failed", exc_info=True)


def _missing_sandbox_tool() -> str | None:
    """The first sandbox binary that is not installed, if any.

    Returns:
        An executable name, or None when the probe can try to start.
    """
    for name in ("unshare", "timeout", "pivot_root", "mount", "umount", "setpriv"):
        if not _which_tool(name):
            return name
    return None


def _root_user_namespace_works() -> bool:
    """True when this root process can create a user namespace.

    ``unshare --user`` returns EPERM inside a chroot even for root. The
    slide sandbox then keeps the mount, pid, and net namespaces and drops
    to nobody with setpriv. Non-root workers are not cached here. The
    root answer is cached for the life of the process.

    Returns:
        True when ``unshare --user true`` exits 0.
    """
    global _root_user_ns
    if os.geteuid() != 0:
        return False
    if _root_user_ns is not None:
        return _root_user_ns
    binary = _which_tool("unshare")
    if not binary:
        _root_user_ns = False
        return False
    try:
        completed = subprocess.run(
            [binary, "--user", "true"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _root_user_ns = False
        logger.warning(
            "slide sandbox user namespace unavailable (step=unshare exit=1 stderr=%s); continuing with setpriv",
            _sanitize_probe_text(str(exc)),
        )
        return False
    if completed.returncode == 0:
        _root_user_ns = True
        return True
    _root_user_ns = False
    logger.warning(
        "slide sandbox user namespace unavailable (step=unshare exit=%s stderr=%s); continuing with setpriv",
        completed.returncode,
        _sanitize_probe_text(completed.stderr.decode("utf-8", "replace")),
    )
    return False


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
    return tostring_with_default_namespace(root, _REL_NS)


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

    A successful probe is cached. A failed probe is logged and blocks
    conversion for a few minutes, then it is tried again. Callers do
    not run LibreOffice unsandboxed.

    Returns:
        ``(unshare_path, timeout_path)``.

    Raises:
        ValueError: The sandbox cannot be started. The message is safe
            to show to the teacher.
    """
    global _sandbox_ready, _sandbox_failed_at, _sandbox_probe_detail
    with _sandbox_lock:
        if isinstance(_sandbox_ready, tuple):
            return _sandbox_ready
        if _sandbox_failed_at is not None:
            elapsed = time.monotonic() - _sandbox_failed_at
            if elapsed < SANDBOX_PROBE_RETRY_SECONDS:
                raise ValueError(_SANDBOX_MESSAGE)
        _sandbox_probe_detail = None
        found = _probe_sandbox()
        if found is None:
            _sandbox_failed_at = time.monotonic()
            _sandbox_ready = None
            detail = _sandbox_probe_detail or _format_probe_failure(
                "unshare", None, "probe failed"
            )
            _report_sandbox_refusal(detail)
            raise ValueError(_SANDBOX_MESSAGE)
        _sandbox_failed_at = None
        _sandbox_probe_detail = None
        _sandbox_ready = found
        return found


def _probe_sandbox() -> tuple[str, str] | None:
    """Try one isolated ``true`` inside the slide sandbox.

    A missing binary or a non-zero sandbox sets :data:`_sandbox_probe_detail`
    to the public failure line. The caller logs that line.

    Returns:
        ``(unshare, timeout)`` when the probe exits 0, else None.
    """
    global _sandbox_probe_detail
    unshare = _which_tool("unshare")
    timeout_bin = _which_tool("timeout")
    missing = _missing_sandbox_tool()
    if missing or not unshare or not timeout_bin:
        _sandbox_probe_detail = _format_probe_failure(
            missing or "unshare", 127, "not found"
        )
        return None
    work = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    proc: subprocess.Popen[bytes] | None = None
    err = b""
    try:
        try:
            proc = _launch_sandbox(["true"], work, office_env(work), str(work))
            _out, err = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            if proc is not None:
                _kill_process_group(proc)
            _sandbox_probe_detail = _format_probe_failure("true", 124, "timed out")
            return None
        except OSError as exc:
            if proc is not None:
                _kill_process_group(proc)
            _sandbox_probe_detail = _format_probe_failure("unshare", 1, str(exc))
            return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(
            Path("/dev/shm") / f"lloves-root-{work.name}", ignore_errors=True
        )
    if proc.returncode != 0:
        _sandbox_probe_detail = _failure_from_output(err, int(proc.returncode or 1))
        return None
    return unshare, timeout_bin


def sandbox_argv(
    work: Path,
    command: list[str],
    *,
    unshare: str | None = None,
    user_namespace: bool | None = None,
) -> list[str]:
    """Argv that runs ``command`` with only ``work`` exposed.

    Host root uses a user namespace so the parent can map nobody before
    the script continues, when the kernel allows it. A chroot refuses
    that namespace, and root then stays in the host user namespace and
    drops to nobody with setpriv. Any other worker maps itself to
    namespace root, which is already unprivileged on the host.

    Args:
        work: Job directory. It is bind-mounted at the same path.
        command: Converter argv. Not passed through a shell.
        unshare: Absolute ``unshare`` path. Looked up when omitted.
        user_namespace: Force ``--user`` for root on or off. ``None``
            asks the kernel with a one-shot ``unshare --user true``.

    Returns:
        Argv beginning with ``unshare``.
    """
    binary = unshare or _which_tool("unshare") or "unshare"
    flags = ["--mount", "--pid", "--net", "--fork", "--kill-child=SIGKILL"]
    if os.geteuid() != 0:
        flags.append("--map-root-user")
    else:
        use_ns = (
            _root_user_namespace_works() if user_namespace is None else user_namespace
        )
        if use_ns:
            flags.insert(0, "--user")
    return [
        binary,
        *flags,
        "sh",
        "-c",
        _SANDBOX_SCRIPT,
        "sh",
        str(work),
        *command,
    ]


def _arm_root_mapping(proc: subprocess.Popen[bytes]) -> None:
    """Map namespace root and nobody for a worker that is host root.

    The unshare child waits until this map is installed. nobody on the
    host is uid 65534, so a converter that escapes the mount namespace
    is not host root.

    Args:
        proc: The unshare supervisor started with ``--fork``.

    Raises:
        OSError: The child never appeared, or the map could not be written.
    """
    deadline = time.monotonic() + 2
    target: int | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            kids = Path(f"/proc/{proc.pid}/task/{proc.pid}/children").read_text().split()
        except OSError:
            kids = []
        if kids:
            target = int(kids[0])
            break
        time.sleep(0.01)
    if target is None:
        raise OSError("slide sandbox did not fork")
    Path(f"/proc/{target}/setgroups").write_text("allow\n")
    Path(f"/proc/{target}/uid_map").write_text("0 0 1\n65534 65534 1\n")
    Path(f"/proc/{target}/gid_map").write_text("0 0 1\n65534 65534 1\n")



def _borrow_fd3() -> bool:
    """Open fd 3 when it is closed, without replacing an open descriptor.

    ``/bin/sh`` can redirect only fds 0 through 9, so the child reads the
    wait pipe on fd 3. ``Popen`` requires that number to be open before
    ``pass_fds`` will keep it. A descriptor the process already holds is
    left in place.

    Returns:
        True when fd 3 was opened here. The caller closes it after the
        child has started.
    """
    probe = os.open(os.devnull, os.O_RDONLY)
    try:
        if probe == 3:
            return True
        if probe > 3:
            return False
        try:
            os.fstat(3)
        except OSError:
            os.dup2(probe, 3)
            return True
        return False
    finally:
        if probe != 3:
            os.close(probe)


def _launch_sandbox(
    command: list[str],
    work: Path,
    env: dict[str, str],
    cwd: str,
) -> subprocess.Popen[bytes]:
    """Start ``command`` inside the slide sandbox.

    Args:
        command: Converter argv. Not passed through a shell.
        work: Job directory bind-mounted into the new root.
        env: Environment for the child. Pass :func:`office_env`.
        cwd: Working directory. Use the job directory.

    Returns:
        The unshare supervisor. The caller waits and kills the group.

    Raises:
        OSError: The root uid map could not be installed. The message
            includes the child's stderr with paths removed.

    The wait pipe and ``pass_fds=(3,)`` apply only when this process is
    host root and the user namespace is in use. ``/bin/sh`` reads that
    pipe on fd 3, then closes it. ``Popen`` closes every descriptor that
    is not in ``pass_fds`` after ``preexec_fn``, so fd 3 is listed there.
    A worker that already has fd 3 open keeps it. An unprivileged worker
    uses ``--map-root-user`` and does not pass fd 3.
    """
    child_env = dict(env)
    read_fd: int | None = None
    write_fd: int | None = None
    borrowed_fd3 = False
    saved_inherit: bool | None = None
    use_user_ns = os.geteuid() == 0 and _root_user_namespace_works()
    if use_user_ns:
        read_fd, write_fd = os.pipe()
        os.set_inheritable(read_fd, True)
        if read_fd != 3:
            borrowed_fd3 = _borrow_fd3()
            if not borrowed_fd3:
                saved_inherit = os.get_inheritable(3)
        child_env["LLOVES_SANDBOX_WAIT"] = "1"

    def preexec() -> None:
        """SIGKILL this process when the worker dies, and park the pipe on fd 3."""
        _die_with_parent()
        if read_fd is not None and read_fd != 3:
            os.dup2(read_fd, 3)

    try:
        proc = subprocess.Popen(
            sandbox_argv(work, command, user_namespace=use_user_ns),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            cwd=cwd,
            start_new_session=True,
            pass_fds=(3,) if read_fd is not None else (),
            preexec_fn=preexec,
        )
    except Exception:
        if read_fd is not None:
            os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if borrowed_fd3:
            os.close(3)
        elif saved_inherit is not None:
            os.set_inheritable(3, saved_inherit)
        raise
    if saved_inherit is not None:
        os.set_inheritable(3, saved_inherit)
    if borrowed_fd3:
        os.close(3)
    if write_fd is None or read_fd is None:
        return proc
    os.close(read_fd)
    try:
        _arm_root_mapping(proc)
        os.write(write_fd, b"\n")
    except Exception as exc:
        os.close(write_fd)
        _kill_process_group(proc)
        err = b""
        try:
            _out, err = proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            err = b""
        public = _sanitize_probe_text((err or b"").decode("utf-8", "replace"))
        raise OSError(f"{exc}; stderr={public}") from exc
    os.close(write_fd)
    return proc


def _diagnostic_command(argv: list[str], *, timeout: float = 5) -> tuple[bool, str]:
    """Run one diagnostic command and return pass or fail.

    Args:
        argv: Command to run. Not a shell string.
        timeout: Seconds before the command is failed.

    Returns:
        ``(passed, detail)``. ``detail`` has the exit code and sanitized stderr.
    """
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"exit=none stderr={_sanitize_probe_text(str(exc))}"
    err = _sanitize_probe_text(completed.stderr.decode("utf-8", "replace"))
    return completed.returncode == 0, f"exit={completed.returncode} stderr={err}"


def _diagnostic_present(name: str) -> tuple[bool, str]:
    """Pass when ``name`` is an executable sandbox binary.

    Args:
        name: Executable name, such as ``pivot_root``.

    Returns:
        ``(passed, detail)`` with exit 0 or 127.
    """
    if _which_tool(name):
        return True, "exit=0 stderr="
    return False, "exit=127 stderr=not found"

def _diagnostic_host_data() -> tuple[bool, str]:
    """Pass when the sandbox cannot list the host data directory.

    Runs ``ls /data`` through the same ``pivot_root`` sandbox as a
    conversion and returns that listing in the detail. A root worker
    drops to nobody before the command. A listing is a leak. A sandbox
    that does not start is a failure. This does not cache a probe
    failure and does not read the application database.

    Returns:
        ``(passed, detail)``. A leak starts with ``LEAK``.
    """
    work = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    proc: subprocess.Popen[bytes] | None = None
    try:
        try:
            proc = _launch_sandbox(
                ["sh", "-c", "id -u; ls /data; echo ls:$?"],
                work,
                office_env(work),
                str(work),
            )
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            if proc is not None:
                _kill_process_group(proc)
            return False, "exit=124 stderr=timed out"
        except OSError as exc:
            if proc is not None:
                _kill_process_group(proc)
            return False, f"exit=1 stderr={_sanitize_probe_text(str(exc))}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(Path("/dev/shm") / f"lloves-root-{work.name}", ignore_errors=True)
    text_out = (out or b"").decode("utf-8", "replace").replace("\x00", "")
    err_text = _sanitize_probe_text((err or b"").decode("utf-8", "replace"))
    lines = [line.strip() for line in text_out.splitlines() if line.strip()]
    uid = lines[0] if lines else ""
    ls_code: int | None = None
    listing: list[str] = []
    for line in lines[1:]:
        if line.startswith("ls:"):
            try:
                ls_code = int(line.split(":", 1)[1])
            except ValueError:
                ls_code = None
        else:
            listing.append(line)
    shown = " ".join(listing)[:_PROBE_STDERR_LIMIT]
    code = proc.returncode if proc is not None else "none"
    detail = f"uid={uid} ls={ls_code} out={shown} stderr={err_text}"
    if ls_code == 0:
        return False, f"LEAK host /data is visible {detail}"[:400]
    if ls_code is None:
        return False, f"exit={code} {detail}"[:400]
    if os.geteuid() == 0 and uid != "65534":
        return False, f"exit={code} not nobody {detail}"[:400]
    return True, f"exit={ls_code} hidden {detail}"[:400]


def _diagnostic_fonts() -> tuple[bool, str]:
    """List font families fontconfig can see inside the slide sandbox.

    The sandbox is unchanged: ``fc-list`` runs through the same
    ``pivot_root`` launch as a conversion. The step passes when every
    metric Office substitute is present. The detail lists the families
    it found, with paths removed.

    Returns:
        ``(passed, detail)``.
    """
    fc_list = _which_tool("fc-list")
    if not fc_list:
        return False, "exit=127 stderr=fc-list not found"
    work = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    proc: subprocess.Popen[bytes] | None = None
    out = b""
    err = b""
    try:
        try:
            proc = _launch_sandbox(
                [fc_list, ":", "family"],
                work,
                office_env(work),
                str(work),
            )
            out, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            if proc is not None:
                _kill_process_group(proc)
            return False, "exit=124 stderr=timed out"
        except OSError as exc:
            if proc is not None:
                _kill_process_group(proc)
            return False, f"exit=1 stderr={_sanitize_probe_text(str(exc))}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(Path("/dev/shm") / f"lloves-root-{work.name}", ignore_errors=True)
    code = proc.returncode if proc is not None else "none"
    if code != 0:
        err_text = _sanitize_probe_text((err or b"").decode("utf-8", "replace"))
        return False, f"exit={code} stderr={err_text}"
    families: set[str] = set()
    for line in (out or b"").decode("utf-8", "replace").splitlines():
        for part in line.split(","):
            name = " ".join(part.split())
            if name and not name.startswith("/"):
                families.add(name)
    missing = [name for name in METRIC_FONT_FAMILIES if name not in families]
    listing = ", ".join(sorted(families))
    if len(listing) > 500:
        listing = listing[:500] + f" … ({len(families)} families)"
    if missing:
        return False, f"exit=0 missing={', '.join(missing)} families={listing}"
    return True, f"exit=0 families={listing}"


def sandbox_diagnostic_steps() -> list[tuple[str, bool, str]]:
    """Run each slide-sandbox check on its own.

    Read-only aside from a temporary directory the full probe deletes.
    This does not cache a failure, so a refused check does not block a
    later import in the same process. A failing full probe still means
    the app refuses the import. The fonts step lists the families
    visible inside the sandbox. The host-data step lists ``/data``
    inside the sandbox and passes only when that list fails. A root
    worker runs that list as nobody.

    Returns:
        ``(name, passed, detail)`` in probe order.
    """
    global _sandbox_probe_detail
    steps: list[tuple[str, bool, str]] = []
    unshare = _which_tool("unshare")
    if unshare:
        steps.append(("unshare", *_diagnostic_command([unshare, "true"])))
        steps.append(("unshare-user", *_diagnostic_command([unshare, "--user", "true"])))
    else:
        steps.append(("unshare", False, "exit=127 stderr=not found"))
        steps.append(("unshare-user", False, "exit=127 stderr=not found"))
    timeout_bin = _which_tool("timeout")
    if timeout_bin:
        steps.append(("timeout", *_diagnostic_command([timeout_bin, "1", "true"])))
    else:
        steps.append(("timeout", False, "exit=127 stderr=not found"))
    steps.append(("pivot_root", *_diagnostic_present("pivot_root")))
    steps.append(("mount", *_diagnostic_present("mount")))
    steps.append(("umount", *_diagnostic_present("umount")))
    setpriv = _which_tool("setpriv")
    if os.geteuid() == 0 and setpriv:
        steps.append(
            (
                "setpriv",
                *_diagnostic_command(
                    [
                        setpriv,
                        "--reuid=65534",
                        "--regid=65534",
                        "--clear-groups",
                        "--inh-caps=-all",
                        "--bounding-set=-all",
                        "--no-new-privs",
                        "--",
                        "true",
                    ]
                ),
            )
        )
    else:
        steps.append(("setpriv", *_diagnostic_present("setpriv")))
    if unshare:
        flags = [unshare, "--mount"]
        if os.geteuid() != 0:
            flags.append("--map-root-user")
        flags.extend(
            [
                "sh",
                "-c",
                "d=$(mktemp -d /tmp/lloves-sandbox-diag-XXXXXX) || exit 1; "
                "mount --bind /usr \"$d\"; status=$?; umount \"$d\" || true; "
                "rmdir \"$d\" || true; exit $status",
            ]
        )
        steps.append(("bind-mount", *_diagnostic_command(flags)))
    else:
        steps.append(("bind-mount", False, "exit=127 stderr=not found"))
    office = soffice_bin()
    if office:
        steps.append(("soffice", True, "exit=0 stderr="))
    else:
        steps.append(("soffice", False, "exit=127 stderr=not found"))
    steps.append(("fonts", *_diagnostic_fonts()))
    previous = _sandbox_probe_detail
    found = _probe_sandbox()
    if found:
        steps.append(("full-probe", True, "exit=0 stderr="))
    else:
        detail = _sandbox_probe_detail or _format_probe_failure("unshare", None, "probe failed")
        steps.append(("full-probe", False, detail))
    _sandbox_probe_detail = previous
    steps.append(("host-data", *_diagnostic_host_data()))
    return steps


def run_sandboxed(
    command: list[str],
    *,
    work: Path,
    timeout: float,
    env: dict[str, str],
    cwd: str,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``command`` in the sandbox and kill the group on timeout.

    Args:
        command: Converter argv. Not passed through a shell.
        work: Job directory.
        timeout: Seconds before the group is killed.
        env: Environment for the child. Pass :func:`office_env`.
        cwd: Working directory inside the job directory.

    Returns:
        The completed unshare process.

    Raises:
        subprocess.TimeoutExpired: The group was killed after ``timeout``.
        OSError: The sandbox could not be started.
    """
    proc = _launch_sandbox(command, work, env, cwd)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise
    if proc.returncode not in (0, None):
        _kill_process_group(proc)
    return subprocess.CompletedProcess(command, int(proc.returncode or 0), out, err)


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
        for prefix in (
            "lloves-slides-",
            "lloves-slide-upload-",
            "lloves-root-",
        ):
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



def _office_path() -> str:
    """PATH for LibreOffice, including the directories that hold mount tools.

    Returns:
        A PATH string that contains ``/usr/sbin`` and ``/sbin``.
    """
    raw = os.environ.get(
        "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    parts = [part for part in raw.split(":") if part]
    for extra in ("/usr/sbin", "/sbin"):
        if extra not in parts:
            parts.insert(0, extra)
    return ":".join(parts)


def _sfnt_font(data: bytes) -> bool:
    """True when ``data`` starts with a TrueType or OpenType signature.

    Args:
        data: Font file bytes, or at least the first four.
    """
    return data.startswith(_SFNT_MAGICS)


def _xml_attr(value: str) -> str:
    """Escape a path for a fontconfig attribute.

    Args:
        value: Text placed in an attribute.
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _convert_embedded_eot(src: Path, dest: Path) -> bool:
    """Turn one Embedded OpenType face into TrueType.

    Args:
        src: EOT bytes on disk.
        dest: Where the TrueType file should be written.

    Returns:
        True when ``dest`` is a TrueType or OpenType font.
    """
    tool = _which_tool("eot2ttf")
    if not tool:
        return False
    try:
        completed = subprocess.run(
            [tool, str(src), str(dest)],
            capture_output=True,
            timeout=_EOT2TTF_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        dest.unlink(missing_ok=True)
        return False
    if completed.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        return False
    with dest.open("rb") as handle:
        head = handle.read(12)
    if not _sfnt_font(head):
        dest.unlink(missing_ok=True)
        return False
    return True


def extract_embedded_deck_fonts(raw: bytes, dest: Path) -> int:
    """Stage TrueType faces embedded in a PowerPoint package.

    PowerPoint stores each face as Embedded OpenType. ``eot2ttf`` converts
    those when it is installed. A face that is already TrueType is copied.
    A face that does not convert is skipped, and the metric substitutes
    still apply. The deck bytes and the face names are not logged.

    Args:
        raw: pptx bytes. A non-zip is ignored.
        dest: Directory to write ``face-N.ttf`` files into.

    Returns:
        How many TrueType files were written.
    """
    if not raw.startswith(b"PK"):
        return 0
    try:
        package = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return 0
    written = 0
    dest.mkdir(parents=True, exist_ok=True)
    with package:
        faces = [
            info
            for info in package.infolist()
            if info.filename.lower().endswith(".fntdata")
            and ".." not in info.filename.replace("\\", "/").split("/")
            and 0 < info.file_size <= _EMBEDDED_FONT_MAX_BYTES
        ]
        for info in faces[:_EMBEDDED_FONT_MAX_FACES]:
            try:
                data = package.read(info)
            except (KeyError, zipfile.BadZipFile, OSError):
                continue
            if len(data) > _EMBEDDED_FONT_MAX_BYTES:
                continue
            target = dest / f"face-{written}.ttf"
            if _sfnt_font(data):
                target.write_bytes(data)
                written += 1
                continue
            eot = dest / f"face-{written}.eot"
            try:
                eot.write_bytes(data)
                if _convert_embedded_eot(eot, target):
                    written += 1
            finally:
                eot.unlink(missing_ok=True)
    return written


def _fontconfig_face_edits() -> list[str]:
    """Fontconfig edits that replace an Office family with its metric clone.

    These have to run before ``/etc/fonts/fonts.conf`` is included. The
    system map treats Arimo as the same family as Arial, so a prefer
    rule that appears later still loses.

    Returns:
        XML lines, one match element per Office family.
    """
    lines: list[str] = []
    for requested, clone in _OFFICE_FACE_EDITS:
        lines.append(
            "  <match target=\"pattern\">\n"
            "    <test name=\"family\" qual=\"first\">"
            f"<string>{_xml_attr(requested)}</string></test>\n"
            "    <edit name=\"family\" mode=\"assign\" binding=\"same\">"
            f"<string>{_xml_attr(clone)}</string></edit>\n"
            "  </match>"
        )
    return lines


def _job_fontconfig(root: Path, font_dir: Path | None) -> Path:
    """Write a fontconfig file the sandbox can read.

    Office families are rewritten to their metric clones first. The
    system config comes next, then the generic sans/serif/mono map, then
    any faces staged from the deck. The map file is copied into ``root``
    because the sandbox does not expose the application tree.

    Args:
        root: Job directory. It is bind-mounted inside the sandbox.
        font_dir: Directory of staged faces, or None.

    Returns:
        Path of the fontconfig file.
    """
    bundled = Path(__file__).resolve().parent / "fonts" / _OFFICE_METRICS_CONF
    local_map = root / "office-metrics.conf"
    if bundled.is_file():
        local_map.write_bytes(bundled.read_bytes())
    lines = [
        '<?xml version="1.0"?>',
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">',
        "<fontconfig>",
        *_fontconfig_face_edits(),
        '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>',
    ]
    if local_map.is_file():
        lines.append(
            f'  <include ignore_missing="yes">{_xml_attr(str(local_map))}</include>'
        )
    if font_dir is not None and font_dir.is_dir():
        lines.append(f"  <dir>{_xml_attr(str(font_dir))}</dir>")
    lines.append("</fontconfig>")
    path = root / "fonts.conf"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def office_env(root: Path, *, fontconfig_file: str | None = None) -> dict[str, str]:
    """Environment for LibreOffice with no application secrets.

    The child gets ``PATH`` and locale, plus a ``HOME`` inside the job
    directory. Secrets that arrived in the worker environment are not
    copied.

    Args:
        root: Job directory. It becomes ``HOME`` and the temp dir.
        fontconfig_file: Fontconfig file inside the job directory. When
            set, LibreOffice resolves faces from that file.

    Returns:
        A new environment mapping.
    """
    env: dict[str, str] = {
        "PATH": _office_path(),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(root),
        "TMPDIR": str(root),
        "TEMP": str(root),
        "TMP": str(root),
        "XDG_CONFIG_HOME": str(root),
        "XDG_CACHE_HOME": str(root),
    }
    if fontconfig_file:
        env["FONTCONFIG_FILE"] = fontconfig_file
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
    _unshare, timeout_bin = sandbox_tools()
    root = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
    try:
        source = root / f"deck{suffix}"
        outdir = root / "out"
        profile = root / "profile"
        outdir.mkdir()
        source.write_bytes(raw)
        embed_dir = root / "embed-fonts"
        staged = extract_embedded_deck_fonts(raw, embed_dir)
        fontconfig = _job_fontconfig(root, embed_dir if staged else None)
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
        try:
            completed = run_sandboxed(
                inner,
                work=root,
                timeout=limit + 5,
                env=office_env(root, fontconfig_file=str(fontconfig)),
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
