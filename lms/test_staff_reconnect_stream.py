"""A dropped live stream is a teacher-strip cause, not a board-poll gate.

``live_news_wire.js`` is loaded by student pages too. The new callbacks
are optional, so a student tab that does not pass them behaves as before.
"""

from __future__ import annotations

import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent


class StaffStreamDropTests(unittest.TestCase):
    """onDown does not change the student wire and does not call hasStream."""

    def test_stream_drop_is_optional_and_does_not_gate_the_board_poll(self) -> None:
        """The teacher page records the drop. The board poll is not skipped."""
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        wire = (LMS_DIR / "static" / "live_news_wire.js").read_text(encoding="utf-8")
        self.assertNotIn("hasStream()", staff)
        self.assertIn("handlers.onDown?.()", wire)
        self.assertIn("handlers.onUp?.()", wire)
        self.assertIn("noteStaffStreamDown()", staff)
        self.assertIn("staffStreamClosedHint = true", staff)


if __name__ == "__main__":
    unittest.main()
