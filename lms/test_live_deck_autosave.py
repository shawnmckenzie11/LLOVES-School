#!/usr/bin/env python3
"""Live-class deck edits persist and the next open loads that version."""

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


SEED_C3 = LMS_DIR / "seeds" / "live_classes" / "MCF3M" / "M1" / "C3.json"


class LiveDeckAutosaveTests(unittest.TestCase):
    """MCF3M M1 C3 edits survive cache, refresh, and a new session."""

    def setUp(self) -> None:
        """Teacher-owned MCF3M class with an active M1 C3 live session."""
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
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C3",
            stage="round",
            page_id="round_1",
            class_set=True,
        )
        self.seed_before = SEED_C3.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _question_ids(self, metadata: dict[str, Any]) -> list[str]:
        """Return question ids from merged live metadata."""
        return [
            str(row.get("id") or "")
            for row in metadata.get("questions") or []
            if isinstance(row, dict) and row.get("id")
        ]

    def _add_c3_question(self, text: str = "C3 autosave stem") -> dict[str, Any]:
        """Add one staff MC onto MCF3M M1 C3 and return the placement item."""
        placement = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C3",
            question_type="mc",
            text=text,
            page_number=4,
            stage="round",
            options=["A", "B", "C", "D"],
            correct_index=1,
        )
        return placement.get("item") or {}

    def test_c3_question_survives_poisoned_seed_cache(self) -> None:
        """A cached seed pack cannot hide a later playlist edit."""
        warm = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C3", fresh=False
        )
        seed_ids = self._question_ids(warm)
        for key, value in list(self.school._live_metadata_cache.items()):
            if key[0] == self.class_id and key[3] == "C3":
                poisoned = dict(value)
                poisoned["questions"] = []
                self.school._live_metadata_cache[key] = poisoned
        item = self._add_c3_question()
        token = str(item.get("id") or "")
        self.assertTrue(token.startswith("staff-q-"))
        reloaded = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C3", fresh=False
        )
        ids = self._question_ids(reloaded)
        self.assertIn(token, ids)
        for seed_id in seed_ids:
            self.assertIn(seed_id, ids)
        self.assertEqual(SEED_C3.read_text(encoding="utf-8"), self.seed_before)

    def test_c3_deck_api_returns_fresh_edit_and_no_store(self) -> None:
        """GET /deck rebuilds from overlays and refuses HTTP caching."""
        item = self._add_c3_question("Deck API stem")
        token = str(item.get("id") or "")
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C3/deck?fresh=1"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertEqual(rv.headers.get("Cache-Control"), "no-store")
        body = rv.get_json() or {}
        ids = self._question_ids(body.get("live_metadata") or {})
        self.assertIn(token, ids)
        self.assertTrue(str(body.get("deck_revision") or ""))
        self.assertIn(
            token,
            self._question_ids(
                (body.get("live_metadata") or {})
            ),
        )

    def test_c3_edit_reopens_on_new_session(self) -> None:
        """End class and Set Class C3 again still has the saved question."""
        item = self._add_c3_question("Next open stem")
        token = str(item.get("id") or "")
        self.school.end_live_class_session(self.session_id, clear_moods=False)
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        new_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            new_id,
            live_module="M1",
            live_slot="C3",
            stage="round",
            class_set=True,
        )
        metadata = self.school.live_class_metadata_for_session(new_id)
        self.assertIn(token, self._question_ids(metadata))
        self.school.ensure_live_session_items(new_id)
        lifecycle_ids = [
            str(row.get("item_id") or "")
            for row in self.school.list_live_session_items(new_id)
        ]
        self.assertIn(token, lifecycle_ids)
        state = self.school.get_live_session_state(new_id, light=False)
        self.assertIn(token, self._question_ids(state.get("live_metadata") or {}))
        self.assertTrue(str(state.get("deck_revision") or ""))

    def test_c3_text_only_stem_overlay_survives_reopen(self) -> None:
        """C3 copy persists even though the slot clears active media."""
        self.school.set_live_session_active_media(
            self.session_id,
            challenge="C3",
            stem="C3 courtyard stem",
            caption="Saved caption",
        )
        overlay = self.school.get_class_live_media_copy(self.class_id, "M1", "C3")
        self.assertEqual(overlay["stem"], "C3 courtyard stem")
        self.assertEqual(overlay["caption"], "Saved caption")
        self.school.end_live_class_session(self.session_id, clear_moods=False)
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        new_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            new_id, live_module="M1", live_slot="C3", class_set=True
        )
        metadata = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C3", fresh=True
        )
        media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
        self.assertEqual(media.get("stem"), "C3 courtyard stem")
        self.assertEqual(media.get("caption"), "Saved caption")

    def test_c2_media_copy_overlay_survives_reopen(self) -> None:
        """C2 no longer skips class overlay persist on its early return."""
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", class_set=True
        )
        self.school.set_live_session_active_media(
            self.session_id,
            challenge="C2",
            stem="C2 edited stem",
            caption="C2 edited caption",
        )
        overlay = self.school.get_class_live_media_copy(self.class_id, "M1", "C2")
        self.assertEqual(overlay["stem"], "C2 edited stem")
        self.assertEqual(overlay["caption"], "C2 edited caption")
        metadata = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2", fresh=True
        )
        media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
        self.assertEqual(media.get("stem"), "C2 edited stem")


if __name__ == "__main__":
    unittest.main(verbosity=2)
