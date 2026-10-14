#!/usr/bin/env python3
"""Import-time question bank linter (broken images, raw equations, answer leaks).

Ports the read-only prod audit (``bank-audit/report.md``) so the same checks
run whenever questions enter a bank. Findings land in ``question_lint`` and
feed the bank UI's *Needs fixing* filter.

Issue codes (machine names for the spec's prose filters):

- ``broken_image``: empty ``src``; relative / ``$IMS-CC-FILEBASE$`` path not
  in ``library_files`` (or no blob); remote URL with no ``bank_image_cache``
  row, including dead hosts (``wiris.net`` serves ``text/plain``, ``gyazo``
  503s). ``network=True`` also GETs remote URLs (200 + ``image/*``).
- ``raw_equation``: math stored as an image (Equatio / wiris), inline MathML,
  carets / unicode super- and subscripts / radicals / bare TeX in prose,
  ``x2 - 3x`` exponents, unbalanced ``$``, two ``$amounts`` in one node, and
  KaTeX parse errors (when ``node`` is available).
- ``answer_in_stem``: an option image also in the stem, or option text inside
  the stem. Rank questions are skipped.
- ``empty_stem``: no stem text and no stem image.

The linter never blocks an import: every hook goes through :func:`safe_lint`,
which logs and reports errors and returns ``None``.

CLI (read-only report)::

    python -m lms.bank_lint --library-id 14 [--network] [--json out.json]
"""

from __future__ import annotations

import argparse
import base64
import collections
import gzip
import html
import json
import logging
import re
import shutil
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from question_math import _MATH_TOKEN_RE, normalize_house_tex
except ImportError:  # package import
    from lms.question_math import _MATH_TOKEN_RE, normalize_house_tex

log = logging.getLogger("bank_lint")

CODES = ("broken_image", "raw_equation", "answer_in_stem", "empty_stem")
LMS_DIR = Path(__file__).resolve().parent
KATEX_JS = LMS_DIR / "static" / "vendor" / "katex" / "katex.min.js"

#: Remote hosts whose images are known dead unless already mirrored.
DEAD_HOSTS = (("wiris.net", "wiris"), ("gyazo.com", "gyazo"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS question_lint (
  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  code TEXT NOT NULL, subtype TEXT NOT NULL, field TEXT NOT NULL,
  excerpt TEXT NOT NULL DEFAULT '', proposed TEXT, confidence TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (question_id, code, subtype, field)
);
CREATE INDEX IF NOT EXISTS idx_question_lint_code ON question_lint(code);
CREATE TABLE IF NOT EXISTS question_lint_checked (
  question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  findings INTEGER NOT NULL DEFAULT 0,
  checked_at TEXT NOT NULL
);
"""


@dataclass
class LintFinding:
    """One problem on one field of one question."""

    code: str
    subtype: str
    field: str
    excerpt: str
    proposed: str | None
    confidence: str  # "high" | "needs-human"


# ---------------------------------------------------------------- helpers

IMG = re.compile(r"<img\b[^>]*>", re.I)
SRC = re.compile(r"""src\s*=\s*["']([^"']*)["']""", re.I)
TITLE = re.compile(r"""(?:title|alt)\s*=\s*["']([^"']*\\[^"']*)["']""", re.I)
SUP = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿˣ", "0123456789-+nx"))
SUB = dict(zip("₀₁₂₃₄₅₆₇₈₉ₙ", "0123456789n"))
CARET = re.compile(r"(\(?[A-Za-z0-9\.\)\]]+)\^(\([^()]{1,20}\)|-?[A-Za-z0-9\.]+)")
UNI = re.compile(r"([A-Za-z0-9\)\]]*)([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿˣ]+)")
UNISUB = re.compile(r"([A-Za-z])([₀-₉ₙ]+)")
SQRT = re.compile(r"√(?:\(([^()]+)\)|([0-9A-Za-z\.]+))")
BARE = re.compile(
    r"\\(frac|dfrac|sqrt|left|right|pi|theta|times|div|cdot|le|ge|pm|circ|text|mathrm)\b"
)
X2 = re.compile(r"(?<![A-Za-z0-9])([a-z])([2-3])(?=\s*[-+=)])")
CUR = re.compile(r"(?<![\\$])\$(\d[\d,\s]*\d|\d)(\.\d+)?(?![\d$])")
FN = {"sin", "cos", "tan", "log", "ln", "sec", "csc", "cot"}
TRIVIAL_OPTIONS = {"true", "false", "yes", "no", "none of the above", "all of the above"}


class _Nodes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.t: list[str] = []

    def handle_data(self, data: str) -> None:
        self.t.append(data)


def text_nodes(raw: str) -> list[str]:
    """Text nodes of an HTML fragment (the renderer formats math per node)."""
    parser = _Nodes()
    parser.feed(raw or "")
    parser.close()
    return parser.t


def plain(raw: str) -> str:
    """Whitespace-collapsed plain text with images removed."""
    return re.sub(r"\s+", " ", "".join(text_nodes(IMG.sub(" ", raw or "")))).strip()


def _norm(text: str) -> str:
    return (
        re.sub(r"[\s\u00a0]+", " ", html.unescape(text)).strip().lower().replace("−", "-").rstrip(".")
    )


def _img_src(tag: str) -> str:
    match = SRC.search(tag)
    return html.unescape(match.group(1)) if match else ""


def mml2tex(e: ET.Element) -> str:
    """Convert a small MathML tree to TeX (raises on unknown tags)."""
    t = e.tag.split("}")[-1]
    ch = list(e)

    def g(i: int) -> str:
        return mml2tex(ch[i])

    def j() -> str:
        return "".join(mml2tex(c) for c in ch)

    if t in ("math", "mrow", "mstyle", "mpadded", "semantics"):
        return j()
    if t in ("mi", "mn", "mtext"):
        x = (e.text or "").strip()
        if t == "mi" and x in FN:
            return "\\" + x + " "
        greek = {"π": "\\pi", "θ": "\\theta", "°": "^{\\circ}", "α": "\\alpha", "β": "\\beta"}
        return greek.get(x, x if len(x) <= 1 or t != "mtext" else "\\text{" + x + "}")
    if t == "mo":
        x = (e.text or "").replace("\u00a0", " ").strip()
        ops = {"×": "\\times ", "·": "\\cdot ", "÷": "\\div ", "−": "-", "≤": "\\le ",
               "≥": "\\ge ", "≠": "\\ne ", "±": "\\pm ", "°": "^{\\circ}"}
        return ops.get(x, x)
    if t == "msup":
        return "{" + g(0) + "}^{" + g(1) + "}"
    if t == "msub":
        return "{" + g(0) + "}_{" + g(1) + "}"
    if t == "msubsup":
        return "{" + g(0) + "}_{" + g(1) + "}^{" + g(2) + "}"
    if t == "mfrac":
        return "\\frac{" + g(0) + "}{" + g(1) + "}"
    if t == "msqrt":
        return "\\sqrt{" + j() + "}"
    if t == "mroot":
        return "\\sqrt[" + g(1) + "]{" + g(0) + "}"
    if t == "mfenced":
        return "\\left" + e.get("open", "(") + ",".join(mml2tex(x) for x in ch) + "\\right" + e.get("close", ")")
    if t == "mspace":
        return " "
    raise ValueError("mml tag " + t)


def _mml_conf(mml: str | None, tex: str | None) -> str:
    ok = (
        tex and len(tex) <= 120
        and not re.search(r"linebreak|mtable|mlabeledtr", mml or "")
        and "{)}" not in tex and "{(}" not in tex
    )
    return "high" if ok else "needs-human"


def wiris_tex(src: str) -> tuple[str | None, str | None]:
    """Decode a wiris ``mmlgz=`` / ``mml=`` image URL to (MathML, TeX)."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(src)).query)
    try:
        if "mmlgz" in query:
            mml = gzip.decompress(base64.b64decode(query["mmlgz"][0])).decode()
        elif "mml" in query:
            mml = query["mml"][0]
        else:
            return None, None
    except Exception:  # noqa: BLE001 - corrupt payload is just "not convertible"
        return None, None
    try:
        return mml, mml2tex(ET.fromstring(mml))
    except Exception:  # noqa: BLE001
        return mml, None


def _exp_tex(e: str) -> str:
    e = e.strip("()")
    m = re.fullmatch(r"(-?)([0-9A-Za-z\.]+)/([0-9A-Za-z\.]+)", e)
    return f"{m.group(1)}\\frac{{{m.group(2)}}}{{{m.group(3)}}}" if m else e


def fix_prose(t: str) -> str:
    """Proposed house-style rewrite of a prose text node."""
    t = UNI.sub(lambda m: f"{m.group(1)}$^{{{''.join(SUP[c] for c in m.group(2))}}}$", t)
    t = UNISUB.sub(lambda m: f"{m.group(1)}$_{{{''.join(SUB[c] for c in m.group(2))}}}$", t)
    t = CARET.sub(lambda m: f"{m.group(1)}$^{{{_exp_tex(m.group(2))}}}$", t)
    t = SQRT.sub(lambda m: f"$\\sqrt{{{m.group(1) or m.group(2)}}}$", t)
    t = X2.sub(lambda m: f"${m.group(1)}^{{{m.group(2)}}}$", t)
    return re.sub(r"\$\s*\$", " ", t)


# ---------------------------------------------------------------- fields

def question_fields(
    payload: dict[str, Any], overlay: dict[str, Any] | None = None
) -> tuple[list[tuple[str, str]], list[tuple[str, str, bool | None]]]:
    """Return ``(stem_fields, option_fields)`` for one stored payload.

    Stem fields are ``(field, html)``; the first is the stem itself. Option
    fields are ``(field, html, correct)``; ``correct`` is None when unknown.
    """
    p = payload if isinstance(payload, dict) else {}
    stem: tuple[str, str] | None = None
    for key in ("stem_html", "text_html", "text", "prompt", "question", "stem"):
        if p.get(key):
            stem = (key, p[key])
            break
    if overlay and overlay.get("stem_text"):
        stem = ("overlay.stem_text", overlay["stem_text"])
    stems = [stem or ("stem_html", "")]
    if not isinstance(stems[0][1], str):
        stems[0] = (stems[0][0], json.dumps(stems[0][1]))
    opts: list[tuple[str, str, bool | None]] = []

    def _opt_text(c: Any) -> str:
        if isinstance(c, str):
            return c
        if isinstance(c, dict):
            return str(c.get("html") or c.get("text") or c.get("label") or "")
        return str(c)

    if isinstance(p.get("choices"), list):
        opts = [
            (f"choices[{i}]", _opt_text(c), None if not isinstance(c, dict) else bool(c.get("correct")))
            for i, c in enumerate(p["choices"])
        ]
    elif isinstance(p.get("options"), list):
        correct = str(p.get("correct_answer") or p.get("answer") or "").strip()
        opts = [
            (f"options[{i}]", _opt_text(c), (_opt_text(c).strip() == correct) if correct else None)
            for i, c in enumerate(p["options"])
        ]
    elif isinstance(p.get("rank_options"), list):
        opts = [(f"rank_options[{i}]", _opt_text(c), None) for i, c in enumerate(p["rank_options"])]
    for bi, blank in enumerate(p.get("blanks") or []):
        if not isinstance(blank, dict):
            continue
        stems.append((f"blanks[{bi}].label_html", str(blank.get("label_html") or "")))
        opts += [
            (f"blanks[{bi}].choices[{i}]", _opt_text(c), bool(c.get("correct")) if isinstance(c, dict) else None)
            for i, c in enumerate(blank.get("choices") or [])
        ]
    if overlay and overlay.get("options_json") not in (None, "", "[]"):
        try:
            opts = [
                (f"overlay.options[{i}]", _opt_text(c), None)
                for i, c in enumerate(json.loads(overlay["options_json"]))
            ]
        except (TypeError, ValueError):
            pass
    return stems, opts


# ---------------------------------------------------------------- images

class ImageResolver:
    """Decide whether an image ``src`` will paint for one library.

    Args:
        school: Open ``SchoolDB`` (or None for offline checks).
        network: When True, remote URLs with no mirror row are fetched.
        fetch: Optional ``url -> (status, content_type)`` for tests.
    """

    def __init__(
        self,
        school: Any = None,
        *,
        network: bool = False,
        fetch: Callable[[str], tuple[int, str]] | None = None,
    ) -> None:
        self.school = school
        self.network = network
        self.fetch = fetch or _http_head
        self._memo: dict[tuple[int, str], tuple[bool, str]] = {}

    def check(self, library_id: int, src: str) -> tuple[bool, str]:
        """Return ``(ok, why)`` for one src."""
        key = (int(library_id or 0), str(src or "").strip())
        if key not in self._memo:
            self._memo[key] = self._check(*key)
        return self._memo[key]

    def _row(self, sql: str, args: tuple[Any, ...]) -> Any:
        if self.school is None:
            return None
        try:
            return self.school.conn.execute(sql, args).fetchone()
        except Exception:  # noqa: BLE001 - table may not exist yet
            return None

    def _library_file(self, library_id: int, relpath: str) -> bool:
        if self.school is None:
            return False
        try:
            from components import library_file_path
        except ImportError:
            from lms.components import library_file_path
        try:
            return library_file_path(self.school, Path(self.school.data_dir), library_id, relpath) is not None
        except Exception:  # noqa: BLE001
            return False

    def _check(self, library_id: int, src: str) -> tuple[bool, str]:
        if not src:
            return False, "empty src"
        if src.startswith("data:image/"):
            return True, "inline data"
        if src.startswith(("http://", "https://", "//")):
            row = self._row(
                "SELECT relpath FROM bank_image_cache WHERE library_id = ? AND source_url = ?",
                (library_id, src),
            )
            if row is not None:
                ok = self._library_file(library_id, str(row[0]))
                return ok, "mirror cache " + ("ok" if ok else "blob missing")
            host = (urllib.parse.urlparse(src if not src.startswith("//") else "https:" + src).hostname or "").lower()
            for dead, label in DEAD_HOSTS:
                if host == dead or host.endswith("." + dead):
                    return False, f"dead host {label} (not mirrored)"
            if self.network:
                try:
                    status, ctype = self.fetch(src)
                except Exception as exc:  # noqa: BLE001
                    return False, f"fetch failed: {exc}"[:120]
                return status == 200 and ctype.startswith("image/"), f"HTTP {status} {ctype}"
            return False, "remote, not mirrored"
        m = re.match(r"^/api/classes/(\d+)/live-question-images/([^/?#]+)$", src)
        if m:
            try:
                path = self.school.live_question_image_path(int(m.group(1)), m.group(2))
            except Exception:  # noqa: BLE001
                path = None
            return path is not None, "live-question-image " + ("ok" if path else "missing")
        if src.startswith("/static/"):
            ok = (LMS_DIR / src.lstrip("/").split("?")[0]).exists()
            return ok, "repo static"
        m = re.match(r"^/(?:staff/class|api/classes)/\d+/module-files/(.+)$", src)
        rel = m.group(1) if m else src
        rel = (
            rel.replace("$IMS-CC-FILEBASE$/", "web_resources/")
            .replace("%24IMS-CC-FILEBASE%24/", "web_resources/")
            .replace("../web_resources/", "web_resources/")
            .split("?")[0]
        )
        ok = self._library_file(library_id, rel)
        return ok, "library_files " + ("ok" if ok else "missing")


def _http_head(url: str) -> tuple[int, str]:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "lloves-bank-lint"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - lint-only GET
        return int(resp.status), str(resp.headers.get("Content-Type") or "")


# ---------------------------------------------------------------- katex

class KatexChecker:
    """Batch KaTeX parse check through ``node`` and the vendored KaTeX."""

    SCRIPT = (
        "const vm=require('vm'),fs=require('fs');const ctx={};vm.createContext(ctx);ctx.self=ctx;"
        "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);const k=ctx.katex;"
        "const items=JSON.parse(fs.readFileSync(0,'utf8'));const out={};"
        "for(const [id,tex] of items){try{k.renderToString(tex,{throwOnError:true});}"
        "catch(e){out[id]=String(e.message).slice(0,160);}}"
        "console.log(JSON.stringify({version:k.version,errors:out}));"
    )

    def __init__(self) -> None:
        self.status = "ok"

    def errors(self, expressions: list[str]) -> dict[int, str] | None:
        """Return ``{index: message}`` or None when the check was skipped."""
        if not expressions:
            return {}
        node = shutil.which("node")
        if not node or not KATEX_JS.exists():
            self.status = "skipped"
            return None
        try:
            run = subprocess.run(
                [node, "-e", self.SCRIPT, str(KATEX_JS)],
                input=json.dumps([[str(i), t] for i, t in enumerate(expressions)]),
                capture_output=True, text=True, timeout=120, check=True,
            )
            data = json.loads(run.stdout)
        except Exception as exc:  # noqa: BLE001 - never fail an import on KaTeX
            log.warning("bank_lint: KaTeX check skipped: %s", exc)
            self.status = "skipped"
            return None
        return {int(k): v for k, v in (data.get("errors") or {}).items()}


# ---------------------------------------------------------------- checks

class _Ctx:
    def __init__(self) -> None:
        self.findings: list[LintFinding] = []
        self.katex_jobs: list[tuple[str, str, str]] = []  # field, tex, source

    def add(self, code: str, sub: str, field: str, excerpt: str, proposed: str | None, conf: str) -> None:
        self.findings.append(LintFinding(code, sub, field, str(excerpt)[:220], proposed, conf))


def _check_field(ctx: _Ctx, library_id: int, field: str, raw: str, resolver: ImageResolver) -> None:
    raw = raw or ""
    for tag in IMG.findall(raw):
        src = _img_src(tag)
        lower = tag.lower()
        if "equation_image" in lower:
            tm = TITLE.search(tag)
            if tm:
                ctx.katex_jobs.append((field, html.unescape(tm.group(1)), "equation_image"))
                continue
        if "equatio-api.texthelp.com/svg/" in src:
            tex = urllib.parse.unquote(src.split("/svg/", 1)[1])
            ctx.add("raw_equation", "math_as_image_equatio", field, tag, "$" + tex + "$", "high")
            ctx.katex_jobs.append((field, tex, "equatio"))
            continue
        if "wiris.net" in src:
            mml, tex = wiris_tex(src)
            conf = _mml_conf(mml, tex)
            proposal = ("$" + tex + "$") if tex else "MathML not convertible; re-enter as KaTeX"
            ok, why = resolver.check(library_id, src)
            if not ok:
                ctx.add("broken_image", "wiris_image_unavailable", field, f"{why} {src[:90]}",
                        ("$" + tex + "$") if tex else "needs re-entry", conf)
            ctx.add("raw_equation", "math_as_image_wiris", field, (mml or src)[:200], proposal, conf)
            if tex:
                ctx.katex_jobs.append((field, tex, "wiris"))
            continue
        ok, why = resolver.check(library_id, src)
        if ok:
            continue
        if why == "empty src":
            ctx.add("broken_image", "empty_img_tag", field, tag, "remove the empty <img> tag", "high")
        elif "gyazo" in why:
            ctx.add("broken_image", "gyazo_image_unavailable", field, f"{why}: {src[:120]}",
                    "re-upload image to library web_resources/ and point src at it", "needs-human")
        else:
            ctx.add("broken_image", "unresolvable", field, f"{why}: {src[:120]}",
                    "re-upload image to library web_resources/ (or remove) and point src at it", "needs-human")
    for mm in re.findall(r"<math\b.*?</math>", raw, re.I | re.S):
        try:
            tex = mml2tex(ET.fromstring(re.sub(r'\sclass="[^"]*"', "", mm)))
        except Exception:  # noqa: BLE001
            tex = None
        ctx.add("raw_equation", "mathml_markup", field, re.sub(r"\s+", " ", mm)[:200],
                ("$" + tex + "$") if tex else "convert MathML to $...$", _mml_conf(mm, tex))
        if tex:
            ctx.katex_jobs.append((field, tex, "mathml"))
    no_mathml = re.sub(r"<math\b.*?</math>", " ", raw, flags=re.I | re.S)
    for node in text_nodes(IMG.sub(" ", no_mathml)):
        text = html.unescape(node)
        norm = normalize_house_tex(text)
        last = 0
        prose: list[str] = []
        for m in _MATH_TOKEN_RE.finditer(norm):
            prose.append(norm[last:m.start()])
            last = m.end()
            tex = next(g for g in m.groups() if g)
            if m.group(4) and CUR.fullmatch("$" + tex.split()[0]) and re.search(r"[A-Za-z]{3,}\s", tex):
                ctx.add("raw_equation", "currency_dollar_parsed_as_math", field, m.group(0),
                        'write amounts as "N dollars" (house style) instead of $N', "high")
                continue
            ctx.katex_jobs.append((field, tex, "delimited"))
        prose.append(norm[last:])
        p = "".join(prose)
        if len(re.findall(r"(?<!\\)\$", p)) % 2 == 1 and not CUR.search(p) and (BARE.search(p) or CARET.search(p)):
            ctx.add("raw_equation", "unbalanced_delimiter", field, p.strip(), "balance $...$", "needs-human")
        subs = []
        if CARET.search(p):
            subs.append("caret_in_prose")
        if re.search(r"[A-Za-z0-9\)]_[A-Za-z0-9{]", p) and not re.search(r"\w_\w+_\w", p):
            subs.append("underscore_in_prose")
        if UNI.search(p) and re.search("[⁰¹²³⁴⁵⁶⁷⁸⁹⁻ⁿ]", p):
            subs.append("unicode_superscript")
        if UNISUB.search(p):
            subs.append("unicode_subscript")
        if "√" in p or "∛" in p:
            subs.append("unicode_radical")
        if BARE.search(p):
            subs.append("bare_latex")
        if X2.search(p) and re.search(r"[a-z][23]\s*[-+=]", p):
            subs.append("x2_style_exponent")
        if subs:
            fixed = fix_prose(text).strip()
            human = {"bare_latex", "x2_style_exponent"} & set(subs) or "∛" in p or "√((" in p
            for sub in subs:
                ctx.add("raw_equation", sub, field, text.strip(), fixed, "needs-human" if human else "high")


def lint_question(
    payload: dict[str, Any],
    *,
    library_id: int,
    image_resolver: ImageResolver | None = None,
    katex: KatexChecker | None = None,
    item_type: str = "",
    overlay: dict[str, Any] | None = None,
    _ctx: _Ctx | None = None,
) -> list[LintFinding]:
    """Lint one question payload; never raises on odd payload shapes.

    Args:
        payload: Stored ``questions.payload_json`` (decoded).
        library_id: Owning library (image resolution).
        image_resolver: Resolver; an offline one is used when omitted.
        katex: KaTeX checker; None skips KaTeX parse errors.
        item_type: ``questions.item_type`` (``text_only_question`` has no stem).
        overlay: ``library_question_overlays`` row, if any.
    """
    resolver = image_resolver or ImageResolver(None)
    ctx = _ctx or _Ctx()
    start = len(ctx.findings)
    payload = payload if isinstance(payload, dict) else {}
    stems, opts = question_fields(payload, overlay)
    for field, raw in stems:
        _check_field(ctx, library_id, field, raw, resolver)
    for field, raw, _correct in opts:
        _check_field(ctx, library_id, field, raw, resolver)
    image_url = payload.get("image_url")
    if image_url:
        ok, why = resolver.check(library_id, str(image_url))
        if not ok:
            ctx.add("broken_image", "image_url_field", "image_url", f"{why}: {image_url}",
                    "re-upload or clear image_url", "needs-human")
    stem_field, stem_html = stems[0]
    stem_plain = plain(stem_html)
    if (
        not stem_plain
        and not IMG.search(stem_html or "")
        and not re.search(r"<math\b", stem_html or "", re.I)
        and not payload.get("image_url")
        and str(item_type or payload.get("item_type") or "") != "text_only_question"
    ):
        ctx.add("empty_stem", "empty_stem", stem_field, stem_html or "", "write the question stem", "needs-human")
    is_rank = bool(payload.get("rank_options")) or "rank" in str(payload.get("kind") or "")
    if not is_rank:
        nst = _norm(stem_plain)
        stem_imgs = {_img_src(t) for t in IMG.findall(stem_html or "") if "equation_image" not in t.lower()} - {""}
        for field, raw, correct in opts:
            opt_imgs = {_img_src(t) for t in IMG.findall(raw or "") if "equation_image" not in t.lower()} - {""}
            shared = stem_imgs & opt_imgs
            if shared:
                ctx.add("answer_in_stem", "option_image_in_stem", field, "shared img: " + sorted(shared)[0][:120],
                        "remove the option image from the stem", "needs-human")
            po = _norm(plain(raw))
            if not po or po in TRIVIAL_OPTIONS or not nst:
                continue
            hit = (correct and len(po) >= 6 and re.search(r"[0-9=+^/()√²]", po) and po in nst) or (
                len(po) >= 15 and po in nst
            )
            if not hit:
                continue
            tag = " (correct)" if correct else ""
            if nst.endswith(po):
                stripped = re.sub(re.escape(plain(raw).strip().rstrip(".")) + r"\.?\s*$", "", stem_plain).strip()
                ctx.add("answer_in_stem", "option_text_glued_at_stem_end" + tag, field, "stem: …" + stem_plain[-160:],
                        "stem → " + stripped, "high" if len(stripped) > 15 else "needs-human")
            else:
                ctx.add("answer_in_stem", "option_text_inside_stem" + tag, field,
                        f'option "{plain(raw)[:60]}" in stem: {stem_plain[:160]}',
                        "rewrite stem so it does not state the option text", "needs-human")
    if _ctx is None and katex is not None:
        _apply_katex(ctx, katex)
    return ctx.findings[start:]


def _apply_katex(ctx: _Ctx, katex: KatexChecker) -> None:
    errors = katex.errors([tex for _f, tex, _s in ctx.katex_jobs])
    for idx, msg in sorted((errors or {}).items()):
        field, tex, source = ctx.katex_jobs[idx]
        ctx.add("raw_equation", "katex_parse_error_" + source, field, tex, "fix TeX: " + msg, "needs-human")
    ctx.katex_jobs.clear()


# ---------------------------------------------------------------- storage

def ensure_schema(school: Any) -> None:
    """Create ``question_lint`` if missing."""
    school.conn.executescript(SCHEMA_SQL)


def _rows_for(school: Any, where: str, args: tuple[Any, ...]) -> list[Any]:
    has_overlays = school.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_question_overlays'"
    ).fetchone()
    overlay_cols = (
        "o.stem_text, o.options_json" if has_overlays else "NULL AS stem_text, NULL AS options_json"
    )
    overlay_join = (
        "LEFT JOIN library_question_overlays o ON o.library_id = b.library_id AND o.question_id = q.id"
        if has_overlays else ""
    )
    return school.conn.execute(
        f"""
        SELECT q.id, q.item_type, q.payload_json, b.library_id, {overlay_cols}
        FROM questions q JOIN question_banks b ON b.id = q.bank_id
        {overlay_join}
        WHERE {where}
        ORDER BY q.id
        """,
        args,
    ).fetchall()


def lint_rows(
    school: Any, rows: Iterable[Any], *, network: bool = False, store: bool = True,
    resolver: ImageResolver | None = None,
) -> dict[int, list[LintFinding]]:
    """Lint question rows and (optionally) replace their ``question_lint`` rows."""
    resolver = resolver or ImageResolver(school, network=network)
    katex = KatexChecker()
    ctx = _Ctx()
    owner: list[int] = []  # question id per finding / job index
    job_owner: list[int] = []
    results: dict[int, list[LintFinding]] = {}
    for row in rows:
        qid = int(row[0])
        try:
            payload = json.loads(row[2] or "{}")
        except (TypeError, ValueError):
            payload = {}
        overlay = {"stem_text": row[4], "options_json": row[5]} if row[4] is not None or row[5] else None
        before_jobs = len(ctx.katex_jobs)
        found = lint_question(payload, library_id=int(row[3]), image_resolver=resolver,
                              item_type=str(row[1] or ""), overlay=overlay, _ctx=ctx)
        owner += [qid] * len(found)
        job_owner += [qid] * (len(ctx.katex_jobs) - before_jobs)
        results[qid] = list(found)
    errors = katex.errors([tex for _f, tex, _s in ctx.katex_jobs])
    for idx, msg in sorted((errors or {}).items()):
        field, tex, source = ctx.katex_jobs[idx]
        results[job_owner[idx]].append(
            LintFinding("raw_equation", "katex_parse_error_" + source, field, tex[:220], "fix TeX: " + msg, "needs-human")
        )
    if store and results:
        ensure_schema(school)
        now = datetime.now(timezone.utc).isoformat()
        ids = list(results)
        lock = getattr(school, "_lock", None)
        if lock is not None:
            lock.acquire()
        try:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                school.conn.execute(
                    f"DELETE FROM question_lint WHERE question_id IN ({','.join('?' * len(chunk))})", chunk
                )
            school.conn.executemany(
                """INSERT OR REPLACE INTO question_lint
                   (question_id, code, subtype, field, excerpt, proposed, confidence, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (qid, f.code, f.subtype, f.field, f.excerpt, f.proposed, f.confidence, now)
                    for qid, found in results.items() for f in found
                ],
            )
            school.conn.executemany(
                "INSERT OR REPLACE INTO question_lint_checked (question_id, findings, checked_at) VALUES (?, ?, ?)",
                [(qid, len(found), now) for qid, found in results.items()],
            )
            school.conn.commit()
        finally:
            if lock is not None:
                lock.release()
    results_meta = {"katex": katex.status}
    lint_rows.last_meta = results_meta  # type: ignore[attr-defined]
    return results


def lint_questions(school: Any, question_ids: Iterable[int], *, network: bool = False) -> dict[int, list[LintFinding]]:
    """Lint specific questions (single-question saves, seeded banks)."""
    ids = sorted({int(q) for q in question_ids if q})
    rows: list[Any] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += _rows_for(school, f"q.id IN ({','.join('?' * len(chunk))})", tuple(chunk))
    return lint_rows(school, rows, network=network)


def lint_bank(school: Any, bank_id: int, *, network: bool = False) -> dict[int, list[LintFinding]]:
    """Lint every question in one bank."""
    return lint_rows(school, _rows_for(school, "q.bank_id = ?", (int(bank_id),)), network=network)


def lint_library(school: Any, library_id: int, *, network: bool = False, store: bool = True) -> dict[str, Any]:
    """Lint every question in one library and return a summary."""
    rows = _rows_for(school, "b.library_id = ?", (int(library_id),))
    results = lint_rows(school, rows, network=network, store=store)
    return summarize(results, library_id=int(library_id))


def summarize(results: dict[int, list[LintFinding]], *, library_id: int | None = None) -> dict[str, Any]:
    """Counts per code (questions and hits) plus per-subtype hits."""
    questions: collections.Counter[str] = collections.Counter()
    hits: collections.Counter[str] = collections.Counter()
    subtypes: collections.Counter[str] = collections.Counter()
    for found in results.values():
        for code in {f.code for f in found}:
            questions[code] += 1
        for f in found:
            hits[f.code] += 1
            subtypes[f"{f.code}/{f.subtype}"] += 1
    return {
        "library_id": library_id,
        "questions": len(results),
        "flagged": sum(1 for f in results.values() if f),
        "by_code": {c: {"questions": questions[c], "hits": hits[c]} for c in CODES},
        "by_subtype": dict(subtypes.most_common()),
        "katex": getattr(lint_rows, "last_meta", {}).get("katex", "ok"),
    }


def lint_counts(school: Any, library_id: int) -> dict[str, int]:
    """Questions per code already stored for one library (Needs-fixing pane)."""
    ensure_schema(school)
    rows = school.conn.execute(
        """SELECT l.code, COUNT(DISTINCT l.question_id) FROM question_lint l
           JOIN questions q ON q.id = l.question_id JOIN question_banks b ON b.id = q.bank_id
           WHERE b.library_id = ? GROUP BY l.code""",
        (int(library_id),),
    ).fetchall()
    out = {c: 0 for c in CODES}
    out.update({str(r[0]): int(r[1]) for r in rows})
    return out


def fix_codes(school: Any, question_ids: Iterable[int]) -> dict[int, list[str]]:
    """Stored codes per question id (bank list rows)."""
    ensure_schema(school)
    ids = sorted({int(q) for q in question_ids})
    out: dict[int, list[str]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for qid, code in school.conn.execute(
            f"SELECT DISTINCT question_id, code FROM question_lint WHERE question_id IN ({','.join('?' * len(chunk))})",
            chunk,
        ):
            out.setdefault(int(qid), []).append(str(code))
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------- hooks

def safe_lint(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a lint call; log + report any error and return None. Never raises."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - lint must never block an import
        log.warning("bank_lint failed (%s); import continues", exc, exc_info=True)
        try:
            import sentry_sdk

            sentry_sdk.capture_exception(exc)
        except Exception:  # noqa: BLE001
            pass
        return None


def safe_lint_library(school: Any, library_id: int) -> Any:
    """Hook form of :func:`lint_library` (offline, never raises)."""
    return safe_lint(lint_library, school, int(library_id), network=False)


def safe_lint_questions(school: Any, question_ids: Iterable[int]) -> Any:
    """Hook form of :func:`lint_questions` (offline, never raises)."""
    return safe_lint(lint_questions, school, list(question_ids or []), network=False)


def lint_unchecked(school: Any, library_id: int) -> dict[int, list[LintFinding]]:
    """Lint questions in one library that have never been linted.

    Seed hooks run on every boot; this keeps a re-seed cheap (only new rows).
    """
    ensure_schema(school)
    rows = _rows_for(
        school,
        "b.library_id = ? AND NOT EXISTS (SELECT 1 FROM question_lint_checked c WHERE c.question_id = q.id)",
        (int(library_id),),
    )
    return lint_rows(school, rows)


def lints_library_after(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate a bank seed/import ``fn(school, library_id, ...)``.

    After ``fn`` returns, new questions in that library are linted. The lint
    never changes the return value and never raises.
    """
    import functools
    import inspect

    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        try:
            bound = sig.bind_partial(*args, **kwargs)
            school = bound.arguments.get("school") or bound.arguments.get("self") or bound.arguments.get("db")
            library_id = bound.arguments.get("library_id")
        except Exception:  # noqa: BLE001
            return result
        if school is not None and library_id:
            safe_lint(lint_unchecked, school, int(library_id))
        return result

    return wrapper


def lints_question_after(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorate a single-question write; lints ``question_id`` (arg or return).

    Runs synchronously and offline so the editor's warnings update on save.
    Never changes the return value and never raises.
    """
    import functools
    import inspect

    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        try:
            bound = sig.bind_partial(*args, **kwargs)
            school = bound.arguments.get("self") or bound.arguments.get("school")
            qid = bound.arguments.get("question_id")
            if qid is None and isinstance(result, int):
                qid = result
        except Exception:  # noqa: BLE001
            return result
        if school is not None and qid:
            safe_lint_questions(school, [int(qid)])
        return result

    return wrapper


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    """Read-only audit: ``python -m lms.bank_lint --library-id N``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--library-id", type=int, required=True)
    ap.add_argument("--db", default=None, help="sqlite path (default: the app's DB)")
    ap.add_argument("--network", action="store_true", help="GET unmirrored remote images")
    ap.add_argument("--json", dest="json_out", default=None, help="write findings JSON here")
    args = ap.parse_args(argv)
    import sqlite3

    db_path = args.db
    if db_path is None:
        import os

        db_path = os.environ.get("LLOVES_DB") or str(LMS_DIR / "data" / "lloves.sqlite")

    class _RO:
        def __init__(self, path: str) -> None:
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self.data_dir = Path(path).parent

        def live_question_image_path(self, class_id: int, filename: str) -> Path | None:
            p = self.data_dir / "live-question-images" / str(class_id) / filename
            return p if p.exists() else None

    school = _RO(db_path)
    rows = _rows_for(school, "b.library_id = ?", (args.library_id,))
    results = lint_rows(school, rows, network=args.network, store=False)
    summary = summarize(results, library_id=args.library_id)
    print(json.dumps(summary, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"summary": summary, "issues": {q: [asdict(f) for f in fs] for q, fs in results.items() if fs}},
                       indent=1),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
