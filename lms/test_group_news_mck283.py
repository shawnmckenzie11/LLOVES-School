"""MCK-283: a group postcard reaches that team and the teacher.

Other teams stay on the safety poll. A student whose team is not known
yet still receives the postcard. The group part of the student poll
stamp follows the caller's team when membership is known.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from live_news_wire import (
    NewsAudience,
    _maybe_sync_group_teams,
    event_visible,
)
from test_rank_challenge_mck171 import NAMES
from test_rank_turns_mck184 import TurnsHarness

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent

# Teacher moves every group at once, so these two stay class-wide.
_CLASS_WIDE_GROUP_EMITS = frozenset({"api_team_draw_clear", "api_end_group_voting"})


def _load_harness():
    """Import scripts/load_class.py without running it."""
    path = REPO / "scripts" / "load_class.py"
    spec = importlib.util.spec_from_file_location("load_class_mck283", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def group_scope_emits(src: str) -> dict[str, list[bool]]:
    """Map each ``scope="group"`` emit to whether that call passes ``team_id``.

    Args:
        src: Text of ``lms/app.py``.

    Returns:
        Function name to one flag per group emit in that function.
    """
    found: dict[str, list[bool]] = {}
    parts = src.split("\n    def ")
    for chunk in parts[1:]:
        name = chunk.split("(", 1)[0].strip()
        start = 0
        while True:
            at = chunk.find('scope="group"', start)
            if at < 0:
                break
            emit_at = chunk.rfind("emit_answer_landed(", 0, at)
            window = chunk[emit_at:at] if emit_at >= 0 else ""
            found.setdefault(name, []).append("team_id=" in window)
            start = at + len('scope="group"')
    return found


class GroupPostcardVisibilityTests(unittest.TestCase):
    """Staff, the writing team, other teams, and an unknown team."""

    def _check(self, event: dict) -> None:
        """One group postcard: staff and team 7 yes, team 8 no, unknown yes."""
        staff = NewsAudience("staff")
        teammate = NewsAudience("student", student_id=1, team_ids=frozenset({7}))
        other = NewsAudience("student", student_id=2, team_ids=frozenset({8}))
        unknown = NewsAudience("student", student_id=3)
        self.assertTrue(event_visible(event, staff))
        self.assertTrue(event_visible(event, teammate))
        self.assertFalse(event_visible(event, other))
        self.assertTrue(event_visible(event, unknown))
        wide = dict(event)
        wide.pop("team_id", None)
        self.assertTrue(event_visible(wide, other))

    def test_group_submit_reaches_only_that_team(self) -> None:
        """A group multiple-choice or vote postcard is team-scoped."""
        self._check(
            {
                "type": "response_landed",
                "scope": "group",
                "team_id": 7,
                "count": 1,
                "state_seq": 3,
            }
        )

    def test_rank_turn_reaches_only_that_team(self) -> None:
        """A take-turns placement postcard is team-scoped."""
        self._check(
            {
                "type": "response_landed",
                "scope": "group",
                "team_id": 7,
                "state_seq": 4,
            }
        )

    def test_lazy_team_lookup_caches_a_hit_only(self) -> None:
        """Membership filled after the stream opened is cached; a miss is not."""

        class Hit:
            def student_open_group_team_ids(self, session_id: int, student_id: int) -> frozenset[int]:
                """Return the open-group team for this student."""
                del session_id, student_id
                return frozenset({7})

        class Miss:
            def student_open_group_team_ids(self, session_id: int, student_id: int) -> frozenset[int]:
                """No membership yet."""
                del session_id, student_id
                return frozenset()

        event = {"type": "response_landed", "scope": "group", "team_id": 7}
        waiting = NewsAudience("student", student_id=4)
        _maybe_sync_group_teams(Miss(), 1, waiting, event)
        self.assertEqual(waiting.team_ids, frozenset())
        self.assertTrue(event_visible(event, waiting))
        _maybe_sync_group_teams(Hit(), 1, waiting, event)
        self.assertEqual(waiting.team_ids, frozenset({7}))
        self.assertTrue(event_visible(event, waiting))
        other = {"type": "response_landed", "scope": "group", "team_id": 8}
        self.assertFalse(event_visible(other, waiting))


class GroupEmitTeamIdTests(unittest.TestCase):
    """Every group emit names a team, except the two class-wide teacher moves."""

    def test_scope_group_emits_pass_team_id(self) -> None:
        found = group_scope_emits((LMS_DIR / "app.py").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(found), 10)
        for name, flags in found.items():
            if name in _CLASS_WIDE_GROUP_EMITS:
                self.assertTrue(flags)
                self.assertFalse(any(flags), name)
            else:
                self.assertTrue(all(flags), name)
        self.assertTrue(_CLASS_WIDE_GROUP_EMITS <= set(found))


class PhoneScriptTests(unittest.TestCase):
    """The node checks CI would otherwise skip."""

    def test_phone_save_and_heartbeat_scripts(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        static = LMS_DIR / "static"
        for name in ("phone_save.test.mjs", "student-live-session.test.mjs"):
            done = subprocess.run(
                [node, str(static / name)],
                cwd=str(static),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class LoadHarnessMathTests(unittest.TestCase):
    """CPU quota text and builds-per-tap, without starting gunicorn."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_harness()

    def test_cpu_max_matches_the_shared_baseline(self) -> None:
        self.assertEqual(self.mod.cpu_max_spec(6.25), "5000 80000")
        self.assertEqual(self.mod.cpu_max_spec(25), "20000 80000")

    def test_builds_per_tap(self) -> None:
        self.assertIsNone(self.mod.builds_per_tap(10, 0))
        self.assertEqual(self.mod.builds_per_tap(30, 1), 30)
        self.assertEqual(self.mod.builds_per_tap(5, 1), 5)


class TeamScopedStampTests(TurnsHarness):
    """Another team's write does not rebuild this phone. The same team does."""

    def _snapshot(self, name: str) -> tuple[object, object, str]:
        """Seq, stamp, and the direct per-student stamp token."""
        body = self._poll(name)
        direct = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[name]
        )
        return body.get("state_seq"), body.get("stamp"), direct

    def test_rank_turn_and_group_pick_leave_the_other_team_unchanged(self) -> None:
        """Take turns and a group multiple-choice pick stay on that team."""
        placer, teammate, outsider = NAMES[0], NAMES[2], NAMES[1]
        item = self._turns_item()
        teams = self.school.student_open_group_team_ids(self.session_id, self.ids[placer])
        self.assertEqual(
            teams,
            self.school.student_open_group_team_ids(self.session_id, self.ids[teammate]),
        )
        self.assertNotEqual(
            teams,
            self.school.student_open_group_team_ids(self.session_id, self.ids[outsider]),
        )
        wide_before = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        unknown_before = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, 9_999_999
        )
        self.assertEqual(wide_before, unknown_before)
        mate_seq, mate_stamp, mate_direct = self._snapshot(teammate)
        out_seq, out_stamp, out_direct = self._snapshot(outsider)
        rv = self._post(placer, item, "rank-turn", {"option_id": "o1"})
        self.assertEqual(rv.status_code, 200)
        self.assertIn("team_id", (rv.get_json().get("group_submit") or {}))
        mate_after = self._poll(teammate, mate_seq, mate_stamp)
        out_after = self._poll(outsider, out_seq, out_stamp)
        self.assertFalse(mate_after.get("unchanged"))
        self.assertTrue(out_after.get("unchanged"))
        self.assertNotEqual(
            mate_direct,
            self.school.live_student_poll_stamp(
                self.session_id, self.class_id, self.ids[teammate]
            ),
        )
        self.assertEqual(
            out_direct,
            self.school.live_student_poll_stamp(
                self.session_id, self.class_id, self.ids[outsider]
            ),
        )
        wide_after = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertNotEqual(wide_before, wide_after)
        self.assertEqual(
            wide_after,
            self.school.live_student_poll_stamp(self.session_id, self.class_id, 9_999_999),
        )

        self._close(item)
        mc = self._publish(
            self._add(
                {
                    "type": "mc",
                    "text": "Which representation?",
                    "options": ["Graph", "Table", "Equation", "Points"],
                    "correct_index": 0,
                }
            )
        )
        time.sleep(1.1)
        mate_rev = self.school._team_group_response_rev(self.session_id, self.ids[teammate])
        out_rev = self.school._team_group_response_rev(self.session_id, self.ids[outsider])
        mate_seq, mate_stamp, mate_direct = self._snapshot(teammate)
        rv = self._post(outsider, mc, "group-pick", {"choice": "Graph"})
        self.assertEqual(rv.status_code, 200)
        # The team row moves only for the picker's team. Vote rows stay
        # class-wide, so the other phone's full stamp still changes.
        self.assertEqual(
            mate_rev,
            self.school._team_group_response_rev(self.session_id, self.ids[teammate]),
        )
        self.assertNotEqual(
            out_rev,
            self.school._team_group_response_rev(self.session_id, self.ids[outsider]),
        )
        self.assertNotEqual(
            mate_direct,
            self.school.live_student_poll_stamp(
                self.session_id, self.class_id, self.ids[teammate]
            ),
        )
        self.assertFalse(self._poll(teammate, mate_seq, mate_stamp).get("unchanged"))


if __name__ == "__main__":
    unittest.main()
