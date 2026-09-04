"""Course-code-generic IMSCC ingest into normalized LLOVES components.

Reads an unpacked Common Cartridge and writes ``pages`` / ``assignments`` /
``quizzes`` / ``question_banks`` / ``questions`` / ``module_outlines`` /
``module_items`` rows for one ``content_libraries`` row. Binary payloads land
in the shared content-addressed blob store, so two teachers pointing at the
same library never duplicate files.

There is no per-course branching: any Canvas export for any Ontario code goes
through the same path.
"""

from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from content_store import ContentBlobStore
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.content_store import ContentBlobStore

logger = logging.getLogger(__name__)

GOOGLE_DOC_HOSTS = {"docs.google.com", "drive.google.com"}
GOOGLE_SLIDES_PATHS = ("/presentation", "/slides")
PAGE_KIND_HTML = "html"
PAGE_KIND_PDF = "pdf"
PAGE_KIND_GDOC = "gdoc"
PAGE_KIND_GSLIDES = "gslides"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_TITLE_RE = re.compile(
    r"""<meta\s+name=["']title["']\s+content=["'](.*?)["']""", re.I | re.S
)
_META_IDENT_RE = re.compile(
    r"""<meta\s+name=["']identifier["']\s+content=["'](.*?)["']""", re.I | re.S
)


@dataclass
class IngestResult:
    """Counts written for one library ingest."""

    library_id: int
    pages: int = 0
    assignments: int = 0
    quizzes: int = 0
    question_banks: int = 0
    questions: int = 0
    outlines: int = 0
    items: int = 0
    blobs: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly summary."""
        return {
            "library_id": self.library_id,
            "pages": self.pages,
            "assignments": self.assignments,
            "quizzes": self.quizzes,
            "question_banks": self.question_banks,
            "questions": self.questions,
            "outlines": self.outlines,
            "items": self.items,
            "blobs": self.blobs,
            "skipped": self.skipped,
        }


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag name."""
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    """Return the text of the first child with a matching local name."""
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def classify_url(url: str) -> str | None:
    """Classify an external URL as a Google Doc/Slides page kind.

    Args:
        url: Absolute URL from a cartridge external-URL item.

    Returns:
        ``gslides``, ``gdoc``, or None when the URL is not Google Workspace.
    """
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host not in GOOGLE_DOC_HOSTS:
        return None
    path = (parsed.path or "").lower()
    if any(path.startswith(prefix) for prefix in GOOGLE_SLIDES_PATHS):
        return PAGE_KIND_GSLIDES
    return PAGE_KIND_GDOC


def parse_manifest_resources(unpacked: Path) -> dict[str, dict[str, Any]]:
    """Map manifest resource identifiers to type/href metadata.

    Args:
        unpacked: Root of the unpacked cartridge.

    Returns:
        ``{identifier: {"type": str, "href": str | None, "files": [str]}}``.
    """
    manifest = Path(unpacked) / "imsmanifest.xml"
    if not manifest.is_file():
        return {}
    try:
        root = ET.parse(manifest).getroot()
    except ET.ParseError:
        logger.exception("Unreadable imsmanifest.xml in %s", unpacked)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for resource in root.iter():
        if _local(resource.tag) != "resource":
            continue
        rid = resource.get("identifier")
        if not rid:
            continue
        files = [
            child.get("href")
            for child in resource
            if _local(child.tag) == "file" and child.get("href")
        ]
        out[rid] = {
            "identifier": rid,
            "type": resource.get("type") or "",
            "href": resource.get("href"),
            "files": files,
        }
    return out


def parse_module_meta(unpacked: Path) -> list[dict[str, Any]]:
    """Parse ordered modules and items from ``course_settings/module_meta.xml``.

    Args:
        unpacked: Root of the unpacked cartridge.

    Returns:
        Modules with ``identifier``, ``title``, ``position``, and ``items``.
    """
    meta = Path(unpacked) / "course_settings" / "module_meta.xml"
    if not meta.is_file():
        return []
    try:
        root = ET.parse(meta).getroot()
    except ET.ParseError:
        logger.exception("Unreadable module_meta.xml in %s", unpacked)
        return []
    modules: list[dict[str, Any]] = []
    for index, module in enumerate(
        el for el in root if _local(el.tag) == "module"
    ):
        items: list[dict[str, Any]] = []
        for holder in module:
            if _local(holder.tag) != "items":
                continue
            for position, item in enumerate(
                el for el in holder if _local(el.tag) == "item"
            ):
                items.append(
                    {
                        "identifier": item.get("identifier")
                        or f"item-{index}-{position}",
                        "title": _child_text(item, "title") or "Untitled",
                        "content_type": _child_text(item, "content_type") or "",
                        "identifierref": _child_text(item, "identifierref"),
                        "url": _child_text(item, "url"),
                        "position": int(_child_text(item, "position") or position + 1),
                    }
                )
        modules.append(
            {
                "identifier": module.get("identifier") or f"module-{index}",
                "title": _child_text(module, "title") or "Module",
                "position": int(_child_text(module, "position") or index + 1),
                "items": items,
            }
        )
    modules.sort(key=lambda mod: mod["position"])
    return modules


def _html_title(text: str, fallback: str) -> str:
    """Pick a page title from cartridge HTML meta tags."""
    for pattern in (_META_TITLE_RE, _TITLE_RE):
        match = pattern.search(text)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            if title:
                return title
    return fallback


def _html_identifier(text: str) -> str | None:
    """Return the Canvas identifier embedded in cartridge HTML, if present."""
    match = _META_IDENT_RE.search(text)
    return match.group(1).strip() if match else None


# --------------------------------------------------------------------------- #
# QTI parsing
#
# A Canvas cartridge describes the same quiz twice. The thin Common Cartridge
# copy in ``<quiz-folder>/assessment_qti.xml`` only keeps the question types
# CC v1.1 can express, and Canvas empties it entirely whenever the quiz draws
# from question banks. The Canvas-native copy in
# ``non_cc_assessments/<identifier>.xml.qti`` is the complete one: it holds
# every item, plus ``sourcebank_ref`` group draws and ``bankentry_item``
# pointers into standalone ``objectbank`` files. Item idents differ between the
# two copies, so the sources must never be merged - one is chosen.
# --------------------------------------------------------------------------- #

CC_PROFILE_TYPES = {
    "cc.multiple_choice.v0p1": "multiple_choice_question",
    "cc.multiple_response.v0p1": "multiple_answers_question",
    "cc.true_false.v0p1": "true_false_question",
    "cc.essay.v0p1": "essay_question",
    "cc.fib.v0p1": "short_answer_question",
    "cc.pattern_match.v0p1": "short_answer_question",
    "cc.text_only.v0p1": "text_only_question",
}

_RESPONSE_TAGS = {"response_lid", "response_str", "response_xy", "response_num"}


@dataclass
class QtiAssessment:
    """One ``<assessment>`` (a quiz) parsed out of a QTI file."""

    ident: str
    title: str
    items: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    entries: list[dict[str, Any]] = field(default_factory=list)

    def has_content(self) -> bool:
        """True when this copy of the quiz carries items or bank references."""
        return bool(self.items or self.groups or self.entries)


@dataclass
class QtiObjectBank:
    """One standalone ``<objectbank>`` (a Canvas question bank)."""

    ident: str
    title: str
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class QtiIndex:
    """Every Canvas-native QTI document found in one cartridge.

    Attributes:
        assessments: Quiz ident -> ``(file path, parsed assessment)``.
        banks: Object-bank ident -> parsed bank.
    """

    assessments: dict[str, tuple[Path, QtiAssessment]] = field(default_factory=dict)
    banks: dict[str, QtiObjectBank] = field(default_factory=dict)

    def bank_items(self, sourcebank_ref: str) -> list[dict[str, Any]]:
        """Return the parsed items of a referenced object bank (empty if absent).

        Args:
            sourcebank_ref: Ident from ``<sourcebank_ref>``.
        """
        bank = self.banks.get(str(sourcebank_ref))
        return list(bank.items) if bank else []

    def bank_item(self, sourcebank_ref: str, item_ref: str) -> dict[str, Any] | None:
        """Return one item pinned by a ``<bankentry_item>``, if resolvable.

        Args:
            sourcebank_ref: Owning object-bank ident.
            item_ref: Item ident inside that bank.
        """
        for item in self.bank_items(sourcebank_ref):
            if item.get("ident") == str(item_ref):
                return item
        return None


def _qti_meta_fields(element: ET.Element) -> dict[str, str]:
    """Flatten ``qtimetadatafield`` label/entry pairs under an element.

    Args:
        element: Any element containing ``<qtimetadata>`` descendants.

    Returns:
        ``{fieldlabel: fieldentry}``; later duplicates win.
    """
    out: dict[str, str] = {}
    for field_el in element.iter():
        if _local(field_el.tag) != "qtimetadatafield":
            continue
        label = _child_text(field_el, "fieldlabel")
        if not label:
            continue
        out[label] = _child_text(field_el, "fieldentry") or ""
    return out


def _mattext_html(material: ET.Element) -> str:
    """Return the HTML carried by one ``<material>`` element.

    ``mattext`` bodies are escaped HTML when ``texttype`` is ``text/html`` and
    literal prose otherwise, so plain text is escaped here to keep every stored
    fragment safe to drop straight into a page.

    Args:
        material: A ``<material>`` element.
    """
    parts: list[str] = []
    for child in material:
        if _local(child.tag) != "mattext":
            continue
        text = (child.text or "").strip()
        if not text:
            continue
        if (child.get("texttype") or "text/html").lower() == "text/html":
            parts.append(text)
        else:
            parts.append(html.escape(text))
    return "\n".join(parts)


def _presentation_stem(presentation: ET.Element) -> str:
    """Collect the question stem, ignoring answer-choice material.

    Args:
        presentation: The item's ``<presentation>`` element.
    """
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        """Append stem material, stopping at any response subtree."""
        for child in node:
            name = _local(child.tag)
            if name in _RESPONSE_TAGS:
                continue
            if name == "material":
                parts.append(_mattext_html(child))
            elif name in {"flow", "flow_mat"}:
                walk(child)

    walk(presentation)
    return "\n".join(part for part in parts if part)


def _response_groups(presentation: ET.Element) -> list[dict[str, Any]]:
    """Parse each response group (choice list or free-text blank) of an item.

    Args:
        presentation: The item's ``<presentation>`` element.

    Returns:
        Groups in document order with ``ident``, ``label_html``, ``kind``
        (``choice`` or ``text``), and ``choices``.
    """
    groups: list[dict[str, Any]] = []
    for node in presentation.iter():
        if _local(node.tag) not in _RESPONSE_TAGS:
            continue
        label = ""
        for child in node:
            if _local(child.tag) == "material":
                label = _mattext_html(child)
        choices: list[dict[str, Any]] = []
        for render in node.iter():
            if _local(render.tag) != "render_choice":
                continue
            for label_el in render:
                if _local(label_el.tag) != "response_label":
                    continue
                text = ""
                for material in label_el:
                    if _local(material.tag) == "material":
                        text = _mattext_html(material)
                choices.append({"id": label_el.get("ident") or "", "html": text})
        groups.append(
            {
                "ident": node.get("ident") or "",
                "label_html": label,
                "kind": "choice" if choices else "text",
                "choices": choices,
            }
        )
    return groups


def _condition_awards_credit(condition: ET.Element) -> bool:
    """True when a ``<respcondition>`` sets a positive SCORE.

    Canvas emits one scoring condition per correct answer plus feedback-only
    conditions; only the scoring ones identify correct responses.

    Args:
        condition: A ``<respcondition>`` element.
    """
    for child in condition:
        if _local(child.tag) != "setvar":
            continue
        if (child.get("varname") or "").upper() != "SCORE":
            continue
        try:
            if float((child.text or "0").strip()) > 0:
                return True
        except ValueError:
            continue
    return False


def _collect_varequal(
    node: ET.Element, negated: bool, out: dict[str, list[str]]
) -> None:
    """Gather non-negated ``<varequal>`` values by response ident.

    ``multiple_answers_question`` scoring wraps the distractors in ``<not>``,
    so negated branches are skipped rather than read as correct answers.

    Args:
        node: Element to walk (usually ``<conditionvar>``).
        negated: True while inside a ``<not>`` branch.
        out: Accumulator ``{respident: [values]}``.
    """
    for child in node:
        name = _local(child.tag)
        if name == "not":
            _collect_varequal(child, not negated, out)
        elif name == "varequal":
            if not negated:
                rid = child.get("respident") or "response1"
                value = (child.text or "").strip()
                if value not in out.setdefault(rid, []):
                    out[rid].append(value)
        else:
            _collect_varequal(child, negated, out)


def _correct_by_respident(item: ET.Element) -> dict[str, list[str]]:
    """Map each response ident to its correct values, from ``<resprocessing>``.

    Args:
        item: A QTI ``<item>`` element.
    """
    out: dict[str, list[str]] = {}
    for processing in item.iter():
        if _local(processing.tag) != "resprocessing":
            continue
        for condition in processing:
            if _local(condition.tag) != "respcondition":
                continue
            if not _condition_awards_credit(condition):
                continue
            for var in condition:
                if _local(var.tag) == "conditionvar":
                    _collect_varequal(var, False, out)
    return out


def _points_possible(raw: str | None) -> float | None:
    """Parse a ``points_possible`` metadata entry into a float, or None."""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def parse_qti_item(item: ET.Element) -> dict[str, Any]:
    """Parse one QTI ``<item>`` into a storable question record.

    Args:
        item: A QTI ``<item>`` element.

    Returns:
        ``{"ident", "title", "item_type", "payload"}`` where ``payload`` holds
        ``stem_html``, ``points_possible``, and - depending on the type -
        ``choices`` (each flagged ``correct``), ``correct_answers``, or
        ``blanks`` for matching / multi-blank items.
    """
    meta: dict[str, str] = {}
    for holder in item:
        if _local(holder.tag) == "itemmetadata":
            meta = _qti_meta_fields(holder)
            break
    item_type = meta.get("question_type") or CC_PROFILE_TYPES.get(
        meta.get("cc_profile", ""), ""
    )

    presentation: ET.Element | None = None
    for holder in item:
        if _local(holder.tag) == "presentation":
            presentation = holder
            break

    stem = _presentation_stem(presentation) if presentation is not None else ""
    groups = _response_groups(presentation) if presentation is not None else []
    correct = _correct_by_respident(item)

    payload: dict[str, Any] = {
        "stem_html": stem,
        "points_possible": _points_possible(meta.get("points_possible")),
    }

    def _correct_for(group: dict[str, Any]) -> list[str]:
        """Correct values for one group, tolerating a bare ``response1``."""
        return correct.get(group["ident"]) or (
            correct.get("response1", []) if len(groups) == 1 else []
        )

    if len(groups) == 1 and groups[0]["kind"] == "choice":
        wanted = set(_correct_for(groups[0]))
        payload["choices"] = [
            {**choice, "correct": choice["id"] in wanted}
            for choice in groups[0]["choices"]
        ]
        payload["correct_ids"] = [c for c in wanted if c]
    elif len(groups) == 1:
        payload["correct_answers"] = [v for v in _correct_for(groups[0]) if v]
    elif groups:
        blanks = []
        for group in groups:
            values = set(_correct_for(group))
            blanks.append(
                {
                    "label_html": group["label_html"],
                    "choices": [
                        {**choice, "correct": choice["id"] in values}
                        for choice in group["choices"]
                    ],
                    "correct_answers": (
                        [] if group["choices"] else [v for v in values if v]
                    ),
                }
            )
        payload["blanks"] = blanks

    return {
        "ident": item.get("ident") or "",
        "title": item.get("title") or "",
        "item_type": item_type,
        "payload": payload,
    }


def _parse_selection(section: ET.Element, holder: ET.Element) -> list[dict[str, Any]]:
    """Parse ``<selection_ordering>`` bank draws inside one section.

    Args:
        section: Owning ``<section>`` (its title names the group).
        holder: The ``<selection_ordering>`` element.

    Returns:
        Group descriptors with ``sourcebank_ref``, ``pick``, ``points_per_item``.
    """
    groups: list[dict[str, Any]] = []
    for selection in holder:
        if _local(selection.tag) != "selection":
            continue
        ref = _child_text(selection, "sourcebank_ref")
        if not ref:
            continue
        pick = _child_text(selection, "selection_number")
        per_item: str | None = None
        for extension in selection:
            if _local(extension.tag) == "selection_extension":
                per_item = _child_text(extension, "points_per_item")
        groups.append(
            {
                "sourcebank_ref": ref,
                "title": section.get("title") or "Group",
                "pick": int(pick) if (pick or "").isdigit() else None,
                "points_per_item": _points_possible(per_item),
            }
        )
    return groups


def _parse_section(section: ET.Element, assessment: QtiAssessment) -> None:
    """Walk a ``<section>`` tree, collecting items, bank draws, and pins.

    Args:
        section: A QTI ``<section>`` element.
        assessment: Assessment being filled in place.
    """
    for child in section:
        name = _local(child.tag)
        if name == "item":
            assessment.items.append(parse_qti_item(child))
        elif name == "section":
            _parse_section(child, assessment)
        elif name == "selection_ordering":
            assessment.groups.extend(_parse_selection(section, child))
        elif name == "bankentry_item":
            ref = child.get("sourcebank_ref") or ""
            item_ref = child.get("item_ref") or ""
            if ref and item_ref:
                assessment.entries.append(
                    {
                        "sourcebank_ref": ref,
                        "item_ref": item_ref,
                        "points_possible": _points_possible(
                            child.get("points_possible")
                        ),
                    }
                )


def parse_qti_file(path: Path) -> tuple[list[QtiAssessment], list[QtiObjectBank]]:
    """Parse one QTI document into its assessments and object banks.

    Args:
        path: A ``.qti`` / ``assessment_qti.xml`` file.

    Returns:
        ``(assessments, object_banks)``; both empty when the file is unreadable.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        logger.warning("Unreadable QTI document %s", path)
        return [], []
    assessments: list[QtiAssessment] = []
    banks: list[QtiObjectBank] = []
    for node in root.iter():
        name = _local(node.tag)
        if name == "assessment":
            found = QtiAssessment(
                ident=node.get("ident") or "",
                title=node.get("title") or "",
            )
            for child in node:
                if _local(child.tag) == "section":
                    _parse_section(child, found)
            assessments.append(found)
        elif name == "objectbank":
            meta = {}
            for child in node:
                if _local(child.tag) == "qtimetadata":
                    meta = _qti_meta_fields(child)
                    break
            banks.append(
                QtiObjectBank(
                    ident=node.get("ident") or "",
                    title=meta.get("bank_title") or (node.get("ident") or "Bank"),
                    items=[
                        parse_qti_item(child)
                        for child in node
                        if _local(child.tag) == "item"
                    ],
                )
            )
    return assessments, banks


def build_qti_index(unpacked: Path) -> QtiIndex:
    """Index every Canvas-native QTI document in an unpacked cartridge.

    Canvas writes these as ``non_cc_assessments/<ident>.xml.qti``, but the
    lookup is by parsed ident rather than file name so other exporters that
    name files differently still resolve.

    Args:
        unpacked: Root of the unpacked cartridge.
    """
    index = QtiIndex()
    root = Path(unpacked)
    if not root.is_dir():
        return index
    for path in sorted(root.rglob("*.qti")):
        if not path.is_file():
            continue
        assessments, banks = parse_qti_file(path)
        for assessment in assessments:
            if assessment.ident:
                index.assessments[assessment.ident] = (path, assessment)
        for bank in banks:
            if bank.ident:
                index.banks[bank.ident] = bank
    return index


def resolve_assessment_items(
    assessment: QtiAssessment, index: QtiIndex
) -> list[dict[str, Any]]:
    """Flatten a quiz into the questions a teacher would actually see.

    Inline items come first in document order, then each pinned
    ``bankentry_item``, then every item of each bank the quiz draws from. Bank
    draws are expanded in full because the cartridge records only the draw
    size, not which items a given student received.

    Args:
        assessment: Parsed assessment.
        index: Cartridge-wide QTI index used to resolve bank references.

    Returns:
        Question records with ``position`` set in ``payload``, deduplicated by
        item ident.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(record: dict[str, Any], **extra: Any) -> None:
        """Append one question record, skipping idents already collected."""
        ident = str(record.get("ident") or "")
        if ident and ident in seen:
            return
        if ident:
            seen.add(ident)
        payload = dict(record.get("payload") or {})
        payload.update({k: v for k, v in extra.items() if v is not None})
        payload["position"] = len(out) + 1
        out.append({**record, "payload": payload})

    for item in assessment.items:
        add(item)
    for entry in assessment.entries:
        found = index.bank_item(entry["sourcebank_ref"], entry["item_ref"])
        if found is None:
            continue
        bank = index.banks.get(entry["sourcebank_ref"])
        add(
            found,
            bank_ref=entry["sourcebank_ref"],
            bank_title=bank.title if bank else None,
        )
    for group in assessment.groups:
        bank = index.banks.get(group["sourcebank_ref"])
        if bank is None:
            continue
        for item in bank.items:
            add(
                item,
                bank_ref=group["sourcebank_ref"],
                bank_title=bank.title,
                group_title=group.get("title"),
                group_pick=group.get("pick"),
            )
    return out


class LibraryIngestor:
    """Write normalized components for one shared content library."""

    def __init__(self, db: Any, data_dir: Path) -> None:
        """Create an ingestor.

        Args:
            db: ``SchoolDB``/``LovesDB`` with component tables.
            data_dir: LMS data volume holding the blob store.
        """
        self.db = db
        self.data_dir = Path(data_dir)
        self.store = ContentBlobStore(self.data_dir, db)
        self.qti = QtiIndex()

    def ingest(self, library_id: int, unpacked: Path) -> IngestResult:
        """Ingest an unpacked cartridge into component tables.

        Safe to re-run: rows are keyed by ``(library_id, import_key)`` and
        upserted, and blobs are content addressed.

        Args:
            library_id: ``content_libraries.id``.
            unpacked: Root of the unpacked cartridge.

        Returns:
            Counts of written components.
        """
        unpacked = Path(unpacked)
        result = IngestResult(library_id=int(library_id))
        if not unpacked.is_dir():
            result.skipped.append("unpacked directory missing")
            return result

        resources = parse_manifest_resources(unpacked)
        href_to_page: dict[str, int] = {}
        resource_to_component: dict[str, tuple[str, int]] = {}

        self.qti = build_qti_index(unpacked)
        self._ingest_wiki_pages(library_id, unpacked, result, href_to_page)
        self._ingest_attachments(library_id, unpacked, result, href_to_page)
        self._ingest_folders(library_id, unpacked, result, resource_to_component)
        self._ingest_object_banks(library_id, result)

        for rid, meta in resources.items():
            href = meta.get("href") or ""
            if href in href_to_page:
                resource_to_component[rid] = ("page", href_to_page[href])
                continue
            folder = href.split("/", 1)[0] if "/" in href else href
            if folder and folder in resource_to_component:
                resource_to_component[rid] = resource_to_component[folder]

        self._ingest_outline(
            library_id, unpacked, result, resource_to_component, href_to_page
        )
        return result

    def _ingest_wiki_pages(
        self,
        library_id: int,
        unpacked: Path,
        result: IngestResult,
        href_to_page: dict[str, int],
    ) -> None:
        """Store ``wiki_content/*.html`` as HTML pages."""
        wiki = unpacked / "wiki_content"
        if not wiki.is_dir():
            return
        for path in sorted(wiki.rglob("*.html")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                result.skipped.append(f"unreadable page {path.name}")
                continue
            rel = path.relative_to(unpacked).as_posix()
            import_key = _html_identifier(text) or rel
            page_id = self._upsert_page(
                library_id,
                import_key=import_key,
                kind=PAGE_KIND_HTML,
                title=_html_title(text, path.stem.replace("-", " ").title()),
                html_text=text,
            )
            href_to_page[rel] = page_id
            result.pages += 1

    def _ingest_attachments(
        self,
        library_id: int,
        unpacked: Path,
        result: IngestResult,
        href_to_page: dict[str, int],
    ) -> None:
        """Index cartridge files as blobs; PDFs also become viewable pages.

        Every file is recorded in ``library_files`` by its cartridge-relative
        path so page HTML can resolve ``web_resources/...`` references without
        an unpacked tree on disk.
        """
        web = unpacked / "web_resources"
        if not web.is_dir():
            return
        for path in sorted(web.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(unpacked).as_posix()
            try:
                stored = self.store.put_path(path)
            except OSError:
                result.skipped.append(f"unreadable file {path.name}")
                continue
            result.blobs += 1
            with self.db._lock:  # noqa: SLF001 - shared connection lock
                self.db.conn.execute(
                    """
                    INSERT INTO library_files (
                        library_id, relpath, blob_sha, created_at
                    )
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(library_id, relpath) DO UPDATE SET
                        blob_sha = excluded.blob_sha
                    """,
                    (int(library_id), rel, stored.sha256),
                )
                self.db.conn.commit()
            if path.suffix.lower() != ".pdf":
                continue
            page_id = self._upsert_page(
                library_id,
                import_key=rel,
                kind=PAGE_KIND_PDF,
                title=path.stem,
                blob_sha=stored.sha256,
            )
            href_to_page[rel] = page_id
            result.pages += 1

    def _ingest_folders(
        self,
        library_id: int,
        unpacked: Path,
        result: IngestResult,
        resource_to_component: dict[str, tuple[str, int]],
    ) -> None:
        """Ingest assignment and quiz folders (``g<hex>/``)."""
        for folder in sorted(p for p in unpacked.iterdir() if p.is_dir()):
            settings = folder / "assignment_settings.xml"
            quiz_meta = folder / "assessment_meta.xml"
            if settings.is_file():
                component = self._ingest_assignment(library_id, folder, settings, result)
                if component:
                    resource_to_component[folder.name] = component
            elif quiz_meta.is_file():
                component = self._ingest_quiz(library_id, folder, quiz_meta, result)
                if component:
                    resource_to_component[folder.name] = component

    def _ingest_assignment(
        self,
        library_id: int,
        folder: Path,
        settings: Path,
        result: IngestResult,
    ) -> tuple[str, int] | None:
        """Insert one assignment from ``assignment_settings.xml``."""
        try:
            root = ET.parse(settings).getroot()
        except ET.ParseError:
            result.skipped.append(f"unreadable assignment {folder.name}")
            return None
        title = _child_text(root, "title") or folder.name
        points = _child_text(root, "points_possible")
        body = ""
        for candidate in sorted(folder.glob("*.html")):
            body = candidate.read_text(encoding="utf-8", errors="replace")
            break
        payload = {
            key: _child_text(root, key)
            for key in (
                "submission_types",
                "grading_type",
                "workflow_state",
                "due_at",
            )
            if _child_text(root, key)
        }
        with self.db._lock:  # noqa: SLF001 - shared connection lock
            cur = self.db.conn.execute(
                """
                INSERT INTO assignments (
                    library_id, import_key, title, body_html, points,
                    settings_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(library_id, import_key) DO UPDATE SET
                    title = excluded.title,
                    body_html = excluded.body_html,
                    points = excluded.points,
                    settings_json = excluded.settings_json
                RETURNING id
                """,
                (
                    int(library_id),
                    root.get("identifier") or folder.name,
                    title,
                    body or None,
                    float(points) if points else None,
                    json.dumps(payload),
                ),
            )
            row = cur.fetchone()
            self.db.conn.commit()
        result.assignments += 1
        return ("assignment", int(row["id"]))

    def _ingest_quiz(
        self,
        library_id: int,
        folder: Path,
        meta: Path,
        result: IngestResult,
    ) -> tuple[str, int] | None:
        """Insert one quiz plus its QTI question bank, when parseable."""
        try:
            root = ET.parse(meta).getroot()
        except ET.ParseError:
            result.skipped.append(f"unreadable quiz {folder.name}")
            return None
        identifier = root.get("identifier") or folder.name
        title = _child_text(root, "title") or folder.name
        payload: dict[str, Any] = {
            key: _child_text(root, key)
            for key in (
                "quiz_type",
                "points_possible",
                "time_limit",
                "workflow_state",
                "due_at",
            )
            if _child_text(root, key)
        }
        description = _child_text(root, "description")
        qti, assessment = self._find_qti(folder, identifier)
        questions = (
            resolve_assessment_items(assessment, self.qti)
            if assessment is not None
            else []
        )
        if assessment is not None and assessment.groups:
            payload["question_groups"] = [
                {
                    **group,
                    "available": len(self.qti.bank_items(group["sourcebank_ref"])),
                }
                for group in assessment.groups
            ]
        qti_sha = None
        if qti is not None:
            try:
                qti_sha = self.store.put_path(qti, mime="application/xml").sha256
                result.blobs += 1
            except OSError:
                result.skipped.append(f"unreadable QTI {folder.name}")
        if description:
            payload["description_html"] = description
        with self.db._lock:  # noqa: SLF001 - shared connection lock
            cur = self.db.conn.execute(
                """
                INSERT INTO quizzes (
                    library_id, import_key, title, settings_json,
                    qti_blob_sha, created_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(library_id, import_key) DO UPDATE SET
                    title = excluded.title,
                    settings_json = excluded.settings_json,
                    qti_blob_sha = excluded.qti_blob_sha
                RETURNING id
                """,
                (
                    int(library_id),
                    identifier,
                    title,
                    json.dumps(payload),
                    qti_sha,
                ),
            )
            row = cur.fetchone()
            self.db.conn.commit()
        result.quizzes += 1
        if qti is not None or questions:
            self._ingest_bank(
                library_id,
                f"bank:{identifier}",
                title,
                questions,
                result,
                settings={
                    "kind": "quiz",
                    "quiz_import_key": identifier,
                    "source": qti.name if qti is not None else "",
                    "groups": payload.get("question_groups", []),
                },
            )
        return ("quiz", int(row["id"]))

    def _find_qti(
        self, folder: Path, identifier: str
    ) -> tuple[Path | None, QtiAssessment | None]:
        """Pick the QTI copy that actually holds this quiz's questions.

        The Canvas-native ``non_cc_assessments`` copy wins whenever it carries
        items or bank references; the thin in-folder Common Cartridge copy is
        the fallback. They are never merged, because Canvas assigns different
        item idents in each copy and a union would double every question.

        Args:
            folder: Quiz folder (``g<hex>/``).
            identifier: Quiz identifier from ``assessment_meta.xml``.

        Returns:
            ``(chosen file, parsed assessment)``; either may be None.
        """
        native = self.qti.assessments.get(identifier)
        if native is not None and native[1].has_content():
            return native

        local = folder / "assessment_qti.xml"
        if local.is_file():
            assessments, _banks = parse_qti_file(local)
            for assessment in assessments:
                if assessment.ident in {identifier, ""} and assessment.has_content():
                    return local, assessment
            if native is not None:
                return native
            return local, assessments[0] if assessments else None
        return native if native is not None else (None, None)

    def _ingest_object_banks(self, library_id: int, result: IngestResult) -> None:
        """Store each standalone Canvas question bank found in the cartridge.

        Quizzes that draw from banks own an aggregate bank of their own; these
        rows are the underlying named banks (``bank_title`` in the QTI), so a
        teacher can browse the source pool directly.

        Args:
            library_id: ``content_libraries.id``.
            result: Running counts.
        """
        for ident, bank in sorted(self.qti.banks.items()):
            self._ingest_bank(
                library_id,
                f"objectbank:{ident}",
                bank.title,
                [
                    {**item, "payload": {**item["payload"], "position": index + 1}}
                    for index, item in enumerate(bank.items)
                ],
                result,
                settings={"kind": "objectbank", "bank_ident": ident},
            )

    def _ingest_bank(
        self,
        library_id: int,
        import_key: str,
        title: str,
        questions: list[dict[str, Any]],
        result: IngestResult,
        *,
        settings: dict[str, Any] | None = None,
    ) -> int:
        """Upsert one question bank and the questions it holds.

        Questions that vanished from the cartridge since a previous ingest are
        deleted, so re-importing a trimmed quiz shrinks the bank instead of
        leaving orphans behind.

        Args:
            library_id: ``content_libraries.id``.
            import_key: Stable bank key (``bank:<quiz>`` or ``objectbank:<id>``).
            title: Bank title shown to staff.
            questions: Parsed question records from :func:`parse_qti_item`.
            result: Running counts.
            settings: Extra provenance stored in ``settings_json``.

        Returns:
            ``question_banks.id``.
        """
        with self.db._lock:  # noqa: SLF001 - shared connection lock
            cur = self.db.conn.execute(
                """
                INSERT INTO question_banks (
                    library_id, import_key, title, settings_json, created_at
                )
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(library_id, import_key) DO UPDATE SET
                    title = excluded.title,
                    settings_json = excluded.settings_json
                RETURNING id
                """,
                (
                    int(library_id),
                    import_key,
                    title,
                    json.dumps(settings or {}),
                ),
            )
            bank_id = int(cur.fetchone()["id"])
            self.db.conn.commit()
        result.question_banks += 1

        keys: list[str] = []
        for index, question in enumerate(questions):
            ident = str(question.get("ident") or "") or f"{import_key}-q{index + 1}"
            keys.append(ident)
            with self.db._lock:  # noqa: SLF001 - shared connection lock
                self.db.conn.execute(
                    """
                    INSERT INTO questions (
                        bank_id, import_key, item_type, title,
                        payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(bank_id, import_key) DO UPDATE SET
                        item_type = excluded.item_type,
                        title = excluded.title,
                        payload_json = excluded.payload_json
                    """,
                    (
                        bank_id,
                        ident,
                        str(question.get("item_type") or ""),
                        str(question.get("title") or ""),
                        json.dumps(question.get("payload") or {}),
                    ),
                )
                self.db.conn.commit()
            result.questions += 1
        self._prune_questions(bank_id, keys)
        return bank_id

    def _prune_questions(self, bank_id: int, keep: list[str]) -> None:
        """Delete bank questions no longer present in the cartridge.

        Args:
            bank_id: ``question_banks.id``.
            keep: Import keys written by the current ingest.
        """
        with self.db._lock:  # noqa: SLF001 - shared connection lock
            if keep:
                placeholders = ",".join("?" for _ in keep)
                self.db.conn.execute(
                    "DELETE FROM questions WHERE bank_id = ? "
                    f"AND import_key NOT IN ({placeholders})",
                    (int(bank_id), *keep),
                )
            else:
                self.db.conn.execute(
                    "DELETE FROM questions WHERE bank_id = ?", (int(bank_id),)
                )
            self.db.conn.commit()

    def _ingest_outline(
        self,
        library_id: int,
        unpacked: Path,
        result: IngestResult,
        resource_to_component: dict[str, tuple[str, int]],
        href_to_page: dict[str, int],
    ) -> None:
        """Write the module outline that Modules will render."""
        for module in parse_module_meta(unpacked):
            with self.db._lock:  # noqa: SLF001 - shared connection lock
                cur = self.db.conn.execute(
                    """
                    INSERT INTO module_outlines (
                        library_id, import_key, title, position, created_at
                    )
                    VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(library_id, import_key) DO UPDATE SET
                        title = excluded.title,
                        position = excluded.position
                    RETURNING id
                    """,
                    (
                        int(library_id),
                        module["identifier"],
                        module["title"],
                        int(module["position"]),
                    ),
                )
                outline_id = int(cur.fetchone()["id"])
                self.db.conn.commit()
            result.outlines += 1

            for item in module["items"]:
                component_type = "unsupported"
                component_id: int | None = None
                url = item.get("url")
                ref = item.get("identifierref")
                if item["content_type"] == "ContextModuleSubHeader":
                    component_type = "header"
                elif url and classify_url(url):
                    kind = classify_url(url)
                    assert kind is not None
                    component_id = self._upsert_page(
                        library_id,
                        import_key=item["identifier"],
                        kind=kind,
                        title=item["title"],
                        url=url,
                    )
                    component_type = "page"
                    result.pages += 1
                elif ref and ref in resource_to_component:
                    component_type, component_id = resource_to_component[ref]
                elif ref and ref in href_to_page:
                    component_type, component_id = ("page", href_to_page[ref])

                with self.db._lock:  # noqa: SLF001 - shared connection lock
                    self.db.conn.execute(
                        """
                        INSERT INTO module_items (
                            outline_id, import_key, title, position,
                            component_type, component_id, source_type,
                            source_href, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(outline_id, import_key) DO UPDATE SET
                            title = excluded.title,
                            position = excluded.position,
                            component_type = excluded.component_type,
                            component_id = excluded.component_id,
                            source_type = excluded.source_type,
                            source_href = excluded.source_href
                        """,
                        (
                            outline_id,
                            item["identifier"],
                            item["title"],
                            int(item["position"]),
                            component_type,
                            component_id,
                            item["content_type"],
                            url,
                        ),
                    )
                    self.db.conn.commit()
                result.items += 1

    def _upsert_page(
        self,
        library_id: int,
        *,
        import_key: str,
        kind: str,
        title: str,
        html_text: str | None = None,
        blob_sha: str | None = None,
        url: str | None = None,
    ) -> int:
        """Insert or refresh one page component and return its id."""
        with self.db._lock:  # noqa: SLF001 - shared connection lock
            cur = self.db.conn.execute(
                """
                INSERT INTO pages (
                    library_id, import_key, kind, title, html_text,
                    blob_sha, url, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(library_id, import_key) DO UPDATE SET
                    kind = excluded.kind,
                    title = excluded.title,
                    html_text = excluded.html_text,
                    blob_sha = excluded.blob_sha,
                    url = excluded.url
                RETURNING id
                """,
                (int(library_id), import_key, kind, title, html_text, blob_sha, url),
            )
            row = cur.fetchone()
            self.db.conn.commit()
        return int(row["id"])


def ingest_library(db: Any, data_dir: Path, library_id: int, unpacked: Path) -> dict[str, Any]:
    """Ingest one unpacked cartridge for a shared library.

    Args:
        db: School database with component tables.
        data_dir: LMS data volume (blob store root).
        library_id: ``content_libraries.id``.
        unpacked: Root of the unpacked cartridge.

    Returns:
        Summary counts from :class:`IngestResult`.
    """
    return LibraryIngestor(db, data_dir).ingest(int(library_id), Path(unpacked)).as_dict()
