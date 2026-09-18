"""Mirror remote Canvas/Equatio bank images into LMS blob storage."""

from __future__ import annotations

import hashlib
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    from content_store import ContentBlobStore
except ImportError:
    from lms.content_store import ContentBlobStore

_REMOTE_HOST_RE = re.compile(
    r"https?://(?:"
    r"[^/]*instructure\.com|"
    r"(?:www\.)?wiris\.net|"
    r"equatio-api\.texthelp\.com|"
    r"[a-z0-9-]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com|"
    r"i\.gyazo\.com"
    r")/",
    re.IGNORECASE,
)
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


def is_remote_bank_image_url(url: str) -> bool:
    """Return True when a src URL should be mirrored locally."""
    return bool(_REMOTE_HOST_RE.search(str(url or "")))


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


def _extension_for(content_type: str, url: str) -> str:
    """Guess a file extension from response headers or URL."""
    mime = str(content_type or "").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(mime) or ""
    if ext == ".jpe":
        ext = ".jpg"
    if ext:
        return ext
    path = urllib.parse.urlparse(url).path.lower()
    for candidate in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        if path.endswith(candidate):
            return candidate
    return ".bin"


def _fetch_remote(url: str, *, timeout: float = 20.0) -> tuple[bytes, str]:
    """Download bytes from one remote bank image URL."""
    request = urllib.request.Request(
        str(url),
        headers={"User-Agent": "LLOVES-LMS/1.0 bank-image-mirror"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        content_type = str(response.headers.get("Content-Type") or "")
    return payload, content_type


def mirror_bank_image_url(
    school: Any,
    library_id: int,
    source_url: str,
    *,
    timeout: float = 20.0,
) -> str | None:
    """Download one remote bank image and register it under ``web_resources/``.

    Args:
        school: Open ``SchoolDB`` instance.
        library_id: Owning ``content_libraries.id``.
        source_url: Original Canvas/Equatio URL from ingest HTML.
        timeout: HTTP timeout in seconds.

    Returns:
        Cartridge-relative ``web_resources/...`` path, or ``None`` on failure.
    """
    url = str(source_url or "").strip()
    if not url or not is_remote_bank_image_url(url):
        return None
    ensure_bank_image_cache_schema(school)
    with school._lock:
        cached = school.conn.execute(
            """
            SELECT relpath FROM bank_image_cache
            WHERE library_id = ? AND source_url = ?
            """,
            (int(library_id), url),
        ).fetchone()
    if cached is not None:
        return str(cached["relpath"])
    try:
        payload, content_type = _fetch_remote(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    if not payload:
        return None
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    ext = _extension_for(content_type, url)
    relpath = f"web_resources/bank-mirror/{digest}{ext}"
    store = ContentBlobStore(school.data_dir, school)
    stored = store.put_bytes(payload, filename=relpath, mime=content_type or None)
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
            parts.append(str(choice.get("html") or ""))
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
    return {
        "library_id": int(library_id),
        "unique_urls": len(urls),
        "mirrored": mirrored,
        "failed": failed,
    }
