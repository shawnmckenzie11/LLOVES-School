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

import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

MAX_GOOGLE_SLIDE_PAGES = 40
# Stay under gunicorn's 120s worker timeout.
IMPORT_TIME_LIMIT_SECONDS = 45
MAX_SLIDE_EDGE = 1600
MAX_SLIDE_PIXELS = 1_600_000
_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PNG_BYTES = 3_500_000
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
    """Connect and socket-read timeouts inside the import deadline.

    ``requests`` applies the read value to each socket recv, not to the
    whole download. It is the time still left, so one blocked read cannot
    run past the deadline. Callers also check the deadline between chunks.

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
    before every chunk, so a slow drip cannot outlive the import. A
    response without ``iter_content`` is read from ``content``.

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
            while True:
                _check_time()
                try:
                    chunk = next(pending)
                except StopIteration:
                    break
                if not chunk:
                    continue
                block = bytes(chunk)
                total += len(block)
                if total > cap:
                    _stop("That deck is too large to attach.")
                chunks.append(block)
            return b"".join(chunks)
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


def render_pdf_pages(
    pdf_bytes: bytes, *, deadline: float | None = None
) -> tuple[list[bytes], bool]:
    """Render each PDF page to a PNG, capped at ``MAX_GOOGLE_SLIDE_PAGES``.

    Each page is scaled so the long edge and the pixel count stay under
    the raster cap before pymupdf allocates a pixmap.

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
        pages: list[bytes] = []
        for index in range(limit):
            _ensure_time(deadline)
            page = document.load_page(index)
            png = b""
            for scale in (1.5, 1.0, 0.6):
                used = _raster_scale(page, scale)
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(used, used),
                    alpha=False,
                )
                png = pixmap.tobytes("png")
                if len(png) <= _MAX_PNG_BYTES:
                    break
            if not png.startswith(b"\x89PNG"):
                raise ValueError("That download was not a slide deck.")
            if len(png) > _MAX_PNG_BYTES:
                raise ValueError("That deck is too large to attach.")
            pages.append(png)
        return pages, truncated
    finally:
        document.close()


def import_shared_deck(
    school: Any,
    session_id: int,
    url: str,
    *,
    user: dict[str, Any] | None,
    http: Any | None = None,
    time_limit: float = IMPORT_TIME_LIMIT_SECONDS,
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
    source = "public_export"
    truncated = False
    try:
        pdf = fetch_public_pdf(presentation_id, http=http, deadline=deadline)
        pngs, truncated = render_pdf_pages(pdf, deadline=deadline)
    except SlidesShareNotPublic:
        pngs, truncated = _download_with_oauth(
            school, presentation_id, user=user, http=http, deadline=deadline
        )
        source = "oauth_thumbnail"
    return school.import_google_slides_pages(
        int(session_id),
        presentation_id=presentation_id,
        page_pngs=pngs,
        source=source,
        truncated=truncated,
    )


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
