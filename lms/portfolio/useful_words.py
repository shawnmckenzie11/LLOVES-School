"""Useful-word union from seed topics, statement phrases, and glossary hits."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from paths import MCF3M_SEED, REPO_ROOT

# Phrases that already appear in Ontario specific statements (not invented).
REPRESENTATION_PHRASES: tuple[str, ...] = (
    "tables of values",
    "mapping diagrams",
    "function machines",
    "graphs",
    "graph",
    "equations",
    "equation",
    "numeric",
    "graphical",
    "algebraic",
    "diagrams",
    "diagram",
    "scatter plot",
    "function notation",
    "vertex form",
    "standard form",
    "factored form",
)


def _norm_code(course_code: str) -> str:
    """Uppercase Ontario course code."""
    return re.sub(r"[^A-Z0-9]", "", (course_code or "").upper())


def load_seed_topics(course_code: str, expectation_codes: list[str]) -> list[str]:
    """Topics arrays from ``lms/seeds/mcf3m_expectations.json`` for matching codes.

    MCR3U has no topics arrays in the seed; returns empty for other courses.

    Args:
        course_code: Ontario code.
        expectation_codes: Overall and specific codes from the module map.
    """
    if _norm_code(course_code) != "MCF3M" or not MCF3M_SEED.is_file():
        return []
    wanted = {str(c).strip().upper() for c in expectation_codes}
    payload = json.loads(MCF3M_SEED.read_text(encoding="utf-8"))
    found: list[str] = []
    seen: set[str] = set()
    for strand in payload.get("strands") or []:
        if not isinstance(strand, dict):
            continue
        for group_key in ("overall", "specific"):
            for row in strand.get(group_key) or []:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("code") or "").strip().upper()
                if code not in wanted:
                    continue
                for topic in row.get("topics") or []:
                    text = str(topic).strip()
                    key = text.lower()
                    if not text or key in seen:
                        continue
                    seen.add(key)
                    found.append(text)
    return found


def representation_phrases_in_statements(statements: list[str]) -> list[str]:
    """Return representation phrases that already occur in the statements.

    Args:
        statements: Specific-expectation wording from the curriculum JSON.
    """
    blob = " ".join(statements).lower()
    out: list[str] = []
    seen: set[str] = set()
    for phrase in REPRESENTATION_PHRASES:
        if phrase.lower() in blob and phrase.lower() not in seen:
            seen.add(phrase.lower())
            out.append(phrase)
    return out


def _iter_glossary_json_files(data_dir: Path | None) -> list[Path]:
    """Find IMSCC ``glossary-data.json`` files under libraries."""
    roots: list[Path] = []
    if data_dir is not None:
        roots.append(Path(data_dir) / "libraries")
    roots.append(REPO_ROOT / ".local-data" / "main" / "libraries")
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(root.glob("**/glossary-data.json"))
        found.extend(root.glob("**/m1-change-and-transformation/glossary-data.json"))
    # Preserve order, drop dupes.
    uniq: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        uniq.append(path)
    return uniq


def load_mcf3m_glossary_terms(data_dir: Path | None = None) -> list[str]:
    """``terms[].term`` from MCF3M IMSCC glossary-data.json (course-wide).

    Args:
        data_dir: LMS data root that may contain unpacked libraries.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for path in _iter_glossary_json_files(data_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        rows = payload.get("terms") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            term = str(row.get("term") or "").strip()
            key = term.lower()
            if not term or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


def _extract_pdf_text(path: Path) -> str:
    """Best-effort PDF text; empty when extractors are missing."""
    try:
        import fitz  # type: ignore

        doc = fitz.open(path)
        try:
            return "\n".join(page.get_text() or "" for page in doc)
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


def load_mcr3u_glossary_terms(data_dir: Path | None = None) -> list[str]:
    """Candidate terms from MCR3U glossary PDFs (never invented).

    Args:
        data_dir: LMS data root that may contain library 4 web_resources.
    """
    roots: list[Path] = []
    if data_dir is not None:
        roots.append(Path(data_dir) / "libraries")
    roots.append(REPO_ROOT / ".local-data" / "main" / "libraries")
    pdfs: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        pdfs.extend(root.glob("**/web_resources/**/*Glossary*.pdf"))
        pdfs.extend(root.glob("**/*Glossary*.pdf"))
    text = ""
    seen_files: set[Path] = set()
    for path in pdfs:
        resolved = path.resolve()
        if resolved in seen_files:
            continue
        seen_files.add(resolved)
        if "MCR3U" not in path.name.upper() and "MCR3U" not in str(path).upper():
            # Keep Nelson-style names that sit in an MCR3U library folder.
            if "libraries/4" not in str(path) and "/4/" not in str(path):
                continue
        text += "\n" + _extract_pdf_text(path)
    if not text.strip():
        return []
    # Split likely glossary headwords: lines that look like a short term.
    terms: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        raw = line.strip()
        if not raw or len(raw) > 60:
            continue
        if any(ch.isdigit() for ch in raw) and len(raw) < 4:
            continue
        if raw.lower() in {"glossary", "index", "contents"}:
            continue
        # Headwords are usually Title Case or a single phrase before a dash.
        head = re.split(r"\s[–—-]\s", raw, maxsplit=1)[0].strip()
        if not head or len(head.split()) > 6:
            continue
        key = head.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(head)
    return terms


def glossary_terms_in_statements(terms: list[str], statements: list[str]) -> list[str]:
    """Keep glossary terms that already appear in the specific statements.

    Args:
        terms: Candidate glossary headwords.
        statements: Expectation wording.
    """
    blob = " ".join(statements).lower()
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        needle = str(term).strip().lower()
        if not needle or needle in seen:
            continue
        if needle in blob:
            seen.add(needle)
            out.append(str(term).strip())
    return out


def useful_word_union(
    *,
    course_code: str,
    expectation_codes: list[str],
    statements: list[str],
    data_dir: Path | None = None,
) -> list[str]:
    """Union of seed topics, representation phrases, and matching glossary terms.

    Does not invent terms.

    Args:
        course_code: Ontario code.
        expectation_codes: Codes on this module.
        statements: Specific statements for those codes.
        data_dir: LMS data root for IMSCC glossary files.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add_all(items: list[str]) -> None:
        for item in items:
            key = item.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(item.strip())

    add_all(load_seed_topics(course_code, expectation_codes))
    add_all(representation_phrases_in_statements(statements))
    code = _norm_code(course_code)
    if code == "MCF3M":
        add_all(glossary_terms_in_statements(load_mcf3m_glossary_terms(data_dir), statements))
    elif code == "MCR3U":
        add_all(glossary_terms_in_statements(load_mcr3u_glossary_terms(data_dir), statements))
    return ordered
