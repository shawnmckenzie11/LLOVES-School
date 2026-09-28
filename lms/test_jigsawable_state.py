#!/usr/bin/env python3
"""Jigsawable class-size state machine on the active-media channel."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_metadata import load_live_class_metadata  # noqa: E402
from live_media import apply_active_media_update  # noqa: E402
from state_artifact import (  # noqa: E402
    JIGSAWABLE_EVENTS,
    JIGSAWABLE_FILL_ID,
    JIGSAWABLE_MEDIA_URL,
    STATE_MACHINE_FILLS,
    advance_jigsawable_state,
    initial_jigsawable_state,
)


class JigsawableHelperTests(unittest.TestCase):
    """Peel order, hold, and v0 limits without a session."""

    def test_real_slice_is_the_first_state_machine_fill(self) -> None:
        """Jigsawable is the second fill. Real-slice keeps its own peel blob."""
        self.assertEqual(STATE_MACHINE_FILLS[0]["fill_id"], "real-slice")
        self.assertEqual(STATE_MACHINE_FILLS[0]["artifact_kind"], "state_machine")
        self.assertEqual(STATE_MACHINE_FILLS[1]["fill_id"], JIGSAWABLE_FILL_ID)
        self.assertEqual(
            JIGSAWABLE_EVENTS,
            (
                "seed_16",
                "show_4x4",
                "priya_leaves_pause_15",
                "reveal_5x3",
                "show_algebra",
                "clear",
            ),
        )
        self.assertNotIn("seed_25", JIGSAWABLE_EVENTS)

    def test_reveal_stays_held_until_armed(self) -> None:
        """``reveal_5x3`` does not fire from the Priya peel or a same-request arm."""
        mounted = apply_active_media_update(None, url=JIGSAWABLE_MEDIA_URL)
        assert mounted is not None
        self.assertEqual(mounted["artifact_kind"], "state_machine")
        self.assertEqual(mounted["state_event"], "")
        self.assertEqual(mounted["toast"], "")
        current: dict | None = mounted
        for event in ("seed_16", "show_4x4", "priya_leaves_pause_15"):
            current = apply_active_media_update(current, state_event=event)
            assert current is not None
        self.assertEqual(current["wonder_cue"], "jigsawable:priya_leaves_pause_15")
        self.assertTrue(current["prediction_hold"])
        self.assertFalse(current["reveal_armed"])
        self.assertEqual(current["g"], 4)
        with self.assertRaises(ValueError):
            apply_active_media_update(current, state_event="reveal_5x3")
        with self.assertRaises(ValueError):
            apply_active_media_update(
                current, state_event="reveal_5x3", arm_reveal=True
            )
        armed = apply_active_media_update(current, arm_reveal=True)
        assert armed is not None
        self.assertEqual(armed["state_event"], "priya_leaves_pause_15")
        self.assertTrue(armed["reveal_armed"])
        revealed = apply_active_media_update(armed, state_event="reveal_5x3")
        assert revealed is not None
        self.assertEqual(revealed["g"], 5)
        self.assertEqual(revealed["n"], 3)
        self.assertEqual(revealed["centre"], 4)
        self.assertEqual(revealed["layout"], "grid_5x3")
        self.assertEqual(revealed["wonder_cue"], "")
        with self.assertRaises(ValueError):
            advance_jigsawable_state(revealed, state_event="seed_25")
        with self.assertRaises(ValueError):
            advance_jigsawable_state(initial_jigsawable_state(), class_size=25)

    def test_page_four_catalogues_do_not_rewrite_a_definition_stem(self) -> None:
        """Both M2 C1 page-4 rows mount the fill. No new bank question."""
        catalogue = (LMS_DIR / "seeds" / "live_items.json").read_text(encoding="utf-8")
        self.assertNotIn("in your own words", catalogue.lower())
        for code in ("MCR3U", "MCF3M"):
            meta = load_live_class_metadata(code, "M2", "C1")
            media = [
                row
                for row in meta["items"]
                if row.get("item_type") == "media"
            ]
            self.assertEqual(len(media), 1, code)
            self.assertEqual(media[0]["page_number"], 4, code)
            self.assertEqual(media[0]["stage"], "round", code)
            self.assertEqual(media[0]["fill_id"], JIGSAWABLE_FILL_ID, code)
            self.assertEqual(media[0]["artifact_kind"], "state_machine", code)
            questions = meta.get("questions") or []
            self.assertEqual(
                [row["id"] for row in questions],
                ["teams-spark", "meet-team"],
                code,
            )


class JigsawableChannelTests(unittest.TestCase):
    """Staff peels sync to the student poll and write thin snapshots."""

    def setUp(self) -> None:
        """Isolated MCF3M class. The live slot is moved to M2 C1."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.live_session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        moved = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"live_module": "M2", "live_slot": "C1", "stage": "round", "page_id": "round_1"},
        )
        self.assertEqual(moved.status_code, 200, moved.get_json())

    def tearDown(self) -> None:
        """Close the temp database."""
        self.school.close()
        self.tmp.cleanup()

    def _peel(self, event: str, **extra: object):
        """Post one teacher state event."""
        body = {"state_event": event, **extra}
        posted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json=body,
        )
        return posted

    def test_teacher_peels_sync_and_snapshot(self) -> None:
        """Six peels sync to the student poll. Reveal waits for arm."""
        seeded = self.school.live_session_active_media_payload(self.live_session_id)
        assert seeded is not None
        self.assertEqual(seeded["url"], JIGSAWABLE_MEDIA_URL)
        self.assertEqual(seeded["fill_id"], JIGSAWABLE_FILL_ID)
        self.assertEqual(seeded["challenge"], "")

        denied = self.student.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"state_event": "seed_16"},
        )
        self.assertIn(denied.status_code, (401, 403))

        for event, expect in (
            ("seed_16", {"g": None, "layout": "unmarked"}),
            ("show_4x4", {"g": 4, "n": 4, "centre": 4, "layout": "grid_4x4"}),
        ):
            posted = self._peel(event)
            self.assertEqual(posted.status_code, 200, posted.get_json())
            media = posted.get_json()["active_media"]
            self.assertEqual(media["state_event"], event)
            self.assertEqual(media["class_size"], 16)
            self.assertEqual(media["toast"], "")
            for key, value in expect.items():
                self.assertEqual(media[key], value, event)
            student = self.student.get("/api/student/state").get_json()
            self.assertEqual(student["active_media"]["state_event"], event)
            self.assertNotIn("linked_prompt", student["active_media"])
            self.assertNotIn("seed_25", json.dumps(student["active_media"]))

        skipped = self._peel("reveal_5x3")
        self.assertEqual(skipped.status_code, 400)

        paused = self._peel("priya_leaves_pause_15")
        self.assertEqual(paused.status_code, 200, paused.get_json())
        pause_media = paused.get_json()["active_media"]
        self.assertEqual(pause_media["layout"], "pause_15")
        self.assertEqual(pause_media["wonder_cue"], "jigsawable:priya_leaves_pause_15")
        self.assertFalse(pause_media["reveal_armed"])
        early = self._peel("reveal_5x3", arm_reveal=True)
        self.assertEqual(early.status_code, 400)

        armed = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"arm_reveal": True},
        )
        self.assertEqual(armed.status_code, 200, armed.get_json())
        self.assertTrue(armed.get_json()["active_media"]["reveal_armed"])
        self.assertEqual(
            armed.get_json()["active_media"]["state_event"],
            "priya_leaves_pause_15",
        )

        revealed = self._peel("reveal_5x3")
        self.assertEqual(revealed.status_code, 200, revealed.get_json())
        self.assertEqual(revealed.get_json()["active_media"]["layout"], "grid_5x3")
        self.assertEqual(revealed.get_json()["active_media"]["wonder_cue"], "")

        algebra = self._peel("show_algebra")
        self.assertEqual(algebra.status_code, 200, algebra.get_json())
        linked = algebra.get_json()["active_media"]["linked_prompt"]
        self.assertEqual(linked["kind"], "share")
        self.assertEqual(linked["run_as"], "group")
        ask = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/prompts",
            json={
                "slide_index": linked["slide_index"],
                "kind": "share",
                "payload": {
                    "prompt": linked["prompt"],
                    "text": linked["prompt"],
                    "item_id": "jigsawable-stage-ask",
                },
            },
        )
        self.assertEqual(ask.status_code, 200, ask.get_json())
        shown = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"student_view": {"questions": "student"}},
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        student_ask = self.student.get("/api/student/state").get_json()
        self.assertEqual(student_ask["prompt"]["kind"], "share")
        self.assertIn("animation as evidence", student_ask["prompt"]["payload"]["prompt"])
        self.assertEqual(student_ask["active_media"]["state_event"], "show_algebra")

        cleared = self._peel("clear")
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        self.assertIsNone(cleared.get_json()["active_media"])
        student_clear = self.student.get("/api/student/state").get_json()
        self.assertIsNone(student_clear["active_media"])

        snaps = self.school.list_artifact_state_interactions(self.live_session_id)
        self.assertEqual(
            [row["event"] for row in snaps],
            [
                "seed_16",
                "show_4x4",
                "priya_leaves_pause_15",
                "reveal_5x3",
                "show_algebra",
                "clear",
            ],
        )
        for row in snaps:
            self.assertEqual(row["interaction_kind"], "artifact_state")
            self.assertEqual(row["class_size"], 16)
            self.assertIn("ts", row)
            self.assertEqual(
                set(row) - {"interaction_kind"},
                {"event", "class_size", "g", "n", "centre", "ts"},
            )
        self.assertIsNone(snaps[0]["g"])
        self.assertEqual(snaps[1]["centre"], 4)
        self.assertEqual(snaps[3]["g"], 5)
        self.assertEqual(snaps[3]["n"], 3)
        self.assertIsNone(snaps[-1]["g"])

    def test_visualizer_has_no_student_scrub_or_seed_25(self) -> None:
        """The iframe is watch-only. Wonder cue text stays empty."""
        body = (LMS_DIR / "static" / "live-media" / "jigsawable-class-size.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("<button", body.lower())
        self.assertNotIn("seed_25", body)
        self.assertIn(".labels[hidden]", body)
        self.assertIn(".grid[hidden]", body)
        self.assertIn('data-wonder-cue=""', body)
        self.assertIn("jigsawable:priya_leaves_pause_15", body)
        script = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("state-event-bar", (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8"))
        self.assertIn("postJigsawableEvent", script)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("stateMachineMediaLive", student)
        page = self.staff.get(JIGSAWABLE_MEDIA_URL)
        self.assertEqual(page.status_code, 200)
        page.close()


if __name__ == "__main__":
    unittest.main()
