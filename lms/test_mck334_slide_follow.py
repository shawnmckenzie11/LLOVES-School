"""MCK-334: Publish, then Forward, keeps the header and the student slide.

Needs node, Chrome, and playwright-core (``LLOVES_E2E_NODE_PATH``). The
check is skipped when those are not installed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO))
SCRIPT = LMS_DIR / "e2e" / "slide_follow_mck334.mjs"
SEAT = "S1"
TITLE = "M" * 120


def _chrome() -> str:
    """Return a Chrome binary, or \"\" when this machine has none."""
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/local/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if path and Path(path).exists():
            return path
    return ""


def _node_path() -> str:
    """Directory node uses to resolve playwright-core."""
    return os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()


def _ready() -> bool:
    """True when the browser check can launch.

    A package next to the repo is enough. ``LLOVES_E2E_NODE_PATH`` is optional
    so the other browser checks, which require that variable, stay skipped.
    """
    if not (shutil.which("node") and _chrome()):
        return False
    env = {**os.environ}
    if _node_path():
        env["NODE_PATH"] = _node_path()
    probe = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import { createRequire } from 'node:module';"
                "createRequire(process.cwd() + '/lms/e2e/slide_follow_mck334.mjs')"
                ".resolve('playwright-core');"
            ),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def _pages(count: int) -> list[bytes]:
    """Build distinct PNG pages so each Forward changes the image URL.

    Args:
        count: How many pages to draw.
    """
    import fitz

    pngs: list[bytes] = []
    document = fitz.open()
    try:
        for index in range(count):
            page = document.new_page(width=320, height=180)
            page.draw_rect((0, 0, 320, 180), color=(0, 0, 0), fill=(0.1, 0.4, 0.8))
            page.insert_text((24, 100), f"P{index + 1}", fontsize=48, color=(1, 1, 1))
            pngs.append(page.get_pixmap(alpha=False).tobytes("png"))
    finally:
        document.close()
    return pngs


@unittest.skipUnless(
    _ready(),
    "node + Chrome + playwright-core (LLOVES_E2E_NODE_PATH) not available",
)
class SlideFollowBrowserTests(unittest.TestCase):
    """Staff Forward and the student pane against a 13-page deck."""

    def setUp(self) -> None:
        """One live class, a long deck title, and a joined phone."""
        logging.disable(logging.WARNING)
        os.environ["ALLOW_DEV_VERIFICATION_CODE"] = "1"
        from app import create_app

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]),
            ontario_code="MCF3M",
        )
        self.staff = self.app.test_client()
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCF3M",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=[SEAT],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.school.game.begin_game(self.class_id)
        deck = self.school.import_google_slides_pages(
            self.session_id,
            presentation_id="SlideFollow13",
            page_pngs=_pages(13),
            default_published=False,
            title=TITLE,
        )
        self.assertEqual(len(deck["pages"]), 13)
        self.assertEqual(deck["title"], TITLE)
        self.school.set_live_session_teacher_state(
            self.session_id,
            class_set=True,
            live_module="M1",
            live_slot="C1",
            stage="round",
            page_id="round_1",
            active_tab="slides",
            layout_preset="slides_full",
        )
        phone = self.app.test_client()
        joined = phone.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": SEAT},
        )
        self.assertIn(joined.status_code, (302, 303))
        match = re.search(r"[?&]v=([^&]+)", joined.headers.get("Location", ""))
        self.visit = match.group(1) if match else ""
        phone.post("/student/mood", data={"visit_token": self.visit, "mood": "good"})
        phone.post(
            "/student/character",
            data={"visit_token": self.visit, "character": "fox"},
        )
        self.student = phone
        self.server = None

    def tearDown(self) -> None:
        """Stop the server and close the database before removing the temp dir."""
        try:
            server = getattr(self, "server", None)
            if server is not None:
                server.shutdown()
                server.server_close()
            self.school.close()
        finally:
            logging.disable(logging.NOTSET)
            self.tmp.cleanup()

    def _jar(self, client: Any) -> dict[str, str]:
        """Cookie jar for one test client."""
        return {cookie.key: cookie.value for cookie in client._cookies.values()}

    def test_forward_keeps_active_header_and_student_slide(self) -> None:
        """Six Forwards stay Active at 7/13, and the long title does not cover the header buttons."""
        from werkzeug.serving import make_server

        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        info = {
            "port": self.server.server_port,
            "chrome": _chrome(),
            "class_id": self.class_id,
            "visit": self.visit,
            "staff_cookies": self._jar(self.staff),
            "student_cookies": self._jar(self.student),
        }
        cfg = Path(self.tmp.name) / "e2e.json"
        cfg.write_text(json.dumps(info), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(SCRIPT), str(cfg)],
            env={**os.environ, "NODE_PATH": _node_path()},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, (proc.stderr or "")[-2000:] + (proc.stdout or "")[-2000:])
        out = json.loads(lines[-1])
        self.assertEqual(out["errors"], [], out)
        failed = [step for step in out["steps"] if not step["ok"]]
        self.assertEqual(failed, [], json.dumps(out, ensure_ascii=False)[:4000])
        self.assertEqual(len(out["slides"]), 7)
        self.assertEqual(len(set(out["slides"])), 7)
        self.assertEqual(len(out["overlap"]), 2)


if __name__ == "__main__":
    unittest.main()
