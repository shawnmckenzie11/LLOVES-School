"""Mirror remote Canvas/Equatio bank images into LMS blob storage."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    from content_store import ContentBlobStore
except ImportError:
    from lms.content_store import ContentBlobStore

_ALLOWED_HOST_SUFFIXES = (
    "instructure.com",
    "wiris.net",
    "equatio-api.texthelp.com",
    "i.gyazo.com",
)
_EXECUTE_API_HOST_RE = re.compile(
    r"^[a-z0-9-]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com$",
    re.IGNORECASE,
)
_MAX_REDIRECTS = 3
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_READ_CHUNK = 64 * 1024
_REFUSAL_TTL_S = 3600.0
_SNIFF_EXT = {
    "png": ".png",
    "jpeg": ".jpg",
    "gif": ".gif",
    "webp": ".webp",
    "svg": ".svg",
}
_SNIFF_MIME = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}
_HOSTILE_SVG_RE = re.compile(
    br"(?:<\s*script\b|<\s*foreignobject\b|javascript\s*:|(?:^|[\s/])on[a-z0-9]+\s*=)",
    re.IGNORECASE,
)
_refusal_lock = threading.Lock()
_refusals: dict[tuple[int, str], float] = {}
_inflight: dict[tuple[int, str], threading.Event] = {}
_flight_owner = threading.local()
_IMG_TITLE_LATEX_RE = re.compile(
    r"""title\s*=\s*["']([^"']*\\[^"']*)["']""",
    re.IGNORECASE,
)
_IMG_ALT_LATEX_RE = re.compile(
    r"""alt\s*=\s*["']([^"']*\\[^"']*)["']""",
    re.IGNORECASE,
)
_EQUATION_IMG_RE = re.compile(
    r"""<img[^>]*class\s*=\s*["'][^"']*equation_image[^"']*["'][^>]*>""",
    re.IGNORECASE,
)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def ensure_bank_image_cache_schema(school: Any) -> None:
    """Create the url→blob cache table when missing."""
    with school._lock:
        school.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bank_image_cache (
                library_id INTEGER NOT NULL,
                source_url TEXT NOT NULL,
                relpath TEXT NOT NULL,
                blob_sha TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (library_id, source_url)
            )
            """
        )
        school.conn.commit()


def _host_allowed(hostname: str) -> bool:
    """Return True when a parsed hostname may serve a bank image.

    The match is the whole hostname or a dot-boundary suffix. A lookalike
    such as ``evilinstructure.com`` does not pass.

    Args:
        hostname: Hostname from ``urlparse``, without userinfo or a port.
    """
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    if _EXECUTE_API_HOST_RE.fullmatch(host):
        return True
    for suffix in _ALLOWED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def is_remote_bank_image_url(url: str) -> bool:
    """Return True when a src URL should be mirrored locally.

    Only https URLs are accepted. The hostname is parsed, so a fragment,
    query, or userinfo string cannot borrow an allowlisted name.

    Args:
        url: Image ``src`` from bank HTML.
    """
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https":
        return False
    return _host_allowed(parsed.hostname or "")


def clear_bank_image_refusals() -> None:
    """Drop the in-memory refusal cache so a test starts with no memory of failures."""
    with _refusal_lock:
        _refusals.clear()
        _inflight.clear()


def _refusal_key(library_id: int, url: str) -> tuple[int, str]:
    """Return the negative-cache key for one library and source URL.

    Args:
        library_id: Owning ``content_libraries.id``.
        url: Remote image URL.
    """
    return (int(library_id), url)


def _refusal_active(library_id: int, url: str) -> bool:
    """Return True when this URL was refused recently enough to skip the network.

    Args:
        library_id: Owning library id.
        url: Remote image URL.
    """
    key = _refusal_key(library_id, url)
    now = time.monotonic()
    with _refusal_lock:
        expiry = _refusals.get(key)
        if expiry is None:
            return False
        if expiry <= now:
            _refusals.pop(key, None)
            return False
        return True


def _remember_refusal(library_id: int, url: str) -> None:
    """Remember a refused or failed download for about one hour.

    Args:
        library_id: Owning library id.
        url: Remote image URL that must not be fetched again yet.
    """
    with _refusal_lock:
        _refusals[_refusal_key(library_id, url)] = time.monotonic() + _REFUSAL_TTL_S


def _forget_refusal(library_id: int, url: str) -> None:
    """Drop a refusal after a positive cache hit or a successful store.

    Args:
        library_id: Owning library id.
        url: Remote image URL.
    """
    with _refusal_lock:
        _refusals.pop(_refusal_key(library_id, url), None)


def _flight_keys() -> set[tuple[int, str]]:
    """Return the URLs this thread is already downloading."""
    keys = getattr(_flight_owner, "keys", None)
    if keys is None:
        keys = set()
        _flight_owner.keys = keys
    return keys


def _claim_flight(library_id: int, url: str) -> tuple[threading.Event, bool]:
    """Claim the single in-flight download for one URL, or join the owner.

    Args:
        library_id: Owning library id.
        url: Remote image URL.

    Returns:
        The event the owner sets when the download finishes, and whether
        this caller must perform the download.
    """
    key = _refusal_key(library_id, url)
    with _refusal_lock:
        existing = _inflight.get(key)
        if existing is not None:
            return existing, False
        event = threading.Event()
        _inflight[key] = event
        return event, True


def extract_latex_from_img_tag(tag: str) -> str:
    """Pull LaTeX from a Canvas ``equation_image`` title or alt attribute."""
    raw = str(tag or "")
    match = _IMG_TITLE_LATEX_RE.search(raw) or _IMG_ALT_LATEX_RE.search(raw)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def latex_img_tag_to_math_span(tag: str) -> str:
    """Replace an equation-image tag with a KaTeX-ready span when possible."""
    latex = extract_latex_from_img_tag(tag)
    if not latex:
        return tag
    escaped = (
        latex.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )
    return f'<span class="math-latex" data-latex="{escaped}">{escaped}</span>'


def _svg_document(payload: bytes) -> bool:
    """Return True when the body is an SVG document.

    The root element must be ``svg`` after an optional XML prolog and
    leading whitespace. HTML that mentions ``svg`` later is not an SVG.

    Args:
        payload: Response body.
    """
    text = payload.lstrip(b"\xef\xbb\xbf \t\r\n")
    lower = text.lower()
    if lower.startswith(b"<?xml"):
        end = lower.find(b"?>")
        if end < 0:
            return False
        text = text[end + 2 :].lstrip(b" \t\r\n")
        lower = text.lower()
    if not lower.startswith(b"<svg"):
        return False
    if len(text) <= 4:
        return False
    return text[4:5] in b" \t\r\n>/"


def _sniff_image_kind(payload: bytes) -> str | None:
    """Return the image kind from magic bytes, ignoring Content-Type.

    SVG is accepted only as a document whose root is ``svg``. A script,
    an ``on*`` handler, a ``javascript:`` URL, or ``foreignObject`` refuses
    the file.

    Args:
        payload: Response body.
    """
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    if not _svg_document(payload):
        return None
    if _HOSTILE_SVG_RE.search(payload):
        return None
    return "svg"


class _Redirect(Exception):
    """One redirect hop that must be allowlisted before it is opened."""

    def __init__(self, location: str) -> None:
        """Store the next URL.

        Args:
            location: Absolute Location target.
        """
        super().__init__(location)
        self.location = location


class _CaptureRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib from following a redirect on its own."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        """Close this hop and hand the next URL back to the caller.

        Args:
            req: Request that was redirected.
            fp: Redirect response body.
            code: HTTP status.
            msg: Reason phrase.
            headers: Response headers.
            newurl: Location target.

        Raises:
            _Redirect: Always. The next host is checked by the caller.
        """
        if fp is not None:
            try:
                fp.close()
            except OSError:
                pass
        base = str(getattr(req, "full_url", "") or "")
        raise _Redirect(urllib.parse.urljoin(base, str(newurl)))


def _open_remote(request: urllib.request.Request, timeout: float) -> Any:
    """Open one URL without following redirects.

    Tests patch this function. A redirect surfaces as ``_Redirect``.

    Args:
        request: Prepared request whose host was already allowlisted.
        timeout: Socket timeout in seconds.

    Returns:
        The open response. The caller reads and closes it.
    """
    opener = urllib.request.build_opener(_CaptureRedirect)
    return opener.open(request, timeout=timeout)


def _read_capped(response: Any) -> bytes:
    """Read a response body up to the image size cap.

    Args:
        response: Open HTTP response.

    Raises:
        ValueError: The body is larger than ``_MAX_IMAGE_BYTES``.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        block = response.read(_READ_CHUNK)
        if not block:
            break
        total += len(block)
        if total > _MAX_IMAGE_BYTES:
            raise ValueError("remote image exceeds size cap")
        chunks.append(block)
    return b"".join(chunks)


def _fetch_remote(url: str, *, timeout: float = 20.0) -> bytes:
    """Download one remote image, re-checking every redirect hop.

    At most three redirects are followed. Each hop must be https on an
    allowlisted host. The body is streamed and capped.

    Args:
        url: Initial https URL.
        timeout: Socket timeout in seconds for each hop.

    Returns:
        Response body.

    Raises:
        ValueError: Disallowed host, too many redirects, or an oversized body.
        urllib.error.URLError: Transport or HTTP failure.
    """
    current = str(url)
    for hop in range(_MAX_REDIRECTS + 1):
        if not is_remote_bank_image_url(current):
            raise ValueError("remote image host is not allowed")
        request = urllib.request.Request(
            current,
            headers={"User-Agent": "LLOVES-LMS/1.0 bank-image-mirror"},
        )
        try:
            response = _open_remote(request, timeout)
        except _Redirect as redirect:
            if hop >= _MAX_REDIRECTS:
                raise ValueError("remote image redirected too many times") from redirect
            current = redirect.location
            continue
        except urllib.error.HTTPError as exc:
            closer = getattr(exc, "close", None)
            if callable(closer):
                closer()
            raise
        try:
            return _read_capped(response)
        finally:
            closer = getattr(response, "close", None)
            if callable(closer):
                closer()
    raise ValueError("remote image redirected too many times")


def _cached_relpath(school: Any, library_id: int, url: str) -> str | None:
    """Return the stored mirror path when this URL is already cached.

    Args:
        school: Open ``SchoolDB``.
        library_id: Owning library id.
        url: Remote image URL.
    """
    with school._lock:
        cached = school.conn.execute(
            """
            SELECT relpath FROM bank_image_cache
            WHERE library_id = ? AND source_url = ?
            """,
            (int(library_id), url),
        ).fetchone()
    if cached is None:
        return None
    return str(cached["relpath"])


def _store_mirrored_image(
    school: Any,
    library_id: int,
    url: str,
    payload: bytes,
    kind: str,
) -> str:
    """Write one accepted image into the blob store and the URL cache.

    Args:
        school: Open ``SchoolDB``.
        library_id: Owning library id.
        url: Remote image URL.
        payload: Image bytes.
        kind: Sniffed kind from ``_sniff_image_kind``.

    Returns:
        Cartridge-relative ``web_resources/...`` path.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    relpath = f"web_resources/bank-mirror/{digest}{_SNIFF_EXT[kind]}"
    store = ContentBlobStore(school.data_dir, school)
    stored = store.put_bytes(payload, filename=relpath, mime=_SNIFF_MIME[kind])
    try:
        from school_db import _now as _stamp_now
    except ImportError:
        from lms.school_db import _now as _stamp_now
    stamp = _stamp_now()
    with school._lock:
        school.conn.execute(
            """
            INSERT INTO bank_image_cache (
                library_id, source_url, relpath, blob_sha, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(library_id, source_url) DO UPDATE SET
                relpath = excluded.relpath,
                blob_sha = excluded.blob_sha
            """,
            (int(library_id), url, relpath, stored.sha256, stamp),
        )
        school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(library_id, relpath) DO UPDATE SET
                blob_sha = excluded.blob_sha
            """,
            (int(library_id), relpath, stored.sha256, stamp),
        )
        school.conn.commit()
    return relpath


def mirror_bank_image_url(
    school: Any,
    library_id: int,
    source_url: str,
    *,
    timeout: float = 20.0,
) -> str | None:
    """Download one remote bank image and register it under ``web_resources/``.

    A sqlite cache hit wins over the in-memory refusal cache. A URL that
    was refused or failed to download is remembered for about one hour and
    is not fetched again while building a student payload. One in-flight
    download is shared so a class poll does not stampede the same URL.

    Args:
        school: Open ``SchoolDB`` instance.
        library_id: Owning ``content_libraries.id``.
        source_url: Original Canvas/Equatio URL from ingest HTML.
        timeout: HTTP timeout in seconds.

    Returns:
        Cartridge-relative ``web_resources/...`` path, or ``None`` when the
        download fails or the body is not an image.
    """
    url = str(source_url or "").strip()
    if not url or not is_remote_bank_image_url(url):
        return None
    library = int(library_id)
    ensure_bank_image_cache_schema(school)
    cached = _cached_relpath(school, library, url)
    if cached:
        _forget_refusal(library, url)
        return cached
    if _refusal_active(library, url):
        return None
    if (library, url) in _flight_keys():
        return None
    event, owner = _claim_flight(library, url)
    if not owner:
        event.wait(timeout=max(float(timeout), 1.0))
        cached = _cached_relpath(school, library, url)
        if cached:
            _forget_refusal(library, url)
            return cached
        return None
    _flight_keys().add((library, url))
    try:
        try:
            payload = _fetch_remote(url, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            _remember_refusal(library, url)
            return None
        kind = _sniff_image_kind(payload)
        if not kind:
            _remember_refusal(library, url)
            return None
        relpath = _store_mirrored_image(school, library, url, payload, kind)
        _forget_refusal(library, url)
        return relpath
    finally:
        _flight_keys().discard((library, url))
        with _refusal_lock:
            _inflight.pop((library, url), None)
        event.set()


def rewrite_remote_images_in_html(
    raw_html: str,
    *,
    school: Any | None = None,
    library_id: int | None = None,
    class_id: int | None = None,
) -> str:
    """Mirror remote imgs to local blobs and convert equation images to KaTeX spans."""
    try:
        from question_math import resolve_bank_image_url
    except ImportError:
        from lms.question_math import resolve_bank_image_url

    text = str(raw_html or "")
    if not text:
        return ""

    def _rewrite_tag(tag: str) -> str:
        if "equation_image" in tag.lower():
            latex_tag = latex_img_tag_to_math_span(tag)
            if latex_tag != tag:
                return latex_tag
        src_match = _SRC_RE.search(tag)
        if not src_match:
            return tag
        src = str(src_match.group(1) or "").strip()
        resolved_src = src
        if school is not None and library_id is not None and is_remote_bank_image_url(src):
            relpath = mirror_bank_image_url(school, int(library_id), src)
            if relpath:
                resolved_src = resolve_bank_image_url(relpath, class_id=class_id)
        elif src.startswith("$IMS-CC-FILEBASE$") or "../web_resources/" in src:
            resolved_src = resolve_bank_image_url(src, class_id=class_id)
        new_tag = _SRC_RE.sub(f'src="{resolved_src}"', tag, count=1)
        return new_tag

    return _IMG_TAG_RE.sub(lambda match: _rewrite_tag(match.group(0)), text)



def _choice_html(choice: Any) -> str:
    """Return the HTML of one answer choice, whether it is a string or a dict.

    Args:
        choice: A choice string, or a dict with ``html`` or ``text``.
    """
    if isinstance(choice, str):
        return choice
    if isinstance(choice, dict):
        return str(choice.get("html") or choice.get("text") or "")
    return ""


def mirror_library_bank_images(
    school: Any,
    library_id: int,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    """Prefetch unique remote image URLs referenced by one library's MC banks.

    Args:
        school: Open ``SchoolDB``.
        library_id: Target library id.
        limit: Optional cap for dev/test runs.

    Returns:
        Summary counts for mirrored, cached, and failed URLs.
    """
    import json

    urls: list[str] = []
    seen: set[str] = set()
    rows = school.conn.execute(
        """
        SELECT q.payload_json
        FROM questions q
        JOIN question_banks b ON b.id = q.bank_id
        WHERE b.library_id = ?
          AND q.item_type = 'multiple_choice_question'
        """,
        (int(library_id),),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        parts = [str(payload.get("stem_html") or "")]
        for choice in payload.get("choices") or []:
            parts.append(_choice_html(choice))
        html = "\n".join(parts)
        for match in _SRC_RE.finditer(html):
            url = str(match.group(1) or "").strip()
            if not is_remote_bank_image_url(url) or url in seen:
                continue
            seen.add(url)
            urls.append(url)
    mirrored = 0
    failed = 0
    for index, url in enumerate(urls):
        if limit is not None and index >= int(limit):
            break
        relpath = mirror_bank_image_url(school, int(library_id), url)
        if relpath:
            mirrored += 1
        else:
            failed += 1
    # broken_image depends on bank_image_cache: re-lint (report-only).
    try:
        from bank_lint import safe_lint_library
    except ImportError:
        from lms.bank_lint import safe_lint_library
    safe_lint_library(school, int(library_id))
    return {
        "library_id": int(library_id),
        "unique_urls": len(urls),
        "mirrored": mirrored,
        "failed": failed,
    }
