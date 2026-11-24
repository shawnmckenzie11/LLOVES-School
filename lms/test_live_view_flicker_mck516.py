#!/usr/bin/env python3
"""MCK-516: live MC bars and whiteboard thumbnails update without a flicker.

Bars move from the width already on screen. An unchanged thumbnail is
not redrawn. The poll stays on the existing interval, and the public
landing page drops the retired request and ended lines.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402
from auth import CLASS_ENDED_JOIN_NOTE  # noqa: E402

SCRIPT = LMS_DIR / "static" / "live_view_flicker_mck516.test.mjs"


class LiveViewFlickerTests(unittest.TestCase):
    """In-place bars, quiet thumbnails, and the landing copy."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the scripts this change is allowed to touch."""
        cls.graph_js = (LMS_DIR / "static" / "mc_results_graph.js").read_text(encoding="utf-8")
        cls.graph_css = (LMS_DIR / "static" / "mc_results_graph.css").read_text(encoding="utf-8")
        cls.board_js = (LMS_DIR / "static" / "formative_live_board.js").read_text(encoding="utf-8")
        cls.staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        cls.wire_js = (LMS_DIR / "static" / "live_news_wire.js").read_text(encoding="utf-8")

    def test_bars_and_thumbnails_update_in_place(self) -> None:
        """Same-count polls write nothing. A new count does not start at zero.

        An unchanged whiteboard canvas is not replaced or redrawn. The
        board whose ink changed is the only one painted, on the same canvas.
        """
        proc = subprocess.run(
            ["node", str(SCRIPT)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            (proc.stdout or "")[-4000:] + (proc.stderr or "")[-4000:],
        )
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload.get("failed"), [], payload)

    def test_bars_transition_from_the_current_width(self) -> None:
        """The shared fill eases width and does not scale up from zero."""
        fill = self.graph_css.split(".mc-results-fill {")[1].split("}")[0]
        self.assertIn("transition: width", fill)
        self.assertNotIn("mc-results-grow", self.graph_css)
        self.assertNotIn("scaleX(0)", self.graph_css)
        self.assertIn("background: #93c5fd", fill)

    def test_poll_interval_and_reads_stay_put(self) -> None:
        """Intervals are unchanged. This adds no student ``/state`` read and no write."""
        self.assertIn("export const FALLBACK_POLL_MS = 20000", self.wire_js)
        self.assertIn("export const NO_STREAM_POLL_MS = 4000", self.wire_js)
        self.assertIn("export const ENDED_POLL_MS = 30000", self.wire_js)
        self.assertIn("const TILE_POLL_MS = 2000", self.board_js)
        self.assertIn("const PANEL_POLL_MS = 500", self.board_js)
        self.assertIn("return fallbackPollMs(staffNewsWire)", self.staff_js)
        self.assertIn("api(`/api/live-sessions/${id}/state${qs}`", self.staff_js)
        for name, text in (
            ("mc_results_graph.js", self.graph_js),
            ("formative_live_board.js", self.board_js),
            ("staff_ap.js", self.staff_js),
        ):
            self.assertNotIn("/api/student/state", text, name)
        self.assertNotIn("fetch(", self.graph_js)
        self.assertNotIn("method: \"POST\"", self.graph_js)
        self.assertEqual(self.board_js.count('method: "POST"'), 3)
        paint = self.staff_js.split("function paintLifecycleBarsInPlace(")[1].split(
            "\nfunction "
        )[0]
        self.assertIn("syncMcResultsBars(", paint)
        self.assertLess(paint.index("syncMcResultsBars("), paint.index("existing.outerHTML"))

    def test_landing_drops_request_hint_and_ended_note(self) -> None:
        """The home page no longer shows the request hint or the ended sentence."""
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        client = app.test_client()
        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        body = home.get_data(as_text=True)
        ended = client.get("/?ended=1")
        self.assertEqual(ended.status_code, 200)
        ended_body = ended.get_data(as_text=True)
        for page in (body, ended_body):
            self.assertNotIn("New to ALC?", page)
            self.assertNotIn(CLASS_ENDED_JOIN_NOTE, page)
            self.assertNotIn('id="class-ended-note"', page)
            self.assertNotIn('id="landing-request-access-inline"', page)
        self.assertIn('id="landing-request-access"', body)
        self.assertIn("<h1>Join Class</h1>", body)


if __name__ == "__main__":
    unittest.main()
