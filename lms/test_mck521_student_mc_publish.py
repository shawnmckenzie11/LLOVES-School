#!/usr/bin/env python3
"""MCK-521: student MC publish speed, live tallies, and image caching."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)

from app import create_app  # noqa: E402
from paths import DEFAULT_IT_EMAIL  # noqa: E402


class Mck521StudentMcPublishTests(unittest.TestCase):
    """Stale tallies, card cache, and image route headers."""

    def setUp(self) -> None:
        """One live class and three roster seats."""

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff(DEFAULT_IT_EMAIL)
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["P1", "P2", "P3"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.session_id = int(
            self.school.start_live_class_session(
                self.class_id, int(teacher["id"])
            )["id"]
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_number=4,
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        self.ids = [int(row["id"]) for row in rows]
        for row in rows:
            self.school.join_live_class_session(
                self.session_id,
                int(row["id"]),
                codename=str(row["codename"]),
            )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp directory."""

        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add_mc(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Publish one MC item and return the lifecycle row and prompt."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            page_number=4,
            stage="round",
            question_type="mc",
            text="Pick one",
            options=["kite", "moth", "wren", "lark"],
            correct_index=0,
        )
        token = str(placed.get("item_id") or "")
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == token
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        return published, prompt

    def _counts(self, results: dict[str, Any] | None) -> dict[str, int]:
        """Map tally labels to counts."""

        return {
            str(choice.get("label")): int(choice.get("count") or 0)
            for choice in (results or {}).get("choices") or []
            if isinstance(choice, dict)
        }

    def test_early_answerer_stamp_and_tally_refresh(self) -> None:
        """After B answers, A stays unchanged but tally patches refresh bars."""

        _item, prompt = self._add_mc()
        seat_a, seat_b, seat_c = self.ids[0], self.ids[1], self.ids[2]
        self.school.submit_live_prompt_response(
            int(prompt["id"]), seat_a, {"choice": "A"}
        )
        stamp_a = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, seat_a
        )
        seq = int(
            self.school.live_session_teacher_state_payload(self.session_id).get(
                "state_seq"
            )
            or 0
        )
        tally_a = self.school._live_tally_poll_stamp(self.session_id, seat_a)
        stamp_c = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, seat_c
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), seat_b, {"choice": "B"}
        )
        self.assertEqual(
            stamp_a,
            self.school.live_student_poll_stamp(
                self.session_id, self.class_id, seat_a
            ),
        )
        body = self.school.student_live_poll_unchanged(
            self.session_id,
            self.class_id,
            seq,
            stamp_a,
            seat_a,
            tally_a,
        )
        self.assertIsNotNone(body)
        self.assertTrue(body.get("unchanged"))
        patch = (body.get("active_questions") or [])[0]
        counts = self._counts(patch.get("results"))
        self.assertEqual(counts.get("kite"), 1)
        self.assertEqual(counts.get("moth"), 1)
        self.assertEqual(
            stamp_c,
            self.school.live_student_poll_stamp(
                self.session_id, self.class_id, seat_c
            ),
        )

    def test_student_card_cache_and_invalidation(self) -> None:
        """Second seat hits cache; prompt update busts it; copies stay isolated."""

        item, prompt = self._add_mc()
        session_row = self.school.get_live_session(self.session_id)
        assert session_row is not None
        class_id = int(session_row["class_id"])
        raw = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
        key_kwargs = {
            "class_id": class_id,
            "module": "M1",
            "live_prompt_id": int(prompt["id"]),
            "live_item_rev": str(item.get("updated_at") or ""),
            "live_prompt_rev": str(prompt.get("updated_at") or ""),
        }
        try:
            from question_math import clean_question_stem_fields
        except ImportError:
            from lms.question_math import clean_question_stem_fields

        with mock.patch(
            "question_math.clean_question_stem_fields",
            wraps=clean_question_stem_fields,
        ) as clean_fields:
            first = self.school._student_public_item_payload(raw, **key_kwargs)
            after_first = clean_fields.call_count
            self.school._student_public_item_payload(raw, **key_kwargs)
            self.assertEqual(clean_fields.call_count, after_first)
        first["text"] = "mutated"
        third = self.school._student_public_item_payload(raw, **key_kwargs)
        self.assertNotEqual(third.get("text"), "mutated")
        busted_kwargs = {
            **key_kwargs,
            "live_prompt_rev": "2099-01-01T00:00:00",
        }
        with mock.patch(
            "question_math.clean_question_stem_fields",
            wraps=clean_question_stem_fields,
        ) as clean_fields:
            self.school._student_public_item_payload(raw, **busted_kwargs)
            clean_fields.assert_called()

    def test_publish_build_uses_card_cache(self) -> None:
        """Full item payload for two students shares one card build."""

        try:
            from question_math import clean_question_stem_fields
        except ImportError:
            from lms.question_math import clean_question_stem_fields

        self._add_mc()
        with mock.patch(
            "question_math.clean_question_stem_fields",
            wraps=clean_question_stem_fields,
        ) as clean_fields:
            self.school.student_live_items_payload(self.session_id, self.ids[0])
            after_first = clean_fields.call_count
            self.school.student_live_items_payload(self.session_id, self.ids[1])
            self.assertEqual(clean_fields.call_count, after_first)

    def test_image_routes_cache_and_conditional(self) -> None:
        """Module and curriculum images send private cache headers and ETags."""

        from curriculum_bank_images import CARTRIDGE_IMAGE_DIR

        cartridge = next(CARTRIDGE_IMAGE_DIR.glob("*.png"), None)
        self.assertIsNotNone(cartridge)
        assert cartridge is not None
        rel = f"cartridge/{cartridge.name}"
        url = f"/curriculum-bank-images/MCF3M/{rel}"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        etag = first.headers.get("ETag")
        self.assertTrue(etag)
        cache_control = first.headers.get("Cache-Control", "")
        self.assertIn("private", cache_control)
        self.assertIn("3600", cache_control)
        self.assertEqual(first.headers.get("X-Content-Type-Options"), "nosniff")
        second = self.client.get(url, headers={"If-None-Match": etag})
        self.assertEqual(second.status_code, 304)

        other_class = self.class_id + 999_999
        denied = self.client.get(
            f"/api/classes/{other_class}/module-files/web_resources/missing.png"
        )
        self.assertIn(denied.status_code, {403, 404})

    def test_live_question_card_eager_images(self) -> None:
        """Node harness rejects lazy loading on student stem images."""

        node = LMS_DIR / "static" / "live_question_card.test.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_fanout_guard_tests_still_listed(self) -> None:
        """CI still runs MCK-283 guard and load budget on deploy."""

        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("fan-out guard", ci)
        self.assertIn("name: 30-seat load budget", ci)


if __name__ == "__main__":
    unittest.main()