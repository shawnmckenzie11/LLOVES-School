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
    C3_PARENT_MEDIA_URL,
    LEAD_MATCH,
    LEAD_MISS,
    PARENT_CHOICES,
    PARENT_TRANSFORMATIONS_ARTIFACT_ID,
    MATCH_CHALLENGE_TOAST,
    PARENT_FUNCTION_LABEL,
    PARENT_TRANSFORMATIONS_STEM,
    TRANSFORMATIONS_ARTIFACT_ID,
    TRANSFORMATIONS_STEM,
    match_challenge_title,
    artifact_feedback_fragment,
    artifact_snapshot_matches,
    format_artifact_answer,
    format_vertex_equation,
    grade_parent_snapshot,
    grade_transform_snapshot,
    normalize_accuracy_margin,
    parent_transformations_prompt_payload,
    slider_margin,
    transformations_prompt_payload,
)
from app import create_app
from live_media import (
    apply_active_media_update,
    challenge_clears_active_media,
    is_c2_transform,
)
from live_class_metadata import load_live_class_metadata
from team_challenge import live_class_seed_media


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

    def test_normalize_accuracy_margin_is_ten_or_twenty(self) -> None:
        """Only 10% and 20% are legal; everything else snaps to 10%."""
        self.assertEqual(normalize_accuracy_margin(None), 0.10)
        self.assertEqual(normalize_accuracy_margin(0.10), 0.10)
        self.assertEqual(normalize_accuracy_margin("20%"), 0.20)
        self.assertEqual(normalize_accuracy_margin(20), 0.20)
        self.assertEqual(normalize_accuracy_margin(0.15), 0.10)

    def test_grade_twenty_percent_band(self) -> None:
        """A miss at 10% can still match when the teacher chose 20%."""
        target = {"a": 3.0, "h": 5.0, "k": -1.0}
        near = {"a": 3.45, "h": 5.0, "k": -1.0}
        tight = grade_transform_snapshot(near, target, margin=0.10)
        wide = grade_transform_snapshot(near, target, margin=0.20)
        self.assertFalse(tight["match"], tight)
        self.assertTrue(wide["match"], wide)
        self.assertAlmostEqual(tight["per_key"]["a"]["allowed"], 0.30)
        self.assertAlmostEqual(wide["per_key"]["a"]["allowed"], 0.60)
        payload_20 = transformations_prompt_payload(
            snapshot=target, accuracy_margin=0.20
        )
        self.assertEqual(payload_20["accuracy_margin"], 0.20)
        self.assertTrue(artifact_snapshot_matches(payload_20, {"params": near}))
        payload_10 = transformations_prompt_payload(snapshot=target)
        self.assertEqual(payload_10["accuracy_margin"], 0.10)
        self.assertFalse(artifact_snapshot_matches(payload_10, {"params": near}))

    def test_match_challenge_title_increments(self) -> None:
        """Locked minted titles are Match challenge N."""
        self.assertEqual(match_challenge_title(1), "Match challenge 1")
        self.assertEqual(match_challenge_title(2), "Match challenge 2")
        self.assertEqual(match_challenge_title(0), "Match challenge 1")

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
        """C2 Artifact media seeds; generic C3 still clears without a URL."""
        self.assertFalse(challenge_clears_active_media("C2"))
        self.assertTrue(challenge_clears_active_media("C3"))
        seeded = apply_active_media_update(None, challenge="C2")
        assert seeded is not None
        self.assertTrue(is_c2_transform(seeded))
        self.assertEqual(seeded["url"], C2_TRANSFORM_MEDIA_URL)
        self.assertIsNone(apply_active_media_update(seeded, challenge="C3"))
        copied = apply_active_media_update(
            None, challenge="C3", url=C2_TRANSFORM_MEDIA_URL
        )
        assert copied is not None
        self.assertEqual(copied["url"], C2_TRANSFORM_MEDIA_URL)
        seed = live_class_seed_media("MCF3M", "M1", "C3")
        assert seed is not None
        self.assertEqual(seed["url"], C2_TRANSFORM_MEDIA_URL)
        meta = load_live_class_metadata("MCF3M", "M1", "C3")
        self.assertEqual(meta["media"]["file"], C2_TRANSFORM_MEDIA_URL)
        mcr = load_live_class_metadata("MCR3U", "M1", "C3")
        self.assertEqual(mcr["media"]["file"], C3_PARENT_MEDIA_URL)

    def test_c2_media_page_has_artifact_chrome(self) -> None:
        """C2 iframe ships Wonder labels and stays same-origin."""
        rv = self.staff.get(C2_TRANSFORM_MEDIA_URL)
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True)
        self.assertIn("Make match challenge", body)
        self.assertIn('<button type="button" id="t-random">', body)
        self.assertIn("Show hot/cold sliders", body)
        self.assertIn("Group Q", body)
        self.assertIn('id="t-acc-10"', body)
        self.assertIn('id="t-acc-20"', body)
        self.assertIn("accuracy_margin", body)
        self.assertIn("a(x − h)² + k", body)
        self.assertIn("Show graph", body)
        self.assertIn("Show equation", body)
        self.assertIn("lloves-m1c2-transforms", body)
        self.assertIn("artifact-teacher-flags", body)
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
        self.assertEqual(payload["title"], "Match challenge 1")
        self.assertEqual(payload["text"], "Match challenge 1")
        self.assertEqual(payload["match_index"], 1)
        self.assertTrue(body["first_mint"])
        self.assertEqual(body["toast"], MATCH_CHALLENGE_TOAST)
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
        self.assertEqual(str(media.get("toast") or ""), "")

        student = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(student["prompt"]["kind"], ARTIFACT_KIND)
        self.assertEqual(student["prompt"]["payload"]["prompt"], TRANSFORMATIONS_STEM)
        self.assertEqual(student["prompt"]["payload"]["title"], "Match challenge 1")
        self.assertEqual(student["prompt"]["payload"]["text"], "Match challenge 1")
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
        first_body = first.get_json()
        second_body = second.get_json()
        self.assertEqual(first_body["prompt"]["payload"]["title"], "Match challenge 1")
        self.assertTrue(first_body["first_mint"])
        self.assertEqual(first_body["toast"], MATCH_CHALLENGE_TOAST)
        self.assertEqual(second_body["prompt"]["payload"]["title"], "Match challenge 2")
        self.assertFalse(second_body["first_mint"])
        self.assertEqual(second_body["toast"], "")
        self.assertEqual(str(second_body["active_media"].get("toast") or ""), "")
        titles = {
            str(row.get("id") or ""): str(row.get("text") or "")
            for row in second_body["question_cards"]
        }
        self.assertEqual(titles.get(first_item), "Match challenge 1")
        self.assertEqual(titles.get(second_item), "Match challenge 2")

    def test_mcf3m_c3_mint_reuses_c2_artifact_on_c3(self) -> None:
        """MCF3M M1 C3 Make match challenge stays on C3 and keeps C2 chrome."""
        self.school.set_live_session_teacher_state(
            self.live_session_id, live_module="M1", live_slot="C3", stage="play"
        )
        minted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "h": -1, "k": 3},
                "target_mode": "graph",
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_json())
        body = minted.get_json()
        teacher = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertEqual(teacher["live_slot"], "C3")
        self.assertEqual(body["prompt"]["payload"]["artifact_id"], TRANSFORMATIONS_ARTIFACT_ID)
        self.assertEqual(body["active_media"]["url"], C2_TRANSFORM_MEDIA_URL)
        item_id = str(body["prompt"]["payload"]["item_id"])
        stored = {
            str(row.get("item_id") or ""): row
            for row in self.school.list_live_session_items(self.live_session_id)
        }
        self.assertEqual(stored[item_id]["status"], "active")
        second = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": -1, "h": 2, "k": 0},
                "target_mode": "equation",
            },
        )
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertNotEqual(
            str(second.get_json()["prompt"]["payload"]["item_id"]), item_id
        )
        teacher = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertEqual(teacher["live_slot"], "C3")

    def test_mint_teacher_flags_reach_student_prompt(self) -> None:
        """Show hot/cold, Group Q, and 20% accuracy land on the prompt."""
        minted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 3, "h": 5, "k": -1},
                "target_mode": "graph",
                "hot_cold_visible": True,
                "group_q": True,
                "accuracy_margin": 0.20,
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_json())
        payload = minted.get_json()["prompt"]["payload"]
        self.assertTrue(payload["hot_cold_visible"])
        self.assertTrue(payload["group_q"])
        self.assertEqual(payload["accuracy_margin"], 0.20)
        student = self.student.get("/api/student/live-prompt").get_json()
        facing = student["prompt"]["payload"]
        self.assertTrue(facing["hot_cold_visible"])
        self.assertTrue(facing["group_q"])
        self.assertEqual(facing["accuracy_margin"], 0.20)
        self.assertIn("snapshot", facing)
        self.assertNotIn("cement", facing)
        media_art = minted.get_json()["active_media"].get("artifact") or {}
        self.assertEqual(media_art.get("accuracy_margin"), 0.20)
        patched = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={
                "artifact": {
                    **media_art,
                    "hot_cold_visible": False,
                    "group_q": False,
                    "accuracy_margin": 0.10,
                }
            },
        )
        self.assertEqual(patched.status_code, 200, patched.get_json())
        after = self.school.get_active_live_prompt(self.live_session_id)
        assert after is not None
        self.assertFalse(after["payload"]["hot_cold_visible"])
        self.assertFalse(after["payload"]["group_q"])
        self.assertEqual(after["payload"]["accuracy_margin"], 0.10)


class ParentArtifactTests(unittest.TestCase):
    """MCR3U M1 C3 parent-function Artifact grade + mint."""

    def test_parent_grade_and_answer_format(self) -> None:
        """Parent radio must match; sliders use the same ±10% band."""
        target = {"a": 2.0, "k": -1.0, "d": 3.0, "c": -2.0}
        hit = grade_parent_snapshot(
            {"parent": "quadratic", **target},
            target,
            target_parent="quadratic",
        )
        self.assertTrue(hit["match"], hit)
        miss_parent = grade_parent_snapshot(
            {"parent": "linear", **target},
            target,
            target_parent="quadratic",
        )
        self.assertFalse(miss_parent["match"])
        self.assertFalse(miss_parent["parent_match"])
        payload = parent_transformations_prompt_payload(
            snapshot={"a": 2, "k": -1, "d": 3, "c": -2, "parent": "abs"},
            target_mode="equation",
            parent={"kind": "abs"},
        )
        self.assertEqual(payload["artifact_id"], PARENT_TRANSFORMATIONS_ARTIFACT_ID)
        self.assertEqual(payload["slider_keys"], ["a", "k", "d", "c"])
        self.assertEqual([row["kind"] for row in payload["parent_choices"]], [row["kind"] for row in PARENT_CHOICES])
        self.assertEqual(len(payload["parent_choices"]), 4)
        self.assertNotIn("abs", [row["kind"] for row in payload["parent_choices"]])
        fb = artifact_feedback_fragment(
            payload, {"params": {"parent": "abs", "a": 2, "k": -1, "d": 3, "c": -2}}
        )
        assert fb is not None
        self.assertTrue(fb["match"])
        self.assertIn("parent=abs", format_artifact_answer(
            {"params": {"parent": "abs", "a": 2, "k": -1, "d": 3, "c": -2}}
        ))

    def test_artifact_snapshot_matches_requires_parent_and_sliders(self) -> None:
        """Parent kind and sliders must both hit for a parent Artifact match."""
        payload = parent_transformations_prompt_payload(
            snapshot={"a": 2, "k": -1, "d": 3, "c": -2},
            parent={"kind": "quadratic"},
        )
        hit = {"parent": "quadratic", "a": 2, "k": -1, "d": 3, "c": -2}
        self.assertTrue(artifact_snapshot_matches(payload, {"params": hit}))
        self.assertFalse(
            artifact_snapshot_matches(
                payload,
                {"params": {"parent": "linear", "a": 2, "k": -1, "d": 3, "c": -2}},
            )
        )
        self.assertFalse(
            artifact_snapshot_matches(
                payload,
                {"params": {"parent": "quadratic", "a": 0.2, "k": -1, "d": 3, "c": -2}},
            )
        )


class Mcr3uParentMintTests(unittest.TestCase):
    """Staff mint on an MCR3U class writes a C3 parent Artifact question."""

    def setUp(self) -> None:
        """Isolated MCR3U class with staff + student clients."""
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
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
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

    def test_c3_parent_media_has_radios_and_mint(self) -> None:
        """C3 iframe ships parent radios, sliders, and Make match challenge."""
        rv = self.staff.get(C3_PARENT_MEDIA_URL)
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True)
        self.assertIn("Make match challenge", body)
        self.assertIn('<button type="button" id="t-random">', body)
        self.assertIn("Show hot/cold sliders", body)
        self.assertIn("Group Q", body)
        self.assertIn('id="t-acc-10"', body)
        self.assertIn('id="t-acc-20"', body)
        self.assertIn("accuracy_margin", body)
        self.assertIn(PARENT_FUNCTION_LABEL, body)
        self.assertIn('id="parents-heading"', body)
        self.assertNotIn('"toast"', body)
        self.assertNotIn("toast_key", body)
        self.assertIn('name="parent"', body)
        self.assertIn('value="quadratic"', body)
        self.assertIn('value="sqrt"', body)
        self.assertIn('value="reciprocal"', body)
        self.assertNotIn('value="abs"', body)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", body)
        self.assertIn(">a<", body)
        self.assertIn(">k<", body)
        self.assertIn(">d<", body)
        self.assertIn(">c<", body)
        self.assertIn("lloves-mcr3u-m1c3-parents", body)
        self.assertIn("artifact-teacher-flags", body)
        self.assertIn(PARENT_TRANSFORMATIONS_ARTIFACT_ID, body)
        rv.close()

    def test_mint_parent_artifact_on_c3(self) -> None:
        """Teacher mint on C3 writes a Question card and grades parent+sliders."""
        self.school.set_live_session_teacher_state(
            self.live_session_id, live_module="M1", live_slot="C3", stage="play"
        )
        minted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": PARENT_TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "k": -1, "d": 3, "c": 1},
                "target_mode": "equation",
                "parent": {"kind": "sqrt"},
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_json())
        body = minted.get_json()
        payload = body["prompt"]["payload"]
        self.assertEqual(payload["artifact_id"], PARENT_TRANSFORMATIONS_ARTIFACT_ID)
        self.assertEqual(payload["parent"]["kind"], "sqrt")
        self.assertEqual(payload["title"], "Match challenge 1")
        self.assertTrue(body["first_mint"])
        self.assertEqual(body["toast"], MATCH_CHALLENGE_TOAST)
        self.assertEqual(str(body["active_media"].get("toast") or ""), "")
        self.assertEqual(payload["snapshot"]["d"], 3.0)
        teacher = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertEqual(teacher["live_slot"], "C3")
        self.assertIn("mcr3u-m1c3-parent-transformations.html", body["active_media"]["url"])
        student = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(student["prompt"]["payload"]["artifact_id"], PARENT_TRANSFORMATIONS_ARTIFACT_ID)
        self.assertEqual(student["prompt"]["payload"]["title"], "Match challenge 1")
        self.assertEqual(len(student["prompt"]["payload"]["parent_choices"]), 4)
        hit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "response": {
                    "params": {"parent": "sqrt", "a": 2.1, "k": -1.0, "d": 3.0, "c": 1.0}
                }
            },
        )
        self.assertEqual(hit.status_code, 200, hit.get_json())
        self.assertEqual(hit.get_json()["feedback"]["lead"], LEAD_MATCH)
        miss = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "response": {
                    "params": {"parent": "linear", "a": 2.1, "k": -1.0, "d": 3.0, "c": 1.0}
                }
            },
        )
        self.assertEqual(miss.status_code, 200, miss.get_json())
        self.assertEqual(miss.get_json()["feedback"]["lead"], LEAD_MISS)
        roster = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/questions/{body['prompt']['id']}/responses"
        )
        self.assertEqual(roster.status_code, 200, roster.get_json())
        answers = roster.get_json().get("responses") or []
        self.assertTrue(any("parent=" in str(row.get("answer") or "") for row in answers))


class ArtifactGroupQTests(unittest.TestCase):
    """Group Q waits until every teammate matches the Artifact snapshot."""

    def setUp(self) -> None:
        """Isolated MCF3M class with two rostered students on one team."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.maple = self.app.test_client()
        self.birch = self.app.test_client()
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
                "codenames": ["Maple", "Birch", "Cedar"],
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
        self.maple.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.maple.post("/student/mood", data={"mood": "good"})
        self.maple.post("/student/character", data={"character": "fox"})
        self.birch.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Birch"},
            follow_redirects=False,
        )
        self.birch.post("/student/mood", data={"mood": "good"})
        self.birch.post("/student/character", data={"character": "owl"})
        self.maple_id = int(
            self.school.game.find_student_by_codename(self.class_id, "Maple")["id"]
        )
        self.birch_id = int(
            self.school.game.find_student_by_codename(self.class_id, "Birch")["id"]
        )
        self.cedar_id = int(
            self.school.game.find_student_by_codename(self.class_id, "Cedar")["id"]
        )
        self.school.game.begin_game(self.class_id)
        self.school.setup_live_session_groups(
            self.live_session_id,
            n_teams=2,
            mode="manual",
            present_ids=[self.maple_id, self.birch_id, self.cedar_id],
            assignments=[
                {"student_id": self.maple_id, "team_index": 0},
                {"student_id": self.birch_id, "team_index": 0},
                {"student_id": self.cedar_id, "team_index": 1},
            ],
        )

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _mint_group_q(self) -> dict:
        """Mint a C2 Artifact with Group Q on."""
        minted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "h": 1, "k": -1},
                "target_mode": "graph",
                "hot_cold_visible": True,
                "group_q": True,
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_json())
        return minted.get_json()

    def test_group_q_waits_for_teammate_previews(self) -> None:
        """Missing preview is not a match; both matching sliders unlock submit."""
        body = self._mint_group_q()
        prompt = body["prompt"]
        prompt_id = int(prompt["id"])
        hit = {"a": 2.0, "h": 1.0, "k": -1.0}
        miss = {"a": 0.2, "h": 0.0, "k": 4.0}
        idle = self.school.artifact_group_q_status(
            self.live_session_id, self.maple_id, prompt
        )
        self.assertTrue(idle["needed"])
        self.assertFalse(idle["ready"])
        self.assertFalse(idle["matched"])
        self.assertEqual(len(idle["members"]), 2)
        self.assertTrue(all(not row["matched"] for row in idle["members"]))
        maple_only = self.school.artifact_group_q_status(
            self.live_session_id, self.maple_id, prompt, params=hit
        )
        self.assertTrue(maple_only["needed"])
        self.assertTrue(maple_only["matched"])
        self.assertFalse(maple_only["ready"])
        blocked = self.maple.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"params": hit}},
        )
        self.assertEqual(blocked.status_code, 400, blocked.get_json())
        self.assertIn("teammate", str(blocked.get_json().get("error") or "").lower())
        preview = self.birch.post(
            "/api/student/live-prompt/artifact-preview",
            json={"prompt_id": prompt_id, "params": hit},
        )
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertTrue(preview.get_json()["needed"])
        self.assertTrue(preview.get_json()["group_q_ready"])
        ready = self.school.artifact_group_q_status(
            self.live_session_id, self.maple_id, prompt, params=hit
        )
        self.assertTrue(ready["ready"])
        saved = self.maple.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"params": hit}},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()["feedback"]["lead"], LEAD_MATCH)
        student = self.maple.get("/api/student/live-prompt").get_json()
        self.assertTrue(student.get("group_q_ready"))
        cards = student.get("active_questions") or []
        artifact_cards = [
            row for row in cards if str((row.get("content") or {}).get("kind")) == "artifact"
        ]
        self.assertTrue(artifact_cards)
        self.assertTrue(artifact_cards[0].get("group_q_ready"))
        wrong = self.school.artifact_group_q_status(
            self.live_session_id, self.birch_id, prompt, params=miss
        )
        self.assertFalse(wrong["ready"])
        self.assertFalse(wrong["matched"])
        no_flag = dict(prompt)
        no_flag["payload"] = dict(prompt["payload"])
        no_flag["payload"]["group_q"] = False
        skipped = self.school.artifact_group_q_status(
            self.live_session_id, self.maple_id, no_flag, params=hit
        )
        self.assertFalse(skipped["needed"])
        self.assertTrue(skipped["ready"])
        missing = self.maple.post(
            "/api/student/live-prompt/artifact-preview",
            json={"prompt_id": 0, "params": hit},
        )
        self.assertEqual(missing.status_code, 409)

    def test_parent_group_q_requires_function_type(self) -> None:
        """Parent Group Q does not match when a teammate picks the wrong kind."""
        minted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/artifacts",
            json={
                "artifact_id": PARENT_TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "k": -1, "d": 3, "c": 1},
                "target_mode": "graph",
                "parent": {"kind": "sqrt"},
                "group_q": True,
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_json())
        prompt = minted.get_json()["prompt"]
        hit = {"parent": "sqrt", "a": 2, "k": -1, "d": 3, "c": 1}
        wrong_parent = {"parent": "linear", "a": 2, "k": -1, "d": 3, "c": 1}
        self.school.record_artifact_slider_preview(
            self.live_session_id, self.maple_id, int(prompt["id"]), hit
        )
        self.school.record_artifact_slider_preview(
            self.live_session_id, self.birch_id, int(prompt["id"]), wrong_parent
        )
        status = self.school.artifact_group_q_status(
            self.live_session_id, self.maple_id, prompt
        )
        self.assertTrue(status["needed"])
        self.assertFalse(status["ready"])
        birch_row = next(
            row for row in status["members"] if int(row["student_id"]) == self.birch_id
        )
        self.assertFalse(birch_row["matched"])


class ArtifactStaffJsTests(unittest.TestCase):
    """Teacher JS still seeds and paints the C2 Transformations Artifact."""

    def test_staff_js_seeds_and_remembers_c2_artifact(self) -> None:
        """staff_ap.js points C2 at m1c2-transforms.html and keeps lastActiveMedia."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("m1c2-transforms.html", js)
        self.assertIn("lastActiveMedia", js)
        self.assertIn("function usesC2Transforms()", js)
        self.assertIn('ontario === "MCF3M" && module === "M1" && slot === "C3"', js)
        self.assertIn("lloves-mcr3u-m1c3-parents", js)
        self.assertIn("function mintArtifactFromMedia(", js)
        self.assertIn("function patchArtifactTeacherFlags(", js)
        self.assertIn("artifact-teacher-flags", js)
        self.assertIn("accuracy_margin", js)
        self.assertIn("function showStaffMintToast(", js)
        self.assertIn("live-mint-toast", js)
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("content.hot_cold_visible", student_js)
        self.assertIn("data.hot_cold_visible", student_js)
        self.assertIn("function artifactAccuracyMargin(", student_js)
        self.assertIn("data-accuracy-margin", student_js)
        self.assertIn("is-hc-very", student_js)
        self.assertIn("function artifactGroupsRunning(", student_js)
        self.assertIn("function artifactGroupQLocked(", student_js)
        self.assertIn("function syncArtifactGroupQLock(", student_js)
        self.assertIn('data-group-q="1"', student_js)
        lifecycle = student_js.split("if (kind === \"artifact\") {")[1].split(
            "const value = current === \"—\" ? \"\" : current;"
        )[0]
        self.assertIn("disabled", lifecycle)
        shell = student_js.split("} else if (kind === \"artifact\") {")[1].split(
            "} else if (kind === \"share\""
        )[0]
        self.assertIn("disabled", shell)
        self.assertIn("id=\"prompt-artifact-submit\"", shell)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn("is-hc-cold", css)
        self.assertIn("#fde047", css)
        self.assertIn("#ef4444", css)
        mint = js.split("async function mintArtifactFromMedia(")[1].split(
            "function bindActiveMediaControls("
        )[0]
        self.assertIn("paintLiveQuestionCards()", mint)
        self.assertIn("question_cards", mint)
        self.assertIn("first_mint", mint)
        self.assertIn("showStaffMintToast", mint)
        self.assertNotIn("active_media.toast", mint)
        bind = js.split("function bindActiveMediaControls(")[1].split(
            "window.addEventListener(\"message\""
        )[0]
        self.assertNotIn("ensureC1MediaSeeded()", bind)


if __name__ == "__main__":
    os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
    unittest.main()
