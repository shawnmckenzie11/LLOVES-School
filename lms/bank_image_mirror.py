"""Mirror remote Canvas/Equatio bank images into LMS blob storage."""

from __future__ import annotations

import hashlib
import http.client
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from defusedxml.ElementTree import fromstring as _safe_xml_fromstring
from defusedxml.common import DefusedXmlException

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
_REFUSAL_NETWORK_TTL_S = 300.0
_REFUSAL_MAX = 2048
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_SVG_MAX_DEPTH = 64
_SVG_MAX_NODES = 100_000
SVG_SANDBOX_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
_FRAGMENT_RE = re.compile(r"^#[A-Za-z0-9_.:\-]*$")
_SCRIPT_TYPE_RE = re.compile(r"script|ecmascript|vbscript", re.IGNORECASE)
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
_SVG_ELEMENT_NAMES = (
    "svg", "g", "defs", "symbol", "use", "switch", "title", "desc", "metadata",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath", "a", "image", "style", "view",
    "clipPath", "mask", "marker", "pattern",
    "linearGradient", "radialGradient", "stop",
    "filter", "feBlend", "feColorMatrix", "feComponentTransfer",
    "feComposite", "feConvolveMatrix", "feDiffuseLighting",
    "feDisplacementMap", "feDistantLight", "feDropShadow", "feFlood",
    "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur",
    "feImage", "feMerge", "feMergeNode", "feMorphology", "feOffset",
    "fePointLight", "feSpecularLighting", "feSpotLight", "feTile",
    "feTurbulence",
)
_SVG_ELEMENTS = {name.lower(): name for name in _SVG_ELEMENT_NAMES}
_SVG_ATTR_NAMES = (
    "id", "class", "lang", "tabindex", "role", "aria-hidden", "aria-label",
    "x", "y", "x1", "y1", "x2", "y2", "dx", "dy", "width", "height",
    "rx", "ry", "r", "cx", "cy", "fx", "fy", "fr", "d", "points", "pathLength",
    "viewBox", "preserveAspectRatio", "transform", "transform-origin",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
    "stroke-dasharray", "stroke-dashoffset", "stroke-opacity",
    "opacity", "color", "display", "visibility", "overflow",
    "clip-path", "clip-rule", "mask", "filter",
    "marker-start", "marker-mid", "marker-end",
    "font-family", "font-size", "font-weight", "font-style", "font-variant",
    "font-stretch", "text-anchor", "text-decoration", "dominant-baseline",
    "alignment-baseline", "baseline-shift", "letter-spacing", "word-spacing",
    "direction", "writing-mode", "white-space", "unicode-bidi",
    "style", "type", "media", "offset", "stop-color", "stop-opacity",
    "gradientUnits", "gradientTransform", "spreadMethod",
    "patternUnits", "patternContentUnits", "patternTransform",
    "markerUnits", "markerWidth", "markerHeight", "orient", "refX", "refY",
    "clipPathUnits", "maskUnits", "maskContentUnits", "maskType",
    "filterUnits", "primitiveUnits", "result", "in", "in2", "stdDeviation",
    "mode", "values", "operator", "k1", "k2", "k3", "k4", "order",
    "kernelMatrix", "divisor", "bias", "targetX", "targetY", "edgeMode",
    "preserveAlpha", "surfaceScale", "diffuseConstant", "specularConstant",
    "specularExponent", "limitingConeAngle", "pointsAtX", "pointsAtY",
    "pointsAtZ", "azimuth", "elevation", "seed", "numOctaves",
    "baseFrequency", "stitchTiles", "xChannelSelector", "yChannelSelector",
    "tableValues", "intercept", "slope", "amplitude", "exponent",
    "color-interpolation", "color-interpolation-filters", "flood-color",
    "flood-opacity", "lighting-color", "href", "space", "version",
    "zoomAndPan", "textLength", "lengthAdjust", "startOffset", "method",
    "spacing", "side", "rotate", "vector-effect", "shape-rendering",
    "text-rendering", "image-rendering", "pointer-events", "cursor",
    "paint-order", "color-rendering",
)
_SVG_ATTRS = {name.lower(): name for name in _SVG_ATTR_NAMES}
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


_SCHEME_MARK_RE = re.compile(r":[ \t\r\n\f]*/[ \t\r\n\f]*/")


def _raw_authority(url: str) -> str:
    """Return the authority text before the path, query, or fragment.

    Equatio SVG URLs put LaTeX in the path, including a percent-encoded
    backslash. That path is not part of the host. Only the text between
    the scheme separator and the first ``/``, ``?``, or ``#`` is the
    authority. A tab or newline inside ``://`` still counts as the
    separator, so userinfo cannot skip this check.

    Args:
        url: Candidate image URL.

    Returns:
        The raw authority, or an empty string when the URL has none.
    """
    raw = str(url or "").strip()
    match = _SCHEME_MARK_RE.search(raw)
    if match is None:
        return ""
    rest = raw[match.end() :]
    cut = len(rest)
    for token in ("/", "?", "#"):
        index = rest.find(token)
        if 0 <= index < cut:
            cut = index
    return rest[:cut]


def _url_has_userinfo_or_backslash(url: str) -> bool:
    """Return True when the authority carries userinfo or a backslash.

    ``urlparse`` can report an allowlisted hostname while the raw
    authority still has userinfo or a backslash that another parser
    treats as part of the host. Those URLs are refused before any
    connection. The check looks at the authority both raw and
    percent-decoded. A backslash in the path or the query is LaTeX, not
    a host trick, and is left alone.

    Args:
        url: Candidate image URL, including a redirect target.
    """
    authority = _raw_authority(url)
    if not authority:
        return False
    try:
        decoded = urllib.parse.unquote(authority)
    except ValueError:
        return True
    for text in (authority, decoded):
        if "\\" in text or "@" in text:
            return True
    return False


def is_remote_bank_image_url(url: str) -> bool:
    """Return True when a src URL should be mirrored locally.

    Only https URLs are accepted. The hostname is parsed, so a fragment
    or query cannot borrow an allowlisted name. Userinfo and a backslash
    in the authority are refused even when the parsed host is allowlisted.
    A non-numeric port raises ``ValueError`` from the parser and is
    refused here, before any fetch. A broken bracket in the authority
    raises the same error and is refused rather than escaping the caller.

    Args:
        url: Image ``src`` from bank HTML.
    """
    raw = str(url or "").strip()
    try:
        if _url_has_userinfo_or_backslash(raw):
            return False
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme.lower() != "https":
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        # A non-numeric port raises ValueError. Refuse it before the fetch.
        _ = parsed.port
        return _host_allowed(parsed.hostname or "")
    except ValueError:
        return False


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


def _sweep_expired_refusals(now: float) -> None:
    """Drop expired refusal rows.

    The caller holds ``_refusal_lock``.

    Args:
        now: Current ``time.monotonic`` value.
    """
    expired = [key for key, expiry in _refusals.items() if expiry <= now]
    for key in expired:
        _refusals.pop(key, None)


def _refusal_active(library_id: int, url: str) -> bool:
    """Return True when this URL was refused recently enough to skip the network.

    A live row moves to the end of the cache so a cap evicts colder URLs
    first.

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
        _refusals[key] = _refusals.pop(key)
        return True


def _remember_refusal(
    library_id: int,
    url: str,
    *,
    ttl: float = _REFUSAL_TTL_S,
) -> None:
    """Remember a refused or failed download until ``ttl`` seconds from now.

    Expired rows are swept on insert. When the cache is still at the cap,
    the least-recently used row is dropped. A confirmed non-image uses
    about one hour. A network error uses about five minutes.

    Args:
        library_id: Owning library id.
        url: Remote image URL that must not be fetched again yet.
        ttl: Seconds to remember this refusal.
    """
    key = _refusal_key(library_id, url)
    now = time.monotonic()
    with _refusal_lock:
        _refusals.pop(key, None)
        _sweep_expired_refusals(now)
        while len(_refusals) >= _REFUSAL_MAX:
            oldest = next(iter(_refusals), None)
            if oldest is None:
                break
            _refusals.pop(oldest, None)
        _refusals[key] = now + float(ttl)


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

    The root element must be ``svg`` after an optional XML prolog,
    leading comments, and whitespace. Inkscape and Illustrator exports
    start with a comment. HTML that mentions ``svg`` later is not an SVG.

    Args:
        payload: Response body.
    """
    text = payload.lstrip(b"\xef\xbb\xbf \t\r\n")
    while True:
        lower = text.lower()
        if lower.startswith(b"<?xml"):
            end = lower.find(b"?>")
            if end < 0:
                return False
            text = text[end + 2 :].lstrip(b" \t\r\n")
            continue
        if lower.startswith(b"<!--"):
            end = lower.find(b"-->")
            if end < 0:
                return False
            text = text[end + 3 :].lstrip(b" \t\r\n")
            continue
        break
    if not lower.startswith(b"<svg"):
        return False
    if len(text) <= 4:
        return False
    return text[4:5] in b" \t\r\n>/"


def _local_name(tag: str) -> str:
    """Return the local name of an XML tag or attribute.

    Args:
        tag: Clark notation ``{namespace}name`` or a bare name.
    """
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _namespace(tag: str) -> str:
    """Return the namespace URI of an XML tag or attribute.

    Args:
        tag: Clark notation ``{namespace}name`` or a bare name.
    """
    if tag.startswith("{") and "}" in tag:
        return tag[1:tag.index("}")]
    return ""


def _same_document_fragment(value: str) -> bool:
    """Return True when a URL is only a same-document ``#fragment``.

    Args:
        value: Decoded attribute value.
    """
    text = str(value or "").strip()
    if len(text) > 200:
        return False
    return _FRAGMENT_RE.fullmatch(text) is not None


_CSS_ESCAPE_RE = re.compile(
    r"\\(?:(\r\n|[\n\r\f])|([0-9a-fA-F]{1,6})[ \t\r\n\f]?|(.))",
)
_CSS_ACTIVE_TOKENS = (
    "javascript:",
    "vbscript:",
    "expression(",
    "@import",
    "-moz-binding",
    "behavior:",
)


def _unescape_css(value: str) -> str:
    """Decode CSS escapes so a hidden ``url(`` cannot skip the checks.

    A hex escape is a backslash plus one to six hex digits, and it consumes
    one trailing whitespace character. A backslash before a newline is a
    line continuation. Any other escaped character is that character.

    Args:
        value: Raw CSS or presentation-attribute text.
    """

    def replace(match: re.Match[str]) -> str:
        """Replace one CSS escape match.

        Args:
            match: One escape from ``_CSS_ESCAPE_RE``.
        """
        if match.group(1) is not None:
            return ""
        hex_digits = match.group(2)
        if hex_digits is not None:
            code = int(hex_digits, 16)
            if code == 0 or code > 0x10FFFF:
                return "\ufffd"
            return chr(code)
        return str(match.group(3) or "")

    return _CSS_ESCAPE_RE.sub(replace, str(value or ""))


def _unescape_css_fully(value: str) -> str:
    """Decode CSS escapes until the text stops changing.

    One pass turns a hex escape for a backslash plus ``75rl(`` into a
    backslash and ``75rl(``. A browser decodes again, so a hidden
    ``url(`` or ``@import`` would survive a single pass. Decoding stops
    at eight passes.

    Args:
        value: Raw CSS or presentation-attribute text.
    """
    text = str(value or "")
    for _ in range(8):
        decoded = _unescape_css(text)
        if decoded == text:
            return text
        text = decoded
    return text


def _css_name_boundary(text: str, found: int) -> bool:
    """Return True when ``found`` is not in the middle of an identifier.

    Args:
        text: CSS text.
        found: Index of a function or keyword name.
    """
    if found <= 0:
        return True
    prev = text[found - 1]
    return not (prev.isalnum() or prev in "-_")


def _strip_balanced_call(text: str, name: str) -> str:
    """Remove every ``name(...)`` call, including nested parentheses.

    An unclosed call is removed through the end of the text. A match
    inside a longer identifier is left alone.

    Args:
        text: CSS text.
        name: Function name, matched case-insensitively.
    """
    lower = text.lower()
    needle = name.lower()
    out: list[str] = []
    index = 0
    while index < len(text):
        found = lower.find(needle, index)
        if found < 0:
            out.append(text[index:])
            break
        if not _css_name_boundary(text, found):
            out.append(text[index : found + 1])
            index = found + 1
            continue
        cursor = found + len(needle)
        while cursor < len(text) and text[cursor] in " \t\r\n\f":
            cursor += 1
        if cursor >= len(text) or text[cursor] != "(":
            out.append(text[index : found + 1])
            index = found + 1
            continue
        out.append(text[index:found])
        depth = 0
        while cursor < len(text):
            char = text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            cursor += 1
        index = cursor
    return "".join(out)


def _strip_unsafe_css_urls(text: str) -> str:
    """Remove ``url()`` calls that are not a same-document fragment.

    Args:
        text: CSS text that has already been unescaped.
    """
    lower = text.lower()
    out: list[str] = []
    index = 0
    while index < len(text):
        found = lower.find("url", index)
        if found < 0:
            out.append(text[index:])
            break
        if not _css_name_boundary(text, found):
            out.append(text[index : found + 1])
            index = found + 1
            continue
        cursor = found + 3
        while cursor < len(text) and text[cursor] in " \t\r\n\f":
            cursor += 1
        if cursor >= len(text) or text[cursor] != "(":
            out.append(text[index : found + 1])
            index = found + 1
            continue
        start_inner = cursor + 1
        depth = 1
        cursor += 1
        while cursor < len(text) and depth:
            char = text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        end_inner = cursor - 1 if depth == 0 else cursor
        target = text[start_inner:end_inner].strip().strip("'\"").strip()
        if depth == 0 and _same_document_fragment(target):
            out.append(text[index:cursor])
        else:
            out.append(text[index:found])
        index = cursor
    return "".join(out)


def _strip_data_font_faces(text: str) -> str:
    """Remove ``@font-face`` blocks whose body uses a ``data:`` URL.

    Other rules in the same style sheet stay. A font face that does not
    mention ``data:`` stays too.

    Args:
        text: CSS text that has already been unescaped.
    """
    lower = text.lower()
    out: list[str] = []
    index = 0
    while index < len(text):
        found = lower.find("@font-face", index)
        if found < 0:
            out.append(text[index:])
            break
        brace = text.find("{", found)
        if brace < 0:
            out.append(text[index:found])
            break
        depth = 0
        cursor = brace
        while cursor < len(text):
            char = text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    cursor += 1
                    break
            cursor += 1
        block = text[found:cursor]
        if "data:" in block.lower():
            out.append(text[index:found])
        else:
            out.append(text[index:cursor])
        index = cursor
    return "".join(out)


def _strip_css_imports(text: str) -> str:
    """Remove ``@import`` rules through the terminating semicolon.

    Args:
        text: CSS text that has already been unescaped.
    """
    lower = text.lower()
    out: list[str] = []
    index = 0
    while index < len(text):
        found = lower.find("@import", index)
        if found < 0:
            out.append(text[index:])
            break
        semi = text.find(";", found)
        out.append(text[index:found])
        if semi < 0:
            break
        index = semi + 1
    return "".join(out)


def _clean_presentation_css(value: str) -> str | None:
    """Return CSS with remote loads removed, or None when active content remains.

    Escapes are decoded until stable, so a doubled hex escape for a
    backslash cannot hide ``url(`` or ``@import``. ``-webkit-image-set()``
    is removed before
    ``image-set()``, because a leading hyphen would otherwise look like
    part of the function name. ``url()`` stays only for a same-document
    fragment. An ``@font-face`` that points at ``data:`` is removed on
    its own so the rest of the style sheet can stay. ``javascript:``,
    expressions, and binding tokens still refuse the whole value.

    Args:
        value: Decoded attribute or style-element text.

    Returns:
        Cleaned CSS, an empty string when every rule was stripped, or
        None when a token such as ``javascript:`` is still present.
    """
    text = _unescape_css_fully(value)
    text = _strip_data_font_faces(text)
    text = _strip_css_imports(text)
    text = _strip_balanced_call(text, "-webkit-image-set")
    text = _strip_balanced_call(text, "image-set")
    text = _strip_unsafe_css_urls(text)
    lower = text.lower()
    if any(token in lower for token in _CSS_ACTIVE_TOKENS):
        return None
    return text



def _accepted_attribute(name: str, value: str) -> str | None:
    """Return an attribute value to keep, or None when it must be dropped.

    Args:
        name: Lowercased local name.
        value: Decoded attribute value from the XML parser.
    """
    if name.startswith("on"):
        return None
    if name in {"href", "src"}:
        return value.strip() if _same_document_fragment(value) else None
    if name == "type" and _SCRIPT_TYPE_RE.search(value):
        return None
    if name == "space" and value.strip() not in {"preserve", "default"}:
        return None
    cleaned = _clean_presentation_css(value)
    if cleaned is None or not str(cleaned).strip():
        return None
    return cleaned


def _append_text(node: ET.Element, extra: str) -> None:
    """Append text that followed a dropped child.

    Args:
        node: Parent being built.
        extra: Tail of the dropped child.
    """
    if not extra:
        return
    if len(node):
        last = node[-1]
        last.tail = (last.tail or "") + extra
        return
    node.text = (node.text or "") + extra


class _SvgSanitizeRejected(Exception):
    """The SVG is too deep or too wide to sanitise safely."""


def _clean_svg_element(
    elem: ET.Element,
    depth: int,
    budget: list[int],
) -> ET.Element | None:
    """Return one allowlisted element, or None when it must be dropped.

    The local name is what matters. A ``script`` or ``foreignObject`` in
    the SVG namespace or the XHTML namespace is dropped, and so is every
    other name that is not on the allowlist.

    Args:
        elem: Parsed element.
        depth: Nesting depth of this element. The root is 0.
        budget: One-item list counting elements already visited.

    Raises:
        _SvgSanitizeRejected: The document is deeper or wider than the cap.
    """
    budget[0] += 1
    if depth > _SVG_MAX_DEPTH or budget[0] > _SVG_MAX_NODES:
        raise _SvgSanitizeRejected("svg is too large to sanitise")
    if not isinstance(elem.tag, str):
        return None
    canonical = _SVG_ELEMENTS.get(_local_name(elem.tag).lower())
    if canonical is None:
        return None
    node = ET.Element(f"{{{_SVG_NS}}}{canonical}")
    for key, raw_value in elem.attrib.items():
        if not isinstance(key, str):
            continue
        lower = _local_name(key).lower()
        if lower.startswith("on"):
            continue
        attr_name = _SVG_ATTRS.get(lower)
        if attr_name is None:
            continue
        attr_ns = _namespace(key)
        if attr_ns not in {"", _XLINK_NS, _XML_NS}:
            continue
        if attr_ns == _XLINK_NS and lower != "href":
            continue
        if attr_ns == _XML_NS and lower != "space":
            continue
        kept = _accepted_attribute(lower, str(raw_value))
        if kept is None:
            continue
        if attr_ns == _XLINK_NS:
            node.set(f"{{{_XLINK_NS}}}href", kept)
        elif attr_ns == _XML_NS:
            node.set(f"{{{_XML_NS}}}space", kept)
        else:
            node.set(attr_name, kept)
    if canonical == "style":
        style_text = _clean_presentation_css(str(elem.text or ""))
        if style_text and style_text.strip():
            node.text = style_text
    elif elem.text:
        node.text = elem.text
    for child in list(elem):
        cleaned = _clean_svg_element(child, depth + 1, budget)
        if cleaned is None:
            _append_text(node, str(child.tail or ""))
            continue
        cleaned.tail = child.tail
        node.append(cleaned)
    return node


class _SvgSanitizeTransient(Exception):
    """The SVG looked real, but parsing or writing it failed this time.

    A locked or racing writer used to turn a good SVG into a refusal.
    Callers remember that for minutes, not as a confirmed non-image.
    """


def _xml_text(value: str) -> str:
    """Escape text for an XML attribute or character data.

    Args:
        value: Decoded text.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _tree_uses_xlink(elem: ET.Element) -> bool:
    """Return True when the element or a descendant has an xlink attribute.

    Args:
        elem: Sanitised element.
    """
    for key in elem.attrib:
        if isinstance(key, str) and _namespace(key) == _XLINK_NS:
            return True
    return any(_tree_uses_xlink(child) for child in list(elem))


def _serialize_element(elem: ET.Element, *, root: bool, declare_xlink: bool) -> str:
    """Write one element without reading ``ElementTree``'s namespace map.

    Args:
        elem: Sanitised element in the SVG namespace.
        root: When True, declare the SVG namespace on this element.
        declare_xlink: When True, declare the xlink namespace on the root.
    """
    local = _local_name(elem.tag)
    parts = [f"<{local}"]
    if root:
        parts.append(f' xmlns="{_SVG_NS}"')
        if declare_xlink:
            parts.append(f' xmlns:xlink="{_XLINK_NS}"')
    for key, value in elem.attrib.items():
        if not isinstance(key, str):
            continue
        ns = _namespace(key)
        name = _local_name(key)
        if ns == _XLINK_NS:
            qname = f"xlink:{name}"
        elif ns == _XML_NS:
            qname = f"xml:{name}"
        else:
            qname = name
        parts.append(f' {qname}="{_xml_text(value)}"')
    children = list(elem)
    text = elem.text or ""
    if not children and not text:
        parts.append("/>")
        return "".join(parts)
    parts.append(">")
    if text:
        parts.append(_xml_text(text))
    for child in children:
        parts.append(_serialize_element(child, root=False, declare_xlink=False))
        if child.tail:
            parts.append(_xml_text(child.tail))
    parts.append(f"</{local}>")
    return "".join(parts)


def _serialize_svg(elem: ET.Element) -> bytes:
    """Serialise a cleaned SVG element without a process-wide namespace map.

    ``ElementTree.tostring`` reads a process-wide prefix table.
    ``register_namespace`` writes that table. This writer never touches
    it, so a manifest or slide serializer running on another thread
    cannot change this document or be changed by it.

    Args:
        elem: Sanitised SVG root in the SVG namespace.

    Returns:
        UTF-8 XML bytes whose root is an unprefixed ``svg`` element.
    """
    body = _serialize_element(
        elem, root=True, declare_xlink=_tree_uses_xlink(elem)
    )
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}'.encode("utf-8")


_SVG11_PUBLIC_ID = b"-//w3c//dtd svg 1.1//en"
_SVG11_SYSTEM_ID = b"http://www.w3.org/graphics/svg/1.1/dtd/svg11.dtd"


def _strip_standard_svg_doctype(payload: bytes) -> bytes | None:
    """Drop a standard SVG 1.1 doctype without resolving it.

    The doctype is cut out of the bytes and is never fetched. An internal
    subset, or any doctype that is not the SVG 1.1 public and system
    identifiers, refuses the document.

    Args:
        payload: Candidate SVG bytes.

    Returns:
        Bytes safe to parse, or None when a doctype must be refused.
    """
    lower = payload.lower()
    start = lower.find(b"<!doctype")
    if start < 0:
        return payload
    end = payload.find(b">", start)
    if end < 0:
        return None
    declaration = payload[start : end + 1]
    if b"[" in declaration:
        return None
    folded = declaration.lower()
    if not folded.startswith(b"<!doctype"):
        return None
    name = folded[len(b"<!doctype") :].lstrip()
    if not name.startswith(b"svg"):
        return None
    if len(name) > 3 and name[3:4] not in b" \t\r\n>":
        return None
    if _SVG11_PUBLIC_ID not in folded or _SVG11_SYSTEM_ID not in folded:
        return None
    return payload[:start] + payload[end + 1 :]


def sanitize_svg(payload: bytes) -> bytes | None:
    """Return the sanitised SVG to store, or None when the file is refused.

    The mirror stores this return value and does not keep the original
    bytes. Parsing uses defusedxml, so a DTD or an external entity refuses
    the file. A standard SVG 1.1 doctype is removed first and is not
    resolved. Elements whose local name is not allowlisted are removed in
    every namespace. ``on*`` attributes are removed. ``href`` and
    ``xlink:href`` stay only when they are a same-document ``#fragment``.
    ``foreignObject`` is not allowlisted, so it is removed.

    A parser or writer failure that is not a definite rejection raises
    ``_SvgSanitizeTransient`` so the caller can use the short refusal
    window.

    Args:
        payload: Candidate SVG bytes.

    Returns:
        UTF-8 XML for the cleaned document, or None when the body is not
        an SVG document or the parser rejects it.

    Raises:
        _SvgSanitizeTransient: The body looked like SVG, but parsing or
            serialising failed in a way that may succeed later.
    """
    prepared = _strip_standard_svg_doctype(payload)
    if prepared is None:
        return None
    if not _svg_document(prepared):
        return None
    try:
        root = _safe_xml_fromstring(prepared)
    except (DefusedXmlException, ET.ParseError):
        return None
    except (ValueError, TypeError, OSError) as exc:
        raise _SvgSanitizeTransient("svg parse failed") from exc
    if not isinstance(getattr(root, "tag", None), str):
        return None
    if _local_name(root.tag).lower() != "svg":
        return None
    try:
        cleaned = _clean_svg_element(root, 0, [0])
    except _SvgSanitizeRejected:
        return None
    if cleaned is None:
        return None
    try:
        rendered = _serialize_svg(cleaned)
    except (ValueError, TypeError, LookupError) as exc:
        raise _SvgSanitizeTransient("svg serialize failed") from exc
    if not rendered or b"<svg" not in rendered.lower():
        raise _SvgSanitizeTransient("svg serialize produced no root")
    return rendered



def _image_bytes_for_store(payload: bytes) -> tuple[str, bytes] | None:
    """Return the kind and bytes to store, or None when the body is not an image.

    Raster bytes are stored unchanged. An SVG is stored as the sanitiser
    output, so a namespace-prefixed script or an entity-encoded
    ``javascript:`` URL cannot survive into the blob.

    Args:
        payload: Response body.
    """
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", payload
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg", payload
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "gif", payload
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp", payload
    cleaned = sanitize_svg(payload)
    if cleaned is None:
        return None
    return "svg", cleaned


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
        payload: Image bytes. SVG payloads are already sanitised.
        kind: Sniffed kind from ``_image_bytes_for_store``.

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

    A sqlite cache hit wins over the in-memory refusal cache and does not
    read or rewrite the blob. SVGs already on disk stay as they were; the
    module-file routes sandbox them when they are served. A newly
    downloaded SVG is replaced with the sanitiser output before it is
    stored. A confirmed non-image is remembered for about one hour. A
    network error, and a sanitiser failure that may be transient, are
    remembered for about five minutes. The refusal cache
    is capped. One in-flight download is shared so a class poll does not
    stampede the same URL.

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
        except http.client.InvalidURL:
            _remember_refusal(library, url, ttl=_REFUSAL_TTL_S)
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            _remember_refusal(library, url, ttl=_REFUSAL_NETWORK_TTL_S)
            return None
        except ValueError:
            _remember_refusal(library, url, ttl=_REFUSAL_TTL_S)
            return None
        try:
            prepared = _image_bytes_for_store(payload)
        except _SvgSanitizeTransient:
            _remember_refusal(library, url, ttl=_REFUSAL_NETWORK_TTL_S)
            return None
        if prepared is None:
            _remember_refusal(library, url, ttl=_REFUSAL_TTL_S)
            return None
        kind, stored_payload = prepared
        relpath = _store_mirrored_image(
            school, library, url, stored_payload, kind
        )
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


_RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def module_file_kind(rel: str) -> str:
    """Return ``raster``, ``svg``, or ``other`` for a module-file name.

    The match is the download name, case-insensitive. ``.svg.gz`` is an
    SVG. Only png, jpg, jpeg, gif, and webp are raster. Every other
    name, including no extension, is ``other``.

    Args:
        rel: Cartridge-relative path used as the download name.
    """
    name = str(rel or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()
    if name.endswith(".svg.gz") or name.endswith(".svg") or name.endswith(".svgz"):
        return "svg"
    suffix = ""
    if "." in name:
        suffix = "." + name.rsplit(".", 1)[-1]
    if suffix in _RASTER_SUFFIXES:
        return "raster"
    return "other"


def _attachment_filename(rel: str) -> str:
    """Return one path segment safe to put in Content-Disposition.

    Args:
        rel: Cartridge-relative path used as the download name.
    """
    name = str(rel or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    name = (
        name.replace("\\", "")
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
        .replace("\t", "")
    )
    if not name or name in {".", ".."}:
        return "download"
    return name[:180]


def content_disposition_attachment(rel: str) -> str:
    """Return an attachment header that stays latin-1.

    ``filename`` is an ASCII fallback. ``filename*`` is the real name as
    percent-encoded UTF-8 (RFC 5987), so a non-latin-1 name does not
    raise when the header is encoded.

    Args:
        rel: Cartridge-relative path used as the download name.
    """
    cleaned = _attachment_filename(rel)
    ascii_name = "".join(ch for ch in cleaned if ord(ch) < 128).strip(" .")
    if not ascii_name:
        ascii_name = "download"
    encoded = urllib.parse.quote(cleaned, safe="")
    return (
        f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'
    )


def apply_module_file_headers(response: Any, rel: str) -> Any:
    """Apply the module-file allowlist without rewriting the blob.

    Raster images stay inline under the site policy. SVG, svgz, and
    ``.svg.gz`` stay inline as ``image/svg+xml`` with the sandbox policy
    so a graph can still load in an image tag. Every other type is an
    attachment with the same sandbox policy and ``nosniff``. Headers are
    set on the response only. The stored bytes are not changed.

    Args:
        response: File response for the library blob.
        rel: Cartridge-relative path used as the download name.

    Returns:
        The same response.
    """
    kind = module_file_kind(rel)
    if kind == "raster":
        return response
    response.headers["Content-Security-Policy"] = SVG_SANDBOX_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    if kind == "svg":
        response.headers["Content-Type"] = "image/svg+xml"
        return response
    response.headers["Content-Disposition"] = content_disposition_attachment(rel)
    return response


def apply_mirrored_svg_headers(response: Any, rel: str) -> Any:
    """Apply the module-file allowlist. Kept for existing callers.

    Args:
        response: File response for the library blob.
        rel: Cartridge-relative path used as the download name.

    Returns:
        The same response, after :func:`apply_module_file_headers`.
    """
    return apply_module_file_headers(response, rel)
