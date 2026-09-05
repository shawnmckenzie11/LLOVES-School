#!/usr/bin/env python3
"""Live-class slides generator, problem bank, and process-evidence observations."""

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
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_slides import (  # noqa: E402
    build_timeline,
    generate_live_class_slides,
    select_live_problems,
)
from live_class_constants import PROCESS_KEYS  # noqa: E402


class LiveClassSlidesTests(unittest.TestCase):
    """Timeline, selection, mock generate, observations, and IT CRUD."""

    def setUp(self) -> None:
        """Isolated sqlite + Flask client as the IT operator."""
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
        self._login_it()
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.school.get_user_by_email("solutions@mckenzian.com")["id"]),
            ontario_code="MCF3M",
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch", "Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_it(self) -> None:
        """Mock Google + 2SV as solutions@."""
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def test_math_processes_and_phrases_seeded(self) -> None:
        """Seven Ontario processes and default phrases exist after init."""
        procs = self.school.list_math_processes()
        self.assertEqual(len(procs), 7)
        self.assertEqual({p["process_key"] for p in procs}, set(PROCESS_KEYS))
        phrases = self.school.list_quick_phrases(active_only=True)
        self.assertGreaterEqual(len(phrases), 10)
        labels = {p["label"] for p in phrases}
        self.assertIn("Proposed a strategy", labels)
        self.assertIn("Made a conjecture", labels)

    def test_live_problem_bank_covers_strands(self) -> None:
        """MCF3M bank has ministry examples plus original contest items per strand."""
        bank = self.school.list_live_problems(ontario_code="MCF3M", active_only=True)
        self.assertGreaterEqual(len(bank), 12)
        kinds = {row["kind"] for row in bank}
        self.assertIn("warmup", kinds)
        self.assertIn("contest", kinds)
        self.assertIn("standard", kinds)
        contests = [row for row in bank if row["kind"] == "contest"]
        hints = {str(row["module_hint"]) for row in contests}
        self.assertTrue({"A", "B", "C"} <= hints)
        ministry = [row for row in bank if row["source"] == "ontario_curriculum_example"]
        self.assertGreaterEqual(len(ministry), 6)

    def test_timeline_y_of_n(self) -> None:
        """Live Class Y of N counts live placements inside the module."""
        placements = {
            "2026-09-09": {"module": 2, "live": True},
            "2026-09-11": {"module": 2, "live": True},
            "2026-09-14": {"module": 2, "live": True},
            "2026-09-16": {"module": 3, "live": True},
            "2026-09-08": {"module": 1, "live": True},
        }
        titles = {1: "Intro", 2: "Quadratics", 3: "Exponential"}
        snap = build_timeline(
            meeting=date(2026, 9, 11),
            placements=placements,
            module_titles=titles,
        )
        self.assertEqual(snap["live_index"], 2)
        self.assertEqual(snap["live_count"], 3)
        self.assertIn("2 of 3", snap["headline"])
        self.assertIn("Intro", snap["so_far"])
        self.assertEqual(snap["strand"], "A")

    def test_problem_selection_prefers_strand_and_processes(self) -> None:
        """Contest needs problem_solving; standards share a process and strand."""
        bank = self.school.list_live_problems(ontario_code="MCF3M", active_only=True)
        picked = select_live_problems(bank, strand="B")
        self.assertIsNotNone(picked["contest"])
        self.assertIn("problem_solving", picked["contest"]["processes"])
        self.assertGreaterEqual(len(picked["standards"]), 1)
        self.assertTrue(str(picked["contest"]["module_hint"]).upper().startswith("B"))

    def test_mock_generate_is_idempotent(self) -> None:
        """Second generate reuses presentation_id unless force_regenerate."""
        self.client.get("/auth/google/slides")
        self.client.post(f"/staff/class/{self.class_id}/run-live")
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        first = generate_live_class_slides(
            self.school,
            class_id=self.class_id,
            meeting_date=date(2026, 9, 9),
            user=user,
        )
        self.assertTrue(str(first["presentation_id"]).startswith("mock-"))
        self.assertTrue(first["presentation_url"])
        second = generate_live_class_slides(
            self.school,
            class_id=self.class_id,
            meeting_date=date(2026, 9, 9),
            user=user,
        )
        self.assertTrue(second.get("reused"))
        self.assertEqual(first["presentation_id"], second["presentation_id"])
        forced = generate_live_class_slides(
            self.school,
            class_id=self.class_id,
            meeting_date=date(2026, 9, 9),
            user=user,
            force_regenerate=True,
        )
        self.assertFalse(forced.get("reused"))
        self.assertTrue(str(forced["presentation_id"]).startswith("mock-"))

    def test_http_generate_after_run_live(self) -> None:
        """POST live-slides after Class Date returns an Open-able mock URL."""
        self.client.get("/auth/google/slides")
        self.client.post(f"/staff/class/{self.class_id}/run-live")
        rv = self.client.post(
            f"/api/classes/{self.class_id}/live-slides",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertTrue(body.get("presentation_url"))
        course = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = course.get_data(as_text=True)
        self.assertIn("Open slides", html)
        self.assertIn("ap-evidence-panel", html)

    def test_observation_crud_and_coverage(self) -> None:
        """Student-scope observation, coverage counts, then delete."""
        self.client.post(f"/staff/class/{self.class_id}/run-live")
        live = self.school.get_active_live_session_for_class(self.class_id)
        students = self.school.game.dashboard(self.class_id)["students"]
        sid = int(students[0]["id"])
        created = self.client.post(
            f"/api/live-sessions/{int(live['id'])}/observations",
            json={
                "scope": "student",
                "student_ids": [sid],
                "note": "Proposed a strategy",
                "process_keys": ["problem_solving"],
                "source": "quick_phrase",
            },
        )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        obs_id = int(created.get_json()["observation"]["id"])
        cov = self.client.get(
            f"/api/live-sessions/{int(live['id'])}/observations/coverage"
        )
        self.assertEqual(cov.status_code, 200)
        by_student = cov.get_json()["by_student"]
        self.assertEqual(by_student[str(sid)]["problem_solving"], 1)
        patched = self.client.patch(
            f"/api/live-sessions/{int(live['id'])}/observations/{obs_id}",
            json={"note": "Made a conjecture", "evidence_strength": "clear"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.get_json()["observation"]["note"], "Made a conjecture")
        deleted = self.client.delete(
            f"/api/live-sessions/{int(live['id'])}/observations/{obs_id}"
        )
        self.assertEqual(deleted.status_code, 200)
        listed = self.client.get(
            f"/api/live-sessions/{int(live['id'])}/observations"
        )
        self.assertEqual(listed.get_json()["observations"], [])

    def test_it_lists_problems_and_phrases(self) -> None:
        """IT dashboard HTML and APIs expose live problems and quick phrases."""
        dash = self.client.get("/it")
        html = dash.get_data(as_text=True)
        self.assertIn("Live problems", html)
        self.assertIn("Quick phrases", html)
        problems = self.client.get("/api/it/live-problems")
        self.assertEqual(problems.status_code, 200)
        self.assertGreater(len(problems.get_json()["problems"]), 0)
        phrases = self.client.get("/api/it/quick-phrases")
        self.assertEqual(phrases.status_code, 200)
        self.assertGreater(len(phrases.get_json()["phrases"]), 0)
        added = self.client.post(
            "/api/it/quick-phrases",
            json={
                "process_key": "communicating",
                "label": "Used a precise term",
                "category": "general",
                "description": "Named a concept accurately.",
            },
        )
        self.assertEqual(added.status_code, 200)

    def test_slides_connect_mock_stores_token(self) -> None:
        """LOCAL/TESTING Connect Google Slides stores a mock refresh token."""
        rv = self.client.get("/auth/google/slides")
        self.assertEqual(rv.status_code, 302)
        loc = rv.headers.get("Location") or ""
        self.assertIn("slides=mock", loc)
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        token = self.school.get_google_api_token(int(user["id"]))
        self.assertIsNotNone(token)
        status = self.client.get("/api/auth/slides-status")
        self.assertTrue(status.get_json()["connected"])
        self.assertTrue(status.get_json()["allowed"])

    def test_connect_goes_to_google_when_creds_exist_outside_tests(self) -> None:
        """LOCAL_DEV_LOGIN must not swallow Connect when a Web client is set."""
        from unittest.mock import patch

        self.app.config["TESTING"] = False
        with patch("auth.google_client_id", return_value="id.apps.googleusercontent.com"):
            with patch("auth.google_client_secret", return_value="secret"):
                rv = self.client.get("/auth/google/slides")
        self.app.config["TESTING"] = True
        self.assertEqual(rv.status_code, 302)
        loc = rv.headers.get("Location") or ""
        self.assertIn("accounts.google.com", loc)
        self.assertIn("presentations", loc)

    def test_other_teacher_cannot_connect_slides(self) -> None:
        """Staff Gmail is not on the Slides operator allowlist."""
        self.school.register_staff("other@gmail.com")
        other = self.app.test_client()
        other.get("/auth/google?portal=staff")
        other.get("/auth/google/callback?email=other@gmail.com&name=O")
        user = self.school.get_user_by_email("other@gmail.com")
        other.post("/verify-email", data={"code": user["verification_code"]})
        rv = other.get("/auth/google/slides")
        self.assertEqual(rv.status_code, 403)


if __name__ == "__main__":
    unittest.main()
