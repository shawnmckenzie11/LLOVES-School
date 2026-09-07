"""Parse specific (and overall) expectations from the Gr 11–12 math PDF.

Wording is copied from PyMuPDF text of the Ministry PDF. Glyphs and
superscripts may flatten; do not invent statements. Used by the local
Lesson Slides tool and Drive curriculum dump.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from paths import MATH_CURRICULUM_PDF, REPO_ROOT
except ImportError:
    from lms.paths import MATH_CURRICULUM_PDF, REPO_ROOT

COURSE_CODE_RE = re.compile(r"^[A-Z]{3}[34][A-Z0-9]$")
STRAND_RE = re.compile(
    r"(?m)^([A-D])\.\s+([A-Z][A-Z0-9 /,&'-]+?)(?:\s*$)"
)
SPEC_SPLIT_RE = re.compile(r"(?m)^(\d+)\.(\d+)\s+")
OVERALL_SPLIT_RE = re.compile(r"(?m)^(\d+)\.\s+")
SAMPLE_RE = re.compile(r"(?i)\s*Sample problem:\s*")

_HEADER_LINE_RE = re.compile(
    r"(?i)^("
    r"the ontario curriculum.*|"
    r"grade 1[12],.*|"
    r"mathematics for work and everyday life|"
    r"functions and applications|"
    r"advanced functions|"
    r"foundations for college mathematics|"
    r"mathematics of data management|"
    r"mathematics for college technology|"
    r"foundations of college mathematics|"
    r"specific expectations|"
    r"overall expectations|"
    r"by the end of this course, students will:|"
    r"mathematical process expectations|"
    r"[a-z]{3}[34][a-z0-9]|"
    r"\d{1,3}"
    r")$"
)

COURSE_TITLES = {
    "MCR3U": "Functions, Grade 11, University Preparation",
    "MCF3M": "Functions and Applications, Grade 11, University/College Preparation",
    "MBF3C": "Foundations for College Mathematics, Grade 11, College Preparation",
    "MEL3E": "Mathematics for Work and Everyday Life, Grade 11, Workplace Preparation",
    "MHF4U": "Advanced Functions, Grade 12, University Preparation",
    "MCV4U": "Calculus and Vectors, Grade 12, University Preparation",
    "MDM4U": "Mathematics of Data Management, Grade 12, University Preparation",
    "MCT4C": "Mathematics for College Technology, Grade 12, College Preparation",
    "MAP4C": "Foundations for College Mathematics, Grade 12, College Preparation",
    "MEL4E": "Mathematics for Work and Everyday Life, Grade 12, Workplace Preparation",
}


def _normalize_pdf_text(text: str) -> str:
    """Fix common PDF ligatures; keep Ministry punctuation otherwise."""
    repl = (
        ("ﬁ", "fi"),
        ("ﬂ", "fl"),
        ("ﬀ", "ff"),
        ("’", "'"),
        ("‘", "'"),
        ("“", '"'),
        ("”", '"'),
        ("–", "-"),
        ("—", "-"),
        ("\xa0", " "),
    )
    out = text
    for old, new in repl:
        out = out.replace(old, new)
    return out


def _pdf_pages(path: Path) -> list[str]:
    """Return 1-based-index-aligned list of page texts (index 0 unused)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore
    doc = fitz.open(path)
    try:
        pages = [""]
        for page in doc:
            pages.append(_normalize_pdf_text(page.get_text() or ""))
        return pages
    finally:
        doc.close()


def _course_start_pages(pages: list[str]) -> list[tuple[str, int]]:
    """Locate intro pages whose standalone line is an Ontario math code."""
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for num, text in enumerate(pages):
        if num == 0:
            continue
        for line in text.splitlines():
            code = line.strip()
            if not COURSE_CODE_RE.match(code):
                continue
            if code in seen:
                continue
            # TOC / chart pages list many codes; skip those.
            unique_codes = {
                ln.strip()
                for ln in text.splitlines()
                if COURSE_CODE_RE.match(ln.strip())
            }
            if len(unique_codes) > 2:
                continue
            seen.add(code)
            found.append((code, num))
            break
    found.sort(key=lambda row: row[1])
    return found


def _clean_statement(raw: str) -> str:
    """Drop headers, page numbers, and sample-problem tails from a blob."""
    body, *rest = SAMPLE_RE.split(raw, maxsplit=1)
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _HEADER_LINE_RE.match(stripped):
            continue
        if re.match(r"^[A-D]\.\s+[A-Z]", stripped):
            continue
        if re.match(r"^\d+\.\s+[A-Z]", stripped) and not re.match(
            r"^\d+\.\d+", stripped
        ):
            # Overall item or "1. Solving Quadratic..." section title
            if len(stripped) < 80 and not stripped[0:3].startswith("1. "):
                continue
        lines.append(stripped)
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip(" ;")
    # Column bleed: overall list after a specific on the same page.
    text = re.split(
        r"\s+\d+\.\s+(?:expand |demonstrate |solve problems|identify |"
        r"compare |describe |make connections|evaluate |simplify )",
        text,
        maxsplit=1,
    )[0]
    text = re.split(r"\s+[A-D]\.\s+[A-Z]{3,}", text, maxsplit=1)[0]
    return text.strip(" ;")


def _sample_problem(raw: str) -> str:
    """Return Sample problem text after the statement, if present."""
    parts = SAMPLE_RE.split(raw, maxsplit=1)
    if len(parts) < 2:
        return ""
    return _clean_statement(parts[1])


def _parse_specifics(text: str, strand: str) -> list[dict[str, Any]]:
    """Split a page (or span) into specific-expectation dicts."""
    matches = list(SPEC_SPLIT_RE.finditer(text))
    rows: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        overall_n, spec_n = match.group(1), match.group(2)
        try:
            overall_i, spec_i = int(overall_n), int(spec_n)
        except ValueError:
            continue
        if overall_i < 1 or overall_i > 4 or spec_i < 1 or spec_i > 20:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blob = text[start:end]
        statement = _clean_statement(blob)
        if not statement or len(statement) < 24:
            continue
        if statement.startswith("=") or statement[0].isdigit():
            continue
        example = _sample_problem(blob)
        if example and (
            "OVERALL EXPECTATIONS" in example.upper()
            or "By the end of this course" in example
        ):
            example = ""
        code = f"{strand}{overall_n}.{spec_n}"
        rows.append(
            {
                "code": code,
                "overall": f"{strand}{overall_n}",
                "statement": statement,
                "examples": [example] if example else [],
            }
        )
    return rows


def _parse_overall(text: str, strand: str) -> list[dict[str, Any]]:
    """Parse numbered overall statements in an OVERALL EXPECTATIONS block."""
    idx = text.upper().find("OVERALL EXPECTATIONS")
    if idx < 0:
        return []
    chunk = text[idx:]
    spec_at = chunk.upper().find("SPECIFIC EXPECTATIONS")
    if spec_at > 0:
        chunk = chunk[:spec_at]
    matches = list(OVERALL_SPLIT_RE.finditer(chunk))
    rows: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        n = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(chunk)
        statement = _clean_statement(chunk[start:end])
        if not statement or len(statement) < 20:
            continue
        if statement.lower().startswith("solving ") and len(statement) < 60:
            continue
        rows.append(
            {
                "code": f"{strand}{n}",
                "number": int(n),
                "statement": statement,
            }
        )
    return rows


def extract_math_11_12_from_pages(pages: list[str]) -> dict[str, dict[str, Any]]:
    """Build per-course expectation trees from already-extracted page texts.

    Args:
        pages: Index 0 unused; index 1 is PDF page 1.

    Returns:
        Map of Ontario course code to a seed-shaped dict (strands with
        overall + specific lists). Empty strands are omitted.
    """
    starts = _course_start_pages(pages)
    courses: dict[str, dict[str, Any]] = {}
    for i, (code, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(pages)
        strand = ""
        strand_name = ""
        strands: dict[str, dict[str, Any]] = {}
        pdf_pages: list[int] = []
        for num in range(start, end):
            text = pages[num]
            heading = STRAND_RE.search(text)
            if heading:
                strand = heading.group(1)
                strand_name = re.sub(r"\s+", " ", heading.group(2)).strip(" .")
                strands.setdefault(
                    strand,
                    {
                        "code": strand,
                        "name": strand_name.title(),
                        "overall": [],
                        "specific": [],
                    },
                )
                if strand_name:
                    strands[strand]["name"] = strand_name.title()
            if not strand:
                continue
            if "OVERALL EXPECTATIONS" in text.upper():
                for row in _parse_overall(text, strand):
                    existing = {
                        o["code"] for o in strands[strand]["overall"]
                    }
                    if row["code"] not in existing:
                        strands[strand]["overall"].append(row)
            if SPEC_SPLIT_RE.search(text):
                pdf_pages.append(num)
                for row in _parse_specifics(text, strand):
                    existing = {
                        s["code"] for s in strands[strand]["specific"]
                    }
                    if row["code"] not in existing:
                        strands[strand]["specific"].append(row)
        strand_list = [
            strands[key]
            for key in sorted(strands)
            if strands[key]["specific"] or strands[key]["overall"]
        ]
        courses[code] = {
            "course_code": code,
            "course_title": COURSE_TITLES.get(code, code),
            "source_pdf": "math1112currb (Ontario Curriculum Grades 11 and 12 Mathematics)",
            "source_pages_pdf": f"{start}-{end - 1}",
            "verification_status": "extracted_from_pdf_text_unverified_spot_check",
            "notes": (
                "Specific expectation wording copied from PyMuPDF text of the "
                "Ministry PDF. Superscripts and stacked fractions often flatten. "
                "Spot-check the PDF for high-stakes use. Do not treat as invented."
            ),
            "strands": strand_list,
            "specific_count": sum(len(s["specific"]) for s in strand_list),
            "overall_count": sum(len(s["overall"]) for s in strand_list),
        }
    return courses


def extract_math_11_12(
    pdf_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Extract per-course expectations from the Grades 11–12 math PDF.

    Args:
        pdf_path: Ministry PDF. Defaults to Downloads copy, then repo copy.

    Returns:
        Course-code map from ``extract_math_11_12_from_pages``.
    """
    path = pdf_path
    if path is None:
        download = Path(
            "/Users/shawnscomputer/Downloads/math1112currb (2).pdf"
        )
        path = download if download.is_file() else MATH_CURRICULUM_PDF
    if not Path(path).is_file():
        return {}
    return extract_math_11_12_from_pages(_pdf_pages(Path(path)))


def write_course_json_files(
    courses: dict[str, dict[str, Any]], dest_dir: Path
) -> list[Path]:
    """Write one JSON file per course (specific + overall) under ``dest_dir``.

    Args:
        courses: Output of ``extract_math_11_12``.
        dest_dir: Directory to create.

    Returns:
        Paths written.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for code, payload in sorted(courses.items()):
        course_dir = dest_dir / code
        course_dir.mkdir(parents=True, exist_ok=True)
        slim = {
            "course_code": payload["course_code"],
            "course_title": payload["course_title"],
            "source_pdf": payload["source_pdf"],
            "source_pages_pdf": payload["source_pages_pdf"],
            "verification_status": payload["verification_status"],
            "notes": payload["notes"],
            "specific": [],
        }
        for strand in payload.get("strands") or []:
            for spec in strand.get("specific") or []:
                slim["specific"].append(
                    {
                        "code": spec["code"],
                        "overall": spec["overall"],
                        "strand": strand.get("name") or strand.get("code"),
                        "statement": spec["statement"],
                        "examples": spec.get("examples") or [],
                    }
                )
        path = course_dir / f"{code}-specific-expectations.json"
        path.write_text(
            json.dumps(slim, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
        full = course_dir / f"{code}-expectations.json"
        full.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(full)
        md_path = course_dir / f"{code}-specific-expectations.md"
        md_path.write_text(_specific_markdown(slim), encoding="utf-8")
        written.append(md_path)
    index = {
        "source": "Ontario Curriculum Grades 11 and 12 Mathematics",
        "courses": [
            {
                "code": c["course_code"],
                "title": c["course_title"],
                "specific_count": c["specific_count"],
                "overall_count": c["overall_count"],
                "pages": c["source_pages_pdf"],
            }
            for c in (courses[k] for k in sorted(courses))
        ],
    }
    idx_path = dest_dir / "index.json"
    idx_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    written.append(idx_path)
    return written


def _specific_markdown(slim: dict[str, Any]) -> str:
    """Render one course's specific statements as Markdown for Drive review.

    Args:
        slim: Per-course specific dump (code, title, notes, specific rows).
    """
    lines = [
        f"# {slim.get('course_code')} — specific expectations",
        "",
        str(slim.get("course_title") or ""),
        "",
        f"Source: {slim.get('source_pdf') or 'Ontario Curriculum Grades 11–12 Mathematics'}"
        f", PDF pages {slim.get('source_pages_pdf') or '?'}.",
        str(slim.get("notes") or ""),
        "",
        "Authoring note: question banks and live-class notes belong in this",
        "course's Drive `banks/` and `live-notes/` folders, not in git.",
        "",
    ]
    for row in slim.get("specific") or []:
        lines.append(f"## {row.get('code')}")
        lines.append(str(row.get("statement") or "").strip())
        lines.append("")
    return "\n".join(lines) + "\n"


def load_local_specific_rows(course_code: str) -> list[dict[str, Any]]:
    """Load extracted specific rows for slide matching (Drive dump cache).

    Runtime still prefers verified ``expectations`` table rows when present.
    This dump is the authoring copy from the Ministry PDF, not invented text.

    Args:
        course_code: Ontario course code such as ``MCF3M``.
    """
    code = (course_code or "").strip().upper()
    path = default_local_dest() / code / f"{code}-specific-expectations.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for spec in payload.get("specific") or []:
        statement = str(spec.get("statement") or "").strip()
        spec_code = str(spec.get("code") or "").strip().upper()
        if not spec_code or not statement:
            continue
        rows.append(
            {
                "course_code": code,
                "kind": "specific",
                "code": spec_code,
                "parent_code": spec.get("overall"),
                "strand": spec.get("strand"),
                "statement": statement,
                "verification_status": payload.get("verification_status") or "extracted",
            }
        )
    return rows


def default_local_dest() -> Path:
    """Local dump dir (not committed): ``.local-data/curriculum``."""
    return REPO_ROOT / ".local-data" / "curriculum"


def drive_upload_text(
    *,
    access_token: str,
    parent_id: str,
    title: str,
    text: str,
    mime: str = "application/json",
    drive_mime: str | None = None,
    http: Any | None = None,
) -> dict[str, Any]:
    """Create a Drive file under ``parent_id`` (multipart upload).

    Args:
        access_token: Bearer token with Drive scope.
        parent_id: Folder id.
        title: File name.
        text: UTF-8 body.
        mime: Content type of the uploaded bytes.
        drive_mime: Drive file mime (e.g. Google Doc) when converting.
        http: ``requests`` module.

    Returns:
        Drive file resource with id and webViewLink when present.
    """
    import json as _json

    import requests as requests_lib

    session = http or requests_lib
    metadata: dict[str, Any] = {"name": title, "parents": [parent_id]}
    if drive_mime:
        metadata["mimeType"] = drive_mime
    boundary = "llovesCurriculumBoundary"
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{_json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime}; charset=UTF-8\r\n\r\n"
        f"{text}\r\n"
        f"--{boundary}--\r\n"
    )
    resp = session.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={
            "uploadType": "multipart",
            "supportsAllDrives": "true",
            "fields": "id,name,webViewLink",
        },
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body.encode("utf-8"),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json() or {}


def upload_local_dump_to_drive(
    *,
    access_token: str,
    parent_id: str | None = None,
    dest_dir: Path | None = None,
    http: Any | None = None,
    course_folders: dict[str, str] | None = None,
    skip_markdown_codes: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Upload each local JSON/Markdown dump file into the matching Drive folder.

    Markdown is converted to Google Docs for readable feedback. JSON stays JSON.
    Files live under ``dest_dir/{CODE}/``. Full ``*-expectations.json`` trees
    are not uploaded (authoring copy is the specific JSON + review Doc).

    Args:
        access_token: Bearer token with Drive scope.
        parent_id: Fallback folder when a course folder id is missing.
        dest_dir: Local dump directory.
        http: ``requests`` module.
        course_folders: Map of Ontario course code to Drive folder id.
        skip_markdown_codes: Course folders whose ``.md`` is already in Drive.

    Returns:
        Drive file resources created.
    """
    dest = dest_dir or default_local_dest()
    folders = dict(course_folders or {})
    created: list[dict[str, Any]] = []
    paths: list[Path] = []
    for course_dir in sorted(p for p in dest.iterdir() if p.is_dir()):
        paths.extend(
            p
            for p in sorted(course_dir.iterdir())
            if p.is_file()
            and (
                p.name.endswith("-specific-expectations.json")
                or (
                    p.suffix == ".md"
                    and p.name.endswith("-specific-expectations.md")
                )
            )
        )
    for path in paths:
        folder = parent_id
        if path.parent != dest:
            folder = folders.get(path.parent.name) or parent_id
        if not folder:
            continue
        if path.suffix == ".md" and path.parent.name in (skip_markdown_codes or ()):
            continue
        if path.suffix == ".md":
            created.append(
                drive_upload_text(
                    access_token=access_token,
                    parent_id=folder,
                    title=path.name,
                    text=path.read_text(encoding="utf-8"),
                    mime="text/plain",
                    drive_mime="application/vnd.google-apps.document",
                    http=http,
                )
            )
            continue
        created.append(
            drive_upload_text(
                access_token=access_token,
                parent_id=folder,
                title=path.name,
                text=path.read_text(encoding="utf-8"),
                mime="application/json",
                http=http,
            )
        )
    return created

