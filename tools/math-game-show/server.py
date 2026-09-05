#!/usr/bin/env python3
"""Local Math Game Show server — teacher dashboard + ESPN scoreboard.

Bind is 127.0.0.1 so students never get a URL. Same stack as
``scripts/nelson_browse_server.py``: stdlib ``ThreadingHTTPServer`` + sqlite3.

Usage:
    python3 tools/math-game-show/server.py
    python3 tools/math-game-show/server.py --port 8766 --no-browser
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from datetime import date
from urllib.parse import parse_qs, unquote, urlparse

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from csv_import import parse_canvas_grades_csv, roster_summary  # noqa: E402
from db import GameShowDB  # noqa: E402
from schedule import picker_year_semester, wizard_defaults  # noqa: E402

HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 8766
TEMPLATES = APP_DIR / "templates"
STATIC = APP_DIR / "static"

DB: GameShowDB | None = None


def get_db() -> GameShowDB:
    """Return the process-wide database, or raise if the server is not ready."""
    if DB is None:
        raise RuntimeError("Database is not initialized")
    return DB


class GameShowHandler(BaseHTTPRequestHandler):
    """Serve HTML pages, static assets, and JSON teacher APIs."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        """Log requests to stderr with a short prefix."""
        sys.stderr.write("[math-game-show] " + (format % args) + "\n")

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        """Write a raw HTTP response.

        Args:
            status: HTTP status code.
            body: Response bytes.
            content_type: Content-Type header value.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, body: dict[str, Any] | list[Any]) -> None:
        """Write a JSON response.

        Args:
            status: HTTP status code.
            body: JSON-serializable payload.
        """
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, raw, "application/json; charset=utf-8")

    def _send_html_file(self, name: str) -> None:
        """Send a template HTML file.

        Args:
            name: File name under ``templates/``.
        """
        path = TEMPLATES / name
        if not path.is_file():
            self._send_json(500, {"ok": False, "error": f"Missing template {name}"})
            return
        self._send_bytes(200, path.read_bytes(), "text/html; charset=utf-8")

    def _send_static(self, rel: str) -> None:
        """Send a file from ``static/`` if it stays inside that folder.

        Args:
            rel: URL path after ``/static/``.
        """
        safe = Path(unquote(rel))
        if safe.is_absolute() or ".." in safe.parts:
            self._send_json(403, {"ok": False, "error": "Forbidden"})
            return
        path = (STATIC / safe).resolve()
        try:
            path.relative_to(STATIC.resolve())
        except ValueError:
            self._send_json(403, {"ok": False, "error": "Forbidden"})
            return
        if not path.is_file():
            self._send_json(404, {"ok": False, "error": "Not found"})
            return
        ctype = self.guess_type(path)
        self._send_bytes(200, path.read_bytes(), ctype)

    def guess_type(self, path: Path) -> str:
        """Return a Content-Type for a static file.

        Args:
            path: File path.
        """
        suffix = path.suffix.lower()
        mapping = {
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        return mapping.get(suffix, "application/octet-stream")

    def _read_json(self) -> dict[str, Any]:
        """Parse a JSON request body.

        Returns:
            Parsed object, or empty dict if the body is empty.
        """
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def _api_error(self, exc: BaseException) -> None:
        """Map domain exceptions to HTTP JSON errors.

        Args:
            exc: Raised exception.
        """
        if isinstance(exc, KeyError):
            self._send_json(404, {"ok": False, "error": f"Not found: {exc.args[0]}"})
        elif isinstance(exc, FileNotFoundError):
            self._send_json(404, {"ok": False, "error": str(exc)})
        elif isinstance(exc, ValueError):
            self._send_json(400, {"ok": False, "error": str(exc)})
        else:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        """Route HTML pages, static files, and GET APIs."""
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        query = parse_qs(parsed.query)

        if path == "/":
            self._send_html_file("home.html")
            return
        static_match = re.match(r"^/static/(.+)$", path)
        if static_match:
            self._send_static(static_match.group(1))
            return
        class_page = re.match(r"^/class/(\d+)$", path)
        if class_page:
            self._send_html_file("dashboard.html")
            return
        setup_page = re.match(r"^/class/(\d+)/setup$", path)
        if setup_page:
            self._send_html_file("setup.html")
            return
        game_page = re.match(r"^/class/(\d+)/game$", path)
        if game_page:
            self._send_html_file("game.html")
            return
        if path == "/scoreboard" or re.match(r"^/scoreboard/(\d+)$", path):
            self._send_html_file("scoreboard.html")
            return

        if path == "/api/defaults":
            self._send_json(200, {"ok": True, **wizard_defaults()})
            return
        if path == "/api/classes":
            year, semester = picker_year_semester()
            q_year = (query.get("year") or [year])[0]
            q_sem = (query.get("semester") or [semester])[0]
            classes = get_db().list_classes(q_year, q_sem)
            self._send_json(
                200,
                {
                    "ok": True,
                    "year": q_year,
                    "semester": q_sem,
                    "classes": classes,
                },
            )
            return
        dash = re.match(r"^/api/classes/(\d+)/dashboard$", path)
        if dash:
            sort = (query.get("sort") or ["last"])[0]
            try:
                self._send_json(200, {"ok": True, **get_db().dashboard(int(dash.group(1)), sort)})
            except Exception as exc:  # noqa: BLE001 — surface to client
                self._api_error(exc)
            return
        class_get = re.match(r"^/api/classes/(\d+)$", path)
        if class_get:
            try:
                self._send_json(200, {"ok": True, "class": get_db().get_class(int(class_get.group(1)))})
            except Exception as exc:  # noqa: BLE001
                self._api_error(exc)
            return
        game_get = re.match(r"^/api/classes/(\d+)/game$", path)
        if game_get:
            try:
                self._send_json(200, {"ok": True, **get_db().game_state(int(game_get.group(1)))})
            except Exception as exc:  # noqa: BLE001
                self._api_error(exc)
            return
        if path == "/api/scoreboard" or re.match(r"^/api/classes/(\d+)/scoreboard$", path):
            try:
                self._send_json(200, get_db().scoreboard())
            except Exception as exc:  # noqa: BLE001
                self._api_error(exc)
            return
        log_get = re.match(r"^/api/sessions/(\d+)/log$", path)
        if log_get:
            try:
                log_path = get_db().session_log_path(int(log_get.group(1)))
                body = log_path.read_bytes()
                self._send_bytes(200, body, "text/plain; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                self._api_error(exc)
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        """Route JSON mutation APIs."""
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return
        try:
            self._dispatch_post(path, body)
        except Exception as exc:  # noqa: BLE001 — surface to client
            self._api_error(exc)

    def _dispatch_post(self, path: str, body: dict[str, Any]) -> None:
        """Handle a parsed POST body.

        Args:
            path: URL path.
            body: JSON object.
        """
        db = get_db()
        if path == "/api/classes":
            created = db.create_class(
                year=str(body.get("year") or ""),
                semester=str(body.get("semester") or ""),
                course_code=str(body.get("course_code") or ""),
                days_preset=str(body.get("days") or ""),
                time_label=str(body.get("time") or ""),
                csv_text=str(body.get("csv_text") or ""),
            )
            self._send_json(200, {"ok": True, "class": created})
            return
        if path == "/api/csv/preview":
            students = parse_canvas_grades_csv(str(body.get("csv_text") or ""))
            self._send_json(200, {"ok": True, **roster_summary(students)})
            return
        begin = re.match(r"^/api/classes/(\d+)/begin$", path)
        if begin:
            meeting = _optional_date(body.get("meeting_date"))
            state = db.begin_game(int(begin.group(1)), meeting_date=meeting)
            self._send_json(200, {"ok": True, **state})
            return
        meeting_post = re.match(r"^/api/classes/(\d+)/game/meeting$", path)
        if meeting_post:
            chosen = _optional_date(body.get("meeting_date"))
            if chosen is None:
                raise ValueError("meeting_date is required (YYYY-MM-DD)")
            time_label = body.get("time")
            state = db.set_meeting_date(
                int(meeting_post.group(1)),
                chosen,
                time_label=str(time_label) if time_label else None,
            )
            self._send_json(200, {"ok": True, **state})
            return
        cancel = re.match(r"^/api/classes/(\d+)/game/cancel$", path)
        if cancel:
            result = db.cancel_setup(int(cancel.group(1)))
            self._send_json(200, result)
            return
        attendance = re.match(r"^/api/classes/(\d+)/game/attendance$", path)
        if attendance:
            ids = [int(x) for x in (body.get("present_ids") or [])]
            state = db.save_attendance(int(attendance.group(1)), ids)
            self._send_json(200, {"ok": True, **state})
            return
        finalize_att = re.match(
            r"^/api/classes/(\d+)/game/finalize-attendance$", path
        )
        if finalize_att:
            ids = [int(x) for x in (body.get("present_ids") or [])]
            result = db.finalize_attendance_only(int(finalize_att.group(1)), ids)
            self._send_json(200, result)
            return
        step = re.match(r"^/api/classes/(\d+)/game/step$", path)
        if step:
            state = db.set_setup_step(int(step.group(1)), str(body.get("status") or ""))
            self._send_json(200, {"ok": True, **state})
            return
        assign = re.match(r"^/api/classes/(\d+)/game/assign$", path)
        if assign:
            raw_assignments = body.get("assignments")
            if raw_assignments is not None and not isinstance(raw_assignments, list):
                raise ValueError("assignments must be a list")
            state = db.assign_teams(
                int(assign.group(1)),
                int(body.get("n_teams") or 0),
                str(body.get("mode") or ""),
                assignments=raw_assignments,
            )
            self._send_json(200, {"ok": True, **state})
            return
        rename = re.match(r"^/api/classes/(\d+)/game/rename$", path)
        if rename:
            names = body.get("teams") or []
            if not isinstance(names, list):
                raise ValueError("teams must be a list")
            state = db.rename_teams(int(rename.group(1)), names)
            self._send_json(200, {"ok": True, **state})
            return
        round_post = re.match(r"^/api/classes/(\d+)/game/round$", path)
        if round_post:
            state = db.start_round(
                int(round_post.group(1)), int(body.get("round") or 0)
            )
            self._send_json(200, {"ok": True, **state})
            return
        append_round = re.match(r"^/api/classes/(\d+)/game/append-round$", path)
        if append_round:
            spec = body.get("round") if isinstance(body.get("round"), dict) else body
            if not isinstance(spec, dict):
                raise ValueError("round must be an object")
            state = db.append_and_start_round(int(append_round.group(1)), spec)
            self._send_json(200, {"ok": True, **state})
            return
        score = re.match(r"^/api/classes/(\d+)/game/score$", path)
        if score:
            state = db.award_points(
                int(score.group(1)),
                kind=str(body.get("kind") or ""),
                target_id=int(body.get("id") or 0),
                amount=int(body.get("amount") or 0),
                team_rule=(str(body["team_rule"]) if body.get("team_rule") else None),
            )
            self._send_json(200, {"ok": True, **state})
            return
        late = re.match(r"^/api/classes/(\d+)/game/add-student$", path)
        if late:
            state = db.add_late_student(
                int(late.group(1)),
                int(body.get("student_id") or 0),
                int(body.get("team_id") or 0),
            )
            self._send_json(200, {"ok": True, **state})
            return
        end = re.match(r"^/api/classes/(\d+)/game/end$", path)
        if end:
            result = db.end_game(int(end.group(1)))
            self._send_json(200, result)
            return
        add_student = re.match(r"^/api/classes/(\d+)/students$", path)
        if add_student:
            dash = db.add_student(
                int(add_student.group(1)),
                first_name=str(body.get("first_name") or ""),
                last_display=str(body.get("last_display") or ""),
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        del_student = re.match(r"^/api/classes/(\d+)/students/delete$", path)
        if del_student:
            dash = db.delete_student(
                int(del_student.group(1)),
                int(body.get("student_id") or 0),
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        add_session = re.match(r"^/api/classes/(\d+)/sessions$", path)
        if add_session:
            chosen = _optional_date(body.get("meeting_date"))
            if chosen is None:
                raise ValueError("meeting_date is required (YYYY-MM-DD)")
            dash = db.add_session_column(
                int(add_session.group(1)),
                chosen,
                time_label=str(body["time"]) if body.get("time") else None,
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        del_session = re.match(r"^/api/classes/(\d+)/sessions/delete$", path)
        if del_session:
            dash = db.delete_session_column(
                int(del_session.group(1)),
                int(body.get("session_id") or 0),
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        freeze = re.match(r"^/api/classes/(\d+)/subtotals$", path)
        if freeze:
            dash = db.freeze_subtotal(
                int(freeze.group(1)),
                name=str(body["name"]) if body.get("name") else None,
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        rename_sub = re.match(r"^/api/classes/(\d+)/subtotals/rename$", path)
        if rename_sub:
            dash = db.rename_subtotal(
                int(rename_sub.group(1)),
                int(body.get("id") or 0),
                str(body.get("name") or ""),
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        del_sub = re.match(r"^/api/classes/(\d+)/subtotals/delete$", path)
        if del_sub:
            dash = db.delete_subtotal(
                int(del_sub.group(1)),
                int(body.get("id") or 0),
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        stat_window = re.match(r"^/api/classes/(\d+)/stat-window$", path)
        if stat_window:
            dash = db.set_stat_window(
                int(stat_window.group(1)),
                str(body.get("window") or ""),
                sort=str(body.get("sort") or "last"),
            )
            self._send_json(200, {"ok": True, **dash})
            return
        self._send_json(404, {"ok": False, "error": "Unknown API path"})


def _optional_date(value: Any) -> date | None:
    """Parse YYYY-MM-DD from a JSON field, or None if blank.

    Args:
        value: Raw JSON value.
    """
    if value is None or value == "":
        return None
    return date.fromisoformat(str(value)[:10])


def main(argv: list[str] | None = None) -> int:
    """Run the Math Game Show server.

    Args:
        argv: Optional CLI args (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    global DB
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST_DEFAULT, help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT, help="Bind port")
    parser.add_argument(
        "--data-dir",
        default=str(APP_DIR / "data"),
        help="Directory for sqlite, uploads, and logs",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    DB = GameShowDB(data_dir / "app.sqlite", data_dir)

    server = ThreadingHTTPServer((args.host, args.port), GameShowHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Math Game Show: {url}")
    print(f"Database: {DB.db_path}")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        DB.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
