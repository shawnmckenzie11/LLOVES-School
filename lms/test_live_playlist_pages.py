#!/usr/bin/env python3
"""Per-class live-lesson page overlay add/delete and metadata merge tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402


SEED_C2 = LMS_DIR / "seeds" / "live_classes" / "MCR3U" / "M1" / "C2.json"


class LivePlaylistPageOverlayTests(unittest.TestCase):
    """Add/delete pages persist as class overlays and never rewrite seed JSON."""

    def setUp(self) -> None:
        """Teacher-owned MCR3U class with an active M1 C2 live session."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="join",
            page_id="join",
        )
        self.seed_before = SEED_C2.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _page_ids(self, metadata: dict[str, Any]) -> list[str]:
        """Return ordered page ids from merged metadata."""
        return [
            str(row.get("id") or "")
            for row in metadata.get("pages") or []
            if isinstance(row, dict) and row.get("id")
        ]

    def test_add_page_after_join_persists_empty_overlay(self) -> None:
        """New page lands after Join, has no questions, and survives reload."""
        before = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2"
        )
        before_ids = self._page_ids(before)
        self.assertEqual(before_ids[0], "join")
        result = self.school.add_class_playlist_page(
            self.class_id,
            "M1",
            "C2",
            name="Extra practice",
            after_page_id="join",
        )
        page = result["page"]
        self.assertTrue(str(page["id"]).startswith("custom-"))
        self.assertEqual(page["name"], "Extra practice")
        self.assertEqual(page["stage"], "play")
        ids = self._page_ids(result["live_metadata"])
        self.assertEqual(ids[0], "join")
        self.assertEqual(ids[1], page["id"])
        self.assertEqual(len(ids), len(before_ids) + 1)
        bound = [
            row
            for row in result["live_metadata"].get("questions") or []
            if isinstance(row, dict)
            and row.get("page_number") == page.get("page_number")
        ]
        self.assertEqual(bound, [])
        reloaded = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(self._page_ids(reloaded), ids)
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher.get("page_id"), page["id"])
        self.assertEqual(teacher.get("stage"), "play")

    def test_add_welcome_page_uses_teams_stage(self) -> None:
        """Welcome pages land on TEAMS and show the VLC welcome card."""

        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert maple is not None
        result = self.school.add_class_playlist_page(
            self.class_id,
            "M1",
            "C2",
            name="",
            after_page_id="join",
            kind="welcome",
        )
        page = result["page"]
        self.assertEqual(page["stage"], "teams")
        self.assertEqual(page["name"], "Welcome")
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher.get("page_id"), page["id"])
        self.assertEqual(teacher.get("stage"), "teams")
        welcome = self.school.student_game_show_welcome(self.session_id)
        self.assertTrue(welcome)
        student = self.school.student_live_prompt_payload(
            self.session_id, int(maple["id"])
        )
        self.assertTrue(student.get("game_show_welcome"))

    def test_add_winner_page_uses_summary_stage(self) -> None:
        """Winner pages land on Summary and attach the winner snapshot."""

        result = self.school.add_class_playlist_page(
            self.class_id,
            "M1",
            "C2",
            name="",
            after_page_id="join",
            kind="winner",
        )
        page = result["page"]
        self.assertEqual(page["stage"], "summary")
        self.assertEqual(page["name"], "Winner")
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher.get("page_id"), page["id"])
        self.assertEqual(teacher.get("stage"), "summary")
        reloaded = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2"
        )
        persisted = next(
            row
            for row in reloaded.get("pages") or []
            if isinstance(row, dict) and str(row.get("id") or "") == page["id"]
        )
        self.assertEqual(persisted.get("stage"), "summary")
        self.assertEqual(persisted.get("name"), "Winner")
        payload = {"teacher_state": teacher}
        self.school.apply_student_summary_winner(
            payload, self.session_id, self.class_id
        )
        self.assertTrue(payload.get("summary_winner"))
        self.assertTrue(payload.get("winner"))

    def test_delete_custom_page_lands_on_next(self) -> None:
        """Deleting a mid-deck overlay page lands on the following page."""
        added = self.school.add_class_playlist_page(
            self.class_id,
            "M1",
            "C2",
            name="Scratch",
            after_page_id="join",
        )
        custom_id = str(added["page"]["id"])
        result = self.school.delete_class_playlist_page(
            self.class_id, "M1", "C2", custom_id
        )
        self.assertEqual(result["action"], "deleted")
        self.assertEqual(str(result["land_page"]["id"]), "welcome")
        ids = self._page_ids(result["live_metadata"])
        self.assertNotIn(custom_id, ids)
        self.assertEqual(ids[0], "join")
        self.assertEqual(ids[1], "welcome")
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher.get("page_id"), "welcome")
        self.assertEqual(SEED_C2.read_text(encoding="utf-8"), self.seed_before)

    def test_delete_last_page_in_deck_lands_on_previous(self) -> None:
        """Deleting the final page lands on the previous remaining page."""
        pages = self._page_ids(
            self.school.live_class_metadata_for_class_lesson(
                self.class_id, "M1", "C2"
            )
        )
        last_id = pages[-1]
        previous_id = pages[-2]
        added = self.school.add_class_playlist_page(
            self.class_id,
            "M1",
            "C2",
            name="After last",
            after_page_id=last_id,
        )
        custom_id = str(added["page"]["id"])
        result = self.school.delete_class_playlist_page(
            self.class_id, "M1", "C2", custom_id
        )
        self.assertEqual(str(result["land_page"]["id"]), last_id)
        self.assertIn(previous_id, self._page_ids(result["live_metadata"]))

    def test_cannot_delete_last_remaining_page(self) -> None:
        """Last-page rule: refuse when the merged deck would become empty."""
        pages = self._page_ids(
            self.school.live_class_metadata_for_class_lesson(
                self.class_id, "M1", "C2"
            )
        )
        for page_id in pages[1:]:
            self.school.delete_class_playlist_page(
                self.class_id, "M1", "C2", page_id
            )
        remaining = self._page_ids(
            self.school.live_class_metadata_for_class_lesson(
                self.class_id, "M1", "C2"
            )
        )
        self.assertEqual(remaining, [pages[0]])
        with self.assertRaises(ValueError) as ctx:
            self.school.delete_class_playlist_page(
                self.class_id, "M1", "C2", pages[0]
            )
        self.assertIn("last remaining page", str(ctx.exception))
        still = self._page_ids(
            self.school.live_class_metadata_for_class_lesson(
                self.class_id, "M1", "C2"
            )
        )
        self.assertEqual(still, [pages[0]])


class LivePlaylistPageApiTests(unittest.TestCase):
    """HTTP add/delete page routes for a staff-owned class."""

    def setUp(self) -> None:
        """Staff login against an MCR3U class."""
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
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
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
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_add_and_delete_page_api(self) -> None:
        """POST adds after Join; DELETE of that page returns the next land page."""
        add = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/pages",
            json={"name": "Staff extra", "after_page_id": "join"},
        )
        self.assertEqual(add.status_code, 200, add.get_json())
        body = add.get_json() or {}
        page = body.get("page") or {}
        self.assertTrue(body.get("ok"))
        self.assertEqual(page.get("name"), "Staff extra")
        ids = [
            str(row.get("id") or "")
            for row in (body.get("live_metadata") or {}).get("pages") or []
        ]
        self.assertEqual(ids[1], page.get("id"))
        delete = self.client.delete(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/pages/{page['id']}"
        )
        self.assertEqual(delete.status_code, 200, delete.get_json())
        deleted = delete.get_json() or {}
        self.assertEqual(str((deleted.get("land_page") or {}).get("id")), "welcome")

    def test_delete_last_remaining_page_api(self) -> None:
        """DELETE of the sole remaining page is HTTP 400."""
        pages = (
            self.school.live_class_metadata_for_class_lesson(
                self.class_id, "M1", "C2"
            ).get("pages")
            or []
        )
        for row in pages[1:]:
            rv = self.client.delete(
                f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/pages/{row['id']}"
            )
            self.assertEqual(rv.status_code, 200, rv.get_json())
        last_id = str(pages[0]["id"])
        denied = self.client.delete(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/pages/{last_id}"
        )
        self.assertEqual(denied.status_code, 400, denied.get_json())
        self.assertIn(
            "last remaining page",
            str((denied.get_json() or {}).get("error") or ""),
        )


if __name__ == "__main__":
    unittest.main()
