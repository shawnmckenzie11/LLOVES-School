#!/usr/bin/env python3
"""MCK-242: Team Draw group whiteboard icebreaker."""

from __future__ import annotations

import base64
import json
import os
import subprocess
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
from participation_real_students import exclude_participation_answer_student_ids  # noqa: E402
from team_draw import (  # noqa: E402
    BUILTIN_PROMPTS,
    decode_png_payload,
    is_team_draw_item,
    resolved_prompt_text,
)

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
        self, *, prompt_id: str | None = None, publish_mode: str = "group_submit"
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
            self.session_id, int(row["id"]), publish_mode=publish_mode
        )
        expected = "individual" if publish_mode == "individual" else "team_draw"
        self.assertEqual(published["response_mode"], expected)
        return published

    def test_builtin_prompts_and_exclusion_helper(self) -> None:
        self.assertEqual(len(BUILTIN_PROMPTS), 4)
        mystery = next(row for row in BUILTIN_PROMPTS if row["id"] == "mystery_rule")
        self.assertEqual(
            mystery["text"],
            "Mystery rule: write the rule on the board with the Text tool after you draw step 4.",
        )
        self.assertNotIn("title box", mystery["text"])
        exclude_participation_answer_student_ids(self.class_id, self.school)

    def test_mystery_prompt_is_looked_up_live(self) -> None:
        """A published mystery-rule item shows the catalog sentence, not a stored stem."""
        published = self._add_publish_team_draw(prompt_id="mystery_rule")
        question = dict(published["item"])
        question["text"] = (
            "Mystery rule: name the rule in the title box after you draw step 4."
        )
        self.school.conn.execute(
            "UPDATE live_session_items SET item_json = ? WHERE id = ?",
            (json.dumps(question), int(published["id"])),
        )
        self.school.conn.commit()
        fresh = self.school.get_live_session_item(
            self.session_id, int(published["id"])
        )
        prompt = resolved_prompt_text(fresh)
        self.assertIn("Text tool", prompt)
        self.assertNotIn("title box", prompt)
        view = self.school.team_draw_teacher_view(self.session_id, fresh)
        self.assertEqual(view["prompt"], prompt)

    def test_team_draw_submit_updates_teacher_card_and_history(self) -> None:
        """A drawing-only team submit fills the card, gallery, and results history."""
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        card = self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            image_b64=base64.b64encode(_TINY_PNG).decode("ascii"),
        )
        self.assertTrue(card.get("submitted"))
        self.assertEqual(card.get("title"), "")
        view = self.school.live_session_item_results(self.session_id, live_id)
        submitted = [row for row in view["status_board"] if row["submitted"]]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["status_label"], "✓ submitted")
        self.assertEqual(view["response_count"], 1)
        self.assertEqual(
            self.school.lifecycle_response_counts(self.session_id).get(live_id),
            1,
        )
        gallery = self.school.team_draw_gallery(self.session_id, live_id)
        sent = [row for row in gallery if row["submitted"] and row["image_url"]]
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["image_url"].startswith("data:image/png"))
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        snaps = self.school.conn.execute(
            "SELECT answer_json FROM live_result_snapshots WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchall()
        found = False
        for row in snaps:
            payload = json.loads(row["answer_json"] or "{}")
            final = payload.get("final") if isinstance(payload, dict) else None
            if isinstance(final, dict) and final.get("kind") == "team_draw":
                found = True
                self.assertNotIn("image", final)
                self.assertEqual(final.get("title"), "")
        self.assertTrue(found)

    def test_individual_draw_submit_updates_teacher_card_and_history(self) -> None:
        """An individual drawing submit fills that card, gallery, and history."""
        published = self._add_publish_team_draw(publish_mode="individual")
        self.assertEqual(published["response_mode"], "individual")
        live_id = int(published["id"])
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        card = self.school.submit_individual_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            image_b64=base64.b64encode(_TINY_PNG).decode("ascii"),
        )
        self.assertTrue(card.get("submitted"))
        self.assertFalse(card.get("can_submit"))
        view = self.school.live_session_item_results(self.session_id, live_id)
        self.assertTrue(view.get("draw"))
        submitted = [row for row in view["status_board"] if row["submitted"]]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["status_label"], "✓ submitted")
        self.assertEqual(view["response_count"], 1)
        self.assertEqual(
            self.school.lifecycle_response_counts(self.session_id).get(live_id),
            1,
        )
        self.assertGreater(view["eligible_count"], 1)
        gallery = self.school.team_draw_gallery(self.session_id, live_id)
        sent = [row for row in gallery if row["submitted"] and row["image_url"]]
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["image_url"].startswith("data:image/png"))
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        snaps = self.school.conn.execute(
            "SELECT answer_json FROM live_result_snapshots WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchall()
        found = False
        for row in snaps:
            payload = json.loads(row["answer_json"] or "{}")
            if isinstance(payload, dict) and payload.get("kind") == "team_draw":
                found = True
                self.assertNotIn("image", payload)
                self.assertEqual(payload.get("title"), "")
        self.assertTrue(found)

    def test_plain_whiteboard_has_no_submit(self) -> None:
        """The plain board has no Submit control. Draw questions do, with no title box."""
        root = Path(__file__).resolve().parents[1]
        staff = (root / "lms" / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        home = (root / "lms" / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("team-draw-submit", staff)
        self.assertNotIn("team-draw-title", staff)
        self.assertNotIn("team-draw-title", home)
        self.assertIn('id="team-draw-submit" class="team-draw-submit" hidden', home)
        script = """
import { drawSubmitVisible } from "./lms/static/team_draw.js";
if (drawSubmitVisible(null, true)) process.exit(1);
if (drawSubmitVisible({ kind: "whiteboard", response_mode: "individual", status: "active" }, true)) process.exit(2);
const team = {
  status: "active",
  response_mode: "team_draw",
  content: { type: "team_draw" },
  group_submit: { can_submit: true, submitted: false },
};
if (!drawSubmitVisible(team, true)) process.exit(3);
if (drawSubmitVisible(team, false)) process.exit(4);
team.group_submit.submitted = true;
if (drawSubmitVisible(team, true)) process.exit(5);
const solo = {
  status: "active",
  response_mode: "individual",
  content: { type: "team_draw" },
  can_submit: true,
  my_response: null,
};
if (!drawSubmitVisible(solo, true)) process.exit(6);
solo.my_response = { response: { kind: "team_draw" } };
if (drawSubmitVisible(solo, true)) process.exit(7);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

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
        card_a = self.school.student_team_draw_state(
            published, self.student_ids[0]
        )
        card_b = self.school.student_team_draw_state(
            published, self.student_ids[1]
        )
        self.assertTrue(card_a.get("mystery_rule"))
        self.assertEqual(len(card_a.get("mystery_steps") or []), 3)
        turn_a = card_a.get("pass_turn")
        turn_b = card_b.get("pass_turn")
        self.assertIsNotNone(turn_a)
        self.assertIsNotNone(turn_b)
        self.assertEqual(int(turn_a["turn_total"]), 2)
        self.assertEqual(turn_a["holder_name"], turn_b["holder_name"])
        self.assertEqual(int(turn_a["turn_index"]), 1)
        view = self.school.team_draw_teacher_view(self.session_id, published)
        self.assertTrue(view.get("mystery_rule"))
        self.assertEqual(len(view.get("mystery_steps") or []), 3)

    def test_test_run_end_skips_team_draw_snapshot(self) -> None:
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Practice sketch",
            image_b64=base64.b64encode(_TINY_PNG).decode("ascii"),
        )
        session = self.school.get_live_session(self.session_id)
        assert session is not None
        run_key = str(session["run_key"] or "")
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=False
        )
        count = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM live_result_snapshots
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchone()[0]
        self.assertEqual(int(count), 0)

    def test_real_run_end_keeps_team_draw_snapshot(self) -> None:
        """Real End snapshots Team Draw before ``participation_runs`` is written."""
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Class sketch",
            image_b64=base64.b64encode(_TINY_PNG).decode("ascii"),
        )
        session = self.school.get_live_session(self.session_id)
        assert session is not None
        run_key = str(session["run_key"] or "")
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        count = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM live_result_snapshots
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchone()[0]
        self.assertEqual(int(count), 1)

    def test_quit_wipe_purges_team_draw_images(self) -> None:
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Ephemeral",
            image_b64=base64.b64encode(_TINY_PNG).decode("ascii"),
        )
        before = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_team_draw_images WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        self.assertGreater(int(before), 0)
        self.school.finish_live_class(
            self.class_id, persist=False, celebrate=False
        )
        after = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_team_draw_images WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        self.assertEqual(int(after), 0)


if __name__ == "__main__":
    unittest.main()
