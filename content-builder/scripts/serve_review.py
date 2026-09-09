#!/usr/bin/env python3
"""Local review server for content-builder lessons. Not the school LMS.

Serves ``content-builder/`` on 127.0.0.1:8790. POST /api/locks and
/api/instruction write only inside the named lesson folder.
"""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8790


class ReviewHandler(SimpleHTTPRequestHandler):
    """Serve builder files and accept lock / instruction saves."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

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
        lessons_root = (ROOT / "lessons").resolve()
        path = (lessons_root / course / lesson_id).resolve()
        if lessons_root not in path.parents:
            raise ValueError("invalid lesson path")
        return path

    def do_POST(self) -> None:
        """Save locks or instruction JSON for one lesson."""
        parsed = urlparse(self.path)
        try:
            data = self._read_json()
            course = data["course"]
            lesson_id = data["lesson_id"]
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
            self._json(200, {"ok": True, "path": str(dest.relative_to(ROOT))})
            return
        if parsed.path == "/api/instruction":
            dest = lesson / "instruction.json"
            instruction = data.get("instruction")
            if not isinstance(instruction, dict):
                self._json(400, {"ok": False, "error": "instruction object required"})
                return
            dest.write_text(json.dumps(instruction, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self._json(200, {"ok": True, "path": str(dest.relative_to(ROOT))})
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
