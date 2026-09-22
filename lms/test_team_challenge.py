#!/usr/bin/env python3
"""Play + Action mounts the course/slot team-challenge, not a fixed MCF3M stem."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_constants import MCR3U_M1C1_TEAM_CHALLENGE_QUESTION  # noqa: E402
from live_media import DEFAULT_LIVE_MEDIA_STEM, is_c1_real_slice  # noqa: E402
from team_challenge import (  # noqa: E402
    MCR3U_M1C1_MEDIA_URL,
    is_team_challenge_payload,
    live_class_seed_media,
    resolve_team_challenge,
    staff_team_challenge_prompt_payload,
    team_challenge_media_url,
    uses_c1_real_slice,
)


class TeamChallengeResolveTests(unittest.TestCase):
    """Pure resolver: parabola stays MCF3M M1C1-only."""

    def test_real_slice_is_mcf3m_m1c1_only(self) -> None:
        """MCR3U C1 must not inherit the parabola media ride."""
        self.assertTrue(uses_c1_real_slice("MCF3M", "M1", "C1"))
        self.assertFalse(uses_c1_real_slice("MCR3U", "M1", "C1"))
        self.assertFalse(uses_c1_real_slice("MCF3M", "M1", "C2"))

    def test_media_url_is_per_course_slot(self) -> None:
        """Each team-challenge can carry its own graph, including y = √x."""
        self.assertIn("m1c1-c1-real-slice", team_challenge_media_url("MCF3M", "M1", "C1"))
        self.assertEqual(team_challenge_media_url("MCR3U", "M1", "C1"), MCR3U_M1C1_MEDIA_URL)
        seed = live_class_seed_media("MCR3U", "M1", "C1")
        self.assertIsNotNone(seed)
        self.assertEqual(seed["url"], MCR3U_M1C1_MEDIA_URL)
        self.assertIn("inputs", seed["stem"].lower())
        self.assertEqual(team_challenge_media_url("MCR3U", "M1", "C2"), "")
        c2 = live_class_seed_media("MCR3U", "M1", "C2")
        self.assertIsNotNone(c2)
        assert c2 is not None
        self.assertIn("mcr3u-m1c2-parent-transformations.html", c2["url"])
        self.assertIn("Exploratory media", c2["title"])
        c4 = live_class_seed_media("MCR3U", "M1", "C4")
        self.assertIsNotNone(c4)
        assert c4 is not None
        self.assertIn("mcr3u-m1c4-parent-transformations.html", c4["url"])
        self.assertIn("multi-parent", c4["title"])


class TeamChallengeLiveApiTests(unittest.TestCase):
    """Staff Play + Action mounts the selected stem for each course."""

    def _boot(self, ontario_code: str) -> None:
        """Isolated app with one staff class and an open live session.

        Args:
            ontario_code: Course to assign the teacher.
        """
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
            teacher_user_id=int(self.teacher["id"]), ontario_code=ontario_code
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
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])

    def tearDown(self) -> None:
        """Drop the temp sqlite tree."""
        self.tmp.cleanup()

    def _play_action(self) -> None:
        """SET Action, then enter Play."""
        posted = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C1",
                "round": "action",
                "round_flags": {
                    "minds_on": False,
                    "action": True,
                    "consolidation": False,
                },
            },
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        play = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"stage": "play"},
        )
        self.assertEqual(play.status_code, 200, play.get_json())

    def test_mcf3m_m1c1_action_keeps_parabola_stem(self) -> None:
        """MCF3M M1C1 Action still uses the Real-slice parabola question."""
        self._boot("MCF3M")
        row = resolve_team_challenge(
            self.school,
            class_id=self.class_id,
            ontario_code="MCF3M",
            live_module="M1",
            live_slot="C1",
        )
        self.assertTrue(row["use_real_slice"])
        self.assertEqual(row["question"], DEFAULT_LIVE_MEDIA_STEM)
        self._play_action()
        prompt = self.school.get_active_live_prompt(self.session_id)
        self.assertIsNotNone(prompt)
        payload = prompt.get("payload") or {}
        self.assertTrue(is_team_challenge_payload(payload))
        self.assertNotIn("placeholder", payload)
        self.assertIn("parabola", str(payload.get("prompt") or "").lower())
        media = self.school.live_session_active_media_payload(self.session_id)
        self.assertTrue(is_c1_real_slice(media))

    def test_mcr3u_m1c1_action_mounts_nested_square_root(self) -> None:
        """MCR3U M1C1 Action shows Nested Square-Root Range, not the parabola."""
        self._boot("MCR3U")
        row = resolve_team_challenge(
            self.school,
            class_id=self.class_id,
            ontario_code="MCR3U",
            live_module="M1",
            live_slot="C1",
        )
        self.assertFalse(row["use_real_slice"])
        self.assertEqual(row["media_url"], MCR3U_M1C1_MEDIA_URL)
        self.assertIn("inputs", row["question"].lower())
        mounted = staff_team_challenge_prompt_payload(row)
        self.assertNotIn("placeholder", mounted)
        self._play_action()
        prompt = self.school.get_active_live_prompt(self.session_id)
        self.assertIsNotNone(prompt)
        payload = prompt.get("payload") or {}
        self.assertTrue(is_team_challenge_payload(payload))
        self.assertNotIn("placeholder", payload)
        text = str(payload.get("prompt") or "")
        self.assertIn(MCR3U_M1C1_TEAM_CHALLENGE_QUESTION.split("?")[0], text)
        self.assertNotIn("parabola", text.lower())
        self.assertNotIn("Share what your", text)
        media = self.school.live_session_active_media_payload(self.session_id)
        self.assertFalse(is_c1_real_slice(media))
        self.assertIn("mcr3u-m1c1-sqrt.html", str(media.get("url") or ""))
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertTrue((teacher.get("unlocks") or {}).get("media"))
        self.assertEqual(teacher.get("layout_preset"), "media_questions")
        state = self.school.get_live_session_state(self.session_id)
        self.assertEqual(
            (state.get("active_prompt") or {}).get("payload", {}).get("item_id"),
            "team-challenge",
        )

    def test_lesson_slides_pick_wins_for_mcr3u(self) -> None:
        """A saved Lesson Slides question is what Play + Action mounts."""
        self._boot("MCR3U")
        self.school.upsert_lesson_slide_deck(
            self.class_id,
            1,
            1,
            presentation_id="mock-mcr3u-m1c1",
            presentation_url="/staff/mock",
            fill_json={
                "team_challenge": {
                    "title": "Custom MCR3U ask",
                    "context": "A rule subtracts one square root from another.",
                    "question": "Which inputs are allowed in this class?",
                }
            },
        )
        row = resolve_team_challenge(
            self.school,
            class_id=self.class_id,
            ontario_code="MCR3U",
            live_module="M1",
            live_slot="C1",
        )
        self.assertEqual(row["source"], "lesson_slides")
        self.assertEqual(row["question"], "Which inputs are allowed in this class?")
        self.assertEqual(row["media_url"], MCR3U_M1C1_MEDIA_URL)
        self._play_action()
        payload = (self.school.get_active_live_prompt(self.session_id) or {}).get(
            "payload"
        ) or {}
        self.assertIn("Which inputs are allowed in this class?", payload.get("prompt"))
        self.assertNotIn("placeholder", payload)
        media = self.school.live_session_active_media_payload(self.session_id)
        self.assertIn("mcr3u-m1c1-sqrt.html", str((media or {}).get("url") or ""))

    def test_mcr3u_m1c1_staff_get_seeds_sqrt_media(self) -> None:
        """Join-stage staff media GET mounts y = √x for MCR3U M1C1."""
        self._boot("MCR3U")
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C1"},
        )
        got = self.client.get(f"/api/live-sessions/{self.session_id}/active-media")
        self.assertEqual(got.status_code, 200, got.get_json())
        media = got.get_json().get("active_media") or {}
        self.assertIn("mcr3u-m1c1-sqrt.html", str(media.get("url") or ""))
        path = Path(__file__).resolve().parent / "static" / "live-media" / "mcr3u-m1c1-sqrt.html"
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
