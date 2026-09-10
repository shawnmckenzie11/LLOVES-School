#!/usr/bin/env python3
"""Local review server for content-builder lessons. Not the school LMS.

Serves the builder tree on 127.0.0.1:8790. Canonical writes go to
``student-content.json`` via POST /api/student-content. ``instruction.json`` is
retired as an authoring target.
"""

from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8790
sys.path.insert(0, str(Path(__file__).resolve().parent))
from onboarding_lib import (  # noqa: E402
    BuilderPaths,
    resolve_conflict,
    resolve_lesson_context,
    review_payload,
)
from student_authoring import (  # noqa: E402
    EDITABLE_FIELDS,
    approve_student_content,
    content_diff,
    content_revision,
    iter_blocks,
    load_json,
    read_approved,
    save_patch,
)
from coo import list_jobs  # noqa: E402


def list_review_lessons(root: Path, course: str) -> list[dict]:
    """List lessons that have student-content.json under a builder root."""
    lessons_dir = Path(root) / "lessons" / course
    rows: list[dict] = []
    if not lessons_dir.is_dir():
        return rows
    for path in sorted(lessons_dir.glob("*/student-content.json")):
        try:
            doc = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "internal_id": path.parent.name,
                "title": doc.get("title") or path.parent.name,
                "has_student": True,
            }
        )
    return rows


def handler_for(root: Path):
    """Return a request handler bound to ``root`` (used by tests)."""

    builder = Path(root)

    class BoundHandler(ReviewHandler):
        builder_root = builder

        def __init__(self, *args, **kwargs):
            SimpleHTTPRequestHandler.__init__(self, *args, directory=str(builder), **kwargs)

    return BoundHandler


class ReviewHandler(SimpleHTTPRequestHandler):
    """Serve builder files and accept student-content saves."""

    builder_root = ROOT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.builder_root), **kwargs)

    def _root(self) -> Path:
        return Path(getattr(self, "builder_root", ROOT))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _lesson_dir(self, course: str, lesson_id: str) -> Path:
        lessons_root = (self._root() / "lessons").resolve()
        path = (lessons_root / course / lesson_id).resolve()
        if lessons_root not in path.parents:
            raise ValueError("invalid lesson path")
        return path

    def do_GET(self) -> None:
        """Serve onboarding, authoring, and static files."""
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        root = self._root()
        paths = BuilderPaths(root)
        if parsed.path == "/api/onboarding":
            course = (query.get("course") or ["MCF3M"])[0]
            try:
                self._json(200, review_payload(paths, course))
            except (OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/lesson-context":
            course = (query.get("course") or ["MCF3M"])[0]
            lesson = (query.get("lesson") or ["M4-L1-vertex-form"])[0]
            role = (query.get("role") or ["lesson-director"])[0]
            try:
                self._json(200, resolve_lesson_context(paths, course, lesson, role))
            except KeyError as exc:
                self._json(404, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/lessons":
            course = (query.get("course") or ["MCF3M"])[0]
            self._json(200, {"ok": True, "lessons": list_review_lessons(root, course)})
            return
        if parsed.path == "/api/student-content":
            try:
                course = (query.get("course") or ["MCF3M"])[0]
                lesson = (query.get("lesson") or ["M4-L1-vertex-form"])[0]
                lesson_dir = self._lesson_dir(course, lesson)
                dest = lesson_dir / "student-content.json"
                doc = load_json(dest)
                blocks = []
                for block in iter_blocks(doc):
                    field = EDITABLE_FIELDS.get(block.get("type"))
                    if not field:
                        continue
                    blocks.append(
                        {
                            "id": block.get("id"),
                            "type": block.get("type"),
                            "field": field,
                            "value": block.get(field) or "",
                            "label": block.get("label") or "",
                        }
                    )
                preview = f"/build/{course}/{lesson}/index.html"
                self._json(
                    200,
                    {
                        "ok": True,
                        "revision": content_revision(doc),
                        "title": doc.get("title"),
                        "parts": [
                            {"id": p.get("id"), "nav_label": p.get("nav_label")}
                            for p in doc.get("parts") or []
                        ],
                        "blocks": blocks,
                        "preview": preview,
                    },
                )
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/coverage":
            course = (query.get("course") or ["MCF3M"])[0]
            lesson = (query.get("lesson") or ["M4-L1-vertex-form"])[0]
            brief = self._lesson_dir(course, lesson) / "coverage-brief.json"
            if brief.is_file():
                self._json(200, {"ok": True, "coverage": load_json(brief)})
                return
            self._json(200, {"ok": True, "coverage": {}})
            return
        if parsed.path == "/api/pool":
            # Review drawer: committed pool JSON, not the retired ranking file.
            course = (query.get("course") or ["MCF3M"])[0]
            lesson = (query.get("lesson") or ["M4-L1-vertex-form"])[0]
            pool_path = root / "catalogue" / "pools" / course / f"{lesson}.json"
            if not pool_path.is_file():
                self._json(200, {"ok": True, "candidates": []})
                return
            doc = load_json(pool_path)
            rows = []
            for item in doc.get("candidates") or []:
                rows.append(
                    {
                        "id": item.get("id"),
                        "status": item.get("status"),
                        "title": item.get("title"),
                        "student_facing": item.get("student_facing"),
                        "family_id": item.get("family_id"),
                        "bloom": item.get("bloom"),
                        "task_family": item.get("task_family"),
                        "verified": (item.get("verification") or {}).get("verified"),
                    }
                )
            self._json(200, {"ok": True, "candidates": rows})
            return
        if parsed.path == "/api/diff":
            try:
                course = (query.get("course") or ["MCF3M"])[0]
                lesson = (query.get("lesson") or ["M4-L1-vertex-form"])[0]
                lesson_dir = self._lesson_dir(course, lesson)
                current = load_json(lesson_dir / "student-content.json")
                approved = read_approved(lesson_dir)
                rows = content_diff(current, approved)
                self._json(
                    200,
                    {
                        "ok": True,
                        "against": "approved",
                        "rows": rows,
                    },
                )
            except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/coo/jobs":
            self._json(200, {"ok": True, "jobs": list_jobs(root=root)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        """Save student-content patches, locks, approval, or conflict resolutions."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/instruction":
            self._json(
                410,
                {
                    "ok": False,
                    "error": "instruction.json is retired. Edit student-content.json via POST /api/student-content.",
                },
            )
            return
        if parsed.path == "/api/conflicts/resolve":
            try:
                data = self._read_json()
                item = resolve_conflict(
                    BuilderPaths(self._root()),
                    data.get("course") or "MCF3M",
                    data["conflict_id"],
                    data["resolution"],
                    data.get("note"),
                )
                self._json(200, {"ok": True, "conflict": item})
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        try:
            data = self._read_json()
            course = data["course"]
            lesson_id = data.get("lesson_id") or data.get("lesson")
            lesson = self._lesson_dir(course, lesson_id)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/locks":
            dest = lesson / "locks.json"
            dest.write_text(
                json.dumps(
                    {
                        "lesson_id": lesson_id,
                        "locked_keys": data.get("locked_keys", []),
                        "notes": data.get("notes"),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            self._json(200, {"ok": True, "path": str(dest.relative_to(self._root()))})
            return
        if parsed.path == "/api/approve":
            try:
                result = approve_student_content(lesson)
                self._json(200, result)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/student-content":
            try:
                ops = data.get("ops") or []
                result = save_patch(
                    lesson,
                    ops,
                    base_revision=data.get("base_revision"),
                    components=self._root() / "components",
                    rebuild=data.get("rebuild", True),
                )
                preview = f"/build/{course}/{lesson_id}/index.html"
                result["preview"] = preview
                self._json(200, result)
            except PermissionError as exc:
                self._json(403, {"ok": False, "error": str(exc)})
            except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False, "error": "unknown endpoint"})


def main() -> None:
    """Listen on 127.0.0.1:8790."""
    httpd = ThreadingHTTPServer((HOST, PORT), ReviewHandler)
    print(f"Review: http://{HOST}:{PORT}/review/")
    print("Not the LMS (8787). Ctrl+C to stop.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
