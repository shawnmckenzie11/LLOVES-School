"""MCK-231: deploy class-hours guard reads windows from schedule data."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("class_hours_guard", REPO / ".github" / "scripts" / "class_hours_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)  # type: ignore[union-attr]


class ClassHoursGuardTests(unittest.TestCase):
    def test_repo_schedule_has_start_times(self) -> None:
        wins = guard.class_windows(json.loads(guard.SCHEDULE.read_text()))
        self.assertTrue(wins)
        for w in wins:
            self.assertLess(w["start"], w["end"], w["label"])

    def test_blocks_wednesday_mcf3m_and_clears_after(self) -> None:
        self.assertEqual(guard.main(["--at", "2026-10-14T14:40"]), 1)
        self.assertEqual(guard.main(["--at", "2026-10-14T15:30"]), 0)
        self.assertEqual(guard.main(["--at", "2026-10-14T14:40", "--override"]), 0)

    def test_thursday_holiday_and_weekend_are_clear(self) -> None:
        self.assertEqual(guard.main(["--at", "2026-10-15T14:30"]), 0)  # Thu, M/W/F only
        self.assertEqual(guard.main(["--at", "2026-10-12T14:30"]), 0)  # Thanksgiving
        self.assertEqual(guard.main(["--at", "2026-10-17T14:30"]), 0)  # Saturday

    def test_windows_follow_the_data_not_the_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = Path(tmp) / "s.json"
            sched.write_text(json.dumps({"courses": {"X": {"live_days": "T/Th", "live_time": "1:00pm", "live_end_time": "2:00pm"}}}))
            args = ["--schedule", str(sched)]
            self.assertEqual(guard.main(["--at", "2026-10-15T13:30", *args]), 1)
            self.assertEqual(guard.main(["--at", "2026-10-14T14:40", *args]), 0)

    def test_missing_start_time_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = Path(tmp) / "s.json"
            sched.write_text(json.dumps({"courses": {"X": {"live_days": "M", "live_end_time": "2:00pm"}}}))
            self.assertEqual(guard.main(["--at", "2026-10-14T14:40", "--schedule", str(sched)]), 1)


if __name__ == "__main__":
    unittest.main()
