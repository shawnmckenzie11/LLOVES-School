"""Teacher reconnect strip arms only while /state is still in flight.

This stays out of the Sentry logger so the bar gate can ship or revert
without that module.
"""

from __future__ import annotations

import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent


class StaffReconnectBarTests(unittest.TestCase):
    """The pending strip does not import the logging module."""

    def test_gate_lives_on_the_teacher_page(self) -> None:
        """Bar gating is local to staff_ap.js and ignores the Sentry cause."""
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        log = (LMS_DIR / "static" / "staff_reconnect_log.js").read_text(encoding="utf-8")
        self.assertIn("function shouldShowStaffReconnectPending(", staff)
        self.assertNotIn("shouldShowStaffReconnectPending", log)
        gate = staff.split("function shouldShowStaffReconnectPending(")[1].split(
            "function armStaffPendingFeel("
        )[0]
        self.assertNotIn("staffReconnectNextCause", gate)
        self.assertNotIn("staff_reconnect_log.js", gate)
        poll = staff.split("async function pollLiveSessionAttendees(")[1].split(
            "function startStaffNewsWire("
        )[0]
        self.assertLess(poll.index("await api("), poll.index("settleStaffPollNetwork()"))
        self.assertLess(poll.index("settleStaffPollNetwork()"), poll.index("statePainting = true"))


if __name__ == "__main__":
    unittest.main()
