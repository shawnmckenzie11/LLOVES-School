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
_MATH_TOKEN_RE = re.compile(
    r"\$\$(.+?)\$\$"
    r"|\\\[(.+?)\\\]"
    r"|\\\((.+?)\\\)"
    r"|(?<!\\)\$(?!\$)((?:\\.|[^$])+?)(?<!\\)\$(?!\$)"
    r"|(\\frac\{[^{}]+\}\{[^{}]+\}"
    r"|\\dfrac\{[^{}]+\}\{[^{}]+\}"
    r"|\\tfrac\{[^{}]+\}\{[^{}]+\}"
    r"|\\sqrt(?:\[[^\[\]]+\])?\{[^{}]+\})",
    re.DOTALL,
)
_MATH_SPAN_RE = re.compile(
    r"<span\b[^>]*\bdata-latex\s*=\s*[\"']([^\"']*)[\"'][^>]*>.*?</span>",
    re.IGNORECASE | re.DOTALL,
)
_ESCAPED_HTML_RE = re.compile(r"<(?:[a-zA-Z/!])")
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
        self._math_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Emit whitelisted opening tags with safe attributes."""
        name = tag.lower()
        if name not in _MC_ALLOWED_TAGS:
            return
        clean_attrs: list[tuple[str, str]] = []
        class_value = ""
        for key, value in attrs:
            attr = str(key or "").lower()
            if attr not in _MC_ALLOWED_ATTRS:
                continue
            clean_attrs.append((attr, html.escape(str(value or ""), quote=True)))
            if attr == "class":
                class_value = str(value or "")
        if name == "span" and "math-latex" in class_value:
            self._math_depth += 1
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
        if name == "span" and self._math_depth:
            self._math_depth -= 1

    def handle_data(self, data: str) -> None:
        """Escape text nodes and wrap TeX / caret math for KaTeX."""
        text = str(data or "")
        if not text:
            return
        if self._math_depth:
            self._parts.append(html.escape(text))
            return
        self._parts.append(format_math_html(text))

    def sanitized_html(self) -> str:
        """Return the rebuilt HTML fragment."""
        return "".join(self._parts).strip()


def html_to_plain(text: str) -> str:
    """Strip HTML to plain text, preserving caret exponents and TeX spans."""
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(r"<br\s*/?>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</p\s*>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</t[dh]\s*>", " ", cleaned, flags=re.IGNORECASE)

    def _span_to_dollar(match: re.Match[str]) -> str:
        latex = html.unescape(match.group(1)).strip()
        return f"${latex}$" if latex else ""

    cleaned = _MATH_SPAN_RE.sub(_span_to_dollar, cleaned)

    def _sup_to_caret(match: re.Match[str]) -> str:
        inner = re.sub(r"<[^>]+>", "", match.group(1))
        inner = html.unescape(inner).strip()
        return f"^{inner}" if inner else ""

    cleaned = _SUP_RE.sub(_sup_to_caret, cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def maybe_unescape_escaped_html(text: str) -> str:
    """Decode a double-escaped HTML fragment so tags can be sanitized.

    Args:
        text: Ingest HTML or an ``&lt;p&gt;...`` string.

    Returns:
        Original text when real tags are present; otherwise one unescape pass.
    """
    raw = str(text or "")
    if "&lt;" not in raw.lower() and "&#60;" not in raw:
        return raw
    if _ESCAPED_HTML_RE.search(raw):
        return raw
    return html.unescape(raw)


_DOUBLE_TEX_CMD_RE = re.compile(
    r"\\\\(frac|dfrac|tfrac|sqrt|left|right|cdot|times|div|pm|"
    r"leq|geq|neq|approx|sin|cos|tan|ln|log|sum|int|infty|pi|"
    r"theta|alpha|beta|gamma)"
)
_DISPLAY_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
_INLINE_PAREN_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_ASCII_FRAC_RE = re.compile(r"(?<![A-Za-z\\])(\d+)\s*/\s*(\d+)(?![A-Za-z0-9])")


def collapse_double_tex(text: str) -> str:
    """Collapse one extra backslash on common TeX commands.

    Args:
        text: Stem, option, or inner TeX that may contain ``\\\\frac``.

    Returns:
        The same text with ``\\\\frac`` reduced to ``\\frac`` once.
    """
    return _DOUBLE_TEX_CMD_RE.sub(r"\\\1", str(text or ""))


def prefer_tex_fractions(latex: str) -> str:
    """Turn simple ASCII fractions into ``\\frac`` inside math.

    Args:
        latex: Inner TeX (already inside ``$...$`` or equivalent).
    """
    return _ASCII_FRAC_RE.sub(r"\\frac{\1}{\2}", str(latex or ""))


def normalize_house_tex(text: str) -> str:
    """Convert TeX delimiters to house style ``$...$`` / ``$$...$$``.

    Also collapses doubled TeX commands and prefers ``\\frac`` over ``1/2``
    inside math. Prose outside delimiters is unchanged.

    Args:
        text: Teacher or ingest text that may mix delimiter styles.
    """
    raw = collapse_double_tex(str(text or ""))
    raw = _DISPLAY_BRACKET_RE.sub(lambda match: f"$${match.group(1)}$$", raw)
    raw = _INLINE_PAREN_RE.sub(lambda match: f"${match.group(1)}$", raw)

    def _rewrite_display(match: re.Match[str]) -> str:
        return f"$${prefer_tex_fractions(match.group(1))}$$"

    def _rewrite_inline(match: re.Match[str]) -> str:
        return f"${prefer_tex_fractions(match.group(1))}$"

    raw = re.sub(r"\$\$(.+?)\$\$", _rewrite_display, raw, flags=re.DOTALL)
    raw = re.sub(
        r"(?<!\$)\$(?!\$)((?:\\.|[^$])+?)(?<!\$)\$(?!\$)",
        _rewrite_inline,
        raw,
        flags=re.DOTALL,
    )
    return raw


def math_span_html(latex: str, *, display: bool = False) -> str:
    """Return a KaTeX-ready span for one TeX fragment.

    Args:
        latex: Unescaped TeX source.
        display: When True, mark the span as display math.
    """
    cleaned = html.unescape(str(latex or "")).strip()
    cleaned = collapse_double_tex(cleaned)
    cleaned = prefer_tex_fractions(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    escaped = html.escape(cleaned, quote=True)
    visible = html.escape(cleaned)
    cls = "math-latex math-display" if display else "math-latex"
    return f'<span class="{cls}" data-latex="{escaped}">{visible}</span>'


def _latex_from_math_match(match: re.Match[str]) -> tuple[str, bool]:
    """Return ``(latex, is_display)`` from one :data:`_MATH_TOKEN_RE` match."""
    display = match.group(1) is not None or match.group(2) is not None
    for index in range(1, 6):
        if match.group(index):
            return str(match.group(index)), display
    return "", display


def _format_non_math_text(text: str) -> str:
    """Escape prose and turn caret exponents into ``<sup>``."""
    if not text:
        return ""
    escaped = html.escape(str(text))
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _PAREN_EXP_RE.sub(r"(\1)<sup>\2</sup>", escaped)
    escaped = _SIMPLE_EXP_RE.sub(r"\1<sup>\2</sup>", escaped)
    return escaped


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

    text = maybe_unescape_escaped_html(str(raw_html or ""))
    text = rewrite_remote_images_in_html(
        text,
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
    """Escape prose, wrap TeX, and render caret exponents as HTML.

    House style is ``$...$`` / ``$$...$$``. ``\\( \\)`` / ``\\[ \\]`` convert
    first; doubled TeX backslashes collapse once. Dollar / bare ``\\frac`` /
    ``\\sqrt`` become ``math-latex`` spans. Remaining ``x^2`` carets become
    ``<sup>``. HTML entities are unescaped first so ``&lt;`` does not show
    as garbage.
    """
    if not text:
        return ""
    raw = normalize_house_tex(html.unescape(str(text)))
    parts: list[str] = []
    last = 0
    for match in _MATH_TOKEN_RE.finditer(raw):
        parts.append(_format_non_math_text(raw[last : match.start()]))
        latex, display = _latex_from_math_match(match)
        span = math_span_html(latex, display=display)
        parts.append(span or _format_non_math_text(match.group(0)))
        last = match.end()
    parts.append(_format_non_math_text(raw[last:]))
    return "".join(parts)


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
    text = normalize_house_tex(str(live_mc.get("text") or ""))
    live_mc["text"] = text
    options = [normalize_house_tex(str(option)) for option in (live_mc.get("options") or [])]
    live_mc["options"] = options
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
