#!/usr/bin/env python3
"""Build a lightweight inventory of a Canvas IMSCC course export.

Reads either an unpacked working tree or the ``.imscc`` ZIP directly, then
writes ``inventory.json`` and ``INVENTORY.md`` under ``courses/<CODE>/canvas/``.
Those inventory files are meant to be committed so agents can see course
structure without the large unpacked media tree.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMSCC = None  # pass --imscc; packs are not in this repo
DEFAULT_UNPACKED = ROOT / "lms" / "data" / "imscc-unpacked"
DEFAULT_OUT_DIR = ROOT / "lms" / "data"

CC_NS = "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1"
CANVAS_NS = "http://canvas.instructure.com/xsd/cccv1p0"
LOM_NS = "http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest"

NS = {
    "cc": CC_NS,
    "canvas": CANVAS_NS,
    "lom": LOM_NS,
}


class CourseSource:
    """Read files from either an unpacked directory or an IMSCC ZIP."""

    def __init__(self, root: Path | None = None, archive: Path | None = None) -> None:
        """Initialize a reader over ``root`` and/or ``archive``.

        Args:
            root: Unpacked course directory (preferred when present).
            archive: Path to ``.imscc`` ZIP used as fallback or primary source.
        """
        self.root = root
        self.archive = archive
        self._zip: zipfile.ZipFile | None = None
        if archive is not None and archive.is_file():
            self._zip = zipfile.ZipFile(archive, "r")

    def close(self) -> None:
        """Close any open ZIP handle."""
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> CourseSource:
        """Enter context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit context manager and close resources."""
        self.close()

    def exists(self, rel: str) -> bool:
        """Return True if ``rel`` exists in the unpacked tree or ZIP."""
        if self.root is not None and (self.root / rel).is_file():
            return True
        if self._zip is not None:
            try:
                self._zip.getinfo(rel)
                return True
            except KeyError:
                return False
        return False

    def read_bytes(self, rel: str) -> bytes:
        """Read a relative path from unpacked tree or ZIP.

        Raises:
            FileNotFoundError: If the path is not found in either source.
        """
        if self.root is not None:
            path = self.root / rel
            if path.is_file():
                return path.read_bytes()
        if self._zip is not None:
            try:
                return self._zip.read(rel)
            except KeyError as exc:
                raise FileNotFoundError(rel) from exc
        raise FileNotFoundError(rel)

    def read_text(self, rel: str) -> str:
        """Read a relative path as UTF-8 text (replacing bad bytes)."""
        return self.read_bytes(rel).decode("utf-8", errors="replace")

    def list_prefix(self, prefix: str) -> list[str]:
        """List file paths under ``prefix`` (directories excluded)."""
        found: list[str] = []
        if self.root is not None and (self.root / prefix).exists():
            base = self.root / prefix
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    found.append(str(path.relative_to(self.root)).replace("\\", "/"))
        elif self._zip is not None:
            for name in self._zip.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    found.append(name)
        return found


def local(tag: str) -> str:
    """Strip an XML Clark-notation tag to its local name."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def text_of(el: ET.Element | None, child: str, ns: str = CANVAS_NS) -> str | None:
    """Return text of a namespaced child element, or None."""
    if el is None:
        return None
    node = el.find(f"{{{ns}}}{child}")
    if node is None or node.text is None:
        return None
    return node.text.strip()


def parse_course_title(source: CourseSource) -> dict[str, Any]:
    """Extract course title/code from ``course_settings/course_settings.xml``."""
    info: dict[str, Any] = {"title": None, "course_code": None, "identifier": None}
    if not source.exists("course_settings/course_settings.xml"):
        return info
    root = ET.fromstring(source.read_bytes("course_settings/course_settings.xml"))
    info["identifier"] = root.get("identifier")
    info["title"] = text_of(root, "title")
    info["course_code"] = text_of(root, "course_code")
    return info


def parse_manifest_meta(source: CourseSource) -> dict[str, Any]:
    """Extract schema and title metadata from ``imsmanifest.xml``."""
    meta: dict[str, Any] = {
        "schema": None,
        "schemaversion": None,
        "manifest_identifier": None,
        "title": None,
        "export_date": None,
    }
    if not source.exists("imsmanifest.xml"):
        return meta
    root = ET.fromstring(source.read_bytes("imsmanifest.xml"))
    meta["manifest_identifier"] = root.get("identifier")
    schema = root.find(f"{{{CC_NS}}}metadata/{{{CC_NS}}}schema")
    version = root.find(f"{{{CC_NS}}}metadata/{{{CC_NS}}}schemaversion")
    if schema is not None and schema.text:
        meta["schema"] = schema.text.strip()
    if version is not None and version.text:
        meta["schemaversion"] = version.text.strip()
    title = root.find(
        f"{{{CC_NS}}}metadata/{{{LOM_NS}}}lom/{{{LOM_NS}}}general/"
        f"{{{LOM_NS}}}title/{{{LOM_NS}}}string"
    )
    if title is not None and title.text:
        meta["title"] = title.text.strip()
    date = root.find(
        f"{{{CC_NS}}}metadata/{{{LOM_NS}}}lom/{{{LOM_NS}}}lifeCycle/"
        f"{{{LOM_NS}}}contribute/{{{LOM_NS}}}date/{{{LOM_NS}}}dateTime"
    )
    if date is not None and date.text:
        meta["export_date"] = date.text.strip()
    return meta


def build_resource_index(source: CourseSource) -> dict[str, dict[str, Any]]:
    """Map resource identifier → type/href from ``imsmanifest.xml``."""
    index: dict[str, dict[str, Any]] = {}
    if not source.exists("imsmanifest.xml"):
        return index
    root = ET.fromstring(source.read_bytes("imsmanifest.xml"))
    resources = root.find(f"{{{CC_NS}}}resources")
    if resources is None:
        return index
    for res in resources:
        if local(res.tag) != "resource":
            continue
        rid = res.get("identifier")
        if not rid:
            continue
        index[rid] = {
            "identifier": rid,
            "type": res.get("type"),
            "href": res.get("href"),
        }
    return index


def parse_modules(source: CourseSource, resources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse modules and items from ``course_settings/module_meta.xml``."""
    modules: list[dict[str, Any]] = []
    if not source.exists("course_settings/module_meta.xml"):
        return modules
    root = ET.fromstring(source.read_bytes("course_settings/module_meta.xml"))
    for mod in root:
        if local(mod.tag) != "module":
            continue
        items_el = mod.find(f"{{{CANVAS_NS}}}items")
        items: list[dict[str, Any]] = []
        if items_el is not None:
            for item in items_el:
                if local(item.tag) != "item":
                    continue
                ident_ref = text_of(item, "identifierref")
                href = None
                if ident_ref and ident_ref in resources:
                    href = resources[ident_ref].get("href")
                items.append(
                    {
                        "identifier": item.get("identifier"),
                        "title": text_of(item, "title"),
                        "content_type": text_of(item, "content_type"),
                        "workflow_state": text_of(item, "workflow_state"),
                        "position": _as_int(text_of(item, "position")),
                        "indent": _as_int(text_of(item, "indent")),
                        "identifierref": ident_ref,
                        "href": href,
                    }
                )
        items.sort(key=lambda i: i.get("position") or 0)
        modules.append(
            {
                "identifier": mod.get("identifier"),
                "title": text_of(mod, "title"),
                "workflow_state": text_of(mod, "workflow_state"),
                "position": _as_int(text_of(mod, "position")),
                "require_sequential_progress": text_of(
                    mod, "require_sequential_progress"
                )
                == "true",
                "item_count": len(items),
                "items": items,
            }
        )
    modules.sort(key=lambda m: m.get("position") or 0)
    return modules


def _as_int(value: str | None) -> int | None:
    """Parse an optional integer string."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def inventory_wiki_pages(source: CourseSource) -> list[dict[str, Any]]:
    """List wiki pages with title/identifier from HTML meta tags."""
    pages: list[dict[str, Any]] = []
    for rel in source.list_prefix("wiki_content/"):
        if not rel.lower().endswith(".html"):
            continue
        title = Path(rel).stem
        identifier = None
        workflow_state = None
        try:
            text = source.read_text(rel)
        except FileNotFoundError:
            continue
        for line in text.splitlines()[:40]:
            if 'name="identifier"' in line:
                identifier = _meta_content(line)
            elif 'name="workflow_state"' in line:
                workflow_state = _meta_content(line)
            elif "<title>" in line.lower():
                start = line.lower().find("<title>") + len("<title>")
                end = line.lower().find("</title>")
                if end > start:
                    title = line[start:end].strip()
        pages.append(
            {
                "path": rel,
                "title": title,
                "identifier": identifier,
                "workflow_state": workflow_state,
            }
        )
    pages.sort(key=lambda p: p["path"])
    return pages


def _meta_content(line: str) -> str | None:
    """Extract the content attribute from an HTML meta tag line."""
    marker = 'content="'
    start = line.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = line.find('"', start)
    if end < 0:
        return None
    return line[start:end]


def inventory_assignments(source: CourseSource) -> list[dict[str, Any]]:
    """Scan top-level ``g…`` folders for assignment_settings.xml."""
    assignments: list[dict[str, Any]] = []
    settings_files: list[str] = []

    if source.root is not None and source.root.is_dir():
        for path in sorted(source.root.glob("g*/assignment_settings.xml")):
            settings_files.append(str(path.relative_to(source.root)).replace("\\", "/"))
    elif source._zip is not None:
        for name in source._zip.namelist():
            if name.count("/") == 1 and name.endswith("/assignment_settings.xml"):
                settings_files.append(name)

    for rel in settings_files:
        root = ET.fromstring(source.read_bytes(rel))
        folder = rel.split("/", 1)[0]
        html_files = [
            p
            for p in source.list_prefix(f"{folder}/")
            if p.lower().endswith(".html")
        ]
        assignments.append(
            {
                "identifier": root.get("identifier") or folder,
                "title": text_of(root, "title"),
                "workflow_state": text_of(root, "workflow_state"),
                "grading_type": text_of(root, "grading_type"),
                "submission_types": text_of(root, "submission_types"),
                "points_possible": text_of(root, "points_possible"),
                "folder": folder,
                "html": html_files[0] if html_files else None,
            }
        )
    assignments.sort(key=lambda a: (a.get("title") or "", a["identifier"]))
    return assignments


def inventory_quizzes(source: CourseSource) -> list[dict[str, Any]]:
    """Scan top-level ``g…`` folders for assessment_meta.xml quizzes."""
    quizzes: list[dict[str, Any]] = []
    meta_files: list[str] = []

    if source.root is not None and source.root.is_dir():
        for path in sorted(source.root.glob("g*/assessment_meta.xml")):
            meta_files.append(str(path.relative_to(source.root)).replace("\\", "/"))
    elif source._zip is not None:
        for name in source._zip.namelist():
            if name.count("/") == 1 and name.endswith("/assessment_meta.xml"):
                meta_files.append(name)

    for rel in meta_files:
        root = ET.fromstring(source.read_bytes(rel))
        folder = rel.split("/", 1)[0]
        quizzes.append(
            {
                "identifier": root.get("identifier") or folder,
                "title": text_of(root, "title"),
                "quiz_type": text_of(root, "quiz_type"),
                "points_possible": text_of(root, "points_possible"),
                "folder": folder,
            }
        )
    quizzes.sort(key=lambda q: (q.get("title") or "", q["identifier"]))
    return quizzes


def file_map_summary(source: CourseSource) -> dict[str, Any]:
    """Summarize top-level folders and key file counts."""
    counts: Counter[str] = Counter()
    total_files = 0

    names: list[str] = []
    if source.root is not None and source.root.is_dir():
        for path in source.root.rglob("*"):
            if path.is_file():
                names.append(str(path.relative_to(source.root)).replace("\\", "/"))
    elif source._zip is not None:
        names = [n for n in source._zip.namelist() if not n.endswith("/")]

    for name in names:
        total_files += 1
        top = name.split("/", 1)[0]
        counts[top] += 1

    return {
        "total_files": total_files,
        "top_level": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "wiki_html_count": sum(
            1 for n in names if n.startswith("wiki_content/") and n.endswith(".html")
        ),
        "web_resources_count": sum(1 for n in names if n.startswith("web_resources/")),
        "lti_count": sum(1 for n in names if n.startswith("lti_resource_links/")),
        "non_cc_assessments_count": sum(
            1 for n in names if n.startswith("non_cc_assessments/")
        ),
    }


def build_inventory(
    source: CourseSource,
    *,
    source_label: str,
    imscc_path: str | None,
    unpacked_path: str | None,
) -> dict[str, Any]:
    """Assemble the full inventory dict for JSON/Markdown export."""
    resources = build_resource_index(source)
    modules = parse_modules(source, resources)
    pages = inventory_wiki_pages(source)
    assignments = inventory_assignments(source)
    quizzes = inventory_quizzes(source)
    files = file_map_summary(source)
    course = parse_course_title(source)
    manifest_meta = parse_manifest_meta(source)

    content_type_counts: Counter[str] = Counter()
    for mod in modules:
        for item in mod["items"]:
            content_type_counts[item.get("content_type") or "Unknown"] += 1

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source_label,
        "imscc_path": imscc_path,
        "unpacked_path": unpacked_path,
        "course": course,
        "manifest": manifest_meta,
        "counts": {
            "modules": len(modules),
            "module_items": sum(m["item_count"] for m in modules),
            "wiki_pages": len(pages),
            "assignments": len(assignments),
            "quizzes": len(quizzes),
            "manifest_resources": len(resources),
            "files": files["total_files"],
            "content_types": dict(content_type_counts),
        },
        "files": files,
        "modules": modules,
        "wiki_pages": pages,
        "assignments": assignments,
        "quizzes": quizzes,
    }


def render_markdown(inv: dict[str, Any]) -> str:
    """Render a human-readable INVENTORY.md from the inventory dict."""
    course = inv.get("course") or {}
    counts = inv.get("counts") or {}
    lines: list[str] = [
        "# MCF3M Canvas course inventory",
        "",
        "Lightweight manifest of the Canvas Common Cartridge export. "
        "The full unpack lives under `canvas/unpacked/` (gitignored); "
        "the `.imscc` under `sources/` is the archive source of truth.",
        "",
        f"- Generated: `{inv.get('generated_at')}`",
        f"- Source: `{inv.get('source')}`",
    ]
    if inv.get("imscc_path"):
        lines.append(f"- IMSCC: `{inv['imscc_path']}`")
    if inv.get("unpacked_path"):
        lines.append(f"- Unpacked: `{inv['unpacked_path']}`")
    lines.extend(
        [
            f"- Course title: **{course.get('title') or '(unknown)'}**",
            f"- Course code: `{course.get('course_code') or '—'}`",
            "",
            "## Counts",
            "",
            f"| Kind | Count |",
            f"|------|------:|",
            f"| Modules | {counts.get('modules', 0)} |",
            f"| Module items | {counts.get('module_items', 0)} |",
            f"| Wiki pages | {counts.get('wiki_pages', 0)} |",
            f"| Assignments | {counts.get('assignments', 0)} |",
            f"| Quizzes | {counts.get('quizzes', 0)} |",
            f"| Manifest resources | {counts.get('manifest_resources', 0)} |",
            f"| Files (in source) | {counts.get('files', 0)} |",
            "",
        ]
    )

    ctype = counts.get("content_types") or {}
    if ctype:
        lines.extend(["### Module item content types", ""])
        for key, value in sorted(ctype.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{key}`: {value}")
        lines.append("")

    lines.extend(["## Modules", ""])
    for mod in inv.get("modules") or []:
        state = mod.get("workflow_state") or "?"
        lines.append(
            f"### {mod.get('position')}. {mod.get('title')} "
            f"({mod.get('item_count')} items, `{state}`)"
        )
        lines.append("")
        lines.append(f"- Identifier: `{mod.get('identifier')}`")
        lines.append("")
        for item in mod.get("items") or []:
            ctype_s = item.get("content_type") or "?"
            href = item.get("href") or ""
            href_bit = f" → `{href}`" if href else ""
            lines.append(
                f"  {item.get('position')}. **{item.get('title')}** "
                f"(`{ctype_s}`){href_bit}"
            )
        lines.append("")

    lines.extend(["## Wiki pages", ""])
    for page in inv.get("wiki_pages") or []:
        ident = page.get("identifier") or "—"
        lines.append(f"- `{page.get('path')}` — {page.get('title')} (`{ident}`)")
    lines.append("")

    lines.extend(["## Assignments", ""])
    for asg in inv.get("assignments") or []:
        lines.append(
            f"- **{asg.get('title')}** (`{asg.get('identifier')}`, "
            f"`{asg.get('workflow_state')}`)"
        )
    lines.append("")

    lines.extend(["## Quizzes", ""])
    for quiz in inv.get("quizzes") or []:
        lines.append(
            f"- **{quiz.get('title')}** (`{quiz.get('identifier')}`, "
            f"type=`{quiz.get('quiz_type')}`)"
        )
    lines.append("")

    files = inv.get("files") or {}
    top = files.get("top_level") or {}
    if top:
        lines.extend(["## Top-level file map", ""])
        for folder, count in list(top.items())[:40]:
            lines.append(f"- `{folder}/` — {count} files")
        if len(top) > 40:
            lines.append(f"- …and {len(top) - 40} more top-level entries")
        lines.append("")

    lines.extend(
        [
            "## Regenerating",
            "",
            "```bash",
            "python3 scripts/canvas_unpack.py",
            "python3 scripts/canvas_inventory.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_inventory(inv: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Write ``inventory.json`` and ``INVENTORY.md`` under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "inventory.json"
    md_path = out_dir / "INVENTORY.md"
    json_path.write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(inv), encoding="utf-8")
    return json_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for inventory generation."""
    parser = argparse.ArgumentParser(
        description="Generate Canvas course inventory.json + INVENTORY.md."
    )
    parser.add_argument(
        "--imscc",
        type=Path,
        required=False,
        default=None,
        help="IMSCC archive (optional if --unpacked already exists)",
    )
    parser.add_argument(
        "--unpacked",
        type=Path,
        default=DEFAULT_UNPACKED,
        help=f"Unpacked working tree (default: {DEFAULT_UNPACKED})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Inventory output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--prefer",
        choices=("unpacked", "imscc", "auto"),
        default="auto",
        help="Which source to read (auto prefers unpacked if present).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: build and write the Canvas course inventory."""
    args = parse_args(argv)

    use_unpacked = False
    if args.prefer == "unpacked":
        use_unpacked = True
    elif args.prefer == "imscc":
        use_unpacked = False
    else:
        use_unpacked = args.unpacked.is_dir() and (
            args.unpacked / "imsmanifest.xml"
        ).is_file()

    if use_unpacked:
        if not (args.unpacked / "imsmanifest.xml").is_file():
            print(f"error: unpacked tree missing imsmanifest.xml: {args.unpacked}", file=sys.stderr)
            return 1
        source_label = "unpacked"
        root = args.unpacked
        archive = args.imscc if args.imscc.is_file() else None
    else:
        if not args.imscc.is_file():
            print(f"error: IMSCC not found: {args.imscc}", file=sys.stderr)
            return 1
        source_label = "imscc"
        root = None
        archive = args.imscc

    try:
        with CourseSource(root=root, archive=archive) as source:
            inv = build_inventory(
                source,
                source_label=source_label,
                imscc_path=_rel_or_abs(args.imscc) if args.imscc.is_file() else None,
                unpacked_path=_rel_or_abs(args.unpacked) if use_unpacked else None,
            )
            json_path, md_path = write_inventory(inv, args.out_dir)
    except (OSError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    counts = inv["counts"]
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(
        "Counts: "
        f"modules={counts['modules']}, "
        f"items={counts['module_items']}, "
        f"pages={counts['wiki_pages']}, "
        f"assignments={counts['assignments']}, "
        f"quizzes={counts['quizzes']}"
    )
    return 0


def _rel_or_abs(path: Path) -> str:
    """Return a repo-relative path string when possible."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
