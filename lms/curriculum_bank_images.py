"""Resolve bank figures to files the LMS can serve.

Curriculum bank rows store diagrams on ``image_paths`` (and, when present,
on stem, choice, feedback, or figure fields). The seed used to keep only
the stem, so those diagrams never reached the teacher bank, live class,
Lesson Slides, or catch-up.

Only MCF3M modules 5, 6, and 7 gain image fields in stored rows. Display
also resolves stem and option images for MCF3M modules 6, 7, and 8 and
for MCR3U, including the ``MCR3U-2`` section. That resolution returns a
copy. It does not write the question, the playlist card, or a past class
session. A path whose bytes are not on disk is reported and is not
replaced with a drawn stand-in.
"""

from __future__ import annotations

import copy
import html
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from bank_image_mirror import is_remote_bank_image_url
except ImportError:
    from lms.bank_image_mirror import is_remote_bank_image_url

_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
_COURSE_RE = re.compile(r"[^A-Z0-9]")
_IMG_SRC_RE = re.compile(
    r"""<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_EMBEDDED_PATH_RE = re.compile(
    r"""(?:nelson_assets|web_resources)/[^\s"'<>]+?\.(?:png|jpe?g|gif|webp|svg)""",
    re.IGNORECASE,
)
_PATH_KEYS = frozenset(
    {
        "image_paths",
        "image_path",
        "images",
        "figures",
        "figure",
        "figure_path",
        "figure_paths",
        "image_url",
        "image_src",
        "image_urls",
    }
)
#: Course/module pairs whose figure paths are copied onto question payloads.
FIGURE_MODULES = frozenset({("MCF3M", 5), ("MCF3M", 6), ("MCF3M", 7)})
#: MCF3M modules whose stem and option images are resolved at display time.
_DISPLAY_MCF3M_MODULES = frozenset({6, 7, 8})
_MODULE_TOKEN_RE = re.compile(r"(?:^|[^A-Z0-9])M(\d+)(?![0-9A-Z])")
_STEM_HTML_KEYS = ("stem_html", "text_html")
_STEM_URL_KEYS = ("image_url", "image_src", "image_paths", "image_urls")
_PUBLIC_PREFIX = "/curriculum-bank-images/"
_LMS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _LMS_DIR.parent
#: Committed MCF3M cartridge figures. The route serves ``cartridge/<file>``.
CARTRIDGE_IMAGE_DIR = _LMS_DIR / "seeds" / "mcf3m_m567_images"
#: Committed MCF3M module 5–7 bank JSON. Shipped in the image next to the figures.
MCF3M_M567_BANK_DIR = _LMS_DIR / "seeds" / "mcf3m_m567_banks"


def course_code_token(course_code: str) -> str:
    """Return the uppercase course code used in URLs and cache folders.

    Args:
        course_code: Ontario course code, possibly mixed case.
    """
    return _COURSE_RE.sub("", str(course_code or "").upper())


def serves_figure_modules(course_code: str, module_number: int) -> bool:
    """True when this course and module should gain figure fields.

    Args:
        course_code: Ontario course code.
        module_number: One-based module index.
    """
    try:
        number = int(module_number)
    except (TypeError, ValueError):
        return False
    return (course_code_token(course_code), number) in FIGURE_MODULES


def catalogue_course_code(course_code: str) -> str:
    """Return the Ontario course that owns this section's figures.

    ``MCR3U-2`` is the second section of MCR3U. Figure files live under
    ``MCR3U``. Any other code keeps the token used in curriculum URLs.

    Args:
        course_code: Ontario course code or a section label.
    """
    raw = str(course_code or "").strip().upper()
    token = course_code_token(raw)
    if token == "MCR3U2" or raw.startswith("MCR3U-"):
        return "MCR3U"
    return token


def module_number_from_token(token: str) -> int:
    """Return a module number from ``M6``, ``6``, or an import key.

    Args:
        token: A module label or a key such as ``cartridge:MCF3M:M6:item``.

    Returns:
        The module number, or ``0`` when the text has none.
    """
    text = str(token or "").strip().upper()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = _MODULE_TOKEN_RE.search(text)
    if match is None:
        return 0
    return int(match.group(1))


def shows_question_images(course_code: str, module_number: int) -> bool:
    """True when display should resolve stem and option images.

    This is wider than :func:`serves_figure_modules`. MCF3M modules 6, 7,
    and 8 qualify, as does every module of MCR3U, including the
    ``MCR3U-2`` section. Stored rows still gain figure fields only for
    MCF3M modules 5–7.

    Args:
        course_code: Ontario course code or a section label.
        module_number: One-based module index.
    """
    try:
        number = int(module_number)
    except (TypeError, ValueError):
        return False
    code = catalogue_course_code(course_code)
    if code == "MCR3U" and 1 <= number <= 8:
        return True
    return code == "MCF3M" and number in _DISPLAY_MCF3M_MODULES


def question_images_in_scope(course_code: str, module_number: int) -> bool:
    """True when a read should resolve images for this course and module.

    Storage attachment stays on :func:`serves_figure_modules`. Display
    also covers the modules in :func:`shows_question_images`.

    Args:
        course_code: Ontario course code or a section label.
        module_number: One-based module index.
    """
    return serves_figure_modules(course_code, module_number) or shows_question_images(
        course_code, module_number
    )


def _course_dir(course_code: str, cache_root: Path | None) -> Path:
    """Return ``.local-data/curriculum/{CODE}`` or a test override.

    Args:
        course_code: Ontario course code.
        cache_root: Optional curriculum root. ``None`` uses the repo cache.
    """
    try:
        from slide_builder import local_curriculum_course_dir
    except ImportError:
        from lms.slide_builder import local_curriculum_course_dir

    return local_curriculum_course_dir(course_code, cache_root)


def safe_curriculum_image_rel(ref: str) -> str | None:
    """Return a cartridge-relative image path, or ``None`` when it is unsafe.

    Args:
        ref: An ``image_paths`` entry or an ``<img src>``.
    """
    raw = html.unescape(str(ref or "")).strip()
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    if raw.lower().startswith(("http://", "https://", "data:", "javascript:")):
        return None
    if raw.startswith("/static/") or raw.startswith(_PUBLIC_PREFIX):
        return None
    raw = raw.lstrip("/")
    if not raw or "\x00" in raw:
        return None
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    if Path(parts[-1]).suffix.lower() not in _IMAGE_EXT:
        return None
    return "/".join(parts)


def public_curriculum_image_url(course_code: str, relpath: str) -> str:
    """Browser URL for one curriculum figure.

    Args:
        course_code: Ontario course code.
        relpath: Safe path relative to the course cache directory.
    """
    code = course_code_token(course_code)
    return f"{_PUBLIC_PREFIX}{code}/{relpath}"


def _static_file(ref: str) -> tuple[Path, str] | None:
    """Return a repo static image and its URL when ``ref`` names one.

    Args:
        ref: Image reference, possibly a ``/static/...`` URL.
    """
    raw = html.unescape(str(ref or "")).strip().split("?", 1)[0].split("#", 1)[0]
    if not raw.startswith("/static/"):
        return None
    rel = raw.lstrip("/")
    file = (_LMS_DIR / rel).resolve()
    try:
        file.relative_to((_LMS_DIR / "static").resolve())
    except (OSError, ValueError):
        return None
    if not file.is_file() or file.suffix.lower() not in _IMAGE_EXT:
        return None
    return file, raw


def resolve_curriculum_image(
    course_code: str,
    ref: str,
    *,
    cache_root: Path | None = None,
) -> tuple[Path, str] | None:
    """Return the image file and public URL when ``ref`` points at a real file.

    Args:
        course_code: Ontario course code.
        ref: Image reference from a bank item.
        cache_root: Optional curriculum root.
    """
    static = _static_file(ref)
    if static is not None:
        return static
    raw_ref = html.unescape(str(ref or "")).strip()
    marker = f"{_PUBLIC_PREFIX}"
    if marker in raw_ref:
        rest = raw_ref.split(marker, 1)[1]
        _code, _, relpath = rest.partition("/")
        raw_ref = relpath
    rel = safe_curriculum_image_rel(raw_ref)
    if rel is None:
        return None
    if rel.startswith("cartridge/"):
        name = rel.split("/", 1)[1]
        if not name or "/" in name or name.startswith("."):
            return None
        file = (CARTRIDGE_IMAGE_DIR / name).resolve()
        try:
            file.relative_to(CARTRIDGE_IMAGE_DIR.resolve())
        except (OSError, ValueError):
            return None
        if file.is_file() and file.suffix.lower() in _IMAGE_EXT:
            return file, public_curriculum_image_url(course_code, rel)
        return None
    course_dir = _course_dir(course_code, cache_root)
    file = (course_dir / rel).resolve()
    try:
        file.relative_to(course_dir.resolve())
    except (OSError, ValueError):
        return None
    if not file.is_file():
        return None
    return file, public_curriculum_image_url(course_code, rel)


def file_for_curriculum_image_url(
    url: str,
    *,
    cache_root: Path | None = None,
) -> Path | None:
    """Map a curriculum image URL back to the file it serves.

    Args:
        url: Value stored on ``image_url`` or ``image_urls``.
        cache_root: Optional curriculum root.
    """
    raw = str(url or "").strip()
    if not raw.startswith(_PUBLIC_PREFIX):
        return None
    rest = raw[len(_PUBLIC_PREFIX) :]
    code, _, rel = rest.partition("/")
    if not code or not rel:
        return None
    found = resolve_curriculum_image(code, rel, cache_root=cache_root)
    if found is None:
        return None
    return found[0]


def _add_ref(ref: str, found: list[str], seen: set[str]) -> None:
    """Append one reference if it is new and non-empty.

    Args:
        ref: Candidate image reference.
        found: Ordered refs for one question.
        seen: Refs already recorded.
    """
    token = html.unescape(str(ref or "")).strip()
    if not token or token in seen:
        return
    seen.add(token)
    found.append(token)


def _add_value(value: Any, found: list[str], seen: set[str]) -> None:
    """Record image refs from one field value.

    Args:
        value: String, list, or other JSON value.
        found: Ordered refs for one question.
        seen: Refs already recorded.
    """
    if isinstance(value, str):
        _add_ref(value, found, seen)
        return
    if isinstance(value, list):
        for item in value:
            _add_value(item, found, seen)


def collect_image_refs(item: dict[str, Any]) -> list[str]:
    """Return every image reference on one bank item, in first-seen order.

    Looks at figure fields and at ``<img src>`` or ``nelson_assets`` /
    ``web_resources`` paths inside stem, choice, and feedback text.

    Args:
        item: One curriculum bank item.
    """
    found: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        """Visit nested JSON and pull image references."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _PATH_KEYS:
                    _add_value(value, found, seen)
                else:
                    walk(value)
            return
        if isinstance(node, list):
            for value in node:
                walk(value)
            return
        if not isinstance(node, str):
            return
        for match in _IMG_SRC_RE.finditer(node):
            _add_ref(match.group(1), found, seen)
        for match in _EMBEDDED_PATH_RE.finditer(node):
            _add_ref(match.group(0), found, seen)

    walk(item)
    return found


def figure_payload_for_item(
    course_code: str,
    module_number: int,
    item: dict[str, Any],
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Return image fields for one item, or ``{}`` when nothing resolves.

    ``figure_files`` is for Lesson Slides screenshots and is not stored
    in sqlite. ``image_url`` is the first figure; ``image_urls`` is every
    figure, in bank order.

    Args:
        course_code: Ontario course code.
        module_number: One-based module index.
        item: Curriculum bank item.
        cache_root: Optional curriculum root.
    """
    if not serves_figure_modules(course_code, module_number):
        return {}
    urls: list[str] = []
    files: list[str] = []
    seen: set[str] = set()
    for ref in collect_image_refs(item):
        resolved = resolve_curriculum_image(
            course_code, ref, cache_root=cache_root
        )
        if resolved is None:
            continue
        file, url = resolved
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        files.append(str(file))
    if not urls:
        return {}
    return {
        "image_url": urls[0],
        "image_urls": urls,
        "figure_files": "\n".join(files),
    }


def stored_figure_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop filesystem paths before a figure dict is written to sqlite.

    Args:
        fields: Result of :func:`figure_payload_for_item`.
    """
    stored: dict[str, Any] = {}
    if fields.get("image_url"):
        stored["image_url"] = fields["image_url"]
    urls = fields.get("image_urls")
    if isinstance(urls, list) and urls:
        stored["image_urls"] = list(urls)
    return stored


def _image_identity(ref: str) -> str:
    """Return a comparable identity for one image reference.

    Args:
        ref: An ``<img src>``, ``image_url``, or file path.
    """
    raw = html.unescape(str(ref or "")).strip()
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    return raw.lower()


def _refs_from_html(fragment: str) -> list[str]:
    """Return ``<img src>`` values from one HTML fragment, in order.

    Args:
        fragment: Stem or choice HTML.
    """
    return [html.unescape(match.group(1)).strip() for match in _IMG_SRC_RE.finditer(str(fragment or ""))]


def _iter_field_refs(value: Any) -> list[str]:
    """Return string image refs from one stem field.

    Args:
        value: A string, a list of strings, or something else.
    """
    found: list[str] = []
    if isinstance(value, str):
        token = value.strip()
        if token:
            found.append(token)
        return found
    if isinstance(value, list):
        for item in value:
            found.extend(_iter_field_refs(item))
    return found


def _choice_rows(item: dict[str, Any]) -> list[Any]:
    """Return the choice list stored on ``choices`` or ``options``.

    Args:
        item: Question payload.
    """
    choices = item.get("choices")
    if isinstance(choices, list) and choices:
        return choices
    options = item.get("options")
    if isinstance(options, list):
        return options
    return []


def _choice_html(choice: Any) -> str:
    """Return the HTML of one choice.

    Args:
        choice: A choice dict or a plain string.
    """
    if isinstance(choice, dict):
        return str(choice.get("html") or choice.get("text") or "")
    return str(choice or "")


def _resolve_display_ref(
    course_code: str,
    ref: str,
    *,
    cache_root: Path | None,
) -> tuple[str, str]:
    """Resolve one reference for display.

    Canvas and Equatio URLs are left for :mod:`bank_image_mirror`. An
    off-site URL is refused. A curriculum path becomes a public URL only
    when the file is on disk.

    Args:
        course_code: Ontario course code or a section label.
        ref: Image reference from the question.
        cache_root: Optional curriculum root.

    Returns:
        ``(url, reason)``. ``url`` is empty when the reference is not
        rewritten. ``reason`` is empty when nothing is wrong.
    """
    raw = html.unescape(str(ref or "")).strip()
    if not raw:
        return "", ""
    if raw.startswith(("http://", "https://", "//")):
        if is_remote_bank_image_url(raw):
            return "", ""
        return "", "off-site image link refused"
    found = resolve_curriculum_image(
        catalogue_course_code(course_code),
        raw,
        cache_root=cache_root,
    )
    if found is not None:
        return found[1], ""
    if safe_curriculum_image_rel(raw) is not None or _static_file(raw) is not None:
        return "", "image file is not on disk"
    if _PUBLIC_PREFIX in raw or raw.startswith("cartridge/") or "nelson_assets/" in raw or "web_resources/" in raw:
        return "", "image file is not on disk"
    return "", ""


def _rewrite_html_images(
    fragment: str,
    course_code: str,
    *,
    cache_root: Path | None,
    missing: list[dict[str, str]],
    place: str,
    question_id: str,
) -> str:
    """Rewrite resolvable ``<img src>`` values in one HTML fragment.

    The fragment is unchanged when no source resolves. A missing file
    keeps its original ``src`` and is recorded in ``missing``.

    Args:
        fragment: Stem or choice HTML.
        course_code: Ontario course code or a section label.
        cache_root: Optional curriculum root.
        missing: Rows appended for refs that cannot be shown.
        place: ``stem`` or ``option N``.
        question_id: Import key or other stable question id.
    """

    def replace(match: re.Match[str]) -> str:
        """Resolve one ``src`` and keep the tag when it cannot be shown."""
        src = html.unescape(match.group(1)).strip()
        url, reason = _resolve_display_ref(course_code, src, cache_root=cache_root)
        if reason:
            missing.append(
                {
                    "question_id": question_id,
                    "ref": src,
                    "place": place,
                    "reason": reason,
                }
            )
        if not url or url == src:
            return match.group(0)
        return match.group(0).replace(match.group(1), url, 1)

    return _IMG_SRC_RE.sub(replace, str(fragment or ""))


def borrow_missing_image_fields(
    card: dict[str, Any],
    question: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of ``card`` filled with image fields it does not carry.

    The question row is the source. The card's choice ids and choice
    order stay. A choice gains the question's HTML only when that card
    choice is empty and the question choice at the same index has an
    image. Text the card already shows stays. Neither input is mutated.

    Args:
        card: Playlist card or session copy.
        question: Saved question payload, or ``None``.
    """
    view = copy.deepcopy(card)
    if not isinstance(question, dict):
        return view
    for key in _STEM_URL_KEYS:
        if _iter_field_refs(view.get(key)):
            continue
        borrowed = question.get(key)
        if _iter_field_refs(borrowed):
            view[key] = copy.deepcopy(borrowed)
    card_stem = " ".join(str(view.get(key) or "") for key in _STEM_HTML_KEYS)
    question_stem = " ".join(str(question.get(key) or "") for key in _STEM_HTML_KEYS)
    if not _refs_from_html(card_stem) and _refs_from_html(question_stem):
        for key in _STEM_HTML_KEYS:
            if _refs_from_html(str(question.get(key) or "")) and not _refs_from_html(
                str(view.get(key) or "")
            ):
                view[key] = str(question.get(key) or "")
    card_choices = view.get("choices")
    question_choices = question.get("choices")
    if isinstance(card_choices, list) and isinstance(question_choices, list):
        for index, choice in enumerate(card_choices):
            if index >= len(question_choices):
                break
            current = _choice_html(choice).strip()
            if current:
                continue
            borrowed_html = _choice_html(question_choices[index])
            if not _refs_from_html(borrowed_html):
                continue
            if isinstance(choice, dict):
                choice["html"] = borrowed_html
            else:
                card_choices[index] = borrowed_html
    card_htmls = view.get("options_html")
    question_htmls = question.get("options_html")
    if isinstance(card_htmls, list) and isinstance(question_htmls, list):
        for index, fragment in enumerate(card_htmls):
            if index >= len(question_htmls):
                break
            if str(fragment or "").strip():
                continue
            borrowed = str(question_htmls[index] or "")
            if _refs_from_html(borrowed):
                card_htmls[index] = borrowed
    return view


def describe_question_images(
    course_code: str,
    module_number: int,
    item: dict[str, Any],
    *,
    cache_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return a display copy and every image that cannot be shown.

    Stem images come from ``<img>`` in the stem and from ``image_url``,
    ``image_src``, ``image_paths``, and ``image_urls``. Option images
    come from ``<img>`` in each choice. A choice image is not promoted
    onto the stem. Canvas and Equatio URLs are left for
    :mod:`bank_image_mirror`. Off-site links stay refused. A missing
    file is listed and is not replaced.

    The input is not mutated. Running this on the returned copy reports
    the same missing rows and the same URLs.

    Args:
        course_code: Ontario course code or a section label such as
            ``MCR3U-2``.
        module_number: One-based module index.
        item: Question payload, playlist card, or session copy.
        cache_root: Optional curriculum root.

    Returns:
        The display copy (the same object when this course and module
        are out of scope) and missing-image rows. Each row has
        ``question_id``, ``ref``, ``place``, and ``reason``.
    """
    if not isinstance(item, dict):
        return {}, []
    if not question_images_in_scope(course_code, module_number):
        return item, []
    question_id = str(
        item.get("import_key") or item.get("smart_id") or item.get("id") or ""
    ).strip()
    view = copy.deepcopy(item)
    missing: list[dict[str, str]] = []
    choice_idents: set[str] = set()
    option_urls: list[str] = []
    choices = _choice_rows(view)
    htmls = view.get("options_html")
    html_list = htmls if isinstance(htmls, list) else []
    count = max(len(choices), len(html_list))
    for index in range(count):
        place = f"option {index + 1}"
        if index < len(choices):
            choice = choices[index]
            raw_html = _choice_html(choice)
            for src in _refs_from_html(raw_html):
                choice_idents.add(_image_identity(src))
            rewritten = _rewrite_html_images(
                raw_html,
                course_code,
                cache_root=cache_root,
                missing=missing,
                place=place,
                question_id=question_id,
            )
            if isinstance(choice, dict):
                if "html" in choice or rewritten != raw_html:
                    choice["html"] = rewritten
            elif index < len(choices):
                choices[index] = rewritten
            for src in _refs_from_html(rewritten):
                choice_idents.add(_image_identity(src))
        else:
            rewritten = ""
        if index < len(html_list):
            raw_option = str(html_list[index] or "")
            for src in _refs_from_html(raw_option):
                choice_idents.add(_image_identity(src))
            rewritten_option = _rewrite_html_images(
                raw_option,
                course_code,
                cache_root=cache_root,
                missing=missing,
                place=place,
                question_id=question_id,
            )
            html_list[index] = rewritten_option
            if not rewritten:
                rewritten = rewritten_option
        srcs = _refs_from_html(rewritten)
        option_urls.append(srcs[0] if srcs else "")
    stem_html_idents: set[str] = set()
    for key in _STEM_HTML_KEYS:
        raw_stem = str(view.get(key) or "")
        for src in _refs_from_html(raw_stem):
            stem_html_idents.add(_image_identity(src))
        view[key] = _rewrite_html_images(
            raw_stem,
            course_code,
            cache_root=cache_root,
            missing=missing,
            place="stem",
            question_id=question_id,
        )
        for src in _refs_from_html(str(view.get(key) or "")):
            stem_html_idents.add(_image_identity(src))
    gallery: list[str] = []
    seen_gallery: set[str] = set()

    def add_gallery(ref: str) -> None:
        """Append one stem figure that the stem HTML does not already show."""
        ident = _image_identity(ref)
        if ident in choice_idents and ident not in stem_html_idents:
            return
        url, reason = _resolve_display_ref(course_code, ref, cache_root=cache_root)
        if reason:
            missing.append(
                {
                    "question_id": question_id,
                    "ref": ref,
                    "place": "stem",
                    "reason": reason,
                }
            )
            return
        shown = url or (ref if ref.startswith(("http://", "https://", "/")) else "")
        if not shown:
            return
        ident_shown = _image_identity(shown)
        if ident in stem_html_idents or ident_shown in stem_html_idents:
            return
        if ident_shown in seen_gallery:
            return
        seen_gallery.add(ident_shown)
        gallery.append(shown)

    for key in _STEM_URL_KEYS:
        for ref in _iter_field_refs(view.get(key)):
            add_gallery(ref)
    if gallery:
        view["image_urls"] = gallery
        view["image_url"] = gallery[0]
    elif any(ident in choice_idents and ident not in stem_html_idents for ident in (
        _image_identity(str(view.get("image_url") or "")),
        _image_identity(str(view.get("image_src") or "")),
    ) if ident):
        view.pop("image_url", None)
        view.pop("image_src", None)
    if any(option_urls):
        view["options_image_urls"] = option_urls
    return view, _unique_missing(missing)


def _unique_missing(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop duplicate missing-image rows, keeping the first of each.

    Args:
        rows: Rows from one :func:`describe_question_images` call.
    """
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.get("question_id", ""), row.get("ref", ""), row.get("place", ""))
        if key in seen:
            continue
        seen.add(key)
        found.append(row)
    return found


@lru_cache(maxsize=1)
def _git_paths() -> frozenset[str]:
    """Return every path recorded in git history, or an empty set on failure."""
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--all", "--objects"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if proc.returncode != 0:
        return frozenset()
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            names.add(parts[1].strip().replace("\\", "/"))
    return frozenset(names)


def ref_in_git_history(ref: str) -> bool:
    """True when this exact relative image path appears in git history.

    A different file that only shares the basename does not count.

    Args:
        ref: Image reference from a bank item.
    """
    rel = safe_curriculum_image_rel(ref)
    needle = rel or ""
    static = html.unescape(str(ref or "")).strip().split("?", 1)[0]
    if static.startswith("/static/"):
        needle = static.lstrip("/")
    if not needle:
        return False
    names = _git_paths()
    for name in names:
        if name == needle or name.endswith("/" + needle):
            return True
    return False


def _load_items(path: Path) -> list[dict[str, Any]]:
    """Load the ``items`` list from one bank file.

    Args:
        path: ``items.json`` path.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    raw = document.get("items") if isinstance(document, dict) else document
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def audit_bank_images(
    course_code: str,
    modules: tuple[int, ...] | list[int],
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Audit image references for one course and a set of modules.

    A reference resolves only when a real image file is on disk and the
    LMS would serve it. Missing files are listed with a reason. Nothing
    is invented.

    Args:
        course_code: Ontario course code.
        modules: One-based module numbers to read.
        cache_root: Optional curriculum root.
    """
    code = course_code_token(course_code)
    banks = _course_dir(code, cache_root) / "banks"
    questions_checked = 0
    refs: list[dict[str, Any]] = []
    wanted = {int(number) for number in modules}
    seen_modules: set[int] = set()
    if banks.is_dir():
        for module_dir in sorted(banks.glob("M*")):
            if not module_dir.is_dir():
                continue
            suffix = module_dir.name[1:]
            if not suffix.isdigit() or int(suffix) not in wanted:
                continue
            module_number = int(suffix)
            seen_modules.add(module_number)
            for path in sorted(module_dir.glob("*/items.json")):
                strand = path.parent.name
                for item in _load_items(path):
                    questions_checked += 1
                    question_id = str(
                        item.get("smart_id") or item.get("id") or ""
                    ).strip()
                    for ref in collect_image_refs(item):
                        refs.append(
                            _audit_ref(
                                code,
                                module_number,
                                strand,
                                question_id,
                                ref,
                                cache_root=cache_root,
                            )
                        )
    if cache_root is None and code == "MCF3M":
        for module_number in sorted(wanted & {5, 6, 7}):
            if module_number in seen_modules:
                continue
            path = MCF3M_M567_BANK_DIR / f"M{module_number}.json"
            strand = ""
            for item in _load_items(path):
                questions_checked += 1
                question_id = str(
                    item.get("smart_id") or item.get("id") or ""
                ).strip()
                for ref in collect_image_refs(item):
                    refs.append(
                        _audit_ref(
                            code,
                            module_number,
                            strand,
                            question_id,
                            ref,
                            cache_root=cache_root,
                        )
                    )
    return _audit_summary(code, questions_checked, refs)


def _audit_ref(
    course_code: str,
    module_number: int,
    strand: str,
    question_id: str,
    ref: str,
    *,
    cache_root: Path | None,
) -> dict[str, Any]:
    """Classify one image reference.

    Args:
        course_code: Ontario course code.
        module_number: One-based module index.
        strand: Bank strand letter.
        question_id: ``smart_id`` or other item id.
        ref: Image reference.
        cache_root: Optional curriculum root.
    """
    raw_ref = html.unescape(str(ref or "")).strip()
    if _PUBLIC_PREFIX in raw_ref:
        raw_ref = raw_ref.split(_PUBLIC_PREFIX, 1)[1].partition("/")[2]
    rel = safe_curriculum_image_rel(raw_ref)
    on_disk = False
    if rel is not None and rel.startswith("cartridge/"):
        name = rel.split("/", 1)[1]
        on_disk = bool(name) and (CARTRIDGE_IMAGE_DIR / name).is_file()
    elif rel is not None:
        course_dir = _course_dir(course_code, cache_root)
        on_disk = (course_dir / rel).is_file()
    static = _static_file(ref)
    if static is not None:
        on_disk = True
    resolved = resolve_curriculum_image(
        course_code, ref, cache_root=cache_root
    )
    # Other modules must not gain URLs from this audit's "would serve"
    # check. The serve decision for storage is module-scoped; the file
    # check itself is not. ``resolved`` here means the bytes exist and
    # the route can serve them, which is what "the reference resolves"
    # means. Storage attachment stays module-scoped in
    # ``figure_payload_for_item``.
    in_git = ref_in_git_history(ref)
    url = resolved[1] if resolved is not None else ""
    reason = ""
    if resolved is None:
        if rel is None and static is None:
            reason = "not an image path this server can serve"
        elif on_disk:
            reason = "file is on disk but was not served"
        elif in_git:
            reason = "path is in git history but not on disk"
        else:
            reason = "not in the repo or its git history"
    return {
        "course": course_code,
        "module": module_number,
        "strand": strand,
        "question_id": question_id,
        "ref": ref,
        "resolved": resolved is not None,
        "url": url,
        "on_disk": on_disk,
        "in_git": in_git,
        "reason": reason,
    }


def _audit_summary(
    course_code: str,
    questions_checked: int,
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Shape the audit counts the test and the report both read.

    Args:
        course_code: Ontario course code.
        questions_checked: Bank items visited.
        refs: One row per image reference.
    """
    missing = [row for row in refs if not row["resolved"]]
    broken = [row for row in refs if row["on_disk"] and not row["resolved"]]
    return {
        "course": course_code,
        "questions_checked": questions_checked,
        "refs_found": len(refs),
        "refs_resolved": len(refs) - len(missing),
        "refs": refs,
        "missing": missing,
        "broken_but_on_disk": broken,
    }


def audit_mcf3m_modules_5_to_7(
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Audit MCF3M modules 5, 6, and 7.

    Args:
        cache_root: Optional curriculum root. ``None`` reads the repo cache.
    """
    return audit_bank_images("MCF3M", (5, 6, 7), cache_root=cache_root)


def assert_images_resolve(report: dict[str, Any]) -> None:
    """Raise ``AssertionError`` listing every image ref that does not resolve.

    Args:
        report: Result of :func:`audit_bank_images`.

    Raises:
        AssertionError: At least one reference has no served file. The
            message names the question id, the reference, and the reason.
    """
    missing = list(report.get("missing") or [])
    broken = list(report.get("broken_but_on_disk") or [])
    rows = missing or broken
    if not rows:
        return
    lines = [
        (
            f"{row.get('question_id') or '(no id)'}: {row.get('ref')} "
            f"({row.get('reason') or 'unresolved'})"
        )
        for row in rows
    ]
    checked = int(report.get("questions_checked") or 0)
    found = int(report.get("refs_found") or 0)
    resolved = int(report.get("refs_resolved") or 0)
    raise AssertionError(
        f"{checked} questions, {found} image refs, {resolved} resolved, "
        f"{len(rows)} missing:\n" + "\n".join(lines)
    )


_FIGURE_PHRASES = (
    "triangle above",
    "the figure above",
    "the figure below",
    "figure above",
    "figure below",
    "the graph above",
    "the graph below",
    "graph above",
    "graph below",
    "shown above",
    "shown below",
    "the diagram above",
    "the diagram below",
    "the above figure",
    "triangle abc",
)
_EXACT_FIGURE_STEMS = {
    "the amplitude is",
    "the equation of the axis is",
}


def _choice_renders(fragment: str) -> bool:
    """True when a choice has visible text, an image, or TeX.

    Args:
        fragment: Choice HTML stored on the bank item.
    """
    raw = str(fragment or "")
    if re.search(r"<img\b", raw, re.I):
        return True
    if "$" in raw or "data-latex" in raw:
        return True
    try:
        from question_math import html_to_plain
    except ImportError:
        from lms.question_math import html_to_plain

    return bool(html_to_plain(raw).strip())


def _stem_still_needs_figure(stem: str, urls: list[str]) -> bool:
    """True when the wording points at a diagram the row does not carry.

    Args:
        stem: Plain stem text.
        urls: Served figure URLs on the row.
    """
    if urls:
        return False
    text = re.sub(r"\s+", " ", stem or "").strip().lower()
    bare = text.rstrip(":").strip()
    if bare in _EXACT_FIGURE_STEMS:
        return True
    return any(phrase in text for phrase in _FIGURE_PHRASES)


def audit_imported_figure_gaps(
    *,
    cache_root: Path | None = None,
) -> list[dict[str, str]]:
    """List MCF3M module 5–7 items whose figures or choices are incomplete.

    A QTI image source recorded on an imported item must resolve to a file
    that is on the row, or to TeX that is still in the stem or a choice.
    A choice with no text, image, or TeX is a gap. Nothing is invented.

    Args:
        cache_root: Optional curriculum root. ``None`` reads the repo cache.

    Returns:
        One dict per gap with ``question_id``, ``ref``, and ``reason``.
    """
    banks = _course_dir("MCF3M", cache_root) / "banks"
    gaps: list[dict[str, str]] = []
    for module_number in (5, 6, 7):
        paths: list[Path] = []
        module_dir = banks / f"M{module_number}"
        if module_dir.is_dir():
            paths.extend(sorted(module_dir.glob("*/items.json")))
        if not paths and cache_root is None:
            seed = MCF3M_M567_BANK_DIR / f"M{module_number}.json"
            if seed.is_file():
                paths.append(seed)
        for path in paths:
            for item in _load_items(path):
                question_id = str(item.get("smart_id") or item.get("id") or "").strip()
                stem = str(item.get("stem") or "")
                stem_html = str(item.get("stem_html") or "")
                choices = item.get("choices") if isinstance(item.get("choices"), list) else []
                choice_html = [
                    str(choice.get("html") or "") if isinstance(choice, dict) else str(choice or "")
                    for choice in choices
                ]
                urls = [str(url) for url in (item.get("image_urls") or []) if str(url).strip()]
                blob = " ".join([stem_html, *choice_html, *urls])
                if _stem_still_needs_figure(stem, urls):
                    gaps.append(
                        {
                            "question_id": question_id,
                            "ref": stem[:120],
                            "reason": "stem needs a figure",
                        }
                    )
                for index, fragment in enumerate(choice_html):
                    if _choice_renders(fragment):
                        continue
                    gaps.append(
                        {
                            "question_id": question_id,
                            "ref": f"choice {index + 1}",
                            "reason": "empty choice",
                        }
                    )
                recorded = item.get("figure_map")
                if str(item.get("origin") or "") == "canvas_cartridge" and not isinstance(recorded, list):
                    gaps.append(
                        {
                            "question_id": question_id,
                            "ref": "",
                            "reason": "imported item has no figure map",
                        }
                    )
                    recorded = []
                for entry in recorded or []:
                    if not isinstance(entry, dict):
                        gaps.append(
                            {
                                "question_id": question_id,
                                "ref": "",
                                "reason": "figure source maps to nothing",
                            }
                        )
                        continue
                    kind = str(entry.get("kind") or "")
                    src = str(entry.get("src") or "")
                    if kind == "latex":
                        if "$" not in blob and "data-latex" not in blob:
                            gaps.append(
                                {
                                    "question_id": question_id,
                                    "ref": src[:160],
                                    "reason": "equation image maps to nothing",
                                }
                            )
                        continue
                    if kind != "file":
                        gaps.append(
                            {
                                "question_id": question_id,
                                "ref": src[:160],
                                "reason": "figure source maps to nothing",
                            }
                        )
                        continue
                    url = str(entry.get("url") or "").strip()
                    if not url or url not in blob:
                        gaps.append(
                            {
                                "question_id": question_id,
                                "ref": src[:160] or url,
                                "reason": "figure source maps to nothing",
                            }
                        )
                        continue
                    if resolve_curriculum_image("MCF3M", url, cache_root=cache_root) is None:
                        gaps.append(
                            {
                                "question_id": question_id,
                                "ref": url,
                                "reason": "figure file is not served",
                            }
                        )
    return gaps


def assert_imported_figures_complete(*, cache_root: Path | None = None) -> None:
    """Raise when an imported module 5–7 item lost a figure or a choice.

    Args:
        cache_root: Optional curriculum root. ``None`` reads the repo cache.

    Raises:
        AssertionError: At least one stem, choice, or recorded image source
            does not resolve. The message names the question id and reason.
    """
    gaps = audit_imported_figure_gaps(cache_root=cache_root)
    if not gaps:
        return
    lines = [
        f"{row.get('question_id') or '(no id)'}: {row.get('ref')} ({row.get('reason')})"
        for row in gaps
    ]
    raise AssertionError(
        f"{len(gaps)} imported figure gaps:\n" + "\n".join(lines)
    )
