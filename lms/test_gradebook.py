#!/usr/bin/env python3
"""Module portfolio auto-score from the Attendance & Participation tracker."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from gradebook import (  # noqa: E402
    even_module_windows,
    evaluate_module_portfolio,
    gradebook_overview_text,
    math_content_days,
    module_window_for,
)


class ModuleWindowTests(unittest.TestCase):
    """Even ~2-week math module spread from the 2026–27 S1 calendar."""

    def test_eight_even_windows_skip_intro_and_review(self) -> None:
        """80 content days split into eight 10-day modules; M1 is Sep 10–23."""
        days = math_content_days()
        self.assertEqual(days[0], date(2026, 9, 10))
        self.assertEqual(days[-1], date(2027, 1, 15))
        self.assertNotIn(date(2026, 9, 8), days)
        self.assertNotIn(date(2026, 9, 9), days)
        self.assertNotIn(date(2027, 1, 18), days)
        self.assertNotIn(date(2027, 1, 25), days)
        windows = even_module_windows(days)
        self.assertEqual(len(windows), 8)
        self.assertTrue(all(w["day_count"] == 10 for w in windows))
        m1 = module_window_for(1)
        assert m1 is not None
        self.assertEqual(m1["start"], "2026-09-10")
        self.assertEqual(m1["end"], "2026-09-23")


class PortfolioCriteriaTests(unittest.TestCase):
    """Pure evaluator: 3 sessions + 10 R1 + 10 R3 + reflections → 100."""

    def _window(self) -> dict:
        """Module 1 even-split window."""
        window = module_window_for(1)
        assert window is not None
        return window

    def _rows(
        self,
        *,
        present_dates: list[str],
        r1: float = 0,
        r3: float = 0,
        extra_r1_date: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Build tracker sessions/scores for student 7."""
        sessions = []
        scores = []
        for i, iso in enumerate(present_dates, start=1):
            sessions.append(
                {
                    "id": i,
                    "starts_at": f"{iso}T14:00:00",
                    "status": "ended",
                }
            )
            scores.append(
                {
                    "session_id": i,
                    "student_id": 7,
                    "present": 1,
                    "late": 0,
                    "points_r1": r1 if i == 1 else 0,
                    "points_r3": r3 if i == 1 else 0,
                }
            )
        if extra_r1_date:
            sessions.append(
                {
                    "id": 99,
                    "starts_at": f"{extra_r1_date}T14:00:00",
                    "status": "ended",
                }
            )
            scores.append(
                {
                    "session_id": 99,
                    "student_id": 7,
                    "present": 1,
                    "late": 0,
                    "points_r1": 50,
                    "points_r3": 50,
                }
            )
        return sessions, scores

    def test_all_four_criteria_earn_100(self) -> None:
        """Fri office + two live days, 10/10 points, reflections → 100."""
        sessions, scores = self._rows(
            present_dates=["2026-09-11", "2026-09-14", "2026-09-16"],
            r1=10,
            r3=10,
        )
        result = evaluate_module_portfolio(
            student_id=7,
            window=self._window(),
            days_label="M/W/F",
            instructional=set(math_content_days()) | {date(2026, 9, 8), date(2026, 9, 9)},
            sessions=sessions,
            score_rows=scores,
            reflections_complete=True,
        )
        self.assertTrue(result["earned_100"])
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["sessions"], 3)

    def test_missing_reflections_stays_pending(self) -> None:
        """Attendance and points without reflections do not auto-zero."""
        sessions, scores = self._rows(
            present_dates=["2026-09-11", "2026-09-14", "2026-09-16"],
            r1=12,
            r3=11,
        )
        result = evaluate_module_portfolio(
            student_id=7,
            window=self._window(),
            days_label="M/W/F",
            instructional=set(math_content_days()) | {date(2026, 9, 8), date(2026, 9, 9)},
            sessions=sessions,
            score_rows=scores,
            reflections_complete=False,
        )
        self.assertFalse(result["earned_100"])
        self.assertIsNone(result["score"])
        self.assertTrue(result["sessions_met"])
        self.assertTrue(result["r1_met"])
        self.assertTrue(result["r3_met"])

    def test_points_outside_module_window_do_not_count(self) -> None:
        """Intro-day points (Sep 9) are ignored for Module 1."""
        sessions, scores = self._rows(
            present_dates=["2026-09-11", "2026-09-14"],
            r1=0,
            r3=0,
            extra_r1_date="2026-09-09",
        )
        result = evaluate_module_portfolio(
            student_id=7,
            window=self._window(),
            days_label="M/W/F",
            instructional=set(math_content_days()) | {date(2026, 9, 8), date(2026, 9, 9)},
            sessions=sessions,
            score_rows=scores,
            reflections_complete=True,
        )
        self.assertEqual(result["points_r1"], 0.0)
        self.assertEqual(result["sessions"], 2)
        self.assertFalse(result["earned_100"])

    def test_custom_min_sessions_can_unearn_100(self) -> None:
        """Raising the session floor from 3 to 4 leaves a 3-day student pending."""
        sessions, scores = self._rows(
            present_dates=["2026-09-11", "2026-09-14", "2026-09-16"],
            r1=10,
            r3=10,
        )
        result = evaluate_module_portfolio(
            student_id=7,
            window=self._window(),
            days_label="M/W/F",
            instructional=set(math_content_days()) | {date(2026, 9, 8), date(2026, 9, 9)},
            sessions=sessions,
            score_rows=scores,
            reflections_complete=True,
            rules={"min_sessions": 4, "min_r1": 10, "min_r3": 10},
        )
        self.assertFalse(result["earned_100"])
        self.assertFalse(result["sessions_met"])
        self.assertEqual(result["sessions_needed"], 4)


class OverviewTextTests(unittest.TestCase):
    """Staff Overview paragraph follows the live scheme."""

    def test_overview_names_weights_and_module_1_window(self) -> None:
        """Ministry split and Sep 10–23 window appear in the prose."""
        text = gradebook_overview_text(
            weights={"term": 65, "exam": 20, "participation": 15},
            window={"start": "2026-09-10", "end": "2026-09-23"},
            rules={
                "min_sessions": 3,
                "min_r1": 10,
                "min_r3": 10,
                "require_reflections": True,
            },
        )
        self.assertIn("65% Term", text)
        self.assertIn("20% Exam", text)
        self.assertIn("15% Attendance & Participation", text)
        self.assertIn("2026-09-10", text)
        self.assertIn("2026-09-23", text)
        self.assertIn("3+", text)


class GradebookApiTests(unittest.TestCase):
    """Staff gradebook logs 100% from tracker rows + reflections checkbox."""

    def setUp(self) -> None:
        """Isolated app with one MCF3M class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _populate(self) -> tuple[int, int, int]:
        """Create a M/W/F class with two students.

        Returns:
            class_id, aspen_id, birch_id.
        """
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = int(rv.get_json()["class"]["id"])
        students = self.client.get(
            f"/api/classes/{class_id}/dashboard?sort=az"
        ).get_json()["students"]
        aspen = next(s for s in students if s["codename"] == "Aspen")
        birch = next(s for s in students if s["codename"] == "Birch")
        return class_id, int(aspen["id"]), int(birch["id"])

    def _log_day(
        self, class_id: int, iso: str, present_ids: list[int], *, r1: float = 0, r3: float = 0
    ) -> None:
        """Finalize attendance on ``iso`` and stamp round points on present rows."""
        self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": iso},
        )
        done = self.client.post(
            f"/api/classes/{class_id}/game/finalize-attendance",
            json={"present_ids": present_ids, "meeting_date": iso},
        )
        self.assertEqual(done.status_code, 200)
        if r1 or r3:
            self.school.game.conn.execute(
                """
                UPDATE session_scores
                SET points_r1 = ?, points_r3 = ?, points = ?
                WHERE session_id = (
                    SELECT id FROM sessions
                    WHERE class_id = ? AND substr(starts_at, 1, 10) = ?
                    ORDER BY id DESC LIMIT 1
                ) AND student_id = ?
                """,
                (r1, r3, r1 + r3, class_id, iso, present_ids[0]),
            )
            self.school.game.conn.commit()

    def test_tracker_evidence_plus_reflections_logs_m1_100(self) -> None:
        """Aspen meets all four criteria; Birch does not."""
        class_id, aspen_id, birch_id = self._populate()
        self._log_day(class_id, "2026-09-11", [aspen_id], r1=10, r3=10)
        self._log_day(class_id, "2026-09-14", [aspen_id, birch_id])
        self._log_day(class_id, "2026-09-16", [aspen_id])
        flagged = self.client.post(
            f"/api/classes/{class_id}/module-reflections",
            json={"student_id": aspen_id, "module_number": 1, "complete": True},
        )
        self.assertEqual(flagged.status_code, 200)
        body = flagged.get_json()["gradebook"]
        aspen = body["module_1"]["students"][str(aspen_id)]
        birch = body["module_1"]["students"][str(birch_id)]
        self.assertTrue(aspen["earned_100"])
        self.assertEqual(aspen["score"], 100.0)
        self.assertEqual(aspen["sessions"], 3)
        self.assertFalse(birch["earned_100"])
        self.assertIsNone(birch["score"])
        term = next(c for c in body["categories"] if c["id"] == "term")
        self.assertEqual(term["scores"][str(aspen_id)], 100.0)
        tests = next(item for item in body["term_items"] if item["id"] == "m1_test")
        self.assertTrue(tests["placeholder"])
        self.assertIsNone(tests["scores"][str(aspen_id)])

    def test_legacy_weights_migrate_to_ministry_split(self) -> None:
        """Stored 15/60/25 rows rewrite to 15/65/20 on next read."""
        class_id, _, _ = self._populate()
        now = "2026-09-01T00:00:00"
        with self.school._lock:
            for category, pct in (
                ("participation", 15.0),
                ("term", 60.0),
                ("exam", 25.0),
            ):
                self.school.conn.execute(
                    """
                    INSERT INTO grade_category_weights (
                        class_id, category, weight_pct, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(class_id, category) DO UPDATE SET
                        weight_pct = excluded.weight_pct
                    """,
                    (class_id, category, pct, now),
                )
            self.school.conn.commit()
        body = self.client.get(f"/api/classes/{class_id}/gradebook").get_json()
        self.assertEqual(body["weights"]["term"], 65.0)
        self.assertEqual(body["weights"]["exam"], 20.0)

    def test_gradebook_scheme_lists_eight_editable_modules(self) -> None:
        """Term Mark JSON carries all eight module windows and default rules."""
        class_id, _, _ = self._populate()
        body = self.client.get(f"/api/classes/{class_id}/gradebook").get_json()
        self.assertEqual(body["scheme"]["weights"]["term"], 65.0)
        self.assertEqual(body["scheme"]["module_1"]["min_sessions"], 3)
        self.assertTrue(body["scheme"]["module_1"]["editable"])
        modules = body["scheme"]["modules"]
        self.assertEqual([m["number"] for m in modules], list(range(1, 9)))
        self.assertTrue(all(m["editable"] for m in modules))
        self.assertEqual(modules[0]["start"], "2026-09-10")
        self.assertEqual(modules[0]["min_sessions"], 3)

    def test_saving_scheme_re_evaluates_module_1(self) -> None:
        """Raising min sessions to 4 drops a previously earned 100."""
        class_id, aspen_id, _ = self._populate()
        self._log_day(class_id, "2026-09-11", [aspen_id], r1=10, r3=10)
        self._log_day(class_id, "2026-09-14", [aspen_id])
        self._log_day(class_id, "2026-09-16", [aspen_id])
        self.client.post(
            f"/api/classes/{class_id}/module-reflections",
            json={"student_id": aspen_id, "module_number": 1, "complete": True},
        )
        before = self.client.get(f"/api/classes/{class_id}/gradebook").get_json()
        self.assertTrue(before["module_1"]["students"][str(aspen_id)]["earned_100"])
        saved = self.client.post(
            f"/api/classes/{class_id}/grade-scheme",
            json={
                "weights": {"term": 65, "exam": 20, "participation": 15},
                "module_1": {
                    "min_sessions": 4,
                    "min_r1": 10,
                    "min_r3": 10,
                    "require_reflections": True,
                },
            },
        )
        self.assertEqual(saved.status_code, 200)
        book = saved.get_json()["gradebook"]
        aspen = book["module_1"]["students"][str(aspen_id)]
        self.assertFalse(aspen["earned_100"])
        self.assertEqual(aspen["sessions_needed"], 4)
        self.assertEqual(book["scheme"]["module_1"]["min_sessions"], 4)
        m1 = next(m for m in book["scheme"]["modules"] if m["number"] == 1)
        self.assertEqual(m1["min_sessions"], 4)

    def test_scheme_weights_must_sum_to_100(self) -> None:
        """Course weights that do not add to 100 are rejected."""
        class_id, _, _ = self._populate()
        rv = self.client.post(
            f"/api/classes/{class_id}/grade-scheme",
            json={
                "weights": {"term": 50, "exam": 20, "participation": 15},
                "module_1": {"min_sessions": 3, "min_r1": 10, "min_r3": 10},
            },
        )
        self.assertEqual(rv.status_code, 400)
        self.assertIn("100%", rv.get_json()["error"])

    def test_saving_module_2_rules_leaves_module_1(self) -> None:
        """Term Mark dropdown can persist Module 2 without rewriting Module 1."""
        class_id, _, _ = self._populate()
        saved = self.client.post(
            f"/api/classes/{class_id}/grade-scheme",
            json={
                "weights": {"term": 65, "exam": 20, "participation": 15},
                "module_number": 2,
                "module": {
                    "min_sessions": 5,
                    "min_r1": 12,
                    "min_r3": 8,
                    "require_reflections": False,
                },
            },
        )
        self.assertEqual(saved.status_code, 200)
        book = saved.get_json()["gradebook"]
        m1 = next(m for m in book["scheme"]["modules"] if m["number"] == 1)
        m2 = next(m for m in book["scheme"]["modules"] if m["number"] == 2)
        self.assertEqual(m1["min_sessions"], 3)
        self.assertEqual(m2["min_sessions"], 5)
        self.assertEqual(m2["min_r1"], 12.0)
        self.assertFalse(m2["require_reflections"])
        self.assertEqual(book["scheme"]["module_1"]["min_sessions"], 3)

    def test_scheme_rejects_module_out_of_range(self) -> None:
        """Module 9 is not a math content module."""
        class_id, _, _ = self._populate()
        rv = self.client.post(
            f"/api/classes/{class_id}/grade-scheme",
            json={
                "weights": {"term": 65, "exam": 20, "participation": 15},
                "module_number": 9,
                "module": {"min_sessions": 3, "min_r1": 10, "min_r3": 10},
            },
        )
        self.assertEqual(rv.status_code, 400)
        self.assertIn("module_number", rv.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
