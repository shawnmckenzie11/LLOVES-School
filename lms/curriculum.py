"""Ontario curriculum catalog: PDF registry, course codes, MCF3M expectations seed.

Never invents expectation wording. Course titles come from local Ministry PDFs
when extractable; other documents are registered as sources until a PDF exists.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from school_db import SchoolDB
from paths import (
    MCF3M_EXPECTATIONS,
    MATH_CURRICULUM_PDF,
    ONTARIO_SOURCES_DIR,
    REPO_ROOT,
)

logger = logging.getLogger(__name__)

# Official Ontario documents registered in the IT catalog this round:
# Mathematics (existing MCF3M PDF), senior Science 11–12, and HPE.
# The DCP elementary HPE page has no 5-character secondary codes; assignable
# HPE codes come from the Grades 9–12 HPE PDF. Do not add other subjects here.
ONTARIO_DOCUMENTS: list[dict[str, str]] = [
    {
        "title": "The Ontario Curriculum, Grades 11 and 12: Mathematics (revised)",
        "subject": "Mathematics",
        "grades": "11-12",
        "source_url": "https://www.edu.gov.on.ca/eng/curriculum/secondary/math1112currb.pdf",
        "local_path": "lms/sources/ontario-curriculum/ontario-math-curriculum-gr-11-12.pdf",
        "filename": "",
        "code_grade_digits": "34",
    },
    {
        "title": "The Ontario Curriculum, Grades 11 and 12: Science (2009)",
        "subject": "Science",
        "grades": "11-12",
        "source_url": "https://www.edu.gov.on.ca/eng/curriculum/secondary/2009science11_12.pdf",
        "local_path": "lms/sources/ontario-curriculum/science-11-12.pdf",
        "filename": "science-11-12.pdf",
        "code_grade_digits": "34",
    },
    {
        "title": "The Ontario Curriculum, Grades 9-12: Health and Physical Education (2015)",
        "subject": "Health and Physical Education",
        "grades": "9-12",
        "source_url": "https://www.edu.gov.on.ca/eng/curriculum/secondary/health9to12.pdf",
        "local_path": "lms/sources/ontario-curriculum/health-pe-9-12.pdf",
        "filename": "health-pe-9-12.pdf",
        "code_grade_digits": "1234",
    },
    {
        "title": "The Ontario Curriculum, Grades 1-8: Health and Physical Education",
        "subject": "Health and Physical Education (elementary)",
        "grades": "1-8",
        "source_url": "https://www.dcp.edu.gov.on.ca/en/curriculum/elementary-health-and-physical-education",
        "local_path": "",
        "filename": "",
        "extractable": "0",
    },
]

_PATHWAY_ALT = r"University(?:\s*/\s*College)?|College|Workplace|Open"
_GRADE_HEADING_RE = re.compile(r"Grade\s+(9|10|11|12)\b", re.I)
_PREREQ_RE = re.compile(r"(?i)^prerequisite\b")
_CODE_GRADE_TO_YEAR = {"1": 9, "2": 10, "3": 11, "4": 12}

_PATHWAY_MAP = {
    "University": "U",
    "University/College": "M",
    "College": "C",
    "Workplace": "E",
    "Open": "O",
}


_PDF_TEXT_CACHE: dict[str, str] = {}
_COURSE_EXTRACT_CACHE: dict[str, list[dict[str, Any]]] = {}


def _pdf_text(path: Path) -> str:
    """Extract plain text from a PDF using PyMuPDF when available.

    Args:
        path: Local PDF.

    Returns:
        Concatenated page text, or empty if the file/library is missing.
    """
    if not path.is_file():
        return ""
    cache_key = str(path.resolve())
    cached = _PDF_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # older PyMuPDF import name
        except ImportError:
            logger.warning("PyMuPDF is not installed; skipping PDF extract for %s", path)
            return ""
    doc = fitz.open(path)
    try:
        text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
    _PDF_TEXT_CACHE[cache_key] = text
    return text


def _course_line_re(grade_digits: str = "34") -> re.Pattern[str]:
    """Compile a TOC matcher for Ontario codes whose grade digit is in ``grade_digits``.

    Args:
        grade_digits: Allowed fourth characters of the course code (``1``=Gr 9
            through ``4``=Gr 12). Math/science 11–12 PDFs use ``34``; HPE 9–12
            uses ``1234``.
    """
    digits = "".join(ch for ch in (grade_digits or "34") if ch in "1234") or "34"
    return re.compile(
        rf"(.+?),\s*({_PATHWAY_ALT})(?:\s+Preparation)?\s*\(\s*([A-Z]{{3}}[{digits}][A-Z0-9]{{1,2}})\s*\)",
        re.I,
    )


def extract_courses_from_pdf(
    path: Path, *, grade_digits: str = "34"
) -> list[dict[str, Any]]:
    """Extract course codes and titles from a local Ministry PDF.

    Args:
        path: Curriculum PDF. Missing files yield an empty list (never invented).
        grade_digits: Restrict matches to these course-code grade digits.

    Returns:
        Course dicts from ``extract_courses_from_pdf_text``.
    """
    if not path.is_file():
        return []
    key = f"{path.resolve()}:{path.stat().st_mtime_ns}:{grade_digits}"
    cached = _COURSE_EXTRACT_CACHE.get(key)
    if cached is not None:
        return cached
    courses = extract_courses_from_pdf_text(
        _pdf_text(path), grade_digits=grade_digits
    )
    _COURSE_EXTRACT_CACHE[key] = courses
    return courses


def _normalize_pathway_label(label: str) -> str:
    """Map PDF pathway wording onto the keys in ``_PATHWAY_MAP``."""
    compact = re.sub(r"\s+", " ", (label or "").strip())
    compact = compact.replace(" / ", "/")
    for key in _PATHWAY_MAP:
        if compact.lower() == key.lower():
            return key
    return compact


def _clean_course_name(name: str) -> str:
    """Strip TOC leaders, page numbers, and leftover dots from a PDF title."""
    cleaned = (name or "").strip(" .")
    cleaned = re.sub(r"^[\d.\s]+", "", cleaned)
    cleaned = re.sub(r".*\s{2,}", "", cleaned)
    cleaned = re.sub(r"[\s.]+$", "", cleaned)
    cleaned = re.sub(r"\s+\d+$", "", cleaned)
    if len(cleaned) > 90:
        cleaned = cleaned[-90:]
        cleaned = re.sub(r"^[^A-Za-z]+", "", cleaned)
    return cleaned.strip(" ,")


def _pathway_suffix(pathway_label: str) -> str:
    """Official pathway phrase; Open courses are not 'Open Preparation'."""
    if pathway_label == "Open":
        return "Open"
    return f"{pathway_label} Preparation"


def _title_quality(row: dict[str, Any]) -> tuple[int, int, int]:
    """Prefer TOC lines that already include Grade and avoid dotted leaders."""
    title = str(row.get("title") or "")
    return (
        1 if "Grade" in title else 0,
        0 if " . " in title or title.count(".") > 3 else 1,
        -len(title),
    )


def _parse_course_line(
    line: str,
    current_grade: int | None,
    pattern: re.Pattern[str],
) -> dict[str, Any] | None:
    """Return one course dict from a Ministry TOC-style line, or None."""
    if not line or _PREREQ_RE.match(line):
        return None
    match = pattern.search(line)
    if not match:
        return None
    name = _clean_course_name(match.group(1))
    if not name:
        return None
    pathway_label = _normalize_pathway_label(match.group(2))
    code = match.group(3).strip().upper()
    if len(code) < 5:
        return None
    grade = _CODE_GRADE_TO_YEAR.get(code[3:4]) or current_grade
    suffix = _pathway_suffix(pathway_label)
    if grade and "Grade" not in name:
        title = f"{name}, Grade {grade}, {suffix}"
    else:
        title = f"{name}, {suffix}"
    return {
        "code": code,
        "title": title,
        "grade": grade,
        "pathway": _PATHWAY_MAP.get(pathway_label, pathway_label[:1].upper()),
    }


def extract_courses_from_pdf_text(
    text: str, *, grade_digits: str = "34"
) -> list[dict[str, Any]]:
    """Parse Ministry TOC-style ``Title, Pathway Preparation (CODE)`` lines.

    Grade is taken from the course-code digit, falling back to the nearest
    preceding ``Grade 9``–``Grade 12`` heading. Open courses that omit the
    word Preparation are still captured. Adjacent wrapped lines are joined
    so a code split onto the next line still matches.

    Args:
        text: Extracted PDF text.
        grade_digits: Allowed fourth characters of the course code.

    Returns:
        Course dicts with code, title, grade, pathway.
    """
    if not text:
        return []
    pattern = _course_line_re(grade_digits)
    current_grade: int | None = None
    found: dict[str, dict[str, Any]] = {}
    cleaned_lines = [" ".join(raw.split()) for raw in text.splitlines()]
    cleaned_lines = [line for line in cleaned_lines if line]

    def _keep(row: dict[str, Any] | None) -> None:
        """Keep the higher-quality title when the same code appears twice."""
        if not row:
            return
        code = row["code"]
        previous = found.get(code)
        if previous is None or _title_quality(row) > _title_quality(previous):
            found[code] = row

    for index, line in enumerate(cleaned_lines):
        grade_match = _GRADE_HEADING_RE.search(line)
        if grade_match and len(line) < 40:
            current_grade = int(grade_match.group(1))
        # Almost every Ministry course title puts the code in parentheses; skip
        # the rest of the document so seed stays fast on large PDFs.
        if "(" in line:
            _keep(_parse_course_line(line, current_grade, pattern))
        if index + 1 < len(cleaned_lines):
            nxt = cleaned_lines[index + 1]
            if "(" not in line and "(" not in nxt:
                continue
            if not _PREREQ_RE.match(line) and not _PREREQ_RE.match(nxt):
                joined = f"{line} {nxt}"
                if len(joined) < 220:
                    _keep(_parse_course_line(joined, current_grade, pattern))
    return list(found.values())


def load_mcf3m_expectation_rows(seed_path: Path | None = None) -> list[dict[str, Any]]:
    """Flatten ``expectations_seed.json`` into school ``expectations`` rows.

    Args:
        seed_path: Override path (tests).

    Returns:
        Rows with kind/code/parent/strand/statement/verification_status.
        Empty if the seed file is missing (never fabricates statements).
    """
    path = Path(seed_path or MCF3M_EXPECTATIONS)
    if not path.is_file():
        logger.warning("MCF3M expectations seed missing at %s", path)
        return []
    seed = json.loads(path.read_text(encoding="utf-8"))
    status = str(seed.get("verification_status") or "verified_from_pdf_text_extraction")
    rows: list[dict[str, Any]] = []
    for strand in seed.get("strands") or []:
        strand_code = str(strand.get("code") or "")
        strand_name = str(strand.get("name") or strand_code)
        strand_label = f"{strand_code} {strand_name}".strip()
        for overall in strand.get("overall") or []:
            rows.append(
                {
                    "kind": "overall",
                    "code": overall.get("code"),
                    "parent_code": None,
                    "strand": strand_label,
                    "statement": overall.get("statement"),
                    "verification_status": status,
                }
            )
        for specific in strand.get("specific") or []:
            rows.append(
                {
                    "kind": "specific",
                    "code": specific.get("code"),
                    "parent_code": specific.get("overall") or specific.get("parent_code"),
                    "strand": strand_label,
                    "statement": specific.get("statement"),
                    "verification_status": status,
                }
            )
    return rows


def seed_curriculum(school: SchoolDB) -> dict[str, Any]:
    """Register documents, extract course codes from local PDFs, inherit MCF3M.

    Args:
        school: Open school database.

    Returns:
        Summary counts for logs/tests.
    """
    ONTARIO_SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "documents": 0,
        "courses": 0,
        "mcf3m_expectations": 0,
        "pdfs_extracted": [],
        "pdfs_missing": [],
    }
    math_doc_id: int | None = None
    for spec in ONTARIO_DOCUMENTS:
        rel = spec.get("local_path") or ""
        local = (REPO_ROOT / rel) if rel else None
        exists = bool(local and local.is_file())
        doc_id = school.upsert_document(
            title=spec["title"],
            jurisdiction="Ontario",
            grades=spec.get("grades"),
            subject=spec.get("subject"),
            source_url=spec.get("source_url"),
            local_path=rel if exists else rel,
        )
        summary["documents"] += 1
        if spec["subject"] == "Mathematics":
            math_doc_id = doc_id
        if spec.get("extractable") == "0":
            continue
        target = local if exists else None
        if target is None:
            summary["pdfs_missing"].append(spec["title"])
            continue
        courses = extract_courses_from_pdf(
            target, grade_digits=spec.get("code_grade_digits") or "34"
        )
        if not courses:
            summary["pdfs_missing"].append(f"{spec['title']} (no codes extracted)")
            continue
        summary["pdfs_extracted"].append(spec["subject"])
        for course in courses:
            school.upsert_ontario_course(
                course["code"],
                course["title"],
                grade=course.get("grade"),
                pathway=course.get("pathway"),
                document_id=doc_id,
                content_root=None,
                expectations_status=(
                    "verified" if course["code"] == "MCF3M" else "unverified"
                ),
            )

    # MCF3M overall/specific from the existing verified seed — not invented.
    mcf_rows = load_mcf3m_expectation_rows()
    if mcf_rows:
        if math_doc_id is None:
            math_doc_id = school.upsert_document(
                title="The Ontario Curriculum, Grades 11 and 12: Mathematics (revised)",
                jurisdiction="Ontario",
                grades="11-12",
                subject="Mathematics",
                local_path=str(MATH_CURRICULUM_PDF.relative_to(REPO_ROOT)),
            )
        if not school.get_ontario_course("MCF3M"):
            school.upsert_ontario_course(
                "MCF3M",
                "Functions and Applications, Grade 11, University/College Preparation",
                grade=11,
                pathway="M",
                document_id=math_doc_id,
                content_root=None,
                expectations_status="verified",
            )
        summary["mcf3m_expectations"] = school.replace_course_expectations(
            "MCF3M", mcf_rows, status="verified"
        )
    summary["courses"] = len(school.list_ontario_courses())
    return summary
