#!/usr/bin/env python3
"""Source-agnostic MC live tally + Reveal for the teacher Questions card."""

from __future__ import annotations

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
from live_mc import (  # noqa: E402
    build_mc_tally,
    build_numeric_tally,
    extract_choice_labels,
    is_mc_prompt,
)
from live_media import DEFAULT_LIVE_MEDIA_URL  # noqa: E402
from live_teacher_state import MINDS_ON_PROMPT_REF  # noqa: E402
from minds_on import MINDS_ON_CHOICES, minds_on_prompt_payload  # noqa: E402


class LiveMcHelperTests(unittest.TestCase):
    """Pure tally helpers (no Flask)."""

    def test_tally_uses_correct_answer_when_key_is_missing(self) -> None:
        """A deck prompt that only stored correct_answer still marks that choice."""

        prompt = {
            "id": 3,
            "kind": "mc",
            "payload": {
                "item_id": "parabola-a",
                "prompt": "Which graph opens upward?",
                "choices": ["down", "up"],
                "correct_answer": "B",
            },
        }
        tally = build_mc_tally(prompt, responses=[], present=1)
        assert tally is not None
        marked = [row["id"] for row in tally["choices"] if row.get("correct")]
        self.assertEqual(marked, ["B"])

    def test_tally_maps_letter_or_text_and_fingerprints(self) -> None:
        """Choices accept A or full text; seq changes when a bar moves."""
        prompt = {
            "id": 9,
            "kind": "mc",
            "payload": minds_on_prompt_payload(),
        }
        self.assertTrue(is_mc_prompt(prompt))
        empty = build_mc_tally(prompt, responses=[], present=2)
        assert empty is not None
        self.assertEqual(empty["prompt_ref"], "minds_on")
        self.assertEqual(empty["responded"], 0)
        self.assertEqual(empty["present"], 2)
        self.assertEqual(empty["source"], "live_prompt")
        first = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": "A"}}],
            present=2,
        )
        via_text = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": MINDS_ON_CHOICES[0]}}],
            present=2,
        )
        assert first is not None and via_text is not None
        self.assertEqual(first["choices"][0]["count"], 1)
        self.assertEqual(via_text["choices"][0]["count"], 1)
        self.assertEqual(first["response_seq"], via_text["response_seq"])
        other = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": "B"}}],
            present=2,
        )
        assert other is not None
        self.assertNotEqual(first["response_seq"], other["response_seq"])
        self.assertIsNone(build_mc_tally({"kind": "share", "payload": {"prompt": "x"}}))

    def test_meet_chain_is_a_first_class_mc_source(self) -> None:
        """Meet tallies stored responses first; chain picks are fallback only."""
        prompt = {
            "id": 3,
            "kind": "mc",
            "payload": {
                "item_id": "meet-team",
                "artifact_id": "quick-hitter-question-chain",
                "ride": "meet_team",
                "step": "A",
                "kind": "mc",
                "prompt": "Today I’m the teammate who…",
                "choices": ["keeps us kind", "Not sure"],
            },
        }
        chain = {
            "stage": "meet",
            "chain": ["A", "C", "B"],
            "index": 0,
            "a_picks": {"p1": "keeps us kind", "p2": "Not sure"},
            "c_reacts": {},
            "b_picks": {},
            "rotated": ["asks the good question", "brings the calm"],
        }
        from_responses = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": "A"}}],
            meet_chain=chain,
            present=2,
        )
        assert from_responses is not None
        self.assertEqual(from_responses["source"], "live_prompt")
        self.assertEqual(from_responses["responded"], 1)
        self.assertEqual(from_responses["choices"][0]["count"], 1)
        fallback = build_mc_tally(prompt, responses=[], meet_chain=chain, present=2)
        assert fallback is not None
        self.assertEqual(fallback["source"], "meet_chain")
        self.assertEqual(fallback["responded"], 2)
        self.assertEqual(fallback["choices"][0]["count"], 1)
        self.assertEqual(fallback["choices"][1]["count"], 1)

    def test_options_and_unmatched_text_still_fill_bars(self) -> None:
        """Catalogue options and leftover answer text still increment bars."""
        prompt = {
            "id": 9,
            "kind": "mc",
            "payload": {
                "item_id": "meet-team",
                "type": "poll",
                "options": ["keeps us kind", "Not sure"],
            },
        }
        self.assertEqual(
            extract_choice_labels(prompt["payload"]),
            ["keeps us kind", "Not sure"],
        )
        tally = build_mc_tally(
            prompt,
            responses=[
                {"response": {"choice": "keeps us kind"}},
                {"response": {"text": "asks the good question"}},
            ],
            present=3,
        )
        assert tally is not None
        self.assertEqual(tally["responded"], 2)
        by_label = {row["label"]: row["count"] for row in tally["choices"]}
        self.assertEqual(by_label["keeps us kind"], 1)
        self.assertEqual(by_label["asks the good question"], 1)

    def test_tally_survives_more_labels_than_letters(self) -> None:
        """Ninth option or many distinct free-text votes must not raise.

        ``/state`` builds this tally for teacher light and student views.
        ``CHOICE_LETTERS`` is only A–H, so a longer label list used to
        ``IndexError`` inside ``choice_letter``.
        """
        many = [f"option-{index}" for index in range(9)]
        prompt = {
            "id": 13,
            "kind": "mc",
            "payload": {"prompt": "Pick", "kind": "mc", "choices": many},
        }
        tally = build_mc_tally(
            prompt,
            responses=[
                {"response": {"choice": "option-0"}},
                {"response": {"choice": "option-8"}},
                {"response": {"choice": "free text that is not a choice"}},
            ],
            present=3,
        )
        self.assertIsNotNone(tally)
        assert tally is not None
        self.assertLessEqual(len(tally["choices"]), 8)
        self.assertEqual(tally["choices"][0]["count"], 1)
        self.assertEqual(tally["choices"][0]["id"], "A")
        self.assertEqual(tally["responded"], 3)
        self.assertNotIn("option-8", [row["label"] for row in tally["choices"]])

        bare = {
            "id": 13,
            "kind": "mc",
            "payload": {"prompt": "Type anything", "kind": "mc"},
        }
        recovered = build_mc_tally(
            bare,
            responses=[
                {"response": {"choice": f"group note {index}"}} for index in range(12)
            ],
            present=12,
        )
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(len(recovered["choices"]), 8)
        self.assertEqual(recovered["responded"], 12)
        self.assertEqual(sum(row["count"] for row in recovered["choices"]), 8)


class LiveMcApiTests(unittest.TestCase):
    """Staff /state tally + reveal for JOIN Minds-On and CONS MC."""

    def setUp(self) -> None:
        """Isolated app with staff, rostered class, live session, and student."""
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
        self.class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_join_minds_on_populates_and_reveal_stays_in_teacher_state(self) -> None:
        """JOIN Minds-On: live tally is staff-only until Reveal shares + closes."""
        idle = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(idle.status_code, 200, idle.get_json())
        student = self.student.get("/api/student/state").get_json()
        self.assertNotIn("mc_tally", student)
        self.assertFalse(student.get("poll_closed"))
        prompt = student.get("prompt")
        if prompt is None:
            item = next(
                row
                for row in self.school.ensure_live_session_items(self.session_id)
                if str(row.get("item_id") or "") == "minds_on"
            )
            self.school.publish_live_session_item(
                self.session_id, int(item["id"]), publish_mode="individual"
            )
            student = self.student.get("/api/student/state").get_json()
            prompt = student.get("prompt")
            if prompt is None:
                active = student.get("active_questions") or []
                self.assertTrue(active, student)
                prompt = active[0]["prompt"]
        self.assertEqual(prompt["payload"]["item_id"], "minds_on")
        idle = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        idle_tally = idle.get_json()["mc_tally"]
        self.assertEqual(idle_tally["prompt_ref"], "minds_on")
        self.assertEqual(idle_tally["responded"], 0)
        self.assertGreaterEqual(idle_tally["present"], 1)
        self.assertEqual(idle_tally["source"], "live_prompt")
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[0]},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        live = self.staff.get(f"/api/live-sessions/{self.session_id}/state").get_json()
        tally = live["mc_tally"]
        self.assertEqual(tally["responded"], 1)
        self.assertEqual(tally["choices"][0]["count"], 1)
        self.assertGreater(tally["response_seq"], idle_tally["response_seq"])
        self.assertIsNone((live.get("teacher_state") or {}).get("mc_ui"))
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "mc_ui": {
                    "prompt_ref": MINDS_ON_PROMPT_REF,
                    "reveal": True,
                    "reveal_to_students": False,
                }
            },
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        ui = shown.get_json()["teacher_state"]["mc_ui"]
        self.assertTrue(ui["reveal"])
        self.assertTrue(ui["reveal_to_students"])
        self.assertTrue(ui["poll_closed"])
        self.assertIsNone(shown.get_json()["teacher_state"].get("cue_id"))
        student_after = self.student.get("/api/student/state").get_json()
        self.assertEqual(
            (student_after.get("prompt") or {}).get("payload", {}).get("item_id"),
            "minds_on",
        )
        self.assertTrue(student_after.get("poll_closed"))
        self.assertTrue(
            (student_after.get("teacher_state") or {})
            .get("mc_ui", {})
            .get("reveal_to_students")
        )
        blocked = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[1]},
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertTrue(blocked.get_json().get("poll_closed"))
        self.assertEqual(blocked.get_json().get("error"), "Poll is closed.")

    def test_cons_mc_uses_the_same_tally_pipeline(self) -> None:
        """CONS-1 (not Minds-On) fills the same staff mc_tally shape."""
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"stage": "play", "student_view": {"questions": "student"}},
        )
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL, "frozen": True},
        )
        cons = self.staff.post(
            f"/api/live-sessions/{self.session_id}/active-media",
            json={"cons_item": "C1-CONS-1"},
        )
        self.assertEqual(cons.status_code, 200, cons.get_json())
        student = self.student.get("/api/student/state").get_json()
        self.assertEqual(student["prompt"]["payload"]["item_id"], "C1-CONS-1")
        self.assertEqual(student["prompt"]["kind"], "mc")
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": student["prompt"]["id"],
                "response": {"choice": "a > 0"},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        tally = self.staff.get(
            f"/api/live-sessions/{self.session_id}/state"
        ).get_json()["mc_tally"]
        self.assertEqual(tally["prompt_ref"], "C1-CONS-1")
        self.assertEqual(tally["item_id"], "C1-CONS-1")
        self.assertEqual(tally["source"], "live_prompt")
        self.assertEqual(tally["responded"], 1)
        self.assertEqual(tally["choices"][1]["id"], "B")
        self.assertEqual(tally["choices"][1]["count"], 1)
        revealed = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"mc_ui": {"prompt_ref": "C1-CONS-1", "reveal": True}},
        )
        self.assertEqual(revealed.status_code, 200, revealed.get_json())
        self.assertTrue(revealed.get_json()["teacher_state"]["mc_ui"]["reveal"])
        self.assertFalse(
            revealed.get_json()["teacher_state"]["mc_ui"]["reveal_to_students"]
        )


class NumericTallyTests(unittest.TestCase):
    """Decimal buckets on the numeric class chart."""

    def _prompt(self, **payload: object) -> dict:
        """Return a numeric live prompt with the given payload fields."""
        body = {"prompt": "Evaluate", "item_id": "eval-decimal"}
        body.update(payload)
        return {"id": 9, "kind": "numeric", "payload": body}

    def _answers(self, *values: object) -> list[dict]:
        """Wrap raw student values as live response rows."""
        return [{"response": {"value": value}} for value in values]

    def test_decimal_buckets_merge_trailing_zeros(self) -> None:
        """2.50 and 2.5 share one bar, labelled with at most two decimals."""
        tally = build_numeric_tally(
            self._prompt(integer_only=False, correct_answer="2.5"),
            responses=self._answers(2.5, "2.50", 2.25, "1.20", 1.236, " 2.500 "),
            present=6,
        )
        assert tally is not None
        labels = [row["label"] for row in tally["choices"]]
        self.assertEqual(labels, ["1.2", "1.24", "2.25", "2.5"])
        by_label = {row["label"]: row for row in tally["choices"]}
        self.assertEqual(by_label["2.5"]["count"], 3)
        self.assertEqual(by_label["2.5"]["id"], "2.5")
        self.assertTrue(by_label["2.5"]["correct"])
        self.assertFalse(by_label["1.2"]["correct"])
        self.assertEqual(tally["responded"], 6)
        self.assertEqual(tally["kind"], "numeric")

    def test_negative_decimals_sort_numerically(self) -> None:
        """Negative decimals stay on the chart and sort by value, not text."""
        tally = build_numeric_tally(
            self._prompt(integer_only=False, key="-1.5"),
            responses=self._answers("2.50", -1.5, "-0.25", 0, 10, -1.50),
            present=6,
        )
        assert tally is not None
        labels = [row["label"] for row in tally["choices"]]
        self.assertEqual(labels, ["-1.5", "-0.25", "0", "2.5", "10"])
        by_label = {row["label"]: row for row in tally["choices"]}
        self.assertEqual(by_label["-1.5"]["count"], 2)
        self.assertTrue(by_label["-1.5"]["correct"])
        self.assertFalse(by_label["2.5"]["correct"])

    def test_integer_only_prompt_drops_fractions(self) -> None:
        """Whole-number prompts still skip decimals and keep integer labels."""
        tally = build_numeric_tally(
            self._prompt(integer_only=True, correct_answer="-2"),
            responses=self._answers(-2, -2.0, "3", 2.5, "2.50", -1),
            present=6,
        )
        assert tally is not None
        labels = [row["label"] for row in tally["choices"]]
        self.assertEqual(labels, ["-2", "-1", "3"])
        by_label = {row["label"]: row for row in tally["choices"]}
        self.assertEqual(by_label["-2"]["count"], 2)
        self.assertTrue(by_label["-2"]["correct"])
        self.assertFalse(by_label["3"]["correct"])
        self.assertEqual(tally["responded"], 4)
        self.assertNotIn("2.5", labels)

    def test_decimal_correct_answer_uses_tolerance(self) -> None:
        """A decimal key marks the matching bar, including an absolute window."""
        tally = build_numeric_tally(
            self._prompt(
                integer_only=False,
                correct_answer="2.5",
                tolerance=0.1,
                tolerance_kind="absolute",
            ),
            responses=self._answers(2.5, 2.55, 3)
            + [{"response": {"choice": "2.50"}}],
            present=4,
        )
        assert tally is not None
        by_label = {row["label"]: row for row in tally["choices"]}
        self.assertEqual(by_label["2.5"]["count"], 2)
        self.assertTrue(by_label["2.5"]["correct"])
        self.assertTrue(by_label["2.55"]["correct"])
        self.assertFalse(by_label["3"]["correct"])
        percent = build_numeric_tally(
            self._prompt(
                integer_only=False,
                key="10",
                tolerance=10,
                tolerance_kind="percent",
            ),
            responses=self._answers(10.5, 12),
            present=2,
        )
        assert percent is not None
        percent_marks = {row["label"]: row["correct"] for row in percent["choices"]}
        self.assertTrue(percent_marks["10.5"])
        self.assertFalse(percent_marks["12"])

    def test_wrong_answer_rounding_into_the_correct_bar_gets_its_own_bar(self) -> None:
        """MCK-83: 3.141 is wrong for key 3.14 and must not count on the ✓ bar."""
        tally = build_numeric_tally(
            self._prompt(integer_only=False, correct_answer="3.14"),
            responses=self._answers(3.14, "3.140", 3.141, 3.138, 2.5),
            present=5,
        )
        assert tally is not None
        rows = [(row["label"], row["count"], row["correct"]) for row in tally["choices"]]
        self.assertEqual(
            rows,
            [
                ("2.5", 1, False),
                ("3.138", 1, False),
                ("3.14", 2, True),
                ("3.141", 1, False),
            ],
        )
        self.assertEqual(tally["responded"], 5)
        self.assertEqual(sum(row["count"] for row in tally["choices"]), 5)
        self.assertEqual(len({row["id"] for row in tally["choices"]}), 4)

    def test_near_miss_past_six_places_gets_a_distinct_label(self) -> None:
        """Ops LOW-1 on #199: 2.5000001 vs key 2.5 must not print as a second "2.5"."""
        tally = build_numeric_tally(
            self._prompt(integer_only=False, correct_answer="2.5"),
            responses=self._answers(2.5, 2.5000001, 3.0000001),
            present=3,
        )
        assert tally is not None
        rows = [(row["id"], row["label"], row["count"], row["correct"]) for row in tally["choices"]]
        self.assertEqual(
            rows,
            [
                ("2.5", "2.5", 1, True),
                ("2.5000001", "2.5000001", 1, False),
                ("3", "3", 1, False),
            ],
        )
        labels = [row["label"] for row in tally["choices"]]
        self.assertEqual(len(set(labels)), len(labels), "duplicate-looking bars")
        self.assertTrue(all("~" not in str(row["id"]) for row in tally["choices"]))

    def test_key_finer_than_six_places_labels_bars_at_full_precision(self) -> None:
        """Ops LOW on 54fdc0e: key 1e-7 read "0 ✓" and an exact 0 read "≈0 ✗"."""
        tally = build_numeric_tally(
            self._prompt(integer_only=False, correct_answer="0.0000001"),
            responses=self._answers(1e-7, 1.1e-7, 0),
            present=3,
        )
        assert tally is not None
        rows = [(row["label"], row["count"], row["correct"]) for row in tally["choices"]]
        self.assertEqual(
            rows,
            [
                ("0", 1, False),
                ("0.0000001", 1, True),
                ("0.00000011", 1, False),
            ],
        )
        self.assertTrue(all("\u2248" not in label for label, _c, _ok in rows))

    def _rows(self, key: str, *values: object, **extra: object) -> list[tuple]:
        tally = build_numeric_tally(
            self._prompt(integer_only=False, correct_answer=key, **extra),
            responses=self._answers(*values),
            present=len(values),
        )
        assert tally is not None
        rows = [(row["label"], row["count"], row["correct"]) for row in tally["choices"]]
        self.assertTrue(all("\u2248" not in label for label, _c, _ok in rows), rows)
        self.assertEqual(sum(count for _l, count, _ok in rows), len(values))
        self.assertEqual(
            [row["id"] for row in tally["choices"]], [label for label, _c, _ok in rows]
        )
        return rows

    def test_key_with_more_places_than_its_bar_labels_at_full_precision(self) -> None:
        """Ops MED on 41acf60: 3-6 place keys read "3.14 ✓ · ≈3.14 ✗".

        Bars round to 2 places, so the fix triggers whenever the key prints
        differently from its 2-place bar, not only past 6 places.
        """
        self.assertEqual(
            self._rows("3.14159", 3.14159, 3.14159, 3.14, 3.142, 3.1416),
            [
                ("3.14", 1, False),
                ("3.14159", 2, True),
                ("3.1416", 1, False),
                ("3.142", 1, False),
            ],
        )
        self.assertEqual(
            self._rows("0.125", 0.125, 0.13, 0.12),
            [("0.12", 1, False), ("0.125", 1, True), ("0.13", 1, False)],
        )
        self.assertEqual(
            self._rows("0.001", 0.001, 0),
            [("0", 1, False), ("0.001", 1, True)],
        )
        self.assertEqual(
            self._rows("123456.789", 123456.789, 123456.789, 123456.79),
            [("123456.789", 2, True), ("123456.79", 1, False)],
        )
        # Keys that already print as their bar are unchanged.
        self.assertEqual(
            self._rows("2.5", 2.5, "2.50", 2.4),
            [("2.4", 1, False), ("2.5", 2, True)],
        )

    def test_fine_key_with_tolerance_keeps_the_spread_of_right_answers(self) -> None:
        """Ops LOW: correct answers outside the key's bar keep their own bars."""
        self.assertEqual(
            self._rows(
                "1.0000001", 1.005, 1, 0.995, 1.0000001, tolerance=0.01,
                tolerance_kind="absolute",
            ),
            [("1.0000001", 3, True), ("1.01", 1, True)],
        )
        self.assertEqual(
            self._rows(
                "2.505", 2.5, 2.505, 2.51, 2.52, tolerance=0.01,
                tolerance_kind="absolute",
            ),
            # 2.51 shares the key's 2-place bar, so it joins the key's bar.
            [("2.5", 1, True), ("2.505", 2, True), ("2.52", 1, False)],
        )
        # A right answer in the key's bar joins it; an exact wrong one there
        # keeps its plain label (no "≈3.14").
        self.assertEqual(
            self._rows(
                "3.14159", 3.141, 3.14, tolerance=0.001, tolerance_kind="absolute"
            ),
            [("3.14", 1, False), ("3.14159", 1, True)],
        )
        # A right and a wrong answer sharing another bar never share a label.
        tally = build_numeric_tally(
            self._prompt(
                integer_only=False,
                correct_answer="2.505",
                tolerance=0.003,
                tolerance_kind="absolute",
            ),
            responses=self._answers(2.503, 2.5),
            present=2,
        )
        assert tally is not None
        rows = sorted(
            (row["label"], row["count"], row["correct"]) for row in tally["choices"]
        )
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual(sorted(ok for _l, _c, ok in rows), [False, True], rows)

    def test_bar_of_only_wrong_answers_still_rounds(self) -> None:
        """Wrong answers keep sharing a rounded bar when no right one is there."""
        tally = build_numeric_tally(
            self._prompt(integer_only=False, correct_answer="5"),
            responses=self._answers(3.141, 3.138),
            present=2,
        )
        assert tally is not None
        rows = [(row["label"], row["count"], row["correct"]) for row in tally["choices"]]
        self.assertEqual(rows, [("3.14", 2, False)])

    def test_missing_integer_only_keeps_decimal_buckets(self) -> None:
        """A numeric prompt with no integer_only flag still charts decimals."""
        tally = build_numeric_tally(
            self._prompt(),
            responses=self._answers(1.5, "nope", None),
            present=1,
        )
        assert tally is not None
        self.assertEqual([row["label"] for row in tally["choices"]], ["1.5"])
        self.assertEqual(tally["responded"], 1)
        self.assertFalse(tally["choices"][0]["correct"])


if __name__ == "__main__":
    unittest.main()
