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
    _slide_bodies,
    build_timeline,
    generate_live_class_slides,
    lesson_key_from_timeline,
    select_live_problems,
    should_reuse_stored_deck,
    strand_from_module,
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
        self.assertTrue(any(h == "A" or h.startswith("A/") for h in hints))
        self.assertTrue(any(h == "B" or h.startswith("B/") for h in hints))
        self.assertTrue(any(h == "C" or h.startswith("C/") for h in hints))
        titles = {row["title"] for row in bank}
        self.assertIn("How can Mr. M quickly check if his class is jigsaw-able?", titles)
        self.assertIn("Groupable after one student leaves — why?", titles)
        ministry = [row for row in bank if row["source"] == "ontario_curriculum_example"]
        self.assertGreaterEqual(len(ministry), 6)

    def test_m1c1_selects_jigsaw_not_fence(self) -> None:
        """Module 1 live 1 prefers A/M1C1 contest; M1C2 does not."""
        bank = self.school.list_live_problems(ontario_code="MCF3M", active_only=True)
        m1c1 = select_live_problems(bank, strand="A", lesson_key="M1C1")
        self.assertIsNotNone(m1c1["contest"])
        self.assertIn("jigsaw-able", m1c1["contest"]["title"])
        self.assertEqual(m1c1["warmup"]["module_hint"], "A/M1C1")
        self.assertGreaterEqual(len(m1c1["standards"]), 3)
        self.assertLessEqual(len(m1c1["standards"]), 5)
        for row in m1c1["standards"]:
            self.assertIn("M1C1", str(row["module_hint"]).upper())
        m1c2 = select_live_problems(bank, strand="A", lesson_key="M1C2")
        self.assertIsNotNone(m1c2["contest"])
        self.assertNotIn("jigsaw-able", m1c2["contest"]["title"])
        self.assertIn("one student leaves", m1c2["contest"]["title"].lower())

    def test_timeline_lesson_key_and_slide_bodies(self) -> None:
        """M1C1 key from timeline; breakout is contest-only; consolidation lists standards."""
        placements = {
            "2026-09-08": {"module": 1, "live": False},
            "2026-09-09": {"module": 1, "live": False},
            "2026-09-10": {"module": 1, "live": True},
            "2026-09-14": {"module": 1, "live": True},
        }
        snap = build_timeline(
            meeting=date(2026, 9, 10),
            placements=placements,
            module_titles={1: "Quadratics"},
        )
        self.assertEqual(snap["live_index"], 1)
        self.assertEqual(lesson_key_from_timeline(snap), "M1C1")
        bank = self.school.list_live_problems(ontario_code="MCF3M", active_only=True)
        picked = select_live_problems(
            bank, strand=snap["strand"], lesson_key="M1C1"
        )
        slides = {s["title"]: s["body"] for s in _slide_bodies(snap, picked, {})}
        intro = slides["Intro"]
        self.assertIn("Quadratics", intro)
        self.assertIn("Live Class 1 of", intro)
        self.assertIn("course introduction", intro.lower())
        self.assertIn("first Quadratics live problem-solving", intro)
        self.assertNotIn("4×4", intro)
        self.assertNotIn("initials grid", intro.lower())
        breakout = slides["Team Breakout"]
        self.assertIn("jigsaw-able", breakout)
        self.assertIn("Your task:", breakout)
        self.assertNotIn("Equal-group factorizations", breakout)
        self.assertNotIn("f(n) for number of groups", breakout)
        consol = slides["Consolidation"]
        self.assertIn("Equal-group factorizations of 12 and 18", consol)
        self.assertIn("Why 16 supports four equal groups", consol)
        self.assertNotIn("jigsaw-able", consol)

    def test_seed_live_problems_upserts_by_title(self) -> None:
        """Re-seed updates stem for the same ontario_code/kind/title."""
        title = "How can Mr. M quickly check if his class is jigsaw-able?"
        before = [
            row
            for row in self.school.list_live_problems(ontario_code="MCF3M")
            if row["title"] == title
        ]
        self.assertEqual(len(before), 1)
        self.school.upsert_live_problem(
            {**before[0], "stem_html": "<p>stale stem</p>"}
        )
        self.school.seed_live_problems()
        after = [
            row
            for row in self.school.list_live_problems(ontario_code="MCF3M")
            if row["title"] == title
        ]
        self.assertEqual(len(after), 1)
        self.assertNotIn("stale stem", after[0]["stem_html"])
        self.assertIn("16 is his favourite", after[0]["stem_html"])

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
        self.assertNotIn("Create Lesson Slides", html)
        self.assertNotIn("ap-create-slides", html)
        self.assertNotIn("Connect Google Slides", html)
        self.assertIn("ap-evidence-panel", html)
        slides_tab = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        slides_html = slides_tab.get_data(as_text=True)
        self.assertIn("Lesson Slides", slides_html)
        self.assertIn("Connect Google Slides", slides_html)
        self.assertIn("ls-build", slides_html)

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
        self.assertIn("auth%2Fdrive", loc)
        self.assertNotIn("drive.file", loc)

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

    def test_parse_google_file_id(self) -> None:
        """URLs and bare ids parse; junk raises."""
        from slides_template import parse_google_file_id

        fid = "1yn2JQQNyd1AfBrrRK0_BFx4ibkdIgXTgR7bu0QzesRU"
        self.assertEqual(parse_google_file_id(fid), fid)
        self.assertEqual(
            parse_google_file_id(f"https://docs.google.com/presentation/d/{fid}/edit"),
            fid,
        )
        self.assertEqual(
            parse_google_file_id(f"https://drive.google.com/file/d/{fid}/view"),
            fid,
        )
        self.assertEqual(parse_google_file_id(""), "")
        with self.assertRaises(ValueError):
            parse_google_file_id("not-an-id")

    def test_reuse_skips_mock_html_when_talking_to_google(self) -> None:
        """Leftover mock- ids must not satisfy Create Lesson Slides on real Drive."""
        self.assertTrue(
            should_reuse_stored_deck(
                presentation_id="mock-2-2026-09-11",
                presentation_url="/staff/offerings/2/slides/2026-09-11.html",
                force_regenerate=False,
                use_mock=True,
            )
        )
        self.assertFalse(
            should_reuse_stored_deck(
                presentation_id="mock-2-2026-09-11",
                presentation_url="/staff/offerings/2/slides/2026-09-11.html",
                force_regenerate=False,
                use_mock=False,
            )
        )
        self.assertTrue(
            should_reuse_stored_deck(
                presentation_id="1realGoogleFileIdxxxxxxxxxxxxxx",
                presentation_url="https://docs.google.com/presentation/d/1real/edit",
                force_regenerate=False,
                use_mock=False,
            )
        )

    def test_assign_stores_theme_defaults_and_pasted_ids(self) -> None:
        """Assign Course empty fields use school defaults; paste stores the id."""
        from live_class_constants import DEFAULT_SLIDES_TEMPLATE_ID

        self.assertEqual(
            self.offering.get("slides_template_id"), DEFAULT_SLIDES_TEMPLATE_ID
        )
        self.school.create_library("MCF3M", origin="upload")
        staff = self.school.register_staff("themeassign@gmail.com")
        custom = "1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        rv = self.client.post(
            f"/it/staff/{int(staff['id'])}/assign",
            data={
                "ontario_code": "MCF3M",
                "live_days": "M/W/F",
                "live_time": "2:00pm",
                "slides_template": f"https://docs.google.com/presentation/d/{custom}/edit",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True))
        offered = self.school.get_offering_for(
            int(self.school.get_active_semester()["id"]),
            "MCF3M",
            int(staff["id"]),
        )
        assert offered is not None
        self.assertEqual(offered.get("slides_template_id"), custom)

    def test_template_fill_requests_use_named_layouts(self) -> None:
        """batchUpdate requests target TITLE_CLASS placeholders without Google."""
        from slides_template import build_template_fill_requests, layout_fill_values

        presentation = {
            "layouts": [
                {
                    "objectId": "layoutTitle",
                    "layoutProperties": {"name": "TITLE_CLASS"},
                },
                {
                    "objectId": "layoutOpen",
                    "layoutProperties": {"name": "OPEN_QUESTIONS_ROUND"},
                },
            ],
            "slides": [
                {
                    "objectId": "s1",
                    "slideProperties": {"layoutObjectId": "layoutTitle"},
                    "pageElements": [
                        {
                            "objectId": "t1",
                            "shape": {
                                "placeholder": {"type": "TITLE"},
                                "text": {
                                    "textElements": [
                                        {"textRun": {"content": "{{COURSE_LESSON}}\n"}}
                                    ]
                                },
                            },
                        }
                    ],
                },
                {
                    "objectId": "s2",
                    "slideProperties": {"layoutObjectId": "layoutOpen"},
                    "pageElements": [
                        {
                            "objectId": "openBody",
                            "shape": {
                                "placeholder": {"type": "BODY"},
                                "text": {"textElements": [{"textRun": {"content": "\n"}}]},
                            },
                        }
                    ],
                },
            ],
        }
        values = layout_fill_values(
            ontario_code="MCF3M",
            lesson_key="M1C1",
            join_code="ABCD1234",
            selection={
                "contest": {
                    "title": "Jigsaw",
                    "stem_html": "<p>stem</p>",
                    "task_html": "<p>task</p>",
                },
                "standards": [],
            },
        )
        reqs = build_template_fill_requests(presentation, values)
        kinds = [next(iter(r)) for r in reqs]
        self.assertIn("replaceAllText", kinds)
        replaced = [
            r["replaceAllText"]["replaceText"]
            for r in reqs
            if "replaceAllText" in r
            and r["replaceAllText"]["containsText"]["text"] == "{{COURSE_LESSON}}"
        ]
        self.assertEqual(replaced, ["MCF3M: M1C1"])
        self.assertFalse(any(r.get("insertText", {}).get("objectId") == "openBody" for r in reqs))


class StrandFromModuleLiveClassTests(unittest.TestCase):
    """ELC MCF3M module → strand without a map file."""

    def test_mcf3m_modules_two_five_seven(self) -> None:
        """M2→A, M5→C, M7→B (ELC pack, empty titles, no map)."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            self.assertEqual(strand_from_module("", 2, "MCF3M", cache_root=cache), "A")
            self.assertEqual(strand_from_module("", 5, "MCF3M", cache_root=cache), "C")
            self.assertEqual(strand_from_module("", 7, "MCF3M", cache_root=cache), "B")


if __name__ == "__main__":
    unittest.main()
