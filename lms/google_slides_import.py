"""Attach a shared Google Slides deck to a live class as one image per page.

The share link is enough when anyone with the link can view. This module
downloads the public PDF export (no teacher OAuth) and renders each page
to PNG. Images are stored with the same class image helper as other live
deck pictures. No roster, names, or answers are written.

If the public export is not readable, the caller may fall back to a
separate Slides read-only token and the thumbnail API. That path does
not use the lesson-deck Drive token.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

MAX_GOOGLE_SLIDE_PAGES = 40
# Stay under gunicorn's 120s worker timeout.
IMPORT_TIME_LIMIT_SECONDS = 45
MAX_SLIDE_EDGE = 1600
MAX_SLIDE_PIXELS = 1_600_000
# Address-space cap for the convert child and the render child (MCK-330).
# One import runs at a time. This PDF library maps about 900 MiB of
# files before the first page, so a 600 MiB cap, and a 1 GiB cap with
# no headroom, kills a normal deck. Resident use for 40 pages stays
# near 150 MiB. ``SLIDE_IMPORT_RSS_BYTES`` is what stops a child from
# filling the 1 GB machine; the address ceiling only has to let the
# library's file maps exist.
SLIDE_IMPORT_AS_BYTES = 2 * 1024 * 1024 * 1024
SLIDE_IMPORT_RSS_BYTES = 512 * 1024 * 1024
_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PNG_BYTES = 3_500_000
_RENDER_TOO_LARGE = 75
_RENDER_TOO_LONG = 76
_READ_CHUNK = 65536
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_URL_ID_RE = re.compile(
    r"(?:/presentation/d/|/file/d/|[?&]id=)([A-Za-z0-9_-]{10,128})"
)
_PUBLIC_HOSTS = {"docs.google.com", "drive.google.com"}


class SlidesShareNotPublic(Exception):
    """The deck cannot be read from its public export link."""

    def __init__(
        self,
        message: str = (
            "This deck is not shared for anyone with the link. "
            "Share it as a viewer, or Connect Google Slides."
        ),
    ) -> None:
        super().__init__(message)



def _ensure_time(deadline: float | None) -> None:
    """Raise when the import deadline has passed.

    Args:
        deadline: ``time.monotonic`` cutoff, or None when unlimited.

    Raises:
        ValueError: The deck took too long to attach.
    """
    if deadline is not None and time.monotonic() >= deadline:
        raise ValueError("That deck took too long to attach.")


def _hop_timeout(timeout: float, deadline: float | None) -> float:
    """Seconds for the next HTTP hop, clamped to the import deadline.

    Args:
        timeout: Caller's per-hop cap.
        deadline: ``time.monotonic`` cutoff, or None.

    Raises:
        ValueError: No time remains.
    """
    _ensure_time(deadline)
    budget = float(timeout)
    if deadline is not None:
        budget = min(budget, deadline - time.monotonic())
    if budget <= 0:
        raise ValueError("That deck took too long to attach.")
    return budget


def _request_timeout(
    timeout: float, deadline: float | None
) -> tuple[float, float]:
    """Connect and idle-read timeouts inside the import deadline.

    ``requests`` applies the read value to idle time between bytes.
    ``read_capped_response`` closes the socket when the deadline arrives,
    which also stops a body that keeps trickling.

    Args:
        timeout: Caller's per-hop cap.
        deadline: ``time.monotonic`` cutoff, or None.

    Returns:
        ``(connect_seconds, read_seconds)``.

    Raises:
        ValueError: No time remains.
    """
    budget = _hop_timeout(timeout, deadline)
    return (min(10.0, budget), budget)


def _response_socket(response: Any) -> socket.socket | None:
    """Return the TCP socket behind a streamed ``requests`` response.

    Test doubles have no socket. urllib3 keeps the live socket on the
    raw response (``raw._fp.fp.raw._sock`` or the connection ``sock``).

    Args:
        response: ``requests`` response or a test double.
    """
    raw = getattr(response, "raw", None)
    if raw is None:
        return None
    seen: set[int] = set()
    pending: list[Any] = [
        raw,
        getattr(raw, "_connection", None),
        getattr(raw, "_fp", None),
    ]
    while pending:
        obj = pending.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, socket.socket):
            return obj
        if len(seen) > 24:
            break
        for name in ("_sock", "sock", "raw", "fp", "_fp", "_connection"):
            child = getattr(obj, name, None)
            if child is not None and child is not obj:
                pending.append(child)
    return None


class _WallClockCut:
    """Close a download socket when the import deadline arrives.

    ``iter_content`` returns only after a whole chunk is buffered, and the
    requests read timeout measures idle time. A server that sends one byte
    at a time never looks idle and never finishes a chunk. Closing the
    socket makes that blocked read return.
    """

    def __init__(self, response: Any, deadline: float | None) -> None:
        """Arm a daemon timer for ``deadline``, when a socket is present.

        Args:
            response: Streamed response whose socket should close.
            deadline: ``time.monotonic`` cutoff, or None.
        """
        self.fired = False
        self._timer: threading.Timer | None = None
        self._sock: socket.socket | None = None
        if deadline is None:
            return
        sock = _response_socket(response)
        if sock is None:
            return
        self._sock = sock
        delay = float(deadline) - time.monotonic()
        if delay <= 0:
            self._cut()
            return
        timer = threading.Timer(delay, self._cut)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cut(self) -> None:
        """Shut the socket down so a blocked read returns."""
        self.fired = True
        sock = self._sock
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def cancel(self) -> None:
        """Disarm the timer after the body finished or was refused."""
        timer = self._timer
        if timer is not None:
            timer.cancel()


def _close_response(response: Any) -> None:
    """Close a streamed response when it has a close method.

    Args:
        response: ``requests`` response or a test double.
    """
    closer = getattr(response, "close", None)
    if callable(closer):
        closer()


def read_capped_response(
    response: Any, limit: int, *, deadline: float | None = None
) -> bytes:
    """Read a response body up to ``limit`` bytes and the import deadline.

    Streamed responses use ``iter_content``. The deadline is checked
    before every chunk. When the response has a real socket, a timer
    closes that socket at the deadline so a slow drip cannot sit inside
    one chunk. A response without ``iter_content`` is read from ``content``.

    Args:
        response: ``requests`` response or a test double.
        limit: Maximum accepted size in bytes.
        deadline: ``time.monotonic`` cutoff, or None.

    Raises:
        ValueError: The body is larger than ``limit``, or the deadline
            passed before the body finished.
    """
    cap = int(limit)

    def _stop(message: str) -> None:
        """Close the stream and refuse the deck.

        Args:
            message: Student-safe error text.
        """
        _close_response(response)
        raise ValueError(message)

    def _check_time() -> None:
        """Refuse when the import deadline has passed."""
        if deadline is not None and time.monotonic() >= float(deadline):
            _stop("That deck took too long to attach.")

    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        try:
            stream = iterator(_READ_CHUNK)
        except TypeError:
            stream = None
        if stream is not None:
            chunks: list[bytes] = []
            total = 0
            pending = iter(stream)
            cut = _WallClockCut(response, deadline)
            try:
                while True:
                    _check_time()
                    try:
                        chunk = next(pending)
                    except StopIteration:
                        break
                    except Exception:
                        if cut.fired or (
                            deadline is not None
                            and time.monotonic() >= float(deadline)
                        ):
                            _stop("That deck took too long to attach.")
                        raise
                    if not chunk:
                        continue
                    block = bytes(chunk)
                    total += len(block)
                    if total > cap:
                        _stop("That deck is too large to attach.")
                    chunks.append(block)
                return b"".join(chunks)
            finally:
                cut.cancel()
    _check_time()
    data = getattr(response, "content", b"") or b""
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)
    data = bytes(data)
    if len(data) > cap:
        raise ValueError("That deck is too large to attach.")
    return data


def slides_api_url_allowed(url: str) -> bool:
    """True when a URL is the Google Slides API over https.

    Args:
        url: Request or redirect target.
    """
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https":
        return False
    return (parsed.hostname or "").lower() == "slides.googleapis.com"


class _LimitedResponse:
    """Small response stand-in after a capped Google GET."""

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = int(status_code)
        self.content = content

    def json(self) -> Any:
        """Parse the body as JSON."""
        import json

        if not self.content:
            return {}
        return json.loads(self.content.decode("utf-8"))

    def raise_for_status(self) -> None:
        """Raise when Google returned an error status."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def limited_google_get(
    client: Any,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 20,
    allow: Any,
    limit: int,
    deadline: float | None = None,
) -> _LimitedResponse:
    """GET with manual Google-only redirects and a running byte cap.

    Args:
        client: ``requests`` module or a test double with ``get``.
        url: Starting URL. It must pass ``allow``.
        headers: Request headers, sent on every hop.
        params: Query params for the first hop.
        timeout: Per-hop cap, also clamped by ``deadline``.
        allow: Predicate for each absolute URL, including redirects.
        limit: Maximum body bytes.
        deadline: ``time.monotonic`` cutoff for the whole import.

    Raises:
        SlidesShareNotPublic: A hop left the allowed Google hosts.
        ValueError: The body or the import exceeded its limit.
    """
    current = str(url)
    if not allow(current):
        raise SlidesShareNotPublic()
    hop_params = params
    for _hop in range(5):
        _ensure_time(deadline)
        response = client.get(
            current,
            headers=headers,
            params=hop_params,
            timeout=_request_timeout(timeout, deadline),
            allow_redirects=False,
            stream=True,
        )
        hop_params = None
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            if status in {301, 302, 303, 307, 308}:
                response_headers = getattr(response, "headers", {}) or {}
                location = ""
                if hasattr(response_headers, "get"):
                    location = str(response_headers.get("Location") or "")
                nxt = location if urlparse(location).netloc else urljoin(current, location)
                if not allow(nxt):
                    raise SlidesShareNotPublic()
                current = nxt
                continue
            body = read_capped_response(response, int(limit), deadline=deadline)
            return _LimitedResponse(status, body)
        finally:
            closer = getattr(response, "close", None)
            if callable(closer):
                closer()
    raise SlidesShareNotPublic()


def _raster_scale(page: Any, requested: float) -> float:
    """Shrink ``requested`` so the pixmap stays under the pixel cap.

    Args:
        page: A pymupdf page.
        requested: Preferred scale before the cap.

    Returns:
        Scale to pass to ``get_pixmap``.
    """
    rect = page.rect
    width = max(float(rect.width), 1.0)
    height = max(float(rect.height), 1.0)
    scale = min(float(requested), MAX_SLIDE_EDGE / max(width, height))
    pixels = width * height * scale * scale
    if pixels > MAX_SLIDE_PIXELS:
        scale *= (MAX_SLIDE_PIXELS / pixels) ** 0.5
    # pymupdf rounds pixel edges up. Leave a little room.
    while scale > 0.05 and (
        int(width * scale + 0.999) > MAX_SLIDE_EDGE
        or int(height * scale + 0.999) > MAX_SLIDE_EDGE
        or int(width * scale + 0.999) * int(height * scale + 0.999) > MAX_SLIDE_PIXELS
    ):
        scale *= 0.98
    return scale


def require_presentation_id(value: str) -> str:
    """Return a Google file id, or raise ``ValueError``.

    Args:
        value: Bare id or the id already extracted from a share link.
    """
    token = str(value or "").strip()
    if not _ID_RE.fullmatch(token):
        raise ValueError("Paste a Google Slides share link.")
    return token


def parse_presentation_id(raw: str) -> str:
    """Pull the file id from a Google Slides or Drive share link.

    Args:
        raw: ``https://docs.google.com/presentation/d/{id}/edit`` or a
            Drive file link. A bare id is accepted.

    Raises:
        ValueError: The text is not a Google Slides share link.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Paste a Google Slides share link.")
    if _ID_RE.fullmatch(text):
        return text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _PUBLIC_HOSTS:
        raise ValueError("Paste a Google Slides share link.")
    match = _URL_ID_RE.search(f"{parsed.path}?{parsed.query}")
    if not match:
        raise ValueError("Paste a Google Slides share link.")
    return require_presentation_id(match.group(1))


def export_pdf_url(presentation_id: str) -> str:
    """Public PDF export URL for one presentation id.

    Args:
        presentation_id: Validated Google file id.
    """
    token = require_presentation_id(presentation_id)
    return f"https://docs.google.com/presentation/d/{token}/export/pdf"


def public_redirect_allowed(url: str) -> bool:
    """True when a PDF-export redirect stays on Google's download hosts.

    Args:
        url: Absolute redirect target.
    """
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host in _PUBLIC_HOSTS:
        return True
    return host == "googleusercontent.com" or host.endswith(".googleusercontent.com")


def thumbnail_url_allowed(url: str) -> bool:
    """True when a Slides thumbnail URL is an https Google download.

    Args:
        url: ``contentUrl`` from the thumbnail API.
    """
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "googleusercontent.com" or host.endswith(".googleusercontent.com")


def fetch_public_pdf(
    presentation_id: str,
    *,
    http: Any | None = None,
    timeout: float = 30,
    deadline: float | None = None,
) -> bytes:
    """Download the public PDF export. Does not use a teacher OAuth token.

    The body is streamed and refused once it passes 20 MB. Login redirects
    and HTML interstitials are refused. Redirects may only stay on Google
    Docs, Drive, or googleusercontent.

    Args:
        presentation_id: Validated Google file id.
        http: ``requests`` module or a test double with ``get``.
        timeout: Seconds for each hop, also clamped by ``deadline``.
        deadline: ``time.monotonic`` cutoff for the whole import.

    Returns:
        PDF bytes.

    Raises:
        SlidesShareNotPublic: The deck is not publicly readable.
        ValueError: The body is not a PDF, is too large, or the import
            ran out of time.
    """
    client = http or requests
    url = export_pdf_url(presentation_id)
    headers = {"User-Agent": "LLOVES-LMS"}
    for _hop in range(5):
        _ensure_time(deadline)
        response = client.get(
            url,
            headers=headers,
            timeout=_request_timeout(timeout, deadline),
            allow_redirects=False,
            stream=True,
        )
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            if status in {301, 302, 303, 307, 308}:
                location = ""
                response_headers = getattr(response, "headers", {}) or {}
                if hasattr(response_headers, "get"):
                    location = str(response_headers.get("Location") or "")
                nxt = location if urlparse(location).netloc else urljoin(url, location)
                if not public_redirect_allowed(nxt):
                    raise SlidesShareNotPublic()
                url = nxt
                continue
            if status != 200:
                raise SlidesShareNotPublic()
            data = read_capped_response(response, _MAX_PDF_BYTES, deadline=deadline)
        finally:
            closer = getattr(response, "close", None)
            if callable(closer):
                closer()
        if not data.startswith(b"%PDF"):
            raise SlidesShareNotPublic()
        return data
    raise SlidesShareNotPublic()


def _scales_for_page_count(count: int) -> tuple[float, ...]:
    """Raster scales to try, smallest last.

    Decks of 16 pages or more skip the largest scale so the first pixmap
    is not the biggest one.

    Args:
        count: Pages that will be rendered.
    """
    if int(count) >= 16:
        return (1.0, 0.6)
    return (1.5, 1.0, 0.6)


def _png_for_page(page: Any, *, scales: tuple[float, ...]) -> bytes:
    """Rasterize one page and drop the pixmap before the next scale.

    Args:
        page: A pymupdf page.
        scales: Scales to try until the PNG fits.

    Raises:
        ValueError: The page is not a PNG or stays over the byte cap.
    """
    import fitz

    png = b""
    for scale in scales:
        used = _raster_scale(page, scale)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(used, used), alpha=False)
        try:
            png = pixmap.tobytes("png")
        finally:
            pixmap = None
        if len(png) <= _MAX_PNG_BYTES:
            break
    if not png.startswith(b"\x89PNG"):
        raise ValueError("That download was not a slide deck.")
    if len(png) > _MAX_PNG_BYTES:
        raise ValueError("That deck is too large to attach.")
    return png


def render_pdf_pages(
    pdf_bytes: bytes, *, deadline: float | None = None
) -> tuple[list[bytes], bool]:
    """Render each PDF page to a PNG, capped at ``MAX_GOOGLE_SLIDE_PAGES``.

    Each page is scaled so the long edge and the pixel count stay under
    the raster cap before pymupdf allocates a pixmap. Production imports
    use ``render_pdf_to_dir`` so the PNGs are not kept in this list.

    Args:
        pdf_bytes: A Google Slides PDF export.
        deadline: ``time.monotonic`` cutoff for the whole import.

    Returns:
        ``(png_pages, truncated)``. ``truncated`` is true when the deck had
        more pages than the cap.

    Raises:
        ValueError: The file has no pages, a page is still too large, or
            the import ran out of time.
    """
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("That download was not a slide deck.")
    import fitz

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - pymupdf raises several types
        raise ValueError("That download was not a slide deck.") from exc
    try:
        count = int(document.page_count or 0)
        if count < 1:
            raise ValueError("That deck has no slides.")
        truncated = count > MAX_GOOGLE_SLIDE_PAGES
        limit = min(count, MAX_GOOGLE_SLIDE_PAGES)
        scales = _scales_for_page_count(limit)
        pages: list[bytes] = []
        for index in range(limit):
            _ensure_time(deadline)
            png = _png_for_page(document.load_page(index), scales=scales)
            pages.append(png)
        return pages, truncated
    finally:
        document.close()


def _prlimit_prefix(address_limit: int) -> list[str]:
    """``prlimit --as`` argv, or empty when the tool is not installed.

    Args:
        address_limit: Address-space bytes for the child.
    """
    binary = shutil.which("prlimit") or "/usr/bin/prlimit"
    if not Path(binary).is_file():
        return []
    return [binary, f"--as={int(address_limit)}", "--"]


def _cap_address_space(limit: int) -> None:
    """Apply an address-space cap in this process.

    Args:
        limit: Maximum address space in bytes.
    """
    import resource

    cap = max(1, int(limit))
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def _resident_bytes() -> int:
    """Resident set of this process, in bytes.

    Returns:
        VmRSS, or 0 when ``/proc`` cannot be read. File maps that make
        the address space large do not count here.
    """
    try:
        text = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1]) * 1024
    return 0


def _render_pdf_file(pdf_path: Path, out_dir: Path, budget_s: float) -> int:
    """Write one PNG per page and a manifest. One page is in memory at a time.

    Args:
        pdf_path: PDF on disk.
        out_dir: Directory for ``page-000.png`` and ``manifest.json``.
        budget_s: Seconds allowed for the raster.

    Returns:
        0 on success, 75 when a page is too large, 76 when time runs out,
        1 when the file is not a deck.
    """
    deadline = time.monotonic() + max(0.0, float(budget_s))
    import fitz

    if _resident_bytes() > SLIDE_IMPORT_RSS_BYTES:
        sys.stderr.write("lloves-render-error too-large\n")
        return _RENDER_TOO_LARGE
    try:
        document = fitz.open(str(pdf_path))
    except MemoryError:
        sys.stderr.write("lloves-render-error too-large\n")
        return _RENDER_TOO_LARGE
    except Exception:  # noqa: BLE001 - pymupdf raises several types
        sys.stderr.write("lloves-render-error bad-pdf\n")
        return 1
    try:
        count = int(document.page_count or 0)
        if count < 1:
            sys.stderr.write("lloves-render-error bad-pdf\n")
            return 1
        truncated = count > MAX_GOOGLE_SLIDE_PAGES
        limit = min(count, MAX_GOOGLE_SLIDE_PAGES)
        scales = _scales_for_page_count(limit)
        out_dir.mkdir(parents=True, exist_ok=True)
        for index in range(limit):
            if time.monotonic() >= deadline:
                sys.stderr.write("lloves-render-error too-long\n")
                return _RENDER_TOO_LONG
            if _resident_bytes() > SLIDE_IMPORT_RSS_BYTES:
                sys.stderr.write("lloves-render-error too-large\n")
                return _RENDER_TOO_LARGE
            try:
                png = _png_for_page(document.load_page(index), scales=scales)
            except MemoryError:
                sys.stderr.write("lloves-render-error too-large\n")
                return _RENDER_TOO_LARGE
            except ValueError as exc:
                text = str(exc)
                if "too large" in text:
                    sys.stderr.write("lloves-render-error too-large\n")
                    return _RENDER_TOO_LARGE
                if "too long" in text:
                    sys.stderr.write("lloves-render-error too-long\n")
                    return _RENDER_TOO_LONG
                sys.stderr.write("lloves-render-error bad-pdf\n")
                return 1
            dest = out_dir / f"page-{index:03d}.png"
            dest.write_bytes(png)
            del png
            if _resident_bytes() > SLIDE_IMPORT_RSS_BYTES:
                sys.stderr.write("lloves-render-error too-large\n")
                return _RENDER_TOO_LARGE
        (out_dir / "manifest.json").write_text(
            json.dumps({"count": limit, "truncated": bool(truncated)}),
            encoding="utf-8",
        )
        return 0
    finally:
        document.close()


def _render_failure(
    returncode: int, err_text: str, *, address_limit: int
) -> ValueError:
    """Map a capped render child to a teacher-facing error.

    A child that dies under a tiny cap is always "too large", including
    when the loader exits before it can print a memory token. A normal
    cap still reports a file that is not a PDF as "not a slide deck".

    Args:
        returncode: Child status. Negative values are signals.
        err_text: Child stderr. It is not shown to the teacher.
        address_limit: Address-space bytes the child was started with.
    """
    lowered = err_text.lower()
    if returncode == _RENDER_TOO_LONG or "too-long" in lowered:
        return ValueError("That deck took too long to attach.")
    explicit_bad = (
        returncode == 1 and "bad-pdf" in lowered and "too-large" not in lowered
    )
    tiny_cap = int(address_limit) < 64 * 1024 * 1024
    if explicit_bad and not tiny_cap:
        return ValueError("That download was not a slide deck.")
    if returncode != 0:
        return ValueError("That deck is too large to attach.")
    return ValueError("That download was not a slide deck.")


def render_pdf_to_dir(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    deadline: float | None = None,
    address_limit: int | None = None,
) -> tuple[list[Path], bool]:
    """Render a PDF on disk, one PNG at a time, in a capped subprocess.

    The parent does not read the PNG bodies. The child is started with
    ``prlimit --as`` so a deck that overruns the cap dies in that process.
    The parent maps that death to a teacher-facing error and stays up.

    Args:
        pdf_path: PDF file. The parent checks the first bytes only.
        out_dir: Directory that receives ``page-000.png`` and
            ``manifest.json``.
        deadline: ``time.monotonic`` cutoff, or None.
        address_limit: Address-space bytes. Defaults to
            ``SLIDE_IMPORT_AS_BYTES``.

    Returns:
        ``(page_paths, truncated)``.

    Raises:
        ValueError: The file is not a deck, a page is too large, the
            child ran out of memory, or the import ran out of time.
    """
    source = Path(pdf_path)
    target = Path(out_dir)
    with source.open("rb") as handle:
        magic = handle.read(5)
    if not magic.startswith(b"%PDF"):
        raise ValueError("That download was not a slide deck.")
    if deadline is None:
        budget = float(IMPORT_TIME_LIMIT_SECONDS)
    else:
        budget = float(deadline) - time.monotonic()
    if budget <= 0:
        raise ValueError("That deck took too long to attach.")
    limit = int(address_limit or SLIDE_IMPORT_AS_BYTES)
    target.mkdir(parents=True, exist_ok=True)
    command = [
        *_prlimit_prefix(limit),
        sys.executable,
        str(Path(__file__).resolve()),
        "--render-pdf",
        str(source),
        str(target),
        str(budget),
        str(limit),
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _out, err = proc.communicate(timeout=budget + 5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise ValueError("That deck took too long to attach.") from None
    err_text = (err or b"").decode("utf-8", "replace")
    code = int(proc.returncode or 0)
    if code != 0:
        raise _render_failure(code, err_text, address_limit=limit) from None
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("That download was not a slide deck.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("That download was not a slide deck.") from exc
    count = int(manifest.get("count") or 0)
    if count < 1:
        raise ValueError("That deck has no slides.")
    paths = [target / f"page-{index:03d}.png" for index in range(count)]
    for path in paths:
        if not path.is_file():
            raise ValueError("That download was not a slide deck.")
    return paths, bool(manifest.get("truncated"))


def write_shared_deck_pages(
    school: Any,
    presentation_id: str,
    *,
    user: dict[str, Any] | None,
    http: Any | None,
    deadline: float | None,
    work_dir: Path,
) -> tuple[list[Path], bool, str]:
    """Download a share link and write one PNG per page under ``work_dir``.

    Public PDF export is rendered in a capped subprocess. A private deck
    uses the read-only thumbnail API, then each PNG is spilled to disk
    before the caller stores it. This function does not return PNG bodies.

    Args:
        school: ``SchoolDB`` holding the read-only refresh token.
        presentation_id: Validated Google file id.
        user: Signed-in staff row, used only for the read-only fallback.
        http: Optional HTTP client for tests.
        deadline: ``time.monotonic`` cutoff for the whole import.
        work_dir: Directory the caller deletes after the pages are stored.

    Returns:
        ``(page_paths, truncated, source)``. ``source`` is
        ``public_export`` or ``oauth_thumbnail``.

    Raises:
        ValueError: The file is not a usable deck, or time ran out.
        SlidesShareNotPublic: Neither path could read the deck.
    """
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        pdf = fetch_public_pdf(presentation_id, http=http, deadline=deadline)
        pdf_path = root / "deck.pdf"
        pdf_path.write_bytes(pdf)
        del pdf
        paths, truncated = render_pdf_to_dir(
            pdf_path, root / "pages", deadline=deadline
        )
        pdf_path.unlink(missing_ok=True)
        return paths, truncated, "public_export"
    except SlidesShareNotPublic:
        pngs, truncated = _download_with_oauth(
            school, presentation_id, user=user, http=http, deadline=deadline
        )
        page_dir = root / "pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, raw in enumerate(pngs):
            dest = page_dir / f"page-{index:03d}.png"
            dest.write_bytes(bytes(raw))
            paths.append(dest)
        del pngs
        return paths, truncated, "oauth_thumbnail"


def import_shared_deck(
    school: Any,
    session_id: int,
    url: str,
    *,
    user: dict[str, Any] | None,
    http: Any | None = None,
    time_limit: float = IMPORT_TIME_LIMIT_SECONDS,
    default_published: bool = False,
    title: str = "",
    job_id: str | None = None,
) -> dict[str, Any]:
    """Import one share link onto the live session as image pages.

    Public PDF export is tried first. A deck that is not link-shared falls
    back to the operator's Slides read-only token and the thumbnail API.
    The whole import stops at ``time_limit`` seconds, under the worker
    timeout.

    Args:
        school: ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        url: Google Slides share link.
        user: Signed-in staff row, used only for the read-only fallback.
        http: Optional HTTP client for tests.
        time_limit: Seconds allowed for this import. ``0`` fails immediately.
        default_published: Initial shared flag stored with the course deck.
        title: Deck name. Blank uses ``Google Slides``.
        job_id: Slide import row. When set, the deck is stored only
            while that row is still running.

    Returns:
        Teacher deck dict (every page, published flags, current index).

    Raises:
        ValueError: The link or the file is not a usable deck, or the
            import ran out of time.
        SlidesShareNotPublic: Neither the public export nor the read-only
            token could read it.
    """
    if time_limit <= 0:
        raise ValueError("That deck took too long to attach.")
    deadline = time.monotonic() + float(time_limit)
    _ensure_time(deadline)
    presentation_id = parse_presentation_id(url)
    work_dir = Path(tempfile.mkdtemp(prefix="lloves-gslides-"))
    try:
        paths, truncated, source = write_shared_deck_pages(
            school,
            presentation_id,
            user=user,
            http=http,
            deadline=deadline,
            work_dir=work_dir,
        )
        return school.import_google_slides_pages(
            int(session_id),
            presentation_id=presentation_id,
            page_paths=paths,
            source=source,
            truncated=truncated,
            default_published=bool(default_published),
            title=title or "Google Slides",
            job_id=job_id,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _download_with_oauth(
    school: Any,
    presentation_id: str,
    *,
    user: dict[str, Any] | None,
    http: Any | None,
    deadline: float | None = None,
) -> tuple[list[bytes], bool]:
    """Download slide PNGs with the operator's Slides read-only token.

    Args:
        school: ``SchoolDB`` holding the read-only refresh token.
        presentation_id: Validated Google file id.
        user: Signed-in staff row.
        http: Optional HTTP client.
        deadline: ``time.monotonic`` cutoff for the whole import.

    Returns:
        PNG bytes and whether the deck was longer than the page cap.

    Raises:
        SlidesShareNotPublic: No read-only token, or Google refused the file.
    """
    from live_class_slides import GoogleSlidesClient, SlidesConnectRequired

    if not user:
        raise SlidesShareNotPublic()
    try:
        client = GoogleSlidesClient(school, user=user, http=http)
        return client.download_slide_pngs(presentation_id, deadline=deadline)
    except SlidesConnectRequired as exc:
        raise SlidesShareNotPublic() from exc


def _render_cli(argv: list[str]) -> int:
    """Render one PDF to disk under this process's address cap.

    Args:
        argv: ``--render-pdf pdf out_dir budget_seconds address_bytes``.

    Returns:
        0 on success, 75 when a page is too large or memory is exhausted,
        76 when the budget runs out, 1 otherwise.
    """
    if not argv or argv[0] != "--render-pdf" or len(argv) != 5:
        sys.stderr.write("lloves-render-error bad-pdf\n")
        return 1
    _flag, pdf, out_dir, budget_s, address = argv
    try:
        _cap_address_space(int(float(address)))
        return _render_pdf_file(Path(pdf), Path(out_dir), float(budget_s))
    except MemoryError:
        sys.stderr.write("lloves-render-error too-large\n")
        return _RENDER_TOO_LARGE
    except OSError as exc:
        if exc.errno == 12:
            sys.stderr.write("lloves-render-error too-large\n")
            return _RENDER_TOO_LARGE
        sys.stderr.write("lloves-render-error bad-pdf\n")
        return 1
    except Exception as exc:  # noqa: BLE001 - a capped child must not traceback
        if type(exc).__name__ in {"MemoryError", "BrokenPipeError"}:
            sys.stderr.write("lloves-render-error too-large\n")
            return _RENDER_TOO_LARGE
        sys.stderr.write("lloves-render-error bad-pdf\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(_render_cli(sys.argv[1:]))
