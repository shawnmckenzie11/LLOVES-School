#!/usr/bin/env python3
"""Catch-up page: lock a submitted board, teacher row labels, tab copy.

Seeded demo data only. Assertions do not print names, emails, codes, or ids.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
FIXTURE = LMS_DIR / "e2e" / "catchup_submitted_lock.html"


def _evidence_dir() -> Path:
    """Write the submitted-board screenshot somewhere this machine can create."""
    override = os.environ.get("CATCHUP_LOCK_EVIDENCE", "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "mck-catchup-submitted-lock"


EVIDENCE = _evidence_dir()


def _chrome() -> str:
    """Return a Chrome binary, or empty when this machine has none."""
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/local/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if path and Path(path).is_file():
            return path
    return ""


class CatchupPageLayoutTests(unittest.TestCase):
    """Wonder/Mobbin wording and the submitted catch-up board lock."""

    def test_teacher_row_is_one_line_with_fixed_type_labels(self) -> None:
        """Number, type, then stem. No NEW/Page/BANK/Inactive top line."""
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        paint = staff.split("function paintLiveQuestionCards")[1].split(
            "function focusedGroupConfirmButton"
        )[0]
        self.assertIn("function liveQuestionTypeLabel", staff)
        self.assertIn('return "MC"', staff)
        self.assertIn('return "Numeric"', staff)
        self.assertIn('return "Poll"', staff)
        self.assertIn('return "Show Your Work"', staff)
        self.assertNotIn('return "Whiteboard"', staff)
        self.assertIn("live-question-type", paint)
        self.assertIn("live-question-stem", paint)
        self.assertNotIn("live-question-card-meta", paint)
        self.assertNotIn("live-question-new-chip", paint)
        self.assertNotIn("Page ${", paint)
        self.assertNotIn("Bank ·", paint)
        self.assertIn('typeLabel === "Show Your Work"', paint)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .live-question-type {", css)
        self.assertIn("flex: 0 0 9.75rem", css)
        self.assertIn("width: 9.75rem", css)

    def test_catchup_tabs_are_individual_group_formative(self) -> None:
        """Catch-up labels are exactly Individual, Group, Formative."""
        landing = (LMS_DIR / "catchup_landing.py").read_text(encoding="utf-8")
        self.assertIn('COPY_TIER_INDIVIDUAL = "Individual"', landing)
        self.assertIn('COPY_TIER_GROUP = "Group"', landing)
        self.assertIn('COPY_TIER_UNDERSTANDING = "Formative"', landing)
        self.assertNotIn("T1 Individual", landing)
        self.assertNotIn("T3 Understanding", landing)
        js = (LMS_DIR / "static" / "catchup_landing.js").read_text(encoding="utf-8")
        self.assertIn('"Individual"', js)
        self.assertIn('"Group"', js)
        self.assertIn('"Formative"', js)
        self.assertNotIn("T1 Individual", js)
        work = (LMS_DIR / "static" / "catchup.js").read_text(encoding="utf-8")
        self.assertIn('["individual", "Individual"]', work)
        self.assertIn('["group", "Group"]', work)
        self.assertIn('["formative", "Formative"]', work)

    def test_catchup_board_locks_after_submit(self) -> None:
        """The phone greys out, tools disable, and the line is Submitted ✓."""
        ui = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        mount = ui.split("export function mountCatchupQuestionBoards")[1].split(
            "function teacherGalleryHtml"
        )[0]
        self.assertIn("lockOnSubmit: true", mount)
        self.assertIn('submittedLine: "Submitted ✓"', mount)
        self.assertIn("classList.add(\"is-locked\")", ui)
        py = (LMS_DIR / "question_board.py").read_text(encoding="utf-8")
        self.assertIn('SUBMITTED_LINE = "Submitted ✓"', py)
        self.assertIn("CATCHUP_SUBMITTED_LINE = SUBMITTED_LINE", py)
        self.assertIn('if parsed.kind not in {"question", "catchup", "catchup_group"}:', py)
        self.assertNotIn("You can still make changes.", py)
        self.assertNotIn("You can still make changes.", ui)
        self.assertIn('const fallback = options.submittedLine || "Submitted ✓"', ui)
        css = (LMS_DIR / "static" / "whiteboard_formative.css").read_text(encoding="utf-8")
        self.assertIn(".qboard-phone.is-locked", css)
        self.assertIn("pointer-events: none", css)

    def test_submitted_board_screenshot(self) -> None:
        """A phone-width shot of the locked catch-up board and tabs."""
        chrome = _chrome()
        if not chrome:
            self.skipTest("Chrome is not installed on this machine")
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        out = EVIDENCE / "catchup-submitted-lock.png"
        if out.exists():
            out.unlink()
        target = FIXTURE.resolve().as_uri()
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-sandbox",
                "--window-size=390,480",
                f"--screenshot={out}",
                target,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 1000)

    def test_landing_drops_your_code_matched(self) -> None:
        """The matched-class kicker no longer says YOUR CODE MATCHED."""
        landing = (LMS_DIR / "catchup_landing.py").read_text(encoding="utf-8")
        html = (LMS_DIR / "templates" / "student" / "catchup_landing.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("COPY_YOUR_CODE_MATCHED", landing)
        self.assertNotIn("YOUR CODE MATCHED", landing)
        self.assertNotIn("YOUR CODE MATCHED", html)
        self.assertNotIn("your_code_matched", html)


if __name__ == "__main__":
    unittest.main()
