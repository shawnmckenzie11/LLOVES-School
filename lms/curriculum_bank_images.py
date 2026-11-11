"""Resolve MCF3M module 5–7 bank figures to files the LMS can serve.

Curriculum bank rows store diagrams on ``image_paths`` (and, when present,
on stem, choice, feedback, or figure fields). The seed used to keep only
the stem, so those diagrams never reached the teacher bank, live class,
Lesson Slides, or catch-up.

Only MCF3M modules 5, 6, and 7 get new image fields. Other courses and
modules are left alone, including when a file happens to exist. A path
whose bytes are not on disk and not in git history is reported and is
not replaced with a drawn stand-in.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

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
_PUBLIC_PREFIX = "/curriculum-bank-images/"
_LMS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _LMS_DIR.parent
#: Committed MCF3M cartridge figures. The route serves ``cartridge/<file>``.
CARTRIDGE_IMAGE_DIR = _LMS_DIR / "seeds" / "mcf3m_m567_images"


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
    if not banks.is_dir():
        return _audit_summary(code, questions_checked, refs)
    for module_dir in sorted(banks.glob("M*")):
        if not module_dir.is_dir():
            continue
        suffix = module_dir.name[1:]
        if not suffix.isdigit() or int(suffix) not in wanted:
            continue
        module_number = int(suffix)
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
    if not banks.is_dir():
        return gaps
    for module_number in (5, 6, 7):
        module_dir = banks / f"M{module_number}"
        if not module_dir.is_dir():
            continue
        for path in sorted(module_dir.glob("*/items.json")):
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
