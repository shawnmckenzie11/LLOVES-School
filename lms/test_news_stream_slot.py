#!/usr/bin/env python3
"""MCK-109 follow-up (Ops LOW-1 on #197): a failed tape open returns the slot.

``live_news_response`` took a stream slot before opening the tape. When the
open raised (now only after its bounded wait), the route errored and never
released the slot, so the worker lost one of its ``STREAMS_PER_WORKER``
streams until restart.
"""

from __future__ import annotations

import sqlite3
import unittest
from typing import Any
from unittest import mock

import live_news_wire
import test_live_shell as shell


class StreamSlotTests(shell.LiveShellTests):
    """Reuse the live-shell fixture. Only the tests below run here."""

    def tearDown(self) -> None:
        """Close cached tapes and reset the slot count."""
        live_news_wire.reset_logs()
        live_news_wire.reset_stream_budget()
        super().tearDown()

    def test_tape_open_error_releases_the_stream_slot(self) -> None:
        """Every failed open gives its slot back; a later stream still opens."""
        live_news_wire.reset_stream_budget()
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        boom = sqlite3.OperationalError("database is locked")
        with mock.patch.object(live_news_wire, "log_for", side_effect=boom):
            for _ in range(live_news_wire.STREAMS_PER_WORKER + 1):
                try:
                    resp = self.client.get(f"/api/live/session/{sid}/events")
                    self.assertGreaterEqual(resp.status_code, 500)
                except sqlite3.OperationalError:
                    pass  # testing mode re-raises the view's error
                self.assertEqual(live_news_wire._stream_count, 0)
        ok = self.client.get(f"/api/live/session/{sid}/events")
        self.assertEqual(ok.status_code, 200)
        text = ok.get_data(as_text=True)
        self.assertIn("event: hello", text)
        self.assertNotIn('"busy"', text)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run only this file's tests, not the inherited live-shell ones."""
    names = [
        name
        for name, value in vars(StreamSlotTests).items()
        if name.startswith("test_") and callable(value)
    ]
    return unittest.TestSuite(StreamSlotTests(name) for name in sorted(names))


if __name__ == "__main__":
    unittest.main()
