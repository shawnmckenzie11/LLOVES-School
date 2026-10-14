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


class ConfirmedScheduleTests(unittest.TestCase):
    """Shawn's confirmed M/W/F schedule (2026-10-14), America/Toronto."""

    def test_windows_are_pinned(self) -> None:
        from datetime import time as t

        wins = {w["label"]: w for w in guard.class_windows(json.loads(guard.SCHEDULE.read_text()))}
        self.assertEqual(set(wins), {"MCR3U", "MCR3U-2", "MCF3M"})
        expect = {
            "MCR3U": (t(9, 15), t(10, 30)),
            "MCR3U-2": (t(10, 40), t(11, 55)),
            "MCF3M": (t(14, 0), t(15, 15)),
        }
        for label, (start, end) in expect.items():
            self.assertEqual(wins[label]["days"], {0, 2, 4}, label)
            self.assertEqual((wins[label]["start"], wins[label]["end"]), (start, end), label)

    def test_tuesday_930_allowed_wednesday_905_blocked(self) -> None:
        self.assertEqual(guard.main(["--at", "2026-10-20T09:30"]), 0)  # Tue
        self.assertEqual(guard.main(["--at", "2026-10-14T09:05"]), 1)  # Wed, MCR3U pre-window

    def test_no_guard_tue_thu_weekend(self) -> None:
        for day in ("2026-10-20", "2026-10-22", "2026-10-24", "2026-10-25"):
            for hhmm in ("09:30", "11:00", "14:30"):
                self.assertEqual(guard.main(["--at", f"{day}T{hhmm}"]), 0, (day, hhmm))

    def test_mon_wed_fri_classes_blocked(self) -> None:
        for day in ("2026-10-19", "2026-10-21", "2026-10-23"):
            for hhmm in ("09:30", "11:00", "14:30"):
                self.assertEqual(guard.main(["--at", f"{day}T{hhmm}"]), 1, (day, hhmm))


class FailClosedTests(unittest.TestCase):
    """A missing, unreadable or empty schedule refuses the deploy."""

    def _run(self, text: str | None, *extra: str) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.json"
            if text is not None:
                path.write_text(text)
            return guard.main(["--at", "2026-10-20T09:30", "--schedule", str(path), *extra])

    def test_missing_file_fails(self) -> None:
        self.assertEqual(self._run(None), 1)

    def test_unreadable_json_fails(self) -> None:
        self.assertEqual(self._run("{not json"), 1)

    def test_empty_courses_fails(self) -> None:
        self.assertEqual(self._run(json.dumps({"tz": "America/Toronto", "courses": {}})), 1)
        self.assertEqual(self._run(json.dumps({"tz": "America/Toronto"})), 1)

    def test_bad_tz_fails(self) -> None:
        body = {"tz": "Mars/Olympus", "courses": {"X": {"live_days": "M", "live_time": "1:00pm", "live_end_time": "2:00pm"}}}
        self.assertEqual(self._run(json.dumps(body)), 1)

    def test_override_still_deploys(self) -> None:
        self.assertEqual(self._run(None, "--override"), 0)

    def test_tz_field_is_used(self) -> None:
        body = {"tz": "America/Vancouver", "courses": {"X": {"live_days": "T", "live_time": "9:00am", "live_end_time": "10:00am"}}}
        # --at is local to the schedule tz: 9:30 Vancouver on a Tuesday is blocked.
        self.assertEqual(self._run(json.dumps(body)), 1)
        self.assertEqual(guard.schedule_tz({}).key, "America/Toronto")

    def test_repo_file_uses_toronto(self) -> None:
        self.assertEqual(guard.schedule_tz(json.loads(guard.SCHEDULE.read_text())).key, "America/Toronto")


if __name__ == "__main__":
    unittest.main()
