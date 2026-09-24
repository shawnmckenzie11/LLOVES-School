#!/usr/bin/env python3
"""RANK live questions: Borda display, shared team order, and participation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
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
from live_mc import is_mc_prompt  # noqa: E402
from live_rank import (  # noqa: E402
    borda_class_order,
    build_rank_options,
    build_rank_tally,
    parse_rank_order,
    toggle_rank_order,
)
from test_live_class_backend_state import metadata_fixture  # noqa: E402

RANK_OPTIONS = [
    {"id": "o1", "label": "Chewing loudly"},
    {"id": "o2", "label": "Shoddy wifi"},
    {"id": "o3", "label": "Wet socks"},
]
FOUR_OPTIONS = [
    {"id": "o1", "label": "Chewing loudly"},
    {"id": "o2", "label": "Shoddy wifi"},
    {"id": "o3", "label": "Wet socks"},
    {"id": "o4", "label": "Late bus"},
]


def rank_metadata() -> dict[str, Any]:
    """Return the shared live fixture plus one rank question."""

    data = metadata_fixture()
    rank = {
        "id": "q-rank",
        "ref": "test/question/q-rank",
        "item_type": "question",
        "stage": "round",
        "page_number": 1,
        "order": 4,
        "type": "rank",
        "text": "Rank these (1 = worst)",
        "options": [row["label"] for row in RANK_OPTIONS],
        "rank_options": RANK_OPTIONS,
        "default_status": "inactive",
        "publish_modes": ["individual"],
        "response_mode": "individual",
    }
    data["questions"] = [*data["questions"], rank]
    media = data["items"][-1]
    data["items"] = [*data["questions"], media]
    return data


class RankOrderTests(unittest.TestCase):
    """Borda, tap/undo, and defensive order parsing. No database."""

    def test_borda_ties_share_rank_and_first_picks_break_display(self) -> None:
        """Equal points share a rank. More first picks sorts the display."""

        options = FOUR_OPTIONS
        votes = [
            ["o1", "o2", "o3", "o4"],
            ["o2", "o3", "o4", "o1"],
        ]
        rows = borda_class_order(votes, options)
        by_id = {row["option_id"]: row for row in rows}
        self.assertEqual(by_id["o2"]["points"], 5)
        self.assertEqual(by_id["o1"]["points"], 3)
        self.assertEqual(by_id["o3"]["points"], 3)
        self.assertEqual(by_id["o4"]["points"], 1)
        self.assertEqual(
            [row["rank"] for row in rows],
            [1, 2, 2, 4],
        )
        self.assertEqual(rows[1]["option_id"], "o1")
        self.assertGreater(by_id["o1"]["first_picks"], by_id["o3"]["first_picks"])
        self.assertEqual(rows[0]["option_id"], "o2")

    def test_zero_votes_stay_in_authored_order_with_empty_bars(self) -> None:
        """No submits keeps the authored list and does not invent points."""

        rows = borda_class_order([], RANK_OPTIONS)
        self.assertEqual([row["option_id"] for row in rows], ["o1", "o2", "o3"])
        self.assertTrue(all(row["points"] == 0 and row["bar_pct"] == 0 for row in rows))
        self.assertTrue(all(row["rank"] == 1 for row in rows))

    def test_malformed_votes_are_skipped(self) -> None:
        """A bad order never raises and never changes the Borda sum."""

        rows = borda_class_order(
            [["o1", "o2"], "nope", ["o1", "o1", "o2"], ["o2", "o1", "o3"]],
            RANK_OPTIONS,
        )
        by_id = {row["option_id"]: row for row in rows}
        self.assertEqual(by_id["o2"]["points"], 2)
        self.assertEqual(by_id["o1"]["points"], 1)
        self.assertEqual(by_id["o3"]["points"], 0)

    def test_toggle_renumbers_without_gaps(self) -> None:
        """Removing a middle rank shifts every higher badge down by one."""

        allowed = ["o1", "o2", "o3"]
        order = toggle_rank_order([], "o1", allowed)
        order = toggle_rank_order(order, "o2", allowed)
        order = toggle_rank_order(order, "o3", allowed)
        self.assertEqual(order, ["o1", "o2", "o3"])
        undone = toggle_rank_order(order, "o2", allowed)
        self.assertEqual(undone, ["o1", "o3"])
        self.assertEqual(parse_rank_order(undone, allowed, complete=False), undone)

    def test_complete_order_rejects_partial_and_unknown_ids(self) -> None:
        """Submit requires one use of every authored id."""

        allowed = ["o1", "o2", "o3"]
        with self.assertRaises(ValueError):
            parse_rank_order(["o1", "o2"], allowed, complete=True)
        with self.assertRaises(ValueError):
            parse_rank_order(["o1", "o2", "o9"], allowed, complete=True)
        with self.assertRaises(ValueError):
            parse_rank_order("o1", allowed, complete=False)
        tally = build_rank_tally(
            {
                "id": 1,
                "kind": "rank",
                "payload": {"type": "rank", "rank_options": RANK_OPTIONS},
            },
            responses=[{"response": {"order": "nope"}}, {"response": None}],
            present=4,
        )
        assert tally is not None
        self.assertEqual(tally["responded"], 0)
        self.assertFalse(is_mc_prompt({"kind": "rank", "payload": {"options": ["A", "B", "C"]}}))

    def test_authored_options_reject_blanks_dupes_and_the_wrong_count(self) -> None:
        """Add New sends trimmed unique labels, three to six."""

        rows = build_rank_options(["  wifi ", "socks", "chewing"])
        self.assertEqual([row["id"] for row in rows], ["o1", "o2", "o3"])
        self.assertEqual(rows[0]["label"], "wifi")
        with self.assertRaises(ValueError):
            build_rank_options(["wifi", "socks"])
        with self.assertRaises(ValueError):
            build_rank_options(["wifi", "Wifi", "socks"])
        with self.assertRaises(ValueError):
            build_rank_options(["a", "b", "c", "d", "e", "f", "g"])


class RankLiveSessionTests(unittest.TestCase):
    """Group and individual rank against an isolated school database."""

    def setUp(self) -> None:
        """Create one teacher, four students, and a rank-capable live session."""

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
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Aspen", "Birch", "Cedar", "Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        with self.school.game._lock:
            self.students = [
                dict(row)
                for row in self.school.game.conn.execute(
                    """
                    SELECT * FROM students
                    WHERE class_id = ?
                    ORDER BY id ASC
                    """,
                    (self.class_id,),
                ).fetchall()
            ]
        self.student_ids = [int(row["id"]) for row in self.students]
        self.school.live_class_metadata_for_session = lambda _session_id: rank_metadata()
        self.school.set_live_session_teacher_state(
            self.session_id,
            stage="round",
            student_view={"questions": "student"},
        )

    def tearDown(self) -> None:
        """Close sqlite and remove the temp directory."""

        self.school.close()
        self.tmp.cleanup()

    def _join(self, count: int = 4) -> None:
        """Open attendance and join the first ``count`` students."""

        self.school.game.begin_game(self.class_id)
        for student in self.students[:count]:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )

    def _teams(self, n_teams: int = 2) -> None:
        """Assign joined roster ids across ``n_teams`` named teams."""

        assignments = [
            {"student_id": student_id, "team_index": index % n_teams}
            for index, student_id in enumerate(self.student_ids)
        ]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=n_teams,
            mode="manual",
            present_ids=self.student_ids,
            assignments=assignments,
        )

    def _publish_rank(self, mode: str = "group_submit") -> dict[str, Any]:
        """Publish the fixture rank item in group or individual mode."""

        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "q-rank")
        return self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode=mode
        )

    def _submit_order(
        self, item: dict[str, Any], student_id: int, order: list[str]
    ) -> dict[str, Any]:
        """Tap a full shared order, then submit it for the team."""

        for option_id in order:
            self.school.save_group_mc_draft(
                self.session_id,
                int(item["id"]),
                student_id,
                choice="",
                why="",
                tap=option_id,
            )
        return self.school.submit_group_mc_answer(
            self.session_id,
            int(item["id"]),
            student_id,
            choice="",
            why="",
            order=order,
        )

    def test_allow_list_accepts_rank_and_rejects_junk(self) -> None:
        """Staff Add New stores rank option ids and still rejects essay."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C1",
            question_type="rank",
            text="Rank the noises",
            page_number=1,
            options=["wifi", "socks", "chewing"],
        )
        item = placed.get("item") or {}
        self.assertEqual(item.get("type"), "rank")
        self.assertEqual(
            [row["id"] for row in item.get("rank_options") or []],
            ["o1", "o2", "o3"],
        )
        self.assertNotIn("key", item)
        with self.assertRaises(ValueError):
            self.school.add_staff_question_to_class_playlist(
                self.class_id,
                "M1",
                "C1",
                question_type="rank",
                text="Too short",
                page_number=1,
                options=["wifi", "socks"],
            )
        with self.assertRaises(ValueError):
            self.school.add_staff_question_to_class_playlist(
                self.class_id,
                "M1",
                "C1",
                question_type="essay",
                text="Nope",
                page_number=1,
            )

    def test_group_borda_is_one_vote_per_team_and_misses_stay_blank(self) -> None:
        """Class order ignores a waiting team. Status stays waiting or check."""

        self._join(4)
        self._teams(2)
        published = self._publish_rank("group_submit")
        self.assertTrue(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "run_as_group"
            ]
        )
        first = self._submit_order(published, self.student_ids[0], ["o2", "o1", "o3"])
        self.assertEqual(first["phase"], "submitted")
        self.assertEqual(first["order"], ["o2", "o1", "o3"])
        self.assertNotIn("submitter_log", first)
        again = self.school.submit_group_mc_answer(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="",
            why="",
            order=["o2", "o1", "o3"],
        )
        self.assertEqual(again["phase"], "submitted")
        open_view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        self.assertNotIn("reveal", open_view)
        for row in open_view["status_board"]:
            self.assertEqual(set(row), {"team_id", "team_name", "submitted"})
        log = next(
            row for row in open_view["submitter_log"] if row["last_submitter"] == "Aspen"
        )
        self.assertEqual(log["resubmit_count"], 0)
        rank = open_view["rank"]
        self.assertEqual(rank["unit"], "team")
        self.assertEqual(rank["responded"], 1)
        submitted_rows = [row for row in rank["teams"] if row["order"]]
        waiting_rows = [row for row in rank["teams"] if not row["order"]]
        self.assertEqual(len(submitted_rows), 1)
        self.assertEqual(submitted_rows[0]["order"], ["o2", "o1", "o3"])
        self.assertTrue(waiting_rows)
        self.assertEqual(waiting_rows[0]["status"], "waiting")
        by_id = {row["option_id"]: row for row in rank["class_order"]}
        self.assertEqual(by_id["o2"]["points"], 2)
        self.assertEqual(by_id["o2"]["rank"], 1)
        student = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        card = next(
            row for row in student["active_questions"] if row["item_id"] == "q-rank"
        )
        self.assertIsNone(card["results"])
        self.assertEqual(card["group_submit"]["order"], ["o2", "o1", "o3"])
        self.assertNotIn("submitter_log", card)
        other = self.school.student_live_items_payload(
            self.session_id, self.student_ids[1]
        )
        other_card = next(
            row for row in other["active_questions"] if row["item_id"] == "q-rank"
        )
        self.assertIsNone(other_card["results"])
        self.assertEqual(other_card["group_submit"]["order"], [])
        self.school.close_live_session_item(self.session_id, int(published["id"]))
        closed = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        misses = [row for row in closed["reveal"] if row["missed"]]
        hits = [row for row in closed["reveal"] if not row["missed"]]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["order"], ["o2", "o1", "o3"])
        self.assertTrue(misses)
        for row in misses:
            self.assertEqual(row["answer"], "")
            self.assertEqual(row["order"], [])
        credits = self.school.participation_question_credits_for_class(self.class_id)
        self.assertEqual(credits.get(self.student_ids[0], 0), 0)
        state = self.school.get_live_session_state(self.session_id)
        self.assertIsNone(state.get("mc_tally"))

    def test_light_poll_sees_a_new_group_rank_submission(self) -> None:
        """A team submit moves the light-poll token and the teacher collate.

        The staff light poll does not rebuild Borda. It reports how many
        teams have submitted plus a revision that also moves on re-submit,
        which is what makes the Questions card refetch within one tick.
        """

        self._join(4)
        self._teams(2)
        published = self._publish_rank("group_submit")
        item_id = int(published["id"])
        before = self.school.get_live_session_state(self.session_id, light=True)
        counts = before.get("lifecycle_response_counts") or {}
        revs = before.get("lifecycle_rank_revs") or {}
        self.assertEqual(counts.get(item_id), 0)
        self.assertIn(item_id, revs)
        self.assertIsNone(before.get("mc_tally"))
        self._submit_order(published, self.student_ids[0], ["o2", "o1", "o3"])
        after = self.school.get_live_session_state(self.session_id, light=True)
        self.assertEqual(
            (after.get("lifecycle_response_counts") or {}).get(item_id),
            1,
        )
        self.assertNotEqual(
            str((after.get("lifecycle_rank_revs") or {}).get(item_id) or ""),
            str(revs.get(item_id) or ""),
        )
        view = self.school.live_session_item_results(self.session_id, item_id)
        submitted = [row for row in view["rank"]["teams"] if row.get("order")]
        waiting = [row for row in view["rank"]["teams"] if not row.get("order")]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["status"], "submitted")
        self.assertEqual(submitted[0]["order"], ["o2", "o1", "o3"])
        self.assertTrue(waiting)
        self.assertEqual(waiting[0]["status"], "waiting")
        by_id = {row["option_id"]: row for row in view["rank"]["class_order"]}
        self.assertEqual(by_id["o2"]["points"], 2)
        self._submit_order(published, self.student_ids[2], ["o3", "o2", "o1"])
        revised = self.school.get_live_session_state(self.session_id, light=True)
        self.assertEqual(
            (revised.get("lifecycle_response_counts") or {}).get(item_id),
            1,
        )
        self.assertNotEqual(
            str((revised.get("lifecycle_rank_revs") or {}).get(item_id) or ""),
            str((after.get("lifecycle_rank_revs") or {}).get(item_id) or ""),
        )
        again = self.school.live_session_item_results(self.session_id, item_id)
        voted = next(row for row in again["rank"]["teams"] if row.get("order"))
        self.assertEqual(voted["order"], ["o3", "o2", "o1"])
        revised_points = {
            row["option_id"]: row["points"] for row in again["rank"]["class_order"]
        }
        self.assertEqual(revised_points["o3"], 2)
        self.assertIsNone(revised.get("mc_tally"))

    def test_resubmit_overwrites_and_appends_the_staff_log(self) -> None:
        """A changed shared order replaces the team vote and logs the editor."""

        self._join(4)
        self._teams(2)
        published = self._publish_rank("group_submit")
        self._submit_order(published, self.student_ids[0], ["o1", "o2", "o3"])
        revised = self._submit_order(published, self.student_ids[2], ["o3", "o2", "o1"])
        self.assertEqual(revised["submitted_order"], ["o3", "o2", "o1"])
        self.assertEqual(revised["last_submitter"], "Cedar")
        view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        log = next(row for row in view["submitter_log"] if row["last_submitter"] == "Cedar")
        self.assertEqual(log["resubmit_count"], 1)
        voted = next(row for row in view["rank"]["teams"] if row["order"])
        self.assertEqual(voted["order"], ["o3", "o2", "o1"])
        self.assertEqual(view["rank"]["responded"], 1)

    def test_resubmit_after_clear_changes_order_submitter_and_revision(self) -> None:
        """Clear, then a new order, overwrites the team vote and the light token.

        Ops: Change team order, Clear, tap a new order, Submit for team.
        The clear must not freeze the submitted vote. The following submit
        replaces ``submitted_order`` and ``last_submitter`` and moves
        ``lifecycle_rank_revs`` so the teacher light poll refetches.
        """

        self._join(4)
        self._teams(2)
        published = self._publish_rank("group_submit")
        item_id = int(published["id"])
        first = self._submit_order(published, self.student_ids[0], ["o1", "o2", "o3"])
        self.assertEqual(first["submitted_order"], ["o1", "o2", "o3"])
        self.assertEqual(first["last_submitter"], "Aspen")
        cleared = self.school.save_group_mc_draft(
            self.session_id,
            item_id,
            self.student_ids[2],
            choice="",
            why="",
            clear=True,
        )
        self.assertEqual(cleared["order"], [])
        self.assertEqual(cleared["submitted_order"], ["o1", "o2", "o3"])
        self.assertEqual(cleared["last_submitter"], "Aspen")
        drafted = {}
        for option_id in ("o3", "o2", "o1"):
            drafted = self.school.save_group_mc_draft(
                self.session_id,
                item_id,
                self.student_ids[2],
                choice="",
                why="",
                tap=option_id,
            )
        self.assertEqual(drafted["order"], ["o3", "o2", "o1"])
        self.assertEqual(drafted["submitted_order"], ["o1", "o2", "o3"])
        before = self.school.get_live_session_state(self.session_id, light=True)
        revised = self.school.submit_group_mc_answer(
            self.session_id,
            item_id,
            self.student_ids[2],
            choice="",
            why="",
            order=["o3", "o2", "o1"],
        )
        self.assertEqual(revised["submitted_order"], ["o3", "o2", "o1"])
        self.assertEqual(revised["last_submitter"], "Cedar")
        after = self.school.get_live_session_state(self.session_id, light=True)
        self.assertNotEqual(
            str((after.get("lifecycle_rank_revs") or {}).get(item_id) or ""),
            str((before.get("lifecycle_rank_revs") or {}).get(item_id) or ""),
        )
        view = self.school.live_session_item_results(self.session_id, item_id)
        log = next(row for row in view["submitter_log"] if row["last_submitter"] == "Cedar")
        self.assertEqual(log["resubmit_count"], 1)
        voted = next(row for row in view["rank"]["teams"] if row["order"])
        self.assertEqual(voted["order"], ["o3", "o2", "o1"])

    def test_change_mode_local_draft_beats_the_submitted_order(self) -> None:
        """An editing student's draft, including Clear, survives the next poll paint."""

        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertNotIn("editing ? (group.submitted_order", student)
        paint = student.split("function studentGroupCardHtml(")[1].split(
            "function syncGroupSubmitGate("
        )[0]
        self.assertIn("rankEditDisplayOrder", paint)
        self.assertIn('group.phase === "submitted" && !editing', paint)
        match = re.search(
            r"function rankEditDisplayOrder\(input\) \{.*?\n\}",
            student,
            re.S,
        )
        self.assertIsNotNone(match)
        script = (match.group(0) if match else "") + """
const submitted = ["o1", "o2", "o3"];
const cleared = rankEditDisplayOrder({
  editing: true,
  localDraft: [],
  serverOrder: submitted,
});
if (JSON.stringify(cleared) !== "[]") {
  throw new Error("clear was clobbered: " + JSON.stringify(cleared));
}
const tapped = rankEditDisplayOrder({
  editing: true,
  localDraft: ["o3", "o1"],
  serverOrder: submitted,
});
if (JSON.stringify(tapped) !== JSON.stringify(["o3", "o1"])) {
  throw new Error("tap was clobbered: " + JSON.stringify(tapped));
}
const teammate = rankEditDisplayOrder({
  editing: false,
  localDraft: ["o3"],
  serverOrder: ["o2", "o1", "o3"],
});
if (JSON.stringify(teammate) !== JSON.stringify(["o2", "o1", "o3"])) {
  throw new Error("teammate lost the server draft: " + JSON.stringify(teammate));
}
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_simultaneous_taps_keep_a_valid_order(self) -> None:
        """Two teammates tapping at once leave a unique order and no error."""

        self._join(4)
        self._teams(2)
        published = self._publish_rank("group_submit")
        errors: list[BaseException] = []
        taps = ["o1", "o2", "o3", "o1", "o2", "o3"]

        def tap(option_id: str, student_id: int) -> None:
            """Apply one shared tap and record any failure."""

            try:
                self.school.save_group_mc_draft(
                    self.session_id,
                    int(published["id"]),
                    student_id,
                    choice="",
                    why="",
                    tap=option_id,
                )
            except BaseException as exc:  # noqa: BLE001 — the test asserts none
                errors.append(exc)

        threads = [
            threading.Thread(
                target=tap,
                args=(option_id, self.student_ids[0 if index % 2 == 0 else 2]),
            )
            for index, option_id in enumerate(taps)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        card = self.school.student_group_submit_state(published, self.student_ids[0])
        assert card is not None
        order = card["order"]
        self.assertEqual(order, parse_rank_order(order, ["o1", "o2", "o3"], complete=False))
        self.assertEqual(len(order), len(set(order)))
        self.assertTrue(set(order) <= {"o1", "o2", "o3"})
        mate = self.school.student_group_submit_state(published, self.student_ids[2])
        assert mate is not None
        self.assertEqual(mate["order"], order)

    def test_late_joiner_sees_the_shared_card(self) -> None:
        """A teammate who joins after publish lands on the current order."""

        self.school.game.begin_game(self.class_id)
        self.school.join_live_class_session(
            self.session_id, self.student_ids[0], codename="Aspen"
        )
        self._teams(2)
        published = self._publish_rank("group_submit")
        self.school.save_group_mc_draft(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="",
            why="",
            tap="o2",
        )
        self.school.join_live_class_session(
            self.session_id, self.student_ids[2], codename="Cedar"
        )
        late = self.school.student_group_submit_state(published, self.student_ids[2])
        assert late is not None
        self.assertEqual(late["order"], ["o2"])
        self.assertEqual(late["team_id"], self.school.student_group_submit_state(published, self.student_ids[0])["team_id"])
        self.assertEqual(late["phase"], "drafting")

    def test_one_team_is_one_borda_vote(self) -> None:
        """One remaining named team is a single Borda vote, not a crash.

        Setup still requires two teams. This drops the second team after
        lock so the rank strip sees a session with only one team configured.
        """

        self._join(4)
        self._teams(2)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                """
                SELECT id FROM game_teams
                WHERE name != 'Class'
                ORDER BY sort_order ASC, id ASC
                """
            ).fetchall()
            keep = int(rows[0]["id"])
            drop = int(rows[1]["id"])
            self.school.game.conn.execute(
                "UPDATE game_memberships SET team_id = ? WHERE team_id = ?",
                (keep, drop),
            )
            self.school.game.conn.execute(
                "DELETE FROM team_buckets WHERE team_id = ?",
                (drop,),
            )
            self.school.game.conn.execute(
                "DELETE FROM game_teams WHERE id = ?",
                (drop,),
            )
            self.school.game.conn.commit()
        published = self._publish_rank("group_submit")
        self._submit_order(published, self.student_ids[0], ["o3", "o1", "o2"])
        view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        rank = view["rank"]
        self.assertEqual(len(rank["teams"]), 1)
        self.assertEqual(rank["present"], 1)
        self.assertEqual(rank["responded"], 1)
        self.assertEqual(rank["teams"][0]["order"], ["o3", "o1", "o2"])
        self.assertEqual(rank["class_order"][0]["option_id"], "o3")
        self.assertEqual(rank["class_order"][0]["rank"], 1)

    def test_individual_rank_is_a_participation_round_not_game_points(self) -> None:
        """An individual rank counts like MC. Borda does not award points."""

        self._join(1)
        published = self._publish_rank("individual")
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.assertEqual(prompt["kind"], "rank")
        with self.assertRaisesRegex(ValueError, "rank every option"):
            self.school.submit_live_prompt_response(
                int(prompt["id"]),
                self.student_ids[0],
                {"order": ["o1", "o2"]},
            )
        self.school.submit_live_prompt_response(
            int(prompt["id"]),
            self.student_ids[0],
            {"order": ["o1", "o3", "o2"]},
        )
        credits = self.school.participation_question_credits_for_class(self.class_id)
        self.assertEqual(credits.get(self.student_ids[0]), 1)
        state = self.school.get_live_session_state(self.session_id)
        self.assertEqual(int((state.get("game_points") or {}).get(str(self.student_ids[0]), 0)), 0)
        self.assertIsNone(state.get("mc_tally"))
        roster = self.school.live_prompt_response_roster(
            self.session_id, int(prompt["id"])
        )
        self.assertEqual(roster[0]["correct"], None)
        self.assertIn("1 Chewing loudly", roster[0]["answer"])
        results = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        self.assertEqual(results["tally"]["kind"], "rank")
        self.assertNotIn("teams", results["tally"])
        self.assertEqual(results["tally"]["class_order"][0]["option_id"], "o1")
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_session_responses (
                    prompt_id, student_id, participant_uuid, response_json,
                    created_at, updated_at
                ) VALUES (?, NULL, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    int(prompt["id"]),
                    "guest-junk",
                    json.dumps({"order": ["nope", "nope", "nope"]}),
                ),
            )
            self.school.conn.commit()
        again = self.school.get_live_session_state(self.session_id)
        self.assertIsNone(again.get("mc_tally"))
        tally = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )["tally"]
        self.assertEqual(tally["responded"], 1)

    def test_closed_rank_hides_class_order_until_show_live_results(self) -> None:
        """Students see their own order when Show Live Results is off."""

        self._join(4)
        self._teams(2)
        published = self._publish_rank("group_submit")
        self.school.update_live_session_item_settings(
            self.session_id, int(published["id"]), show_live_results=False
        )
        self._submit_order(published, self.student_ids[0], ["o1", "o2", "o3"])
        self.school.close_live_session_item(self.session_id, int(published["id"]))
        hidden = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        card = next(row for row in hidden["closed_results"] if row["item_id"] == "q-rank")
        self.assertIsNone(card["results"])
        self.assertEqual(card["group_submit"]["submitted_order"], ["o1", "o2", "o3"])
        self.school.update_live_session_item_settings(
            self.session_id, int(published["id"]), show_live_results=True
        )
        shown = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        visible = next(row for row in shown["closed_results"] if row["item_id"] == "q-rank")
        self.assertEqual(visible["results"]["kind"], "rank")
        self.assertTrue(visible["results"]["rank"]["class_order"])
        self.assertTrue(visible["results"]["reveal"])

    def test_corrupt_group_order_does_not_fail_the_teacher_view(self) -> None:
        """A stored non-permutation is skipped instead of raising."""

        self._join(2)
        self._teams(2)
        published = self._publish_rank("group_submit")
        self._submit_order(published, self.student_ids[0], ["o1", "o2", "o3"])
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_group_responses
                SET final_answer_json = ?
                WHERE live_item_id = ?
                """,
                (json.dumps({"kind": "rank", "order": ["o1", "o1"]}), int(published["id"])),
            )
            self.school.conn.commit()
        view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        self.assertEqual(view["rank"]["responded"], 0)
        self.assertTrue(all(row["order"] is None for row in view["rank"]["teams"]))
        state = self.school.get_live_session_state(self.session_id)
        self.assertIn("attendees", state)

    def test_teacher_strip_puts_teams_before_class_order(self) -> None:
        """The Questions card leads with team rows. Individual omits them."""

        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        collate = staff.split("function rankCollateHtml")[1].split(
            "function groupSubmitTeacherHtml"
        )[0]
        self.assertLess(collate.index("rank-team-rows"), collate.index("rank-class-order"))
        self.assertIn('unit === "team"', collate)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("data-rank-shared", student)
        self.assertIn("tap: id", student)
        self.assertIn("Change team order", student)
        self.assertIn("Responses &amp; points", staff)
        adopt = staff.split("function adoptLifecycleResponseCounts(")[1].split(
            "let openResponsePromptId"
        )[0]
        self.assertIn("lifecycleRowIsRank", adopt)
        self.assertIn("group_consensus", adopt)
        self.assertIn("refreshLifecycleResults", adopt)
        self.assertIn("lifecycle_rank_revs", staff)


if __name__ == "__main__":
    unittest.main()
