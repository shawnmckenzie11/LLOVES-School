"""Pacing, expectation matching, and index-based fill for Lesson Slides.

The branded Google template has no ``{{placeholders}}`` and no custom layout
names the API can see. Fill by **slide order** (style-guide sequence) into
the largest text boxes, plus speaker notes on the context slide.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from html import escape as html_escape
from pathlib import Path
from typing import Any

from live_class_constants import (
    DEFAULT_ASYNC_LESSON_KEYWORD,
    DEFAULT_LIVES_PER_MODULE,
    DEFAULT_WEEKS_PER_MODULE,
    M1C1_TEAM_CHALLENGE_CONTEXT,
    M1C1_TEAM_CHALLENGE_NOTES,
    M1C1_TEAM_CHALLENGE_QUESTION,
)
from slides_template import STOCK_REFLECTION, TEMPLATE_LAYOUT_NAMES

CODE_RE = re.compile(r"\b([A-E]\d+(?:\.\d+)*)\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'^]{3,}")
STOPWORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "they",
        "them",
        "their",
        "have",
        "will",
        "into",
        "using",
        "used",
        "such",
        "when",
        "which",
        "about",
        "other",
        "than",
        "also",
        "more",
        "some",
        "each",
        "including",
        "students",
        "student",
        "demonstrate",
        "understand",
        "through",
        "simple",
        "related",
        "given",
    }
)
QUESTION_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("factors", ("factor", "product", "group", "divis", "equal")),
    ("functions", ("function", "notation", "f(n)", "f(x)", "mapping")),
    ("squares", ("square", "midpoint", "quadratic", "perfect")),
)


def lesson_key(module_number: int, live_index: int) -> str:
    """Return ``M{module}C{live}`` such as ``M1C1``.

    Args:
        module_number: 1-based module outline position.
        live_index: 1-based live class within the module.
    """
    return f"M{int(module_number)}C{int(live_index)}"


def default_team_challenge(module_number: int, live_index: int) -> dict[str, str]:
    """Pre-fill team-challenge copy for the first M1C1 run.

    Args:
        module_number: Module number.
        live_index: Live class number in the module.
    """
    if int(module_number) == 1 and int(live_index) == 1:
        return {
            "context": M1C1_TEAM_CHALLENGE_CONTEXT,
            "question": M1C1_TEAM_CHALLENGE_QUESTION,
            "speaker_notes": M1C1_TEAM_CHALLENGE_NOTES,
        }
    return {"context": "", "question": "", "speaker_notes": ""}


def live_class_lesson_window(k: int, lives: int, live_index: int) -> tuple[int, int]:
    """Map live class ``i`` of ``L`` onto async Lessons ``1…k`` with overlap.

    ``start = floor((i-1) * k / L) + 1``, ``end = ceil(i * k / L)``.
    Adjacent lives share a Lesson so every async Lesson is covered.

    Args:
        k: Count of keyword-matched async Lessons in the module.
        lives: Live classes per module (default 4).
        live_index: 1-based live class in the module.

    Returns:
        Inclusive ``(start, end)`` Lesson numbers, or ``(0, 0)`` when empty.
    """
    if k <= 0 or lives <= 0 or live_index <= 0:
        return (0, 0)
    start = math.floor((live_index - 1) * k / lives) + 1
    end = math.ceil(live_index * k / lives)
    start = max(1, min(int(start), k))
    end = max(start, min(int(end), k))
    return start, end



def resolve_module_outline(
    outlines: list[dict[str, Any]],
    module_number: int,
) -> dict[str, Any] | None:
    """Pick the outline for teacher Module N (title), not raw list index.

    Packs often start with ``Module 0`` intro, so 1-based position would map
    M1C1 onto the welcome module. Prefer a title matching ``Module N``.

    Args:
        outlines: ``outline_nav`` rows in position order.
        module_number: Teacher module number (1 for M1C1).
    """
    needle = re.compile(rf"\bmodule\s+{int(module_number)}\b", re.I)
    for row in outlines:
        if needle.search(str(row.get("title") or "")):
            return row
    if 1 <= int(module_number) <= len(outlines):
        return outlines[int(module_number) - 1]
    return None

def filter_async_lessons(
    items: list[dict[str, Any]],
    keyword: str = DEFAULT_ASYNC_LESSON_KEYWORD,
) -> list[dict[str, Any]]:
    """Keep module items whose title contains ``keyword``, in outline order.

    Args:
        items: ``module_items`` rows (need ``title``).
        keyword: Case-insensitive substring (default ``Lesson``).
    """
    needle = (keyword or DEFAULT_ASYNC_LESSON_KEYWORD).strip().lower()
    if not needle:
        needle = DEFAULT_ASYNC_LESSON_KEYWORD.lower()
    out: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title") or "")
        if needle not in title.lower():
            continue
        row = dict(item)
        row["lesson_number"] = len(out) + 1
        out.append(row)
    return out


def _plain_html(raw: str) -> str:
    """Collapse HTML to searchable plain text.

    Args:
        raw: HTML or plain text.
    """
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return " ".join(text.split())


def _tokens(blob: str) -> set[str]:
    """Distinctive lowercase tokens from official statements or lesson HTML.

    Args:
        blob: Source text.
    """
    found: set[str] = set()
    for match in TOKEN_RE.finditer(blob.lower()):
        word = match.group(0)
        if word in STOPWORDS:
            continue
        found.add(word)
    return found


def match_expectations(
    blob: str,
    expectations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Match a lesson blob to seeded expectations without inventing wording.

    Hits an expectation when its official ``code`` appears, or when token
    overlap with the official ``statement`` is strong enough.

    Args:
        blob: Lesson title plus page HTML.
        expectations: Seeded rows with ``code`` and ``statement``.

    Returns:
        ``{code, statement}`` using official seed text only.
    """
    text = blob or ""
    codes_in_text = {m.group(1).upper() for m in CODE_RE.finditer(text)}
    lesson_tokens = _tokens(_plain_html(text))
    matched: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in expectations:
        code = str(row.get("code") or "").strip().upper()
        statement = str(row.get("statement") or "").strip()
        if not code or not statement or code in seen:
            continue
        hit = code in codes_in_text
        if not hit:
            stmt_tokens = _tokens(statement)
            if stmt_tokens and lesson_tokens:
                overlap = len(stmt_tokens & lesson_tokens)
                hit = overlap >= 5 and overlap / max(len(stmt_tokens), 1) >= 0.28
        if not hit:
            continue
        seen.add(code)
        matched.append({"code": code, "statement": statement})
    return matched


def _expectations_for_matching(school: Any, ontario: str) -> list[dict[str, Any]]:
    """Prefer verified DB rows; fall back to the local Curriculum dump.

    Curriculum Drive is authored in the other workspace. Class-time matching
    must not call Drive. Local ``.local-data/curriculum/{CODE}/`` is the cache.

    Args:
        school: ``SchoolDB``.
        ontario: Course code.
    """
    rows = list(school.list_expectations(ontario) or [])
    specifics = [
        row
        for row in rows
        if str(row.get("kind") or "").lower() == "specific"
        or "." in str(row.get("code") or "")
    ]
    if specifics:
        return rows
    try:
        from math_expectations_pdf import load_local_specific_rows
    except ImportError:
        from lms.math_expectations_pdf import load_local_specific_rows
    return load_local_specific_rows(ontario)


def module_item_blob(
    school: Any,
    library_id: int,
    item: dict[str, Any],
) -> str:
    """Title plus page HTML for one module item.

    Args:
        school: ``SchoolDB``.
        library_id: Content library id.
        item: Module item row.
    """
    title = str(item.get("title") or "")
    if str(item.get("component_type") or "") != "page":
        return title
    try:
        from components import get_page
    except ImportError:
        from lms.components import get_page
    page = get_page(school, int(library_id), int(item.get("component_id") or 0))
    if not page:
        return title
    html = str(page.get("html_text") or "")
    return f"{title}\n{html}"


def preview_live_class(
    school: Any,
    *,
    offering: dict[str, Any],
    module_number: int = 1,
    live_index: int = 1,
    lives_per_module: int = DEFAULT_LIVES_PER_MODULE,
    weeks_per_module: int = DEFAULT_WEEKS_PER_MODULE,
    keyword: str = DEFAULT_ASYNC_LESSON_KEYWORD,
) -> dict[str, Any]:
    """Build the wizard preview: connected Lessons and expectation codes.

    Args:
        school: ``SchoolDB``.
        offering: Course offering row.
        module_number: 1-based module.
        live_index: 1-based live class in the module.
        lives_per_module: Live classes per module (default 4).
        weeks_per_module: Display-only weeks (default 2).
        keyword: Async title filter (default ``Lesson``).
    """
    module_n = max(int(module_number or 1), 1)
    live_i = max(int(live_index or 1), 1)
    lives = max(int(lives_per_module or DEFAULT_LIVES_PER_MODULE), 1)
    weeks = max(int(weeks_per_module or DEFAULT_WEEKS_PER_MODULE), 1)
    key = lesson_key(module_n, live_i)
    library_id = offering.get("library_id")
    if not library_id:
        lib = school.latest_library_for_code(str(offering.get("ontario_code") or ""))
        library_id = lib["id"] if lib else None
    outlines: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    if library_id:
        try:
            from components import outline_nav
        except ImportError:
            from lms.components import outline_nav
        outlines = outline_nav(school, int(library_id))
        chosen = resolve_module_outline(outlines, module_n)
        if chosen:
            items = list(chosen.get("items") or [])
    lessons = filter_async_lessons(items, keyword)
    k = len(lessons)
    start, end = live_class_lesson_window(k, lives, live_i)
    connected = [row for row in lessons if start <= int(row["lesson_number"]) <= end]
    ontario = str(offering.get("ontario_code") or "MCF3M")
    expectations = _expectations_for_matching(school, ontario)
    try:
        from live_class_slides import strand_from_module
    except ImportError:
        from lms.live_class_slides import strand_from_module
    module_title_guess = str(
        (resolve_module_outline(outlines, module_n) or {}).get("title") or ""
    )
    strand_hint = strand_from_module(
        module_title_guess, module_n, course_code=ontario
    )
    if strand_hint:
        expectations = [
            row
            for row in expectations
            if str(row.get("code") or "").upper().startswith(strand_hint)
            or str(row.get("strand") or "").upper().startswith(strand_hint)
        ]
    matched: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for lesson in connected:
        blob = module_item_blob(school, int(library_id or 0), lesson) if library_id else str(
            lesson.get("title") or ""
        )
        for hit in match_expectations(blob, expectations):
            if hit["code"] in seen_codes:
                continue
            seen_codes.add(hit["code"])
            matched.append(hit)
    names = [f"Lesson {row['lesson_number']}" for row in connected]
    summary = (
        f"{key} connects to {', '.join(names)}" if names else f"{key} connects to no async Lessons"
    )
    challenge = default_team_challenge(module_n, live_i)
    return {
        "ok": True,
        "lesson_key": key,
        "module_number": module_n,
        "live_index": live_i,
        "lives_per_module": lives,
        "weeks_per_module": weeks,
        "keyword": (keyword or DEFAULT_ASYNC_LESSON_KEYWORD).strip() or DEFAULT_ASYNC_LESSON_KEYWORD,
        "async_lesson_count": k,
        "window_start": start,
        "window_end": end,
        "connected_lessons": [
            {
                "lesson_number": int(row["lesson_number"]),
                "title": str(row.get("title") or ""),
                "item_id": row.get("id"),
            }
            for row in connected
        ],
        "expectation_codes": [row["code"] for row in matched],
        "expectations": matched,
        "summary": summary,
        "team_challenge": challenge,
        "module_title": (
            str((resolve_module_outline(outlines, module_n) or {}).get("title") or f"Module {module_n}")
            if outlines
            else f"Module {module_n}"
        ),
        "library_id": int(library_id) if library_id else None,
        "ontario_code": ontario,
        "strand": strand_hint or "",
    }


def local_curriculum_course_dir(
    course_code: str,
    cache_root: Path | None = None,
) -> Path:
    """Return the keyed local cache dir ``.local-data/curriculum/{CODE}``.

    Args:
        course_code: Ontario course code.
        cache_root: Override of ``default_local_dest`` (tests).
    """
    try:
        from math_expectations_pdf import default_local_dest
    except ImportError:
        from lms.math_expectations_pdf import default_local_dest
    code = re.sub(r"[^A-Z0-9]", "", (course_code or "").upper())
    root = cache_root if cache_root is not None else default_local_dest()
    return Path(root) / (code or "_")


def _read_json_file(path: Path) -> Any | None:
    """Load JSON or return ``None`` when the file is missing or invalid.

    Args:
        path: File to read.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None


def _read_text_file(path: Path) -> str:
    """Read UTF-8 text; empty when missing.

    Args:
        path: File to read.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _stem_from_payload(row: dict[str, Any]) -> str:
    """Official or teacher stem already stored on a row — never invented.

    Accepts ``stem`` / ``text``, or a non-empty ``examples`` list of strings
    (extract ``specifics`` rows). Empty strings are skipped.

    Args:
        row: Bank item, sqlite question, or example index entry.
    """
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    stem = str(
        row.get("stem")
        or payload.get("stem")
        or payload.get("text")
        or row.get("text")
        or ""
    ).strip()
    if stem:
        return stem
    examples = row.get("examples")
    if examples is None:
        examples = payload.get("examples")
    if isinstance(examples, list):
        parts = [str(item).strip() for item in examples if str(item or "").strip()]
        if parts:
            return "\n".join(parts)
    return ""


def _candidate_from_row(
    row: dict[str, Any],
    *,
    source: str,
    blob: str = "",
) -> dict[str, str]:
    """Normalize a consolidation candidate.

    Args:
        row: Source record.
        source: ``sqlite``, ``local_bank``, or ``official_example``.
        blob: Flattened text used for clustering when already computed.
    """
    stem = _stem_from_payload(row) or _plain_html(blob)[:800]
    title = str(row.get("title") or row.get("code") or "Question").strip() or "Question"
    item_type = str(row.get("item_type") or row.get("kind") or source)
    cluster_blob = blob or f"{title} {stem} {item_type}"
    return {
        "title": title,
        "stem": stem or title,
        "item_type": item_type,
        "bucket": _bucket_for_question(cluster_blob),
        "source": source,
    }


def _declared_expectation_codes(row: dict[str, Any]) -> set[str]:
    """Codes listed on the item fields — not scraped from stem text.

    Empty ``expectation_codes`` means the row is a strand-level pool item.

    Args:
        row: Local bank item.
    """
    found: set[str] = set()
    raw = row.get("expectation_codes")
    if raw is None:
        raw = row.get("codes")
    if raw is None and "code" in row:
        raw = row.get("code")
    if isinstance(raw, str):
        raw = [raw]
    for item in raw or []:
        code = str(item or "").strip().upper()
        if code:
            found.add(code)
    return found


def _item_is_verified(row: dict[str, Any]) -> bool:
    """True when a bank item is marked ``verified: true``.

    Args:
        row: Local bank item.
    """
    val = row.get("verified")
    if val is True:
        return True
    if isinstance(val, str) and val.strip().lower() in {"true", "1", "yes"}:
        return True
    return False


def _local_bank_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    """Rank verified Nelson stems ahead of unverified real stems.

    Args:
        row: Local bank item.
    """
    verified = _item_is_verified(row)
    nelson = str(row.get("source") or "").strip().lower() == "nelson"
    if nelson and verified:
        return (0, 0)
    if verified:
        return (1, 0)
    if nelson:
        return (2, 0)
    return (3, 0)


def load_local_bank_items(
    course_code: str,
    module_number: int,
    strand: str,
    expectation_codes: set[str],
    *,
    cache_root: Path | None = None,
) -> list[dict[str, str]]:
    """Read ``{CODE}/banks/M{n}/{strand}/items.json`` only — no other courses.

    Empty or missing files yield ``[]``. Stems are copied from the JSON; none
    are invented.

    When an item lists ``expectation_codes``, it must intersect the connected
    lesson codes. When that field is empty, the item stays in the pool because
    it already sits in the requested ``M{n}/{strand}`` file (strand-level
    Nelson extract). Verified Nelson items are returned first.

    Args:
        course_code: Ontario course code.
        module_number: Module N (same as Lesson Slides ``module=``).
        strand: Strand letter (``A``–``E``).
        expectation_codes: Connected specific codes; coded items must hit one.
        cache_root: Optional cache root override.
    """
    letter = str(strand or "").strip().upper()[:1]
    if not letter.isalpha():
        return []
    path = (
        local_curriculum_course_dir(course_code, cache_root)
        / "banks"
        / f"M{int(module_number)}"
        / letter
        / "items.json"
    )
    payload = _read_json_file(path)
    if payload is None:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = list(payload.get("items") or [])
    else:
        return []
    codes = {str(c).upper() for c in expectation_codes}
    kept: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        if not _stem_from_payload(row) and not str(row.get("title") or "").strip():
            continue
        item_codes = _declared_expectation_codes(row)
        if codes and item_codes and not (item_codes & codes):
            continue
        kept.append(row)
    kept.sort(key=_local_bank_sort_key)
    return [_candidate_from_row(row, source="local_bank") for row in kept]


def load_official_example_stems(
    course_code: str,
    expectation_codes: set[str],
    *,
    cache_root: Path | None = None,
) -> list[dict[str, str]]:
    """Look up official sample stems in ``{CODE}/examples-index.json`` by code.

    Indexes ``by_code``, ``examples``, ``items``, and ``specifics``. Does not
    scan every ``examples/*.md``. Missing index or empty stems skip the code.
    File paths listed **in the index** may be read as the stem.

    Args:
        course_code: Ontario course code.
        expectation_codes: Connected specific codes to resolve.
        cache_root: Optional cache root override.
    """
    codes = [str(c).upper() for c in expectation_codes if str(c).strip()]
    if not codes:
        return []
    course_dir = local_curriculum_course_dir(course_code, cache_root)
    index = _read_json_file(course_dir / "examples-index.json")
    if not isinstance(index, dict):
        return []
    by_code: dict[str, Any] = {}
    if isinstance(index.get("by_code"), dict):
        by_code.update(index["by_code"])
    if isinstance(index.get("examples"), dict):
        by_code.update(index["examples"])
    elif isinstance(index.get("examples"), list):
        for row in index["examples"]:
            if not isinstance(row, dict):
                continue
            key = str(row.get("code") or "").strip().upper()
            if key:
                by_code[key] = row
    for key, value in index.items():
        if key in {"by_code", "examples", "course_code", "items", "specifics"}:
            continue
        if CODE_RE.fullmatch(str(key).strip()):
            by_code[str(key).strip().upper()] = value
    for field in ("items", "specifics"):
        if not isinstance(index.get(field), list):
            continue
        for row in index[field]:
            if not isinstance(row, dict):
                continue
            key = str(row.get("code") or "").strip().upper()
            if key:
                by_code[key] = row
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            continue
        entry = by_code.get(code)
        if entry is None:
            continue
        seen.add(code)
        stem = ""
        title = f"Sample problem {code}"
        rel = ""
        if isinstance(entry, str):
            if entry.endswith(".md") or "/" in entry:
                rel = entry
            else:
                stem = entry.strip()
        elif isinstance(entry, dict):
            stem = _stem_from_payload(entry)
            title = str(entry.get("title") or title)
            rel = str(entry.get("path") or entry.get("file") or "").strip()
        if not stem and rel:
            target = Path(rel)
            if not target.is_absolute():
                target = course_dir / rel
            try:
                target.resolve().relative_to(course_dir.resolve())
            except (OSError, ValueError):
                target = None
            if target is not None:
                stem = _read_text_file(target)
        if not stem:
            continue
        out.append(
            _candidate_from_row(
                {"title": title, "stem": stem, "item_type": "official_example", "code": code},
                source="official_example",
            )
        )
    return out


def collect_consolidation_items(
    school: Any,
    *,
    course_code: str,
    module_number: int,
    strand: str,
    expectation_codes: set[str] | list[str],
    library_id: int | None = None,
    cache_root: Path | None = None,
) -> list[dict[str, str]]:
    """Pull consolidation stems by course, module, strand, and codes.

    Preference order (never Drive, never invented wording):

    1. Ingested sqlite questions in this offering's library matching the codes.
    2. Local ``banks/M{n}/{strand}/items.json`` — verified Nelson stems first,
       then unverified real stems (uncoded items in that file stay in the pool).
    3. Official ``examples-index.json`` keyed by those codes.

    Missing cache files return empty for that layer.

    Args:
        school: ``SchoolDB``.
        course_code: Ontario course code.
        module_number: Module N.
        strand: Strand letter from ``strand_from_module``.
        expectation_codes: Connected specific codes from Lesson mapping.
        library_id: Offering library; sqlite layer skipped when absent.
        cache_root: Optional ``.local-data/curriculum`` override.
    """
    codes = {str(c).strip().upper() for c in (expectation_codes or []) if str(c).strip()}
    out: list[dict[str, str]] = []
    seen_stems: set[str] = set()

    def _append(rows: list[dict[str, str]]) -> None:
        for row in rows:
            key = " ".join(
                str(row.get("stem") or "").split()
            ).lower()
            if not key or key in seen_stems:
                continue
            seen_stems.add(key)
            out.append(row)

    if library_id:
        try:
            from components import list_questions_matching_codes
        except ImportError:
            from lms.components import list_questions_matching_codes
        sqlite_rows = list_questions_matching_codes(
            school,
            int(library_id),
            sorted(codes),
            module_number=module_number,
            strand=strand,
        )
        for q in sqlite_rows:
            blob = _question_text(q)
            _append([_candidate_from_row(q, source="sqlite", blob=blob)])
    _append(
        load_local_bank_items(
            course_code,
            module_number,
            strand,
            codes,
            cache_root=cache_root,
        )
    )
    _append(
        load_official_example_stems(
            course_code,
            codes,
            cache_root=cache_root,
        )
    )
    return out


def _question_text(row: dict[str, Any]) -> str:
    """Flatten a bank question for matching.

    Args:
        row: Question row with optional ``payload``.
    """
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    stem = str(payload.get("stem") or payload.get("text") or "")
    return " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("item_type") or ""),
            stem,
            json.dumps(payload, ensure_ascii=False),
        ]
    )


def _bucket_for_question(text: str) -> str:
    """Assign a consolidation cluster from distinctive tokens.

    Args:
        text: Flattened question text.
    """
    lower = text.lower()
    for name, needles in QUESTION_BUCKETS:
        if any(n in lower for n in needles):
            return name
    return "other"


def pick_consolidation_questions(
    school: Any,
    *,
    offering: dict[str, Any],
    preview: dict[str, Any],
    limit: int = 3,
    cache_root: Path | None = None,
) -> list[dict[str, str]]:
    """Pick up to three clustered questions tied to connected expectations.

    Prefers sqlite questions matching connected codes, then local
    ``banks/M{n}/{strand}/items.json`` (verified Nelson first), then official
    ``examples-index.json``.
    Falls back to a library-wide ``list_questions`` scan, then live-problem
    ``standard`` items for the lesson key.

    Args:
        school: ``SchoolDB``.
        offering: Offering row.
        preview: ``preview_live_class`` result.
        limit: Maximum questions (default 3).
        cache_root: Optional local curriculum cache override (tests).
    """
    codes = {str(c).upper() for c in (preview.get("expectation_codes") or [])}
    statements = " ".join(
        str(row.get("statement") or "") for row in (preview.get("expectations") or [])
    )
    distinctive = _tokens(statements)
    library_id = preview.get("library_id") or offering.get("library_id")
    ontario = str(offering.get("ontario_code") or preview.get("ontario_code") or "")
    module_n = int(preview.get("module_number") or 1)
    strand = str(preview.get("strand") or "").strip().upper()[:1]
    if not strand:
        try:
            from live_class_slides import strand_from_module
        except ImportError:
            from lms.live_class_slides import strand_from_module
        strand = strand_from_module(
            str(preview.get("module_title") or ""),
            module_n,
            course_code=ontario,
        )
    candidates = collect_consolidation_items(
        school,
        course_code=ontario,
        module_number=module_n,
        strand=strand,
        expectation_codes=codes,
        library_id=int(library_id) if library_id else None,
        cache_root=cache_root,
    )
    if not candidates and library_id:
        try:
            from components import list_question_banks, list_questions
        except ImportError:
            from lms.components import list_question_banks, list_questions
        for bank in list_question_banks(school, int(library_id)):
            for q in list_questions(school, int(library_id), int(bank["id"])):
                blob = _question_text(q)
                blob_codes = {m.group(1).upper() for m in CODE_RE.finditer(blob)}
                toks = _tokens(blob)
                if codes and (blob_codes & codes or len(toks & distinctive) >= 2):
                    candidates.append(
                        {
                            "title": str(q.get("title") or "Question"),
                            "stem": _plain_html(blob)[:800],
                            "item_type": str(q.get("item_type") or ""),
                            "bucket": _bucket_for_question(blob),
                            "source": "sqlite",
                        }
                    )
    if not candidates:
        key = str(preview.get("lesson_key") or "")
        for row in school.list_live_problems(ontario_code=ontario, active_only=True):
            if str(row.get("kind") or "") != "standard":
                continue
            hint = str(row.get("module_hint") or "").upper()
            if key and key not in hint:
                continue
            stem = _plain_html(str(row.get("stem_html") or row.get("title") or ""))
            blob = f"{row.get('title')} {stem}"
            candidates.append(
                {
                    "title": str(row.get("title") or "Question"),
                    "stem": stem or str(row.get("title") or ""),
                    "item_type": "standard",
                    "bucket": _bucket_for_question(blob),
                }
            )
    picked: list[dict[str, str]] = []
    used_buckets: set[str] = set()
    for row in candidates:
        bucket = row.get("bucket") or "other"
        if bucket in used_buckets and len(picked) < limit:
            continue
        used_buckets.add(bucket)
        picked.append(row)
        if len(picked) >= limit:
            break
    if len(picked) < limit:
        for row in candidates:
            if row in picked:
                continue
            picked.append(row)
            if len(picked) >= limit:
                break
    return picked[:limit]


def chrome_binary() -> str | None:
    """Return a Chrome/Chromium executable if one is on this machine.

    Returns:
        Absolute path or ``None`` (Fly / headless servers often miss Chrome).
    """
    env = (os.environ.get("CHROME_BIN") or "").strip()
    if env and Path(env).is_file():
        return env
    named = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which(
        "chromium-browser"
    )
    if named:
        return named
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac.is_file():
        return str(mac)
    return None


def render_question_pngs(questions: list[dict[str, str]]) -> list[Path]:
    """Screenshot each question card to a PNG via headless Chrome.

    Args:
        questions: ``pick_consolidation_questions`` rows.

    Returns:
        PNG paths in the same order. Empty when Chrome is missing or fails.
    """
    binary = chrome_binary()
    if not binary or not questions:
        return []
    paths: list[Path] = []
    tmp = Path(tempfile.mkdtemp(prefix="lesson-slide-q-"))
    for index, row in enumerate(questions, start=1):
        html_path = tmp / f"q{index}.html"
        png_path = tmp / f"q{index}.png"
        title = html_escape(str(row.get("title") or f"Question {index}"))
        stem = html_escape(str(row.get("stem") or ""))
        html_path.write_text(
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<style>html,body{margin:0;padding:0;background:#0f172a;}"
                "section{width:1040px;min-height:560px;padding:36px 44px;"
                "font-family:Georgia,serif;color:#f8fafc;box-sizing:border-box}"
                "p.kicker{letter-spacing:.08em;text-transform:uppercase;"
                "font-size:14px;color:#5eead4;margin:0 0 12px}"
                "h1{font-size:28px;margin:0 0 18px;line-height:1.2}"
                "p.stem{font-size:22px;line-height:1.45;margin:0}</style></head>"
                f"<body><section><p class='kicker'>Consolidation {index}</p>"
                f"<h1>{title}</h1><p class='stem'>{stem}</p></section></body></html>"
            ),
            encoding="utf-8",
        )
        try:
            subprocess.run(
                [
                    binary,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    f"--window-size=1100,620",
                    f"--screenshot={png_path}",
                    html_path.as_uri(),
                ],
                check=True,
                timeout=20,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if not png_path.is_file() or png_path.stat().st_size < 32:
            return []
        paths.append(png_path)
    return paths


def _emu(value: Any) -> float:
    """Read a Slides size field as a float.

    Args:
        value: Number or ``{magnitude, unit}``.
    """
    if isinstance(value, dict):
        return float(value.get("magnitude") or 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _shape_area(element: dict[str, Any]) -> float:
    """Approximate on-slide area for ranking text boxes.

    Args:
        element: Slides ``pageElements`` item.
    """
    size = element.get("size") or {}
    width = _emu(size.get("width"))
    height = _emu(size.get("height"))
    if width and height:
        return width * height
    transform = element.get("transform") or {}
    return abs(float(transform.get("scaleX") or 0) * float(transform.get("scaleY") or 0))


def _element_text(element: dict[str, Any]) -> str:
    """Concatenate text runs on a shape.

    Args:
        element: Page element.
    """
    chunks: list[str] = []
    shape = element.get("shape") or {}
    for te in (shape.get("text") or {}).get("textElements") or []:
        run = te.get("textRun") or {}
        chunks.append(str(run.get("content") or ""))
    return "".join(chunks)


def _text_shapes(slide: dict[str, Any]) -> list[dict[str, Any]]:
    """Text-bearing shapes on a slide, largest first.

    Args:
        slide: Slides API slide resource.
    """
    found: list[dict[str, Any]] = []
    for el in slide.get("pageElements") or []:
        shape = el.get("shape") or {}
        if not shape or not el.get("objectId"):
            continue
        found.append(el)
    found.sort(key=_shape_area, reverse=True)
    return found


def _set_text_requests(object_id: str, text: str, current: str) -> list[dict[str, Any]]:
    """Delete existing text when present, then insert ``text``.

    Args:
        object_id: Shape object id.
        text: Replacement.
        current: Current concatenated runs.
    """
    reqs: list[dict[str, Any]] = []
    if (current or "").strip():
        reqs.append(
            {
                "deleteText": {
                    "objectId": object_id,
                    "textRange": {"type": "ALL"},
                }
            }
        )
    reqs.append(
        {
            "insertText": {
                "objectId": object_id,
                "text": text,
                "insertionIndex": 0,
            }
        }
    )
    return reqs


def _notes_body_id(slide: dict[str, Any]) -> tuple[str, str] | None:
    """Speaker-notes body shape id and current text.

    Args:
        slide: Slide with ``slideProperties.notesPage``.
    """
    notes = (slide.get("slideProperties") or {}).get("notesPage") or {}
    for el in notes.get("pageElements") or []:
        shape = el.get("shape") or {}
        ph = str((shape.get("placeholder") or {}).get("type") or "")
        if ph != "BODY":
            continue
        oid = str(el.get("objectId") or "")
        if not oid:
            continue
        return oid, _element_text(el)
    return None


def lesson_fill_values(
    *,
    ontario_code: str,
    preview: dict[str, Any],
    context: str,
    question: str,
    speaker_notes: str,
    team_names: list[str] | None = None,
    consolidation_text: str = "",
) -> dict[int, dict[str, str]]:
    """Per-index strings for the 7 style-guide slides.

    Index map: 0 TITLE, 1 TEAM_INTRO, 2 OPEN (empty), 3 CONTEXT, 4 QUESTION,
    5 REFLECTION, 6 CONSOLIDATION.

    Args:
        ontario_code: Course code.
        preview: Wizard preview (needs ``lesson_key``).
        context: Team-challenge context.
        question: Team-challenge question.
        speaker_notes: Notes on the context slide.
        team_names: Optional team labels.
        consolidation_text: Text fallback when images are unavailable.
    """
    key = str(preview.get("lesson_key") or "")
    title = f"{ontario_code}: {key}" if key else str(ontario_code)
    teams = ", ".join(n for n in (team_names or []) if n)
    return {
        0: {"title": title, "body": ""},
        1: {"body": teams},
        2: {},
        3: {"body": (context or "").strip(), "notes": (speaker_notes or "").strip()},
        4: {"body": (question or "").strip()},
        5: {"body": STOCK_REFLECTION},
        6: {"body": (consolidation_text or "").strip()},
    }


def build_index_fill_requests(
    presentation: dict[str, Any],
    values_by_index: dict[int, dict[str, str]],
) -> list[dict[str, Any]]:
    """Build ``batchUpdate`` requests by slide order, not layout names.

    Writes the largest text shape(s). Leaves OPEN_QUESTIONS_ROUND (index 2)
    empty. Does not require ``{{placeholders}}``.

    Args:
        presentation: GET presentations payload.
        values_by_index: ``lesson_fill_values`` map.

    Returns:
        Request list for ``presentations.batchUpdate``.
    """
    requests: list[dict[str, Any]] = []
    slides = list(presentation.get("slides") or [])
    for index, slide in enumerate(slides):
        payload = values_by_index.get(index) or {}
        if index == 2 or index >= len(TEMPLATE_LAYOUT_NAMES):
            continue
        title = (payload.get("title") or "").strip()
        body = (payload.get("body") or "").strip()
        notes = (payload.get("notes") or "").strip()
        shapes = _text_shapes(slide)
        if title and body and len(shapes) >= 2:
            requests.extend(
                _set_text_requests(str(shapes[0]["objectId"]), title, _element_text(shapes[0]))
            )
            requests.extend(
                _set_text_requests(str(shapes[1]["objectId"]), body, _element_text(shapes[1]))
            )
        elif title or body:
            text = title or body
            if shapes:
                requests.extend(
                    _set_text_requests(str(shapes[0]["objectId"]), text, _element_text(shapes[0]))
                )
        if notes:
            notes_box = _notes_body_id(slide)
            if notes_box:
                requests.extend(_set_text_requests(notes_box[0], notes, notes_box[1]))
    return requests


def consolidation_text_fallback(questions: list[dict[str, str]]) -> str:
    """Numbered stems when Chrome/Drive images are unavailable.

    Args:
        questions: Picked consolidation rows.
    """
    lines: list[str] = []
    for index, row in enumerate(questions, start=1):
        title = str(row.get("title") or f"Question {index}")
        stem = str(row.get("stem") or "").strip()
        lines.append(f"{index}. {title}")
        if stem and stem != title:
            lines.append(stem)
    return "\n".join(lines)


def render_lesson_mock_html(
    *,
    title: str,
    values_by_index: dict[int, dict[str, str]],
    questions: list[dict[str, str]],
    image_mode: str,
) -> str:
    """Local HTML stand-in for the 7-slide template (tests / mock token).

    Args:
        title: Deck heading.
        values_by_index: Index fill map.
        questions: Consolidation picks.
        image_mode: ``png`` or ``text``.
    """
    cards: list[str] = []
    for index, name in enumerate(TEMPLATE_LAYOUT_NAMES):
        payload = values_by_index.get(index) or {}
        body = html_escape(payload.get("title") or payload.get("body") or "")
        notes = html_escape(payload.get("notes") or "")
        extra = ""
        if index == 6 and questions:
            extra = "<ol>" + "".join(
                f"<li><strong>{html_escape(q.get('title') or '')}</strong> "
                f"{html_escape(q.get('stem') or '')}</li>"
                for q in questions
            ) + "</ol>"
        notes_html = f"<p class='notes'>Notes: {notes}</p>" if notes else ""
        cards.append(
            "<section class='slide'>"
            f"<p class='kicker'>Slide {index + 1} · {html_escape(name)}</p>"
            f"<pre>{body}</pre>{notes_html}{extra}</section>"
        )
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html_escape(title)}</title>"
        "<style>body{font-family:sans-serif;max-width:48rem;margin:2rem auto}"
        ".slide{border:1px solid #ccc;padding:1rem;margin:1rem 0;border-radius:8px}"
        "pre{white-space:pre-wrap}</style></head><body>"
        f"<h1>{html_escape(title)}</h1><p>Image mode: {html_escape(image_mode)}</p>"
        + "".join(cards)
        + "</body></html>"
    )


def write_lesson_mock_deck(
    school: Any,
    *,
    offering: dict[str, Any],
    lesson_key_value: str,
    html: str,
) -> tuple[str, str]:
    """Write a mock HTML deck under the offering instance.

    Args:
        school: ``SchoolDB``.
        offering: Offering with ``instance_relpath``.
        lesson_key_value: ``M1C1``.
        html: Rendered HTML.

    Returns:
        ``(presentation_id, url)``.
    """
    rel = offering.get("instance_relpath") or f"instances/{offering['id']}"
    dest_dir = Path(school.data_dir) / str(rel) / "slides"
    dest_dir.mkdir(parents=True, exist_ok=True)
    key = (lesson_key_value or "deck").strip().upper()
    pres_id = f"mock-lesson-{offering['id']}-{key}"
    path = dest_dir / f"{key}.html"
    path.write_text(html, encoding="utf-8")
    url = f"/staff/offerings/{int(offering['id'])}/lesson-slides/{key}.html"
    return pres_id, url


def generate_lesson_slides(
    school: Any,
    *,
    class_id: int,
    user: dict[str, Any],
    module_number: int = 1,
    live_index: int = 1,
    lives_per_module: int = DEFAULT_LIVES_PER_MODULE,
    weeks_per_module: int = DEFAULT_WEEKS_PER_MODULE,
    keyword: str = DEFAULT_ASYNC_LESSON_KEYWORD,
    context: str = "",
    question: str = "",
    speaker_notes: str = "",
    force_regenerate: bool = False,
    http: Any | None = None,
) -> dict[str, Any]:
    """Copy the theme template and fill by slide index.

    Args:
        school: ``SchoolDB``.
        class_id: MGS class id.
        user: Signed-in staff/IT row.
        module_number: Module outline position.
        live_index: Live class in the module.
        lives_per_module: Lives per module.
        weeks_per_module: Display-only weeks.
        keyword: Async Lesson filter.
        context: Team-challenge context (defaults for M1C1).
        question: Team-challenge question.
        speaker_notes: Context-slide notes.
        force_regenerate: Overwrite the stored Drive file.
        http: Optional requests-like session.

    Returns:
        Preview plus presentation ids and image mode.

    Raises:
        SlidesOperatorDenied: Wrong Google account.
        SlidesConnectRequired: No stored token (and not mock).
        KeyError: Missing class/offering.
    """
    from live_class_constants import is_slides_operator_email
    from live_class_slides import (
        GoogleSlidesClient,
        SlidesConnectRequired,
        SlidesOperatorDenied,
        mock_slides_enabled,
    )
    from slides_template import deck_drive_name

    email = str(user.get("email") or "")
    if not is_slides_operator_email(email):
        raise SlidesOperatorDenied(
            "Google Slides connect is limited to solutions@ and Shawn's Gmail."
        )
    cls = school.enrich_class(school.game.get_class(class_id))
    offering_id = cls.get("offering_id")
    if not offering_id:
        raise KeyError(f"class {class_id} has no offering")
    offering = school.ensure_offering_instance(school.get_offering(int(offering_id)))
    preview = preview_live_class(
        school,
        offering=offering,
        module_number=module_number,
        live_index=live_index,
        lives_per_module=lives_per_module,
        weeks_per_module=weeks_per_module,
        keyword=keyword,
    )
    defaults = preview.get("team_challenge") or {}
    context_text = (context or defaults.get("context") or "").strip()
    question_text = (question or defaults.get("question") or "").strip()
    notes_text = (speaker_notes or defaults.get("speaker_notes") or "").strip()
    questions = pick_consolidation_questions(school, offering=offering, preview=preview)
    existing = school.get_lesson_slide_deck(
        class_id, int(preview["module_number"]), int(preview["live_index"])
    )
    token_row = school.get_google_api_token(int(user["id"]))
    from live_class_constants import MOCK_SLIDES_REFRESH_TOKEN

    use_mock = mock_slides_enabled() or (
        token_row and token_row.get("refresh_token") == MOCK_SLIDES_REFRESH_TOKEN
    )
    pngs = [] if use_mock else render_question_pngs(questions)
    image_mode = "png" if pngs else "text"
    consol_text = "" if pngs else consolidation_text_fallback(questions)
    team_names: list[str] = []
    try:
        for team in (school.game.dashboard(class_id) or {}).get("teams") or []:
            label = team.get("name") or team.get("label")
            if label:
                team_names.append(str(label))
    except Exception:  # noqa: BLE001
        team_names = []
    values = lesson_fill_values(
        ontario_code=str(preview.get("ontario_code") or "MCF3M"),
        preview=preview,
        context=context_text,
        question=question_text,
        speaker_notes=notes_text,
        team_names=team_names,
        consolidation_text=consol_text,
    )
    if (
        existing
        and existing.get("presentation_id")
        and not force_regenerate
        and (use_mock or not str(existing.get("presentation_id") or "").startswith("mock-"))
    ):
        return {
            "ok": True,
            "reused": True,
            "preview": preview,
            "presentation_id": existing.get("presentation_id"),
            "presentation_url": existing.get("presentation_url"),
            "image_mode": ((existing.get("fill_json") or {}).get("image_mode") if isinstance(existing.get("fill_json"), dict) else None)
            or "text",
            "questions": questions,
        }
    if not use_mock and not token_row:
        raise SlidesConnectRequired()
    key = str(preview["lesson_key"])
    drive_title = deck_drive_name(str(preview.get("ontario_code") or "MCF3M"), key, "")
    if use_mock:
        html = render_lesson_mock_html(
            title=drive_title,
            values_by_index=values,
            questions=questions,
            image_mode=image_mode,
        )
        pres_id, pres_url = write_lesson_mock_deck(
            school, offering=offering, lesson_key_value=key, html=html
        )
        fill_meta = {"image_mode": image_mode, "mock": True, "request_count": 1}
    else:
        client = GoogleSlidesClient(school, user=user, http=http)
        overwrite = ""
        if force_regenerate and existing:
            old = str(existing.get("presentation_id") or "")
            if old and not old.startswith("mock-"):
                overwrite = old
        created = client.copy_template_and_fill_by_index(
            offering=offering,
            drive_title=drive_title,
            module_number=int(preview["module_number"]),
            values_by_index=values,
            png_paths=pngs,
            overwrite_presentation_id=overwrite,
        )
        pres_id = created["presentation_id"]
        pres_url = created["presentation_url"]
        image_mode = created.get("image_mode") or image_mode
        fill_meta = {
            "image_mode": image_mode,
            "mock": False,
            "request_count": int(created.get("request_count") or 0),
        }
    stored = school.upsert_lesson_slide_deck(
        class_id,
        int(preview["module_number"]),
        int(preview["live_index"]),
        presentation_id=pres_id,
        presentation_url=pres_url,
        preview_json=preview,
        fill_json=fill_meta,
    )
    return {
        "ok": True,
        "reused": False,
        "preview": preview,
        "presentation_id": stored.get("presentation_id"),
        "presentation_url": stored.get("presentation_url"),
        "image_mode": image_mode,
        "questions": questions,
        "chrome_available": chrome_binary() is not None,
        "image_fallback_note": (
            None
            if image_mode == "png"
            else "Chrome was not used for question images; three stems were written as text."
        ),
    }
