"""Mint + ±10% grade tests for the Transformations Artifact."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from artifact import (
    ARTIFACT_KIND,
    C2_TRANSFORM_MEDIA_URL,
    LEAD_MATCH,
    LEAD_MISS,
    TRANSFORMATIONS_ARTIFACT_ID,
    TRANSFORMATIONS_STEM,
    artifact_feedback_fragment,
    format_artifact_answer,
    format_vertex_equation,
    grade_transform_snapshot,
    slider_margin,
    transformations_prompt_payload,
)
from app import create_app
from live_media import (
    apply_active_media_update,
    challenge_clears_active_media,
    is_c2_transform,
)


class TransformGradeTests(unittest.TestCase):
    """±10% auto-check for vertex-form sliders."""

    def test_format_artifact_answer(self) -> None:
        """Responses & Points show compact slider values."""
        self.assertEqual(
            format_artifact_answer({"params": {"a": 3, "h": 5, "k": -1}}),
            "a=3, h=5, k=-1",
        )

    def test_format_example_equation(self) -> None:
        """Spec example f(x)=3(x−5)²−1 is formatted with unicode minus."""
        self.assertEqual(
            format_vertex_equation({"a": 3, "h": 5, "k": -1}),
            "f(x)=3(x−5)²−1",
        )

    def test_grade_within_ten_percent(self) -> None:
        """Values inside ±10% of each target match."""
        target = {"a": 3.0, "h": 5.0, "k": -1.0}
        close = {"a": 3.2, "h": 5.4, "k": -1.05}
        result = grade_transform_snapshot(close, target)
        self.assertTrue(result["match"], result)
        self.assertLessEqual(close["a"] - target["a"], slider_margin(3.0, "a"))

    def test_grade_outside_ten_percent(self) -> None:
        """A slider past the ±10% band fails the check."""
        target = {"a": 3.0, "h": 5.0, "k": -1.0}
        miss = {"a": 1.0, "h": 5.0, "k": -1.0}
        result = grade_transform_snapshot(miss, target)
        self.assertFalse(result["match"], result)
        self.assertFalse(result["per_key"]["a"]["match"])
        self.assertTrue(result["per_key"]["h"]["match"])

    def test_feedback_leads(self) -> None:
        """Wonder copy: Matched. / Not yet — watch the meters."""
        payload = transformations_prompt_payload(
            snapshot={"a": 3, "h": 5, "k": -1},
            target_mode="equation",
        )
        hit = artifact_feedback_fragment(payload, {"params": {"a": 3, "h": 5, "k": -1}})
        assert hit is not None
        self.assertTrue(hit["match"])
        self.assertEqual(hit["lead"], LEAD_MATCH)
        miss = artifact_feedback_fragment(payload, {"a": 0.5, "h": 0, "k": 0})
        assert miss is not None
        self.assertFalse(miss["match"])
        self.assertEqual(miss["lead"], LEAD_MISS)


class ArtifactMintChannelTests(unittest.TestCase):
    """Staff mint → live prompt on the current page + frozen student media."""

    def setUp(self) -> None:
        """Isolated app with one rostered class and staff + student clients."""
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
        self.session_code = str(live["session_code"])
        self.live_session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_c2_seeds_transform_media_c3_still_clears(self) -> None:
        """C2 Artifact media seeds; C3 stays text-only."""
        self.assertFalse(challenge_clears_active_media("C2"))
        self.assertTrue(challenge_clears_active_media("C3"))
        seeded = apply_active_media_update(None, challenge="C2")
        assert seeded is not None
        self.assertTrue(is_c2_transform(seeded))
        self.assertEqual(seeded["url"], C2_TRANSFORM_MEDIA_URL)
        self.assertIsNone(apply_active_media_update(seeded, challenge="C3"))

    def test_c2_media_page_has_artifact_chrome(self) -> None:
        """C2 iframe ships Wonder labels and stays same-origin."""
        rv = self.staff.get(C2_TRANSFORM_MEDIA_URL)
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True)
        self.assertIn("Make match challenge", body)
        self.assertIn("Random", body)
        self.assertIn("Show graph", body)
        self.assertIn("Show equation", body)
        self.assertIn("lloves-m1c2-transforms", body)
        self.assertEqual(rv.headers.get("X-Frame-Options"), "SAMEORIGIN")
        path = LMS_DIR / "static" / "live-media" / "m1c2-transforms.html"
        self.assertTrue(path.is_file())
        rv.close()

    def test_mint_stores_parent_and_snapshot_on_current_page(self) -> None:
        """Teacher mint writes a Question card plus the exact stem + snapshot."""
        minted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 3, "h": 5, "k": -1},
                "target_mode": "equation",
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_json())
        body = minted.get_json()
        prompt = body["prompt"]
        self.assertEqual(prompt["kind"], ARTIFACT_KIND)
        self.assertNotEqual(prompt["slide_index"], 800)
        payload = prompt["payload"]
        self.assertEqual(payload["prompt"], TRANSFORMATIONS_STEM)
        self.assertEqual(payload["snapshot"], {"a": 3.0, "h": 5.0, "k": -1.0})
        self.assertEqual(payload["target_mode"], "equation")
        self.assertEqual(payload["equation"], "f(x)=3(x−5)²−1")
        self.assertTrue(str(payload.get("item_id") or "").startswith("artifact-match-"))
        live_item = body["live_item"]
        self.assertEqual(live_item["status"], "active")
        self.assertEqual(int(live_item["prompt_id"]), int(prompt["id"]))
        cards = body["question_cards"]
        self.assertTrue(
            any(str(row.get("id") or "") == payload["item_id"] for row in cards),
            cards,
        )
        media = body["active_media"]
        self.assertEqual(media["url"], C2_TRANSFORM_MEDIA_URL)
        self.assertEqual(media["artifact"]["target_mode"], "equation")
        self.assertEqual(media["artifact"]["snapshot"]["h"], 5.0)

        student = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(student["prompt"]["kind"], ARTIFACT_KIND)
        self.assertEqual(student["prompt"]["payload"]["prompt"], TRANSFORMATIONS_STEM)
        self.assertEqual(student["prompt"]["payload"]["equation"], "f(x)=3(x−5)²−1")
        self.assertNotIn("cement", student["prompt"]["payload"])
        active_q = student.get("active_questions") or []
        self.assertTrue(
            any(
                str((row.get("content") or {}).get("item_id") or row.get("item_id") or "")
                == payload["item_id"]
                for row in active_q
            ),
            active_q,
        )

        hit = self.student.post(
            "/api/student/live-prompt/response",
            json={"response": {"params": {"a": 3.1, "h": 5.2, "k": -1.0}}},
        )
        self.assertEqual(hit.status_code, 200, hit.get_json())
        self.assertEqual(hit.get_json()["feedback"]["lead"], LEAD_MATCH)
        self.assertTrue(hit.get_json()["feedback"]["match"])

        miss = self.student.post(
            "/api/student/live-prompt/response",
            json={"response": {"params": {"a": 1.0, "h": 0.0, "k": 4.0}}},
        )
        self.assertEqual(miss.status_code, 200, miss.get_json())
        self.assertEqual(miss.get_json()["feedback"]["lead"], LEAD_MISS)
        self.assertFalse(miss.get_json()["feedback"]["match"])

        roster = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/questions/{prompt['id']}/responses"
        )
        self.assertEqual(roster.status_code, 200, roster.get_json())
        answers = roster.get_json().get("responses") or []
        self.assertTrue(answers)
        self.assertTrue(any("a=" in str(row.get("answer") or "") for row in answers))

        still = self.school.ensure_waiting_room_minds_on(self.live_session_id)
        self.assertIsNone(still)
        active = self.school.get_active_live_prompt(self.live_session_id)
        assert active is not None
        self.assertEqual(active["kind"], ARTIFACT_KIND)

    def test_each_mint_creates_a_new_question_card(self) -> None:
        """A second Make match challenge does not overwrite the first prompt."""
        first = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "h": 1, "k": 0},
                "target_mode": "graph",
            },
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        first_id = int(first.get_json()["prompt"]["id"])
        first_item = str(first.get_json()["prompt"]["payload"]["item_id"])
        second = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": -1, "h": 4, "k": 3},
                "target_mode": "equation",
            },
        )
        self.assertEqual(second.status_code, 200, second.get_json())
        second_id = int(second.get_json()["prompt"]["id"])
        second_item = str(second.get_json()["prompt"]["payload"]["item_id"])
        self.assertNotEqual(first_id, second_id)
        self.assertNotEqual(first_item, second_item)
        cards = second.get_json()["question_cards"]
        ids = {str(row.get("id") or "") for row in cards}
        self.assertIn(first_item, ids)
        self.assertIn(second_item, ids)
        first_prompt = self.school.get_active_live_prompt(self.live_session_id)
        assert first_prompt is not None
        self.assertEqual(int(first_prompt["id"]), second_id)
        stored = {
            str(row.get("item_id") or ""): row
            for row in self.school.list_live_session_items(self.live_session_id)
        }
        self.assertEqual(stored[first_item]["status"], "active")
        self.assertEqual(stored[second_item]["status"], "active")


class ArtifactStaffJsTests(unittest.TestCase):
    """Teacher JS still seeds and paints the C2 Transformations Artifact."""

    def test_staff_js_seeds_and_remembers_c2_artifact(self) -> None:
        """staff_ap.js points C2 at m1c2-transforms.html and keeps lastActiveMedia."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("m1c2-transforms.html", js)
        self.assertIn("lastActiveMedia", js)
        self.assertIn("function usesC2Transforms()", js)
        self.assertIn("function mintArtifactFromMedia(", js)
        mint = js.split("async function mintArtifactFromMedia(")[1].split(
            "function bindActiveMediaControls("
        )[0]
        self.assertIn("paintLiveQuestionCards()", mint)
        self.assertIn("question_cards", mint)


if __name__ == "__main__":
    os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
    unittest.main()
