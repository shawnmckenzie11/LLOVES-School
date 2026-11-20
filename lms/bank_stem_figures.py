"""Put stripped MCF3M equation images back into the sentence.

Cartridge import kept diagrams and equation labels on ``image_urls`` and
deleted the ``<img>`` from the stem. A stem such as ``if = 12.5`` is that
label missing. ``figure_map`` / ``image_urls`` still list the files in
IMSCC order: the figure that sat above the sentence, then each inline
label.

This module writes those labels back into the open holes. It does not
delete questions, choices, or figure URLs. A second call on the same
HTML changes nothing.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_IMG_SRC_RE = re.compile(
    r"""<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_SPAN_EQ_RE = re.compile(r"<span\b[^>]*>\s*=\s*</span>", re.IGNORECASE)
_DOUBLE_SPACE_RE = re.compile(r" {2,}")
_IS_COMMA_RE = re.compile(r"\b(?:is|by) ,")
_STARTED_RE = re.compile(r"started,\s*is the")
_FOR_WRITTEN_RE = re.compile(r"\bfor written\b")
_STEP_RE = re.compile(r"\d\.(?:&nbsp;|\xa0)")
_CELL_ONE_RE = re.compile(r"<div>1, or\s*</div>", re.IGNORECASE)
_CELL_REST_RE = re.compile(r"<div>\s*, or\s*</div>", re.IGNORECASE)
_COLON_DOT_RE = re.compile(r":\s*\.")
_COLON_SPAN_RE = re.compile(r":\s*<span>\s*</span>\s*\.", re.IGNORECASE)
_EMPTY_PAD_RE = re.compile(r'<td class="pad">\s*</td>', re.IGNORECASE)
_TRAILING_EXPR_RE = re.compile(
    r"\b(?:Evaluate|Simplify) \.|equivalent to \?|\bis \."
)
#: A diagram that sat above the sentence is the first file in IMSCC order.
_FIGURE_FIRST_WORDS = ("above", "figure", "triangle", "table")
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"([A-Za-z0-9°]+)\s*$")


class _Hole:
    """One open spot in a stem, in document order.

    Args:
        start: Inclusive index in the source HTML.
        end: Exclusive index replaced by :meth:`render`.
        slots: How many equation images this spot consumes.
        render: Builds the replacement from that many URLs.
    """

    def __init__(
        self,
        start: int,
        end: int,
        slots: int,
        render: Callable[[list[str]], str],
    ) -> None:
        self.start = start
        self.end = end
        self.slots = slots
        self._render = render

    def render(self, urls: list[str]) -> str:
        """Return the HTML that replaces this hole.

        Args:
            urls: Equation image URLs, length :attr:`slots`.
        """
        return self._render(urls)


def equation_image_tag(url: str) -> str:
    """Return an inline equation ``<img>`` for one public figure URL.

    Args:
        url: Browser path such as ``/curriculum-bank-images/MCF3M/cartridge/ab.jpg``.
    """
    safe = html.escape(str(url or "").strip(), quote=True)
    return f'<img class="equation-inline" src="{safe}" alt="">'


def _plain(fragment: str) -> str:
    """Strip tags and collapse whitespace in one HTML prefix."""
    text = _TAG_RE.sub(" ", str(fragment or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _equals_is_hole(prefix_html: str) -> bool:
    """True when ``=`` is not already preceded by the variable it belongs to.

    ``a = 9`` keeps the equals sign. ``if = 12.5`` and ``side = 74`` lost
    the label image that used to sit in front of the equals sign.

    Args:
        prefix_html: Stem HTML before the equals span.
    """
    token_match = _TOKEN_RE.search(_plain(prefix_html))
    if token_match is None:
        return True
    token = token_match.group(1)
    if len(token) == 1 and token.isalpha():
        return False
    if token.isupper() and 2 <= len(token) <= 4:
        return False
    return True


def _inside_tag(source: str, index: int) -> bool:
    """True when ``index`` sits inside an HTML tag.

    Args:
        source: Stem HTML.
        index: Character offset.
    """
    return source.rfind("<", 0, index) > source.rfind(">", 0, index)


def _keep_open(holes: list[_Hole]) -> list[_Hole]:
    """Drop holes that overlap an earlier one.

    Args:
        holes: Unsorted hole candidates.
    """
    ordered = sorted(holes, key=lambda hole: (hole.start, hole.end))
    kept: list[_Hole] = []
    end = -1
    for hole in ordered:
        if hole.start < end:
            continue
        kept.append(hole)
        end = hole.end
    return kept


def open_equation_holes(stem_html: str) -> list[_Hole]:
    """Return inline holes in one stem, in reading order.

    A hole is a place the cartridge left empty when it removed an equation
    image: a bare equals span, a double space, ``is ,``, a blank proof
    step, an empty population cell, ``: .``, an empty span before a
    period, or an empty ``td.pad`` step cell. Spots that already name the
    variable are not holes.

    Args:
        stem_html: Stored stem HTML.
    """
    source = str(stem_html or "")
    found: list[_Hole] = []

    def one(template: str) -> Callable[[list[str]], str]:
        """Fill ``{img}`` in ``template`` with the first equation image."""

        def render(urls: list[str]) -> str:
            return template.format(img=equation_image_tag(urls[0]))

        return render

    def two(template: str) -> Callable[[list[str]], str]:
        """Fill ``{img0}`` and ``{img1}`` in a two-image cell."""

        def render(urls: list[str]) -> str:
            return template.format(
                img0=equation_image_tag(urls[0]),
                img1=equation_image_tag(urls[1]),
            )

        return render

    for match in _SPAN_EQ_RE.finditer(source):
        if not _equals_is_hole(source[: match.start()]):
            continue
        found.append(_Hole(match.start(), match.end(), 1, one("{img} =")))

    for match in _DOUBLE_SPACE_RE.finditer(source):
        if _inside_tag(source, match.start()):
            continue
        found.append(_Hole(match.start(), match.end(), 1, one(" {img} ")))

    for match in _IS_COMMA_RE.finditer(source):
        word = match.group(0).split()[0]
        found.append(_Hole(match.start(), match.end(), 1, one(f"{word} {{img}},")))

    for match in _STARTED_RE.finditer(source):
        found.append(
            _Hole(match.start(), match.end(), 1, one("started, {img} is the"))
        )

    for match in _FOR_WRITTEN_RE.finditer(source):
        found.append(
            _Hole(match.start(), match.end(), 1, one("for {img} written"))
        )

    for match in _STEP_RE.finditer(source):
        number = match.group(0).split(".", 1)[0]
        found.append(_Hole(match.start(), match.end(), 1, one(f"{number}. {{img}}")))

    for match in _CELL_ONE_RE.finditer(source):
        found.append(
            _Hole(match.start(), match.end(), 1, one("<div>1, or {img}</div>"))
        )

    for match in _CELL_REST_RE.finditer(source):
        found.append(
            _Hole(
                match.start(),
                match.end(),
                2,
                two("<div>{img0}, or {img1}</div>"),
            )
        )

    for match in _COLON_SPAN_RE.finditer(source):
        found.append(_Hole(match.start(), match.end(), 1, one(": {img}.")))

    for match in _COLON_DOT_RE.finditer(source):
        found.append(_Hole(match.start(), match.end(), 1, one(": {img}.")))

    for match in _EMPTY_PAD_RE.finditer(source):
        found.append(
            _Hole(match.start(), match.end(), 1, one('<td class="pad">{img}</td>'))
        )

    for match in _TRAILING_EXPR_RE.finditer(source):
        label = match.group(0)
        if label.endswith("?"):
            template = label[:-1] + "{img}?"
        else:
            template = label[:-1] + "{img}."
        found.append(_Hole(match.start(), match.end(), 1, one(template)))

    return _keep_open(found)


def _embedded_urls(*chunks: str) -> set[str]:
    """Return ``<img src>`` values already present in these fragments."""
    found: set[str] = set()
    for chunk in chunks:
        found.update(_IMG_SRC_RE.findall(str(chunk or "")))
    return found


def _choice_html(choices: Any, options_html: Any) -> str:
    """Join choice HTML so those images are not treated as stem holes.

    Args:
        choices: Bank ``choices`` list. Each row may be a dict with ``html``.
        options_html: Live-card ``options_html`` list.
    """
    parts: list[str] = []
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, dict):
                parts.append(str(choice.get("html") or choice.get("text") or ""))
            else:
                parts.append(str(choice or ""))
    if isinstance(options_html, list):
        parts.extend(str(item or "") for item in options_html)
    return "\n".join(parts)


def _stem_hash(text: str) -> str:
    """Return a stable hash of one plain stem.

    Args:
        text: Stem text with any spacing the editor stored.
    """
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def _payload_stem_text(payload: dict[str, Any]) -> str:
    """Return the plain stem stored on a bank or card payload.

    Args:
        payload: Question or playlist JSON.
    """
    for key in ("text", "stem", "prompt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return _plain(str(payload.get("stem_html") or payload.get("text_html") or ""))


@lru_cache(maxsize=1)
def original_stem_hashes() -> dict[str, str]:
    """Map each MCF3M module 5–7 ``smart_id`` to its cartridge stem hash.

    Returns:
        Hex hashes of the plain stems in the committed bank JSON.
    """
    try:
        from curriculum_bank_images import MCF3M_M567_BANK_DIR
    except ImportError:
        from lms.curriculum_bank_images import MCF3M_M567_BANK_DIR

    found: dict[str, str] = {}
    for module in (5, 6, 7):
        path = MCF3M_M567_BANK_DIR / f"M{module}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        items = document.get("items") if isinstance(document, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            smart_id = str(item.get("smart_id") or "").strip()
            if smart_id:
                found[smart_id] = _stem_hash(str(item.get("stem") or ""))
    return found


def _is_mcf3m_m567(course: str, import_key: str, bank_key: str) -> bool:
    """True when this bank row is MCF3M module 5, 6, or 7.

    Args:
        course: Library Ontario course code.
        import_key: Question import key.
        bank_key: Question-bank import key.
    """
    if str(course or "").strip().upper() != "MCF3M":
        return False
    blob = f"{import_key or ''} {bank_key or ''}"
    return re.search(r"(?:^|:)M[567](?:$|:)", blob) is not None


def _figure_urls(payload: dict[str, Any]) -> list[str]:
    """Return figure URLs in bank order.

    Args:
        payload: Bank payload or live-card item.
    """
    urls: list[str] = []
    raw = payload.get("image_urls")
    if isinstance(raw, list):
        urls.extend(str(url).strip() for url in raw if str(url).strip())
    single = str(payload.get("image_url") or "").strip()
    if single and single not in urls:
        urls.insert(0, single)
    return urls


def inline_equation_urls(
    urls: list[str],
    *,
    stem_html: str,
    choice_html: str,
    stem_text: str,
    holes: list[_Hole],
) -> list[str] | None:
    """Pick the equation images that fill ``holes``, in IMSCC order.

    Images already inside the stem or a choice stay where they are. When
    the stem points at a figure above it and there is one extra image,
    that first image is the diagram and the rest fill the holes. Any
    other mismatch is left unmapped.

    Args:
        urls: ``image_urls`` in bank order.
        stem_html: Stem HTML the images may already be in.
        choice_html: Choice HTML. Images here are answers, not stem labels.
        stem_text: Plain stem, used to notice "figure above".
        holes: Open holes from :func:`open_equation_holes`.

    Returns:
        The URLs to insert, or ``None`` when the counts do not map.
    """
    used = _embedded_urls(stem_html, choice_html)
    orphans = [url for url in urls if url and url not in used]
    slots = sum(hole.slots for hole in holes)
    if not slots or not orphans:
        return [] if not slots else None
    if len(orphans) == slots:
        return orphans
    text = str(stem_text or "").lower()
    if len(orphans) == slots + 1 and any(word in text for word in _FIGURE_FIRST_WORDS):
        return orphans[1:]
    # "Steps below" keeps the expression in the sentence and the steps graphic last.
    if len(orphans) == slots + 1 and "below" in text:
        return orphans[:-1]
    return None


def restore_inline_equation_images(
    stem_html: str,
    urls: list[str],
    *,
    choice_html: str = "",
    stem_text: str = "",
) -> tuple[str, str | None]:
    """Insert equation images into open holes in one stem.

    The input is returned unchanged when there is no hole, when the images
    are already inline, or when the image count does not match the holes.
    Running it again on its own output returns that same output.

    Args:
        stem_html: Stem HTML that may contain holes.
        urls: Figure URLs in IMSCC / ``image_urls`` order.
        choice_html: Choice HTML whose images must not move into the stem.
        stem_text: Plain stem. Falls back to the text of ``stem_html``.

    Returns:
        ``(html, reason)``. ``reason`` is ``None`` when the stem is
        unchanged or the holes were filled. It is ``unmapped`` when holes
        and images do not line up, and the HTML is then unchanged.
    """
    source = str(stem_html or "")
    holes = open_equation_holes(source)
    if not holes:
        return source, None
    plain = stem_text or _plain(source)
    chosen = inline_equation_urls(
        list(urls or []),
        stem_html=source,
        choice_html=choice_html,
        stem_text=plain,
        holes=holes,
    )
    if chosen is None:
        return source, "unmapped"
    if not chosen:
        return source, None
    parts: list[str] = []
    cursor = 0
    index = 0
    for hole in holes:
        parts.append(source[cursor : hole.start])
        parts.append(hole.render(chosen[index : index + hole.slots]))
        index += hole.slots
        cursor = hole.end
    parts.append(source[cursor:])
    return "".join(parts), None


def restore_payload_figures(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a copy with equation images inlined, or ``None`` if unchanged.

    Only ``stem_html`` and ``text_html`` are rewritten. Choices, keys, and
    ``image_urls`` stay so the diagram above the sentence still renders.

    Args:
        payload: Question payload or playlist item JSON.
    """
    if not isinstance(payload, dict):
        return None
    urls = _figure_urls(payload)
    if not urls:
        return None
    choice_html = _choice_html(payload.get("choices"), payload.get("options_html"))
    stem_text = str(payload.get("text") or payload.get("stem") or "")
    updated = dict(payload)
    changed = False
    for key in ("stem_html", "text_html"):
        raw = payload.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        restored, _reason = restore_inline_equation_images(
            raw,
            urls,
            choice_html=choice_html,
            stem_text=stem_text,
        )
        if restored != raw:
            updated[key] = restored
            changed = True
    if not changed:
        return None
    return updated


def unmapped_seed_stems(banks_dir: Path | None = None) -> list[dict[str, str]]:
    """List MCF3M module 5–7 stems whose holes do not match their figures.

    Args:
        banks_dir: Directory of ``M5.json``, ``M6.json``, and ``M7.json``.
            ``None`` uses the committed seed directory.

    Returns:
        One dict per unmapped stem with ``smart_id`` and a short ``stem``.
    """
    if banks_dir is None:
        try:
            from curriculum_bank_images import MCF3M_M567_BANK_DIR
        except ImportError:
            from lms.curriculum_bank_images import MCF3M_M567_BANK_DIR

        banks_dir = MCF3M_M567_BANK_DIR
    unmapped: list[dict[str, str]] = []
    for module in (5, 6, 7):
        path = banks_dir / f"M{module}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        items = document.get("items") if isinstance(document, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            stem_html = str(item.get("stem_html") or "")
            _restored, reason = restore_inline_equation_images(
                stem_html,
                _figure_urls(item),
                choice_html=_choice_html(item.get("choices"), None),
                stem_text=str(item.get("stem") or ""),
            )
            if reason != "unmapped":
                continue
            unmapped.append(
                {
                    "smart_id": str(item.get("smart_id") or ""),
                    "stem": _plain(stem_html)[:160],
                }
            )
    return unmapped


def _table_exists(school: Any, name: str) -> bool:
    """True when ``name`` is a table on this connection."""
    row = school.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _overlay_edited_ids(school: Any) -> set[int]:
    """Return question ids whose teacher overlay replaced the stem.

    Args:
        school: Open school database. The caller holds ``school._lock``.
    """
    if not _table_exists(school, "library_question_overlays"):
        return set()
    rows = school.conn.execute(
        """
        SELECT question_id FROM library_question_overlays
        WHERE TRIM(COALESCE(stem_text, '')) != ''
        """
    ).fetchall()
    return {int(row["question_id"]) for row in rows}


def _repaired_json(
    raw: str,
    *,
    course: str,
    import_key: str,
    bank_key: str,
    smart_id: str,
    question_id: int | None,
    edited_ids: set[int],
) -> str | None:
    """Return replacement JSON when this row is an unedited MCF3M M5–M7 stem.

    A teacher edit (different stem text, ``edited_in_lms``, or an overlay)
    is left alone. Another course is left alone even when its JSON mentions
    an MCF3M figure URL.

    Args:
        raw: Stored JSON text.
        course: Library course code for the source question.
        import_key: Question import key.
        bank_key: Bank import key.
        smart_id: Cartridge id from the row or its source question.
        question_id: ``questions.id`` when this row is or copies one.
        edited_ids: Question ids with a teacher stem overlay.

    Returns:
        The new JSON, or ``None`` when the row must not change.
    """
    if question_id is not None and int(question_id) in edited_ids:
        return None
    if not _is_mcf3m_m567(course, import_key, bank_key):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("edited_in_lms"):
        return None
    token = str(payload.get("smart_id") or smart_id or "").strip()
    if not token and str(import_key or "").startswith("cartridge:"):
        token = str(import_key).rsplit(":", 1)[-1]
    expected = original_stem_hashes().get(token)
    if not expected or _stem_hash(_payload_stem_text(payload)) != expected:
        return None
    restored = restore_payload_figures(payload)
    if restored is None:
        return None
    encoded = json.dumps(restored)
    if encoded == raw:
        return None
    return encoded


def _apply_repairs(school: Any, rows: list[Any], *, column: str, touch_updated: bool) -> int:
    """Write repaired JSON for rows that still match the cartridge stem.

    Args:
        school: Open school database. The caller holds ``school._lock``.
        rows: Query rows with ``id``, ``blob``, ``course``, ``import_key``,
            ``bank_key``, ``smart_id``, and ``question_id``.
        column: JSON column being replaced. Whitelisted by the caller.
        touch_updated: When True, also set ``updated_at`` so a phone stamp
            that reads prompt time moves.

    Returns:
        How many rows were updated.
    """
    if column not in {"payload_json", "item_json", "payload"}:
        return 0
    edited_ids = _overlay_edited_ids(school)
    updated = 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for row in rows:
        raw = str(row["blob"] or "")
        question_id = row["question_id"]
        try:
            question_id = int(question_id) if question_id not in (None, "") else None
        except (TypeError, ValueError):
            question_id = None
        encoded = _repaired_json(
            raw,
            course=str(row["course"] or ""),
            import_key=str(row["import_key"] or ""),
            bank_key=str(row["bank_key"] or ""),
            smart_id=str(row["smart_id"] or ""),
            question_id=question_id,
            edited_ids=edited_ids,
        )
        if encoded is None:
            continue
        table_name = str(row["table_name"])
        if table_name not in {
            "questions",
            "class_live_playlist_placements",
            "live_session_prompts",
            "participation_items",
        }:
            continue
        if touch_updated and table_name == "live_session_prompts":
            cursor = school.conn.execute(
                f"UPDATE {table_name} SET {column} = ?, updated_at = ? WHERE id = ? AND {column} = ?",
                (encoded, now, int(row["id"]), raw),
            )
        else:
            cursor = school.conn.execute(
                f"UPDATE {table_name} SET {column} = ? WHERE id = ? AND {column} = ?",
                (encoded, int(row["id"]), raw),
            )
        if int(cursor.rowcount or 0) > 0:
            updated += 1
    return updated


def _question_rows(school: Any) -> list[Any]:
    """MCF3M figure questions, still filtered by course and module later.

    Args:
        school: Open school database. The caller holds ``school._lock``.
    """
    if not _table_exists(school, "questions"):
        return []
    return list(
        school.conn.execute(
            """
            SELECT q.id, q.payload_json AS blob, 'questions' AS table_name,
                   l.ontario_code AS course, q.import_key AS import_key,
                   b.import_key AS bank_key,
                   json_extract(q.payload_json, '$.smart_id') AS smart_id,
                   q.id AS question_id
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            JOIN content_libraries l ON l.id = b.library_id
            WHERE q.payload_json LIKE '%/curriculum-bank-images/MCF3M/%'
            """
        ).fetchall()
    )


def _placement_rows(school: Any) -> list[Any]:
    """Playlist cards that cite an MCF3M figure, joined to their source question.

    Args:
        school: Open school database. The caller holds ``school._lock``.
    """
    if not _table_exists(school, "class_live_playlist_placements"):
        return []
    return list(
        school.conn.execute(
            """
            SELECT p.id, p.item_json AS blob,
                   'class_live_playlist_placements' AS table_name,
                   l.ontario_code AS course, q.import_key AS import_key,
                   b.import_key AS bank_key,
                   COALESCE(
                       json_extract(p.item_json, '$.smart_id'),
                       json_extract(q.payload_json, '$.smart_id')
                   ) AS smart_id,
                   p.source_question_id AS question_id
            FROM class_live_playlist_placements p
            LEFT JOIN questions q ON q.id = p.source_question_id
            LEFT JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN content_libraries l ON l.id = b.library_id
            WHERE p.item_json LIKE '%/curriculum-bank-images/MCF3M/%'
            """
        ).fetchall()
    )


def _prompt_rows(school: Any) -> list[Any]:
    """Published prompts that cite an MCF3M figure, joined to their source question.

    Args:
        school: Open school database. The caller holds ``school._lock``.
    """
    if not _table_exists(school, "live_session_prompts"):
        return []
    return list(
        school.conn.execute(
            """
            SELECT p.id, p.payload AS blob, 'live_session_prompts' AS table_name,
                   l.ontario_code AS course, q.import_key AS import_key,
                   b.import_key AS bank_key,
                   COALESCE(
                       json_extract(p.payload, '$.smart_id'),
                       json_extract(q.payload_json, '$.smart_id')
                   ) AS smart_id,
                   CAST(json_extract(p.payload, '$.source_question_id') AS INTEGER)
                       AS question_id
            FROM live_session_prompts p
            LEFT JOIN questions q
              ON q.id = CAST(json_extract(p.payload, '$.source_question_id') AS INTEGER)
            LEFT JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN content_libraries l ON l.id = b.library_id
            WHERE p.payload LIKE '%/curriculum-bank-images/MCF3M/%'
            """
        ).fetchall()
    )


def _catchup_rows(school: Any) -> list[Any]:
    """Catch-up copies that cite an MCF3M figure, joined to their source question.

    Args:
        school: Open school database. The caller holds ``school._lock``.
    """
    if not _table_exists(school, "participation_items"):
        return []
    return list(
        school.conn.execute(
            """
            SELECT p.id, p.item_json AS blob, 'participation_items' AS table_name,
                   l.ontario_code AS course, q.import_key AS import_key,
                   b.import_key AS bank_key,
                   COALESCE(
                       json_extract(p.item_json, '$.smart_id'),
                       json_extract(q.payload_json, '$.smart_id')
                   ) AS smart_id,
                   CAST(json_extract(p.item_json, '$.source_question_id') AS INTEGER)
                       AS question_id
            FROM participation_items p
            LEFT JOIN questions q
              ON q.id = CAST(json_extract(p.item_json, '$.source_question_id') AS INTEGER)
            LEFT JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN content_libraries l ON l.id = b.library_id
            WHERE p.item_json LIKE '%/curriculum-bank-images/MCF3M/%'
            """
        ).fetchall()
    )


def apply_inline_equation_repairs(school: Any) -> dict[str, int]:
    """Inline equation images on MCF3M bank rows and copied cards.

    Forward only: existing rows are updated in place when their stem still
    has a hole that matches ``image_urls``. Question rows, playlist
    placements, published prompts, and catch-up copies are included.
    Nothing is deleted. The next call updates zero rows.

    Args:
        school: Open ``SchoolDB``. Safe to call on every boot.

    Returns:
        Counts for ``questions``, ``placements``, ``prompts``, and ``catchup``.
    """
    with school._lock:
        questions = _apply_repairs(
            school, _question_rows(school), column="payload_json", touch_updated=False
        )
        placements = _apply_repairs(
            school, _placement_rows(school), column="item_json", touch_updated=False
        )
        prompts = _apply_repairs(
            school, _prompt_rows(school), column="payload", touch_updated=True
        )
        catchup = _apply_repairs(
            school, _catchup_rows(school), column="item_json", touch_updated=False
        )
        if questions or placements or prompts or catchup:
            school.conn.commit()
    if questions or placements or prompts or catchup:
        logger.info(
            "inline equation repair updated %s questions, %s placements, %s prompts, %s catch-up rows",
            questions,
            placements,
            prompts,
            catchup,
        )
    return {
        "questions": questions,
        "placements": placements,
        "prompts": prompts,
        "catchup": catchup,
    }
