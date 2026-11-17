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

import html
import json
import logging
import re
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
_FIGURE_WORDS = ("above", "below", "figure", "triangle", "table")
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
    step, or an empty population-table cell. Spots that already name the
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
    mentions_figure = any(word in str(stem_text or "").lower() for word in _FIGURE_WORDS)
    if mentions_figure and len(orphans) == slots + 1:
        return orphans[1:]
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


def _repair_json_column(
    school: Any,
    *,
    table: str,
    column: str,
) -> int:
    """Inline equation images in one JSON column. No deletes.

    A row is rewritten only when the stored text is still the text that
    was read and the stem HTML actually changes. A second run writes
    nothing.

    Args:
        school: Open school database. The caller holds ``school._lock``.
        table: ``questions`` or ``class_live_playlist_placements``.
        column: ``payload_json`` or ``item_json``.

    Returns:
        How many rows were updated.
    """
    if table not in {
        "questions",
        "class_live_playlist_placements",
        "live_session_prompts",
        "participation_items",
    }:
        return 0
    if column not in {"payload_json", "item_json", "payload"}:
        return 0
    if not _table_exists(school, table):
        return 0
    rows = school.conn.execute(
        f"""
        SELECT id, {column} AS blob
        FROM {table}
        WHERE {column} LIKE '%/curriculum-bank-images/MCF3M/%'
        """
    ).fetchall()
    updated = 0
    for row in rows:
        raw = str(row["blob"] or "")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        restored = restore_payload_figures(payload)
        if restored is None:
            continue
        encoded = json.dumps(restored)
        if encoded == raw:
            continue
        cursor = school.conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ? AND {column} = ?",
            (encoded, int(row["id"]), raw),
        )
        if int(cursor.rowcount or 0) > 0:
            updated += 1
    return updated


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
        questions = _repair_json_column(
            school, table="questions", column="payload_json"
        )
        placements = _repair_json_column(
            school,
            table="class_live_playlist_placements",
            column="item_json",
        )
        prompts = _repair_json_column(
            school, table="live_session_prompts", column="payload"
        )
        catchup = _repair_json_column(
            school, table="participation_items", column="item_json"
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
