"""Format bank and live-class question text with superscripts and graph images."""

from __future__ import annotations

import hashlib
import html
import json
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
_SNAPSHOT_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SNAPSHOT_HOST_RE = re.compile(
    r"i\.gyazo\.com|gyazo\.com|equatio-api\.texthelp\.com",
    re.IGNORECASE,
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


def _math_key(text: str) -> str:
    """Normalize math text so a glued answer can be compared to an option.

    Args:
        text: Plain text, TeX, or a small HTML fragment.
    """
    raw = html_to_plain(str(text or ""))
    raw = raw.replace("$", "")
    return re.sub(r"\s+", "", raw).lower()


def _answer_is_formula(text: str) -> bool:
    """True when an option looks like an equation rather than a word label.

    Args:
        text: Correct-option text.
    """
    raw = str(text or "")
    return any(token in raw for token in ("=", "^", "\\", "frac", "sqrt", "≤", "≥"))


def _correct_option_text(question: dict[str, Any]) -> str:
    """Return the correct option string for one MC payload, if it has one.

    Args:
        question: Catalogue or prompt payload with options and a key.
    """
    options = list(question.get("options") or question.get("choices") or [])
    key = str(question.get("correct_answer") or question.get("key") or "").strip()
    if not key:
        return ""
    if len(key) == 1 and key.upper() in "ABCDEFGH":
        index = ord(key.upper()) - ord("A")
        if 0 <= index < len(options):
            return str(options[index] or "")
    return key


def _is_formula_appendix(prefix: str) -> bool:
    """True when the text before a formula already finished the question.

    House inline TeX such as ``Find $\\frac{1}{2}$`` is the question, not a
    relic. A formula pasted after ``?``, ``.``, or ``!`` is an answer appendix.

    Args:
        prefix: Stem text that precedes the candidate formula.
    """
    head = re.sub(r"[\s$]+$", "", str(prefix or ""))
    return bool(head) and head[-1] in "?.!"


def _prefix_already_has_formula(prefix: str, correct_key: str) -> bool:
    """True when this formula was already stated earlier in the stem.

    Args:
        prefix: Stem text before the candidate copy.
        correct_key: Normalized correct-option text.
    """
    if not correct_key:
        return False
    return correct_key in _math_key(prefix)


def _strip_trailing_formula(text: str, correct: str) -> str:
    """Drop a trailing answer formula pasted after a finished question.

    A single house-style ``$...$`` token that is the question itself stays.
    The copy is removed only when question words remain and the formula sits
    after sentence punctuation or repeats math already in the stem.

    Args:
        text: Stem plain text or HTML.
        correct: Correct option text.
    """
    raw = str(text or "")
    pieces = [re.escape(ch) for ch in re.sub(r"\s+", "", str(correct or "").replace("$", ""))]
    if len(pieces) < 3 or not raw.strip():
        return raw
    pattern = r"(?:\s|\$)*" + r"(?:\s|\$)*".join(pieces) + r"\s*$"
    updated = re.sub(pattern, "", raw, count=1, flags=re.IGNORECASE)
    if updated == raw or not updated.strip():
        return raw
    removed = raw[len(updated):]
    if "<" in removed:
        return raw
    correct_key = _math_key(correct)
    if not (
        _is_formula_appendix(updated)
        or _prefix_already_has_formula(updated, correct_key)
    ):
        return raw
    return updated.strip()


def _drop_snapshot_imgs(fragment: str) -> str:
    """Remove Gyazo and EquatIO snapshot images from a stem fragment.

    Args:
        fragment: Stem HTML.
    """

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if _SNAPSHOT_HOST_RE.search(tag):
            return ""
        return tag

    return _SNAPSHOT_IMG_RE.sub(replace, str(fragment or ""))


def _drop_matching_math_spans(fragment: str, correct_key: str) -> str:
    """Remove an answer-formula span pasted after a finished question.

    House inline TeX stays when it is the question (``Find $\\frac{1}{2}$``).
    A span is removed only when its TeX matches the correct option and the
    text before it already ended the question or already contained that formula.

    Args:
        fragment: Stem HTML.
        correct_key: Normalized correct-option text.
    """
    source = str(fragment or "")

    def replace(match: re.Match[str]) -> str:
        latex = html.unescape(match.group(1) or "")
        if not correct_key or _math_key(latex) != correct_key:
            return match.group(0)
        prefix = html_to_plain(source[: match.start()])
        if _is_formula_appendix(prefix) or _prefix_already_has_formula(
            prefix, correct_key
        ):
            return ""
        return match.group(0)

    return _MATH_SPAN_RE.sub(replace, source)


def strip_glued_stem_relics(
    stem_html: str,
    stem_plain: str,
    *,
    correct_text: str = "",
) -> tuple[str, str]:
    """Remove answer snapshots and formula appendices glued onto a stem.

    Gyazo and EquatIO snapshots are dropped. A trailing formula or math span
    is dropped only when it repeats the correct option after a finished
    question. House-style inline TeX (``$\\frac{1}{2}$``) stays when it is
    the question itself. Graphs, tables, and other in-stem math stay. The
    function is idempotent.

    Args:
        stem_html: Rendered stem HTML. May be empty.
        stem_plain: Plain stem text.
        correct_text: Correct option text used to detect a glued answer.

    Returns:
        ``(html, plain)`` with relics removed.
    """
    html_out = str(stem_html or "")
    plain_out = str(stem_plain or "")
    correct_key = (
        _math_key(correct_text) if _answer_is_formula(correct_text) else ""
    )
    if html_out:
        stripped = _drop_snapshot_imgs(html_out)
        if html_to_plain(stripped).strip():
            html_out = stripped
        if correct_key:
            stripped = _drop_matching_math_spans(html_out, correct_key)
            if html_to_plain(stripped).strip():
                html_out = stripped
        plain_from_html = html_to_plain(html_out)
        if plain_from_html.strip():
            plain_out = plain_from_html
    if correct_key:
        trimmed = _strip_trailing_formula(plain_out, correct_text)
        if trimmed.strip() and trimmed != plain_out:
            plain_out = trimmed
        if html_out:
            html_out = _strip_trailing_formula(html_out, correct_text)
    return html_out, plain_out


def clean_question_stem_fields(question: dict[str, Any]) -> dict[str, Any]:
    """Strip glued answer snapshots and formulas from one question stem.

    ``equation_latex`` is left in place so a staff equation under the stem
    still renders separately from the question text.

    Args:
        question: Catalogue or prompt payload. Mutated and returned.

    Returns:
        The same dict with stem fields cleaned.
    """
    if not isinstance(question, dict):
        return {}
    correct = _correct_option_text(question)
    text_html = str(question.get("text_html") or "")
    if text_html:
        cleaned_html, cleaned_plain = strip_glued_stem_relics(
            text_html,
            str(question.get("text") or question.get("prompt") or ""),
            correct_text=correct,
        )
        if cleaned_html != text_html:
            question["text_html"] = cleaned_html
            if cleaned_plain and question.get("text"):
                question["text"] = cleaned_plain
    for key in ("text", "prompt", "stem", "question"):
        current = question.get(key)
        if not isinstance(current, str) or not current.strip():
            continue
        _html, updated = strip_glued_stem_relics("", current, correct_text=correct)
        if updated.strip():
            question[key] = updated
    scrub_choice_images_from_stem(question)
    return question


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


def bank_image_identity(src: str) -> str:
    """Return a stable key for one bank image URL.

    The key is the full normalised path, not the file name. Canvas
    ``/files/10/preview`` and ``/files/11/preview`` stay distinct, and so do
    ``U1/image.png`` and ``U2/image.png``. A ``data:`` URL is the hash of
    the whole value, so two payloads that share a tail do not collide.
    A ``web_resources/`` prefix is dropped so a staff module-file URL and
    the cartridge-relative path of the same file compare equal.

    The query string stays in the key. Wiris and equation URLs that differ
    only by query are different images. A hero and a choice that differ
    only by a cache-buster query are therefore not treated as the same
    image. A mirrored file and the remote URL it was copied from compare
    equal only when the caller passes an alias map built from
    ``bank_image_cache``. Display scrub does not read that cache. The
    repair script does, once, while planning.

    Args:
        src: An ``<img src>``, ``image_url``, or filebase token.
    """
    raw = html.unescape(str(src or "")).strip()
    if not raw:
        return ""
    if raw.lower().startswith("data:"):
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
        return f"data:{digest}"
    raw = urllib.parse.unquote(raw)
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme in {"http", "https"}:
        path = parsed.path or ""
        query = parsed.query
        host = parsed.netloc.lower()
    else:
        path, _, query = raw.partition("?")
        host = ""
    path = path.replace("\\", "/")
    marker = "web_resources/"
    lowered = path.lower()
    index = lowered.rfind(marker)
    if index >= 0:
        path = path[index + len(marker) :]
        host = ""
    path = re.sub(r"/+", "/", path).rstrip("/").lower()
    if host:
        tail = path if path.startswith("/") else f"/{path}"
        ident = f"{host}{tail}"
    else:
        ident = path
    if not ident and not query:
        return ""
    if query:
        return f"{ident}?{query}"
    return ident


def _canonical_image_identity(src: str, aliases: dict[str, str] | None) -> str:
    """Map one image URL to its alias group, or its own identity.

    Args:
        src: Image URL or path.
        aliases: Optional identity → canonical identity map from the mirror cache.
    """
    ident = bank_image_identity(src)
    if not ident:
        return ""
    if not aliases:
        return ident
    return aliases.get(ident, ident)


def choice_image_identities(
    chunks: list[str],
    *,
    aliases: dict[str, str] | None = None,
) -> set[str]:
    """Return canonical identities of every image in choice HTML.

    Args:
        chunks: Choice HTML fragments or ``options_image_urls`` values wrapped
            as tags by the caller.
        aliases: Optional mirror-cache identity map.
    """
    found: set[str] = set()
    for chunk in chunks:
        for src in extract_all_image_srcs(chunk):
            ident = _canonical_image_identity(src, aliases)
            if ident:
                found.add(ident)
    return found


def _choice_html_chunks(question: dict[str, Any]) -> list[str]:
    """Return HTML fragments that hold multiple-choice option images.

    Args:
        question: Bank payload, normalized live MC, or prompt card.
    """
    chunks: list[str] = []
    for key in ("_rich_option_htmls", "options_html"):
        value = question.get(key)
        if isinstance(value, list):
            chunks.extend(str(item or "") for item in value)
    urls = question.get("options_image_urls")
    if isinstance(urls, list):
        for url in urls:
            token = str(url or "").strip()
            if token:
                chunks.append(f'<img src="{html.escape(token, quote=True)}">')
    for key in ("choices", "options"):
        value = question.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                chunks.append(str(item.get("html") or item.get("text") or ""))
            else:
                chunks.append(str(item or ""))
    return chunks


def scrub_choice_images_from_stem(
    question: dict[str, Any],
    *,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Drop a hero that was copied from an MC choice.

    The display bug promoted the first choice image onto ``image_url`` /
    ``image_src`` when the stem had no image of its own. That hero is
    removed. An ``<img>`` already inside ``stem_html`` or ``text_html`` was
    authored there. Canvas sometimes repeats that same URL on every choice.
    Those tags stay, and a hero that is that same authored image stays too.
    Choice HTML is not modified. The function is idempotent.

    A mirrored hero matches a remote choice only when ``aliases`` maps them
    to one identity. Display callers omit the map. The repair script passes
    the mirror cache.

    Args:
        question: Bank, live, playlist, or prompt payload. Mutated in place.
        aliases: Optional mirror-cache map from :func:`bank_image_identity`
            of a remote URL to the same key for its mirrored file.

    Returns:
        Hero field names that changed (``image_url``, ``image_src``).
    """
    if not isinstance(question, dict):
        return []
    banned = choice_image_identities(_choice_html_chunks(question), aliases=aliases)
    if not banned:
        return []
    stem_idents: set[str] = set()
    for field in ("stem_html", "text_html"):
        stem_idents |= choice_image_identities(
            [str(question.get(field) or "")],
            aliases=aliases,
        )
    changed: list[str] = []
    for field in ("image_url", "image_src"):
        src = str(question.get(field) or "").strip()
        if not src:
            continue
        ident = _canonical_image_identity(src, aliases)
        if ident and ident in banned and ident not in stem_idents:
            question.pop(field, None)
            changed.append(field)
    return changed


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


_MODULE_FILE_PREFIX_RE = re.compile(
    r"/(?:staff/class|api/classes)/(\d+)/module-files/"
)
_MODULE_FILE_REF_RE = re.compile(
    r"/(staff/class|api/classes)/(\d+)/module-files/([^\"'\s<>?#)]+)"
)


def safe_module_file_relpath(relpath: str) -> str | None:
    """Return a cartridge-relative module-file path, or None when it is unsafe.

    Args:
        relpath: Text after ``module-files/`` in a stored URL.
    """
    raw = urllib.parse.unquote(str(relpath or ""))
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = raw.replace("\\", "/").lstrip("/")
    if not raw or "\x00" in raw:
        return None
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def module_file_lookup_relpaths(relpath: str) -> list[str]:
    """Return library-file keys to try for one module-file URL path.

    Args:
        relpath: Decoded path after ``module-files/``.
    """
    safe = safe_module_file_relpath(relpath)
    if safe is None:
        return []
    found = [safe]
    if not safe.startswith("web_resources/"):
        found.append(f"web_resources/{safe}")
    return found


def student_visible_bank_image_url(
    url: str, *, class_id: int | None = None
) -> str:
    """Rewrite module-file URLs to the student route for one class.

    Bank imports resolve graphs to ``/staff/class/{id}/module-files/...``.
    Students cannot GET that staff route. The twin
    ``/api/classes/{id}/module-files/...`` serves the same library blobs
    to live-session students. When ``class_id`` is set, a URL that still
    names another class is rebased onto this class. The file is then
    read from this class's library. The other class's route stays closed.

    Args:
        url: Image URL or HTML that may contain one.
        class_id: Class the student is actually in. Omit to keep the id
            already in the URL.
    """

    def repl(match: re.Match[str]) -> str:
        """Point one module-file prefix at the student route."""
        current = match.group(1)
        target = str(int(class_id)) if class_id is not None else current
        return f"/api/classes/{target}/module-files/"

    return _MODULE_FILE_PREFIX_RE.sub(repl, str(url or ""))


def rebase_module_file_text(
    text: str, dest_class_id: int
) -> tuple[str, list[tuple[int, str]]]:
    """Rewrite module-file URLs onto ``dest_class_id`` and list the old ones.

    Staff and student spellings both change class id. The path after
    ``module-files/`` is kept. A path with ``..`` is still retargeted so
    the old class is not requested, and it is left out of the copy list.

    Args:
        text: Stored HTML or a bare URL.
        dest_class_id: Class that will own the copied deck.

    Returns:
        The rewritten text, and ``(source_class_id, relpath)`` pairs whose
        class id was not already the destination.
    """
    refs: list[tuple[int, str]] = []
    dest = int(dest_class_id)

    def repl(match: re.Match[str]) -> str:
        """Retarget one module-file URL and remember a safe source path."""
        kind, raw_id, rel = match.group(1), match.group(2), match.group(3)
        try:
            source_id = int(raw_id)
        except ValueError:
            return match.group(0)
        safe = safe_module_file_relpath(rel)
        if source_id != dest and safe is not None:
            refs.append((source_id, safe))
        if source_id == dest:
            return match.group(0)
        return f"/{kind}/{dest}/module-files/{rel}"

    return _MODULE_FILE_REF_RE.sub(repl, str(text or "")), refs


def rebase_module_file_tree(
    value: Any, dest_class_id: int
) -> tuple[Any, list[tuple[int, str]]]:
    """Rebase module-file URLs anywhere in a JSON-like value.

    Args:
        value: String, list, dict, or a scalar left unchanged.
        dest_class_id: Class that will own the copied deck.

    Returns:
        A copy of ``value`` with URLs retargeted, and the source refs.
    """
    refs: list[tuple[int, str]] = []

    def walk(node: Any) -> Any:
        """Copy one node, collecting refs from its strings."""
        if isinstance(node, str):
            rewritten, found = rebase_module_file_text(node, dest_class_id)
            refs.extend(found)
            return rewritten
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        return node

    return walk(value), refs


def rebase_stored_module_file_text(
    text: str, dest_class_id: int
) -> tuple[str, list[tuple[int, str]]]:
    """Rebase a stored column, parsing JSON when the text is an object or array.

    A column that is not JSON, or whose URLs already name ``dest_class_id``,
    is returned unchanged aside from any plain-text URL rewrite. JSON is
    dumped only when a class id actually changes, so a second pass writes
    nothing.

    Args:
        text: Column text from a playlist, seed, or live item.
        dest_class_id: Class that owns the row.

    Returns:
        Replacement text and the source refs that need a library pointer.
    """
    raw = str(text or "")
    stripped = raw.strip()
    if stripped[:1] in "{[":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            rewritten, refs = rebase_module_file_tree(parsed, dest_class_id)
            if not refs:
                return raw, []
            return json.dumps(rewritten), refs
    return rebase_module_file_text(raw, dest_class_id)


def rebase_copied_deck_images(
    questions: list[Any],
    pages: list[Any],
    media: dict[str, Any] | None,
    challenge: dict[str, Any] | None,
    *,
    dest_class_id: int,
) -> tuple[
    list[Any],
    list[Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[tuple[int, str]],
]:
    """Rebase module-file URLs in one deck snapshot onto the destination class.

    The source metadata object is not mutated. Refs name the class id that
    was in the URL before the rewrite, so the caller can copy that
    library pointer onto the destination library.

    Args:
        questions: Question dicts from the source deck.
        pages: Page dicts from the source deck.
        media: Media copy, or None.
        challenge: Team-challenge copy, or None.
        dest_class_id: Class receiving the copy.

    Returns:
        Rewritten questions, pages, media, challenge, and source refs.
    """
    refs: list[tuple[int, str]] = []
    next_questions, found = rebase_module_file_tree(questions, dest_class_id)
    refs.extend(found)
    next_pages, found = rebase_module_file_tree(pages, dest_class_id)
    refs.extend(found)
    next_media: dict[str, Any] | None = None
    if isinstance(media, dict):
        rewritten, found = rebase_module_file_tree(media, dest_class_id)
        next_media = rewritten if isinstance(rewritten, dict) else None
        refs.extend(found)
    next_challenge: dict[str, Any] | None = None
    if isinstance(challenge, dict):
        rewritten, found = rebase_module_file_tree(challenge, dest_class_id)
        next_challenge = rewritten if isinstance(rewritten, dict) else None
        refs.extend(found)
    return next_questions, next_pages, next_media, next_challenge, refs


def _rewrite_student_image_node(node: Any, *, class_id: int | None) -> Any:
    """Return a copy of one prompt node with module-file URLs made public.

    Args:
        node: String, list, dict, or scalar from a student payload.
        class_id: Class the student is viewing, when known.
    """
    if isinstance(node, str):
        return student_visible_bank_image_url(node, class_id=class_id)
    if isinstance(node, list):
        return [
            _rewrite_student_image_node(item, class_id=class_id) for item in node
        ]
    if isinstance(node, dict):
        return {
            key: _rewrite_student_image_node(value, class_id=class_id)
            for key, value in node.items()
        }
    return node


def rewrite_student_prompt_images(
    payload: Any, *, class_id: int | None = None
) -> dict[str, Any]:
    """Copy a prompt payload with module-file image URLs made public.

    Every string is checked, including stem HTML, option HTML, and nested
    items. When ``class_id`` is set, a URL left behind by a deck copy is
    rebased onto that class instead of the class id stored in the deck.

    Args:
        payload: Student-facing prompt JSON (already stripped of keys).
        class_id: Class the student is viewing.
    """
    if not isinstance(payload, dict):
        return {}
    rewritten = _rewrite_student_image_node(payload, class_id=class_id)
    return rewritten if isinstance(rewritten, dict) else {}


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
        try:
            from bank_image_mirror import is_remote_bank_image_url, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import (
                is_remote_bank_image_url,
                mirror_bank_image_url,
            )
        if (
            school is not None
            and library_id is not None
            and is_remote_bank_image_url(hero_src)
        ):
            relpath = mirror_bank_image_url(school, int(library_id), hero_src)
            if relpath:
                hero_src = relpath
        hero_src = resolve_bank_image_url(hero_src, class_id=class_id)
        live_mc["image_src"] = hero_src
    if hero_src and _stem_image_is_hero(rich_stem, text, live_mc["text_html"], hero_src):
        live_mc["image_url"] = hero_src
    else:
        live_mc.pop("image_url", None)
        builder_id = str(live_mc.get("builder_item_id") or "")
        if builder_id in _GRAPH_IMAGE_BY_ITEM:
            fallback = graph_image_for_builder_item(builder_id, text)
            if fallback:
                live_mc["image_url"] = resolve_bank_image_url(fallback, class_id=class_id)
    clean_question_stem_fields(live_mc)
    return live_mc
