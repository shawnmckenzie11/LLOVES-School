"""Format bank and live-class question text with superscripts and graph images."""

from __future__ import annotations

import html
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

_IMG_SRC_RE = re.compile(
    r"""<img[^>]+src\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_SUP_RE = re.compile(r"<sup[^>]*>(.*?)</sup>", re.IGNORECASE | re.DOTALL)
_PAREN_EXP_RE = re.compile(r"\(([^)]+)\)\^(\d+)")
_SIMPLE_EXP_RE = re.compile(r"([a-zA-Z])\^(\d+)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_FILEBASE_RE = re.compile(r"\$IMS-CC-FILEBASE\$", re.IGNORECASE)
_WEB_RES_RE = re.compile(
    r"""^(?:\.\./)+web_resources/""",
    re.IGNORECASE,
)
_GRAPH_KEYWORDS = (
    "graph",
    "sketch",
    "diagram",
    "parabola",
    "plot",
    "graphical",
)
_GRAPH_IMAGE_BY_ITEM: dict[str, str] = {
    "mcf3m-m4-openstax-ia2e-ex960-diagram": "/static/bank-graphs/mcf3m-m4-cts-area-diagram.svg",
    "mcf3m-m4-openstax-ia2e-try9120-sketch": "/static/bank-graphs/parabola-grid.svg",
    "mcf3m-m1-owned-sketch-steps-mc": "/static/bank-graphs/parabola-transformed.svg",
    "mcf3m-m1-ontario-a21-sample": "/static/bank-graphs/parabola-grid.svg",
}
_MC_ALLOWED_TAGS = frozenset(
    {
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "img",
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "sup",
        "sub",
        "span",
        "div",
        "ul",
        "ol",
        "li",
    }
)
_MC_ALLOWED_ATTRS = frozenset({"src", "alt", "class", "colspan", "rowspan", "width", "height", "data-latex"})


class _MCSanitizer(HTMLParser):
    """Rebuild ingest HTML with a safe subset for live MC display."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Emit whitelisted opening tags with safe attributes."""
        name = tag.lower()
        if name not in _MC_ALLOWED_TAGS:
            return
        clean_attrs: list[tuple[str, str]] = []
        for key, value in attrs:
            attr = str(key or "").lower()
            if attr not in _MC_ALLOWED_ATTRS:
                continue
            clean_attrs.append((attr, html.escape(str(value or ""), quote=True)))
        if clean_attrs:
            attrs_text = " ".join(f'{key}="{val}"' for key, val in clean_attrs)
            self._parts.append(f"<{name} {attrs_text}>")
        else:
            self._parts.append(f"<{name}>")

    def handle_endtag(self, tag: str) -> None:
        """Emit whitelisted closing tags."""
        name = tag.lower()
        if name in _MC_ALLOWED_TAGS and name not in {"br", "img"}:
            self._parts.append(f"</{name}>")

    def handle_data(self, data: str) -> None:
        """Escape text nodes while preserving math caret formatting."""
        text = str(data or "")
        if not text:
            return
        escaped = html.escape(text)
        escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
        escaped = _PAREN_EXP_RE.sub(r"(\1)<sup>\2</sup>", escaped)
        escaped = _SIMPLE_EXP_RE.sub(r"\1<sup>\2</sup>", escaped)
        self._parts.append(escaped)

    def sanitized_html(self) -> str:
        """Return the rebuilt HTML fragment."""
        return "".join(self._parts).strip()


def html_to_plain(text: str) -> str:
    """Strip HTML to plain text, preserving caret exponents from ``<sup>`` tags."""
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(r"<br\s*/?>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</p\s*>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</t[dh]\s*>", " ", cleaned, flags=re.IGNORECASE)

    def _sup_to_caret(match: re.Match[str]) -> str:
        inner = re.sub(r"<[^>]+>", "", match.group(1))
        inner = html.unescape(inner).strip()
        return f"^{inner}" if inner else ""

    cleaned = _SUP_RE.sub(_sup_to_caret, cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_image_src(raw_html: str) -> str:
    """Return the first ``<img src>`` from ingest HTML, if any."""
    match = _IMG_SRC_RE.search(str(raw_html or ""))
    return str(match.group(1)).strip() if match else ""


def extract_all_image_srcs(raw_html: str) -> list[str]:
    """Return every ``<img src>`` found in one HTML fragment."""
    return [
        str(match.group(1)).strip()
        for match in _IMG_SRC_RE.finditer(str(raw_html or ""))
        if str(match.group(1)).strip()
    ]


def resolve_bank_image_url(image_src: str, *, class_id: int | None = None) -> str:
    """Resolve ingest or static graph paths to a browser-ready URL."""
    src = str(image_src or "").strip()
    if not src:
        return ""
    if src.startswith("/static/") or src.startswith("http://") or src.startswith("https://"):
        return src
    if class_id is not None:
        root = f"/staff/class/{int(class_id)}/module-files/web_resources"
        if src.startswith("web_resources/"):
            resolved = f"/staff/class/{int(class_id)}/module-files/{urllib.parse.unquote(src)}"
        else:
            resolved = _FILEBASE_RE.sub(root, src)
            resolved = _WEB_RES_RE.sub(f"{root}/", resolved)
        if "/web_resources/" in resolved:
            prefix, _, tail = resolved.partition("/web_resources/")
            decoded_tail = urllib.parse.unquote(tail)
            resolved = f"{prefix}/web_resources/{decoded_tail}"
        if resolved.startswith("/"):
            return resolved
    return src


def _resolve_ingest_html(raw_html: str, *, class_id: int | None) -> str:
    """Rewrite IMSCC file tokens inside one HTML fragment."""

    def _replace_src(match: re.Match[str]) -> str:
        quote = match.group(1)
        src = match.group(2)
        resolved = resolve_bank_image_url(src, class_id=class_id)
        return f"src={quote}{resolved}{quote}"

    text = str(raw_html or "")
    return re.sub(
        r"""src\s*=\s*(["'])(.*?)\1""",
        _replace_src,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def format_mc_html_fragment(
    raw_html: str,
    *,
    class_id: int | None = None,
    school: Any | None = None,
    library_id: int | None = None,
) -> str:
    """Sanitize ingest HTML while preserving tables and graph images."""
    try:
        from bank_image_mirror import rewrite_remote_images_in_html
    except ImportError:
        from lms.bank_image_mirror import rewrite_remote_images_in_html

    text = rewrite_remote_images_in_html(
        str(raw_html or ""),
        school=school,
        library_id=library_id,
        class_id=class_id,
    )
    text = _resolve_ingest_html(text, class_id=class_id)
    if not text.strip():
        return ""
    parser = _MCSanitizer()
    parser.feed(text)
    parser.close()
    rendered = parser.sanitized_html()
    if rendered:
        return rendered
    plain = html_to_plain(text)
    return format_math_html(plain) if plain else ""


def format_math_html(text: str) -> str:
    """Escape plain text and render ``**bold**`` plus caret exponents as HTML."""
    if not text:
        return ""
    escaped = html.escape(str(text))
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _PAREN_EXP_RE.sub(r"(\1)<sup>\2</sup>", escaped)
    escaped = _SIMPLE_EXP_RE.sub(r"\1<sup>\2</sup>", escaped)
    return escaped


def graph_image_for_builder_item(
    item_id: str,
    stem: str,
    *,
    process_tags: list[str] | None = None,
) -> str:
    """Return a static graph asset path for a content-builder bank item."""
    token = str(item_id or "").strip()
    if token in _GRAPH_IMAGE_BY_ITEM:
        return _GRAPH_IMAGE_BY_ITEM[token]
    haystack = " ".join(
        [str(stem or ""), " ".join(str(tag) for tag in (process_tags or []))]
    ).lower()
    if any(keyword in haystack for keyword in _GRAPH_KEYWORDS):
        return "/static/bank-graphs/parabola-grid.svg"
    return ""


def _stem_image_is_hero(
    raw_stem_html: str,
    plain_stem: str,
    text_html: str,
    hero_src: str,
) -> bool:
    """Return True when a separate hero thumbnail should accompany the stem HTML."""
    if not str(hero_src or "").strip():
        return False
    if plain_stem.strip() in {"Refer to the graph.", "Refer to the table."}:
        return True
    if str(hero_src) in str(text_html or ""):
        return False
    lowered = str(raw_stem_html or "").lower()
    if "equation_image" in lowered or "equatio-api.texthelp.com" in lowered:
        return False
    if text_html and "<img" in text_html and extract_image_src(raw_stem_html):
        return False
    return True


def enrich_live_mc_display(
    live_mc: dict[str, Any],
    *,
    class_id: int | None = None,
    school: Any | None = None,
    library_id: int | None = None,
) -> dict[str, Any]:
    """Attach ``text_html``, ``options_html``, and resolved image metadata."""
    text = str(live_mc.get("text") or "")
    options = live_mc.get("options") or []
    rich_stem = str(live_mc.pop("_rich_stem_html", "") or "")
    rich_options = live_mc.pop("_rich_option_htmls", None) or []
    render_kwargs = {
        "class_id": class_id,
        "school": school,
        "library_id": library_id,
    }

    stem_html = format_mc_html_fragment(rich_stem, **render_kwargs) if rich_stem else ""
    live_mc["text_html"] = stem_html or format_math_html(text)

    option_htmls: list[str] = []
    option_image_urls: list[str] = []
    for index, option in enumerate(options):
        raw_html = str(rich_options[index] or "") if index < len(rich_options) else ""
        rendered = format_mc_html_fragment(raw_html, **render_kwargs) if raw_html else ""
        option_htmls.append(rendered or format_math_html(str(option)))
        src = extract_image_src(rendered or raw_html)
        option_image_urls.append(resolve_bank_image_url(src, class_id=class_id) if src else "")

    live_mc["options_html"] = option_htmls
    if any(option_image_urls):
        live_mc["options_image_urls"] = option_image_urls

    hero_src = str(live_mc.get("image_src") or live_mc.get("image_url") or "").strip()
    if hero_src:
        hero_src = resolve_bank_image_url(hero_src, class_id=class_id)
    if hero_src and _stem_image_is_hero(rich_stem, text, live_mc["text_html"], hero_src):
        live_mc["image_url"] = hero_src
    else:
        live_mc.pop("image_url", None)
        builder_id = str(live_mc.get("builder_item_id") or "")
        if builder_id in _GRAPH_IMAGE_BY_ITEM:
            fallback = graph_image_for_builder_item(builder_id, text)
            if fallback:
                live_mc["image_url"] = resolve_bank_image_url(fallback, class_id=class_id)
    return live_mc
