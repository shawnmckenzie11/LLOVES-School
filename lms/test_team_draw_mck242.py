#!/usr/bin/env python3
"""MCK-242: Team Draw group whiteboard icebreaker."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from participation_exclusion import exclude_participation_answer_student_ids  # noqa: E402
from team_draw import BUILTIN_PROMPTS, decode_png_payload, is_team_draw_item  # noqa: E402

# 1x1 PNG
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TeamDrawTests(unittest.TestCase):
    """Publish, submit, gallery, clear, and snapshot rules."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
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
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch", "Cedar"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.school.game.begin_game(self.class_id)
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        self.student_ids = [int(s["id"]) for s in students]
        for student in students:
            self.school.join_live_class_session(
                self.session_id, int(student["id"]), codename=str(student["codename"])
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.student_ids,
            assignments=[
                {"student_id": self.student_ids[0], "team_index": 0},
                {"student_id": self.student_ids[1], "team_index": 0},
                {"student_id": self.student_ids[2], "team_index": 1},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id, run_as_group=True, groups_configured=True
        )

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def _add_publish_team_draw(
        self, *, prompt_id: str | None = None
    ) -> dict:
        row = BUILTIN_PROMPTS[0]
        if prompt_id:
            row = next(p for p in BUILTIN_PROMPTS if p["id"] == prompt_id)
        body = {
            "type": "team_draw",
            "text": row["text"],
            "team_draw_prompt_id": row["id"],
        }
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/add-question",
            json=body,
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item_id = rv.get_json()["placement"]["item"]["id"]
        row = next(
            r
            for r in self.school.list_live_session_items(self.session_id)
            if r["item_id"] == item_id
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="group_submit"
        )
        self.assertEqual(published["response_mode"], "team_draw")
        return published

    def test_builtin_prompts_and_exclusion_helper(self) -> None:
        self.assertEqual(len(BUILTIN_PROMPTS), 4)
        exclude_participation_answer_student_ids(self.class_id, self.school)

    def test_submit_stores_title_not_in_snapshot_on_test_run(self) -> None:
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        card = self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Maple Mascot",
            image_b64=base64.b64encode(_TINY_PNG).decode("ascii"),
        )
        self.assertTrue(card.get("submitted"))
        view = self.school.team_draw_teacher_view(self.session_id, published)
        self.assertEqual(view["response_count"], 1)
        self.assertEqual(view["status_board"][0]["title"], "Maple Mascot")
        gallery = self.school.team_draw_gallery(self.session_id, live_id)
        self.assertTrue(gallery[0]["image_url"].startswith("data:image/png"))
        self.school.team_draw_end_and_clear(self.session_id, live_id)
        gallery_after = self.school.team_draw_gallery(self.session_id, live_id)
        self.assertEqual(gallery_after[0]["image_url"], "")
        self.school.end_live_class_session(self.session_id)
        snaps = self.school.conn.execute(
            "SELECT answer_json FROM live_result_snapshots WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchall()
        for row in snaps:
            payload = json.loads(row["answer_json"] or "{}")
            final = payload.get("final") or {}
            self.assertNotIn("image", final)
        self.assertTrue(is_team_draw_item(published))

    def test_decode_png_rejects_bad_data(self) -> None:
        with self.assertRaises(ValueError):
            decode_png_payload("not-valid")

    def test_mystery_rule_state_includes_steps_and_pass_turn(self) -> None:
        published = self._add_publish_team_draw(prompt_id="mystery_rule")
        live_id = int(published["id"])
        card = self.school.student_team_draw_state(
            published, self.student_ids[0]
        )
        self.assertTrue(card.get("mystery_rule"))
        self.assertEqual(len(card.get("mystery_steps") or []), 3)
        turn = card.get("pass_turn")
        self.assertIsNotNone(turn)
        self.assertEqual(int(turn["turn_total"]), 2)
        view = self.school.team_draw_teacher_view(self.session_id, published)
        self.assertTrue(view.get("mystery_rule"))
        self.assertEqual(len(view.get("mystery_steps") or []), 3)


if __name__ == "__main__":
    unittest.main()
