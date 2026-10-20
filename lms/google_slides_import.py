"""Attach a shared Google Slides deck to a live class as one image per page.

The share link is enough when anyone with the link can view. This module
downloads the public PDF export (no teacher OAuth) and renders each page
to PNG. Images are stored with the same class image helper as other live
deck pictures. No roster, names, or answers are written.

If the public export is not readable, the caller may fall back to the
operator's Connect Google Slides token and the Slides thumbnail API.
That path is the only one that uses a stored OAuth token.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

MAX_GOOGLE_SLIDE_PAGES = 40
_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PNG_BYTES = 3_500_000
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
) -> bytes:
    """Download the public PDF export. Does not use a teacher OAuth token.

    Login redirects and HTML interstitials are refused. Redirects may only
    stay on Google Docs, Drive, or googleusercontent.

    Args:
        presentation_id: Validated Google file id.
        http: ``requests`` module or a test double with ``get``.
        timeout: Seconds for each hop.

    Returns:
        PDF bytes.

    Raises:
        SlidesShareNotPublic: The deck is not publicly readable.
        ValueError: The body is not a PDF or is too large.
    """
    client = http or requests
    url = export_pdf_url(presentation_id)
    headers = {"User-Agent": "LLOVES-LMS"}
    for _hop in range(5):
        response = client.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
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
        data = getattr(response, "content", b"") or b""
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        data = bytes(data)
        if len(data) > _MAX_PDF_BYTES:
            raise ValueError("That deck is too large to attach.")
        if not data.startswith(b"%PDF"):
            raise SlidesShareNotPublic()
        return data
    raise SlidesShareNotPublic()


def render_pdf_pages(pdf_bytes: bytes) -> tuple[list[bytes], bool]:
    """Render each PDF page to a PNG, capped at ``MAX_GOOGLE_SLIDE_PAGES``.

    Args:
        pdf_bytes: A Google Slides PDF export.

    Returns:
        ``(png_pages, truncated)``. ``truncated`` is true when the deck had
        more pages than the cap.

    Raises:
        ValueError: The file has no pages or cannot be read.
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
            page = document.load_page(index)
            png = b""
            for scale in (1.5, 1.0, 0.6):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                png = pixmap.tobytes("png")
                if len(png) <= _MAX_PNG_BYTES:
                    break
            if not png.startswith(b"\x89PNG"):
                raise ValueError("That download was not a slide deck.")
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
) -> dict[str, Any]:
    """Import one share link onto the live session as image pages.

    Public PDF export is tried first. A deck that is not link-shared falls
    back to the signed-in operator's Connect Google Slides token and the
    thumbnail API.

    Args:
        school: ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        url: Google Slides share link.
        user: Signed-in staff row, used only for the OAuth fallback.
        http: Optional HTTP client for tests.

    Returns:
        Teacher deck dict (every page, published flags, current index).

    Raises:
        ValueError: The link or the file is not a usable deck.
        SlidesShareNotPublic: Neither the public export nor OAuth could read it.
    """
    presentation_id = parse_presentation_id(url)
    source = "public_export"
    truncated = False
    try:
        pdf = fetch_public_pdf(presentation_id, http=http)
        pngs, truncated = render_pdf_pages(pdf)
    except SlidesShareNotPublic:
        pngs = _download_with_oauth(school, presentation_id, user=user, http=http)
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
) -> list[bytes]:
    """Download slide PNGs with the operator's stored Slides token.

    Args:
        school: ``SchoolDB`` holding the refresh token.
        presentation_id: Validated Google file id.
        user: Signed-in staff row.
        http: Optional HTTP client.

    Raises:
        SlidesShareNotPublic: No token, or Google refused the file.
    """
    from live_class_slides import GoogleSlidesClient, SlidesConnectRequired

    if not user:
        raise SlidesShareNotPublic()
    try:
        client = GoogleSlidesClient(school, user=user, http=http)
        return client.download_slide_pngs(presentation_id)
    except SlidesConnectRequired as exc:
        raise SlidesShareNotPublic() from exc
