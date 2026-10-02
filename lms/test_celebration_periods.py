#!/usr/bin/env python3
"""MCK-133: Celebrations "Start fresh" award periods and timeframe labels."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import celebration_periods as periods  # noqa: E402
from celebration import (  # noqa: E402
    CELEBRATIONS_FROZEN_ENV,
    SETTING_PUBLIC_SNAPSHOT,
    WONDER_COPY,
    celebrated_students,
    clear_public_celebration_memo,
)
import test_celebration  # noqa: E402

# Reuse the Celebrations fixture and helpers without re-running its tests
# (a module import, so unittest does not collect CelebrationTests twice).
_HELPERS = (
    "setUp",
    "tearDown",
    "_populate",
    "_log_day",
    "_ids",
    "_three_sections",
    "_feature",
)


class PeriodHelperTests(unittest.TestCase):
    """Pure helpers: labels, period membership, school dates."""

    def test_labels(self) -> None:
        """Same-year range, cross-year range, open-ended, unknown start."""
        label = periods.period_label
        self.assertEqual(
            label(date(2026, 9, 8), date(2026, 10, 1), WONDER_COPY), "Sep 8 – Oct 1, 2026"
        )
        self.assertEqual(
            label(date(2026, 12, 1), date(2027, 1, 15), WONDER_COPY),
            "Dec 1, 2026 – Jan 15, 2027",
        )
        self.assertEqual(label(date(2026, 10, 2), None, WONDER_COPY), "Since Oct 2, 2026")
        self.assertEqual(label(None, date(2026, 10, 1), WONDER_COPY), "Through Oct 1, 2026")
        self.assertEqual(label(None, None, WONDER_COPY), "This semester")
        # An end before the start (clock skew) collapses to one day.
        self.assertEqual(
            label(date(2026, 10, 2), date(2026, 10, 1), WONDER_COPY), "Oct 2 – Oct 2, 2026"
        )

    def test_session_in_period(self) -> None:
        """No start counts all; afterwards only sessions at/after the start."""
        inside = periods.session_in_period
        self.assertTrue(inside("2026-09-09T14:00:00", None))
        self.assertTrue(inside("2026-09-09T14:00:00", ""))
        self.assertFalse(inside("2026-09-09T14:00:00", "2026-10-02T14:30:00"))
        self.assertFalse(inside("2026-10-02T14:00:00", "2026-10-02T14:30:00"))
        self.assertTrue(inside("2026-10-02T14:30:00", "2026-10-02T14:30:00"))
        self.assertTrue(inside("2026-10-05T09:00:00", "2026-10-02T14:30:00"))
        self.assertFalse(inside("", "2026-10-02T14:30:00"))
        self.assertFalse(inside("garbage", "2026-10-02T14:30:00"))

    def test_school_date_converts_utc(self) -> None:
        """UTC timestamps become Toronto dates; naive ones are wall time."""
        self.assertEqual(periods.school_date("2026-10-02T02:00:00+00:00"), date(2026, 10, 1))
        self.assertEqual(periods.school_date("2026-10-01T20:30:00+00:00"), date(2026, 10, 1))
        self.assertEqual(periods.school_date("2026-10-02T02:00:00"), date(2026, 10, 2))
        self.assertEqual(periods.school_date("2026-09-08"), date(2026, 9, 8))
        self.assertIsNone(periods.school_date(""))
        self.assertIsNone(periods.school_date("nope"))


class StartFreshTests(unittest.TestCase):
    """Staff Start fresh, the award tally, and the public timeframe."""

    for _name in _HELPERS:
        locals()[_name] = getattr(test_celebration.CelebrationTests, _name)
    del _name

    def _at(self, stamp: str) -> None:
        """Pin MCK-133 "now" (school wall time); call again to move it."""
        clock = getattr(self, "_clock", None)
        if clock is None:
            clock = patch.object(periods, "school_now")
            self._clock = clock.start()
            self.addCleanup(clock.stop)
        self._clock.return_value = datetime.fromisoformat(stamp)

    _now = _at

    def _other_teacher_class(self) -> int:
        """A class owned by a second teacher (MCR3U section), with Oak."""
        other = self.school.register_staff("other@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="MCR3U"
        )
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=other@gmail.com&name=O")
        client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("other@gmail.com")["verification_code"]},
        )
        created = client.post(
            "/api/staff/classes",
            json={"offering_id": offering["id"], "days": "T/Th/F", "time": "2:00pm",
                  "codenames": ["Elm"]},
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        return int(created.get_json()["class"]["id"])

    def _board(self) -> dict[str, dict]:
        """Public Most Engaged cards by course, memo cleared."""
        clear_public_celebration_memo()
        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        return {c["course"]: c for c in cards if c["key"] == "engaged"}

    def _fresh(self, **body):
        """POST Start fresh as the signed-in teacher."""
        return self.client.post("/api/staff/celebrations/start-fresh", json=body)

    def _two_courses(self) -> dict[str, tuple[int, dict[str, int]]]:
        """MCF3M (Maple leads) and MCR3U (Oak leads), one day on Sep 9."""
        offerings = self._three_sections()
        out = {}
        for course, names in (("MCF3M", ["Maple", "Birch"]), ("MCR3U", ["Oak", "Pine"])):
            class_id = self._populate(names, offerings[course])
            ids = self._ids(class_id)
            self._log_day(class_id, "2026-09-09", [ids[names[0]]], {ids[names[0]]: 5})
            out[course] = (class_id, ids)
        return out

    def _session_rows(self, class_id: int) -> list[tuple]:
        """Every saved attendance/points row for one class."""
        with self.school.game._lock:
            return [
                tuple(row)
                for row in self.school.game.conn.execute(
                    """
                    SELECT ss.session_id, ss.student_id, ss.present, ss.points, se.status
                    FROM session_scores ss JOIN sessions se ON se.id = ss.session_id
                    WHERE se.class_id = ? ORDER BY ss.session_id, ss.student_id
                    """,
                    (class_id,),
                )
            ]

    def test_one_class_restarts_only_that_class(self) -> None:
        """Live board: the reset class counts from now; the other is unchanged.

        Saved attendance and points stay in the database and the class
        dashboard, untouched.
        """
        os.environ[CELEBRATIONS_FROZEN_ENV] = "0"
        rows = self._two_courses()
        mcf, ids = rows["MCF3M"]
        board = self._board()
        self.assertEqual(board["MCF3M"]["names"], ["Maple"])
        # First period, no snapshot: semester day 1 to FIRST_AWARD_PERIOD_END.
        self.assertEqual(board["MCF3M"]["period_label"], "Sep 8 – Oct 1, 2026")
        self.assertEqual(board["MCF3M"]["period_start"], "2026-09-08")
        self.assertEqual(board["MCF3M"]["period_end"], "2026-10-01")
        saved = self._session_rows(mcf)
        dashboard = self.school.game.dashboard(mcf, sort="az")

        self._at("2026-09-15T12:00:00")
        rv = self._fresh(scope="class", class_id=mcf)
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json()
        self.assertEqual([p["class_id"] for p in body["periods"]], [mcf])
        self.assertEqual(body["periods"][0]["starts_at"], "2026-09-15T12:00:00")

        board = self._board()
        self.assertNotIn("MCF3M", board)  # nobody yet in the new period
        self.assertEqual(board["MCR3U"]["names"], ["Oak"])
        self.assertEqual(board["MCR3U"]["period_label"], "Sep 8 – Oct 1, 2026")
        self.assertEqual(self._session_rows(mcf), saved)
        self.assertEqual(self.school.game.dashboard(mcf, sort="az"), dashboard)

        self._log_day(mcf, "2026-09-16", [ids["Birch"]], {ids["Birch"]: 1})
        board = self._board()
        self.assertEqual(board["MCF3M"]["names"], ["Birch"])
        self.assertEqual(board["MCF3M"]["detail"], "1 class present · 1 pts")
        # A later period with no snapshot is still open.
        self.assertEqual(board["MCF3M"]["period_label"], "Since Sep 15, 2026")
        self.assertIsNone(board["MCF3M"]["period_end"])
        self.assertEqual(board["MCF3M"]["period_start"], "2026-09-15")
        engaged = [r for r in celebrated_students(self.school) if r["card"] == "engaged"]
        by_course = {r["course"]: r for r in engaged}
        self.assertEqual(by_course["MCF3M"]["period"], body["periods"][0]["id"])
        self.assertEqual(by_course["MCR3U"]["period"], 0)

    def test_all_scope_restarts_every_class_of_this_teacher(self) -> None:
        """``scope=all`` opens a period for each of the teacher's classes only."""
        os.environ[CELEBRATIONS_FROZEN_ENV] = "0"
        rows = self._two_courses()
        foreign = self._other_teacher_class()
        self._at("2026-09-15T12:00:00")
        rv = self._fresh(scope="all")
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertEqual(self._fresh(scope="class", class_id=foreign).status_code, 403)
        mine = sorted(cid for cid, _ids in rows.values())
        self.assertEqual(sorted(p["class_id"] for p in rv.get_json()["periods"]), mine)
        current = periods.current_periods(
            self.school, int(self.school.get_active_semester()["id"])
        )
        self.assertEqual(sorted(current), mine)
        self.assertNotIn(foreign, current)
        self.assertEqual(self._board(), {})
        listed = self.client.get("/api/staff/celebrations/periods").get_json()["classes"]
        self.assertEqual(
            {c["class_id"]: c["since_label"] for c in listed},
            {cid: "Since Sep 15, 2026" for cid in mine},
        )

    def test_frozen_board_keeps_old_card_until_new_winner(self) -> None:
        """Frozen: the reset course keeps its first-period card, then swaps.

        First period label runs from semester day 1 to the day the card was
        frozen. The other course stays frozen as it was.
        """
        self._at("2026-09-20T16:00:00")
        rows = self._two_courses()
        mcf, ids = rows["MCF3M"]
        board = self._board()
        self.assertEqual(board["MCF3M"]["names"], ["Maple"])
        self.assertEqual(board["MCF3M"]["period_label"], "Sep 8 – Sep 20, 2026")
        self.assertEqual(board["MCR3U"]["period_label"], "Sep 8 – Sep 20, 2026")
        mcr_before = next(
            c for c in json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))["cards"]
            if c["course"] == "MCR3U"
        )

        self._now("2026-09-21T09:00:00")
        rv = self._fresh(scope="class", class_id=mcf)
        self.assertEqual(rv.status_code, 200, rv.get_json())
        period_id = rv.get_json()["periods"][0]["id"]
        stored = {
            c["course"]: c
            for c in json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))["cards"]
        }
        self.assertTrue(stored["MCF3M"]["retired"])
        self.assertNotIn("retired", stored["MCR3U"])
        # Old card stays on show with its old timeframe.
        board = self._board()
        self.assertEqual(board["MCF3M"]["names"], ["Maple"])
        self.assertEqual(board["MCF3M"]["period_label"], "Sep 8 – Sep 20, 2026")

        self._now("2026-09-23T15:00:00")
        self._log_day(mcf, "2026-09-23", [ids["Birch"]], {ids["Birch"]: 2})
        board = self._board()
        self.assertEqual(board["MCF3M"]["names"], ["Birch"])
        self.assertEqual(board["MCF3M"]["period_label"], "Sep 21 – Sep 23, 2026")
        self.assertEqual(board["MCR3U"]["names"], ["Oak"])
        self.assertEqual(board["MCR3U"]["period_label"], "Sep 8 – Sep 20, 2026")
        stored = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))["cards"]
        self.assertEqual([c["course"] for c in stored], ["MCR3U", "MCF3M"])
        self.assertEqual(next(c for c in stored if c["course"] == "MCR3U"), mcr_before)
        new = next(c for c in stored if c["course"] == "MCF3M")
        self.assertEqual(
            (new["period_id"], new["period_start"], new["through"]),
            (period_id, "2026-09-21", "2026-09-23"),
        )
        self.assertNotIn("retired", new)
        engaged = {
            r["course"]: r for r in celebrated_students(self.school) if r["card"] == "engaged"
        }
        self.assertEqual(engaged["MCF3M"]["period"], period_id)
        self.assertEqual(engaged["MCF3M"]["student_id"], ids["Birch"])
        self.assertEqual(engaged["MCR3U"]["period"], 0)
        # Later attendance does not move the frozen new card.
        self._log_day(mcf, "2026-09-25", [ids["Maple"]], {ids["Maple"]: 9})
        self._log_day(mcf, "2026-09-28", [ids["Maple"]], {ids["Maple"]: 9})
        self.assertEqual(self._board()["MCF3M"]["names"], ["Birch"])

    def test_legacy_snapshot_label_uses_taken_at_in_toronto(self) -> None:
        """A pre-MCK-133 snapshot shows semester day 1 to its Toronto date."""
        self._two_courses()
        self._board()
        raw = self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT)
        snap = json.loads(raw)
        for card in snap["cards"]:
            for key in ("period_id", "period_start", "through"):
                card.pop(key)
        snap["taken_at"] = "2026-10-02T02:00:00+00:00"  # Oct 1, 22:00 ET
        self.school.set_school_setting(SETTING_PUBLIC_SNAPSHOT, json.dumps(snap))
        board = self._board()
        for course in ("MCF3M", "MCR3U"):
            self.assertEqual(board[course]["period_label"], "Sep 8 – Oct 1, 2026")
            self.assertEqual(board[course]["period_start"], "2026-09-08")
            self.assertEqual(board[course]["period_end"], "2026-10-01")
        rows = [r for r in celebrated_students(self.school) if r["card"] == "engaged"]
        self.assertEqual({r["period"] for r in rows}, {0})

    def test_public_card_keys(self) -> None:
        """Only Most Engaged gains the three timeframe keys; no ids leak."""
        rows = self._two_courses()
        mcf, ids = rows["MCF3M"]
        self._feature(mcf, ids["Birch"], "Great question.")
        clear_public_celebration_memo()
        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        base = {"key", "name", "names", "course", "detail", "title", "kicker"}
        for card in cards:
            extra = {"period_start", "period_end", "period_label"} if card["key"] == "engaged" else set()
            self.assertEqual(set(card), base | extra, card["key"])
        dumped = json.dumps(cards)
        self.assertNotIn("class_id", dumped)
        self.assertNotIn("period_id", dumped)

    def test_shoutout_is_not_reset(self) -> None:
        """Start fresh leaves the teacher's Shoutout as it is."""
        rows = self._two_courses()
        mcf, ids = rows["MCF3M"]
        self._feature(mcf, ids["Birch"], "Great question.")
        self._at("2026-09-15T12:00:00")
        self.assertEqual(self._fresh(scope="all").status_code, 200)
        clear_public_celebration_memo()
        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        award = [c for c in cards if c["key"] == "award"]
        self.assertEqual([c["name"] for c in award], ["Birch"])

    def test_bad_requests(self) -> None:
        """400 bad scope / class id, 403 someone else's class, 409 live class."""
        rows = self._two_courses()
        mcf, _ids = rows["MCF3M"]
        self.assertEqual(self._fresh().status_code, 400)
        self.assertEqual(self._fresh(scope="everything").status_code, 400)
        self.assertEqual(self._fresh(scope="class").status_code, 400)
        self.assertEqual(self._fresh(scope="class", class_id="x").status_code, 400)
        self.assertEqual(self._fresh(scope="class", class_id=999999).status_code, 403)
        started = self.client.post(f"/api/classes/{mcf}/live-session/start", json={})
        self.assertEqual(started.status_code, 200, started.get_json())
        busy = self._fresh(scope="class", class_id=mcf)
        self.assertEqual(busy.status_code, 409, busy.get_json())
        self.assertEqual(self._fresh(scope="all").status_code, 409)
        self.assertEqual(
            periods.current_periods(self.school, int(self.school.get_active_semester()["id"])),
            {},
        )
        anon = self.app.test_client()
        rv = anon.post("/api/staff/celebrations/start-fresh", json={"scope": "all"})
        self.assertNotEqual(rv.status_code, 200)

    def test_staff_home_shows_start_fresh(self) -> None:
        """The staff home has the plain Start fresh control per class."""
        rows = self._two_courses()
        page = self.client.get("/staff").get_data(as_text=True)
        self.assertIn('id="award-start-fresh"', page)
        self.assertIn('id="award-fresh-go"', page)
        for class_id, _ids in rows.values():
            self.assertIn(f'<option value="{class_id}">', page)
        self.assertIn("award tally Since Sep 8, 2026", page)

    def test_deleting_teacher_removes_their_periods(self) -> None:
        """Permanent staff delete clears that teacher's period rows."""
        rows = self._two_courses()
        self._at("2026-09-15T12:00:00")
        self.assertEqual(self._fresh(scope="all").status_code, 200)
        semester_id = int(self.school.get_active_semester()["id"])
        self.assertEqual(len(periods.current_periods(self.school, semester_id)), 2)
        self.school.delete_staff_permanently(int(self.teacher["id"]), 999999)
        self.assertEqual(periods.current_periods(self.school, semester_id), {})
        del rows

    # --- First-period end date (prod has no stored snapshot) -------------

    def _assert_first_period(self, board: dict[str, dict], courses=("MCF3M", "MCR3U")) -> None:
        for course in courses:
            self.assertEqual(board[course]["period_label"], "Sep 8 – Oct 1, 2026", course)
            self.assertEqual(board[course]["period_start"], "2026-09-08")
            self.assertEqual(board[course]["period_end"], "2026-10-01")

    def test_no_snapshot_freeze_off_uses_defined_end(self) -> None:
        """Freeze off, no snapshot row (prod v209): Sep 8 – Oct 1, 2026."""
        os.environ[CELEBRATIONS_FROZEN_ENV] = "0"
        self._at("2026-10-02T15:00:00")
        self._two_courses()
        self.assertIsNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))
        self._assert_first_period(self._board())
        self.assertIsNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))

    def test_no_snapshot_freeze_on_uses_defined_end(self) -> None:
        """Freeze on, nothing stored (a live class runs): Sep 8 – Oct 1, 2026."""
        self._at("2026-10-02T15:00:00")
        self._two_courses()
        busy = self._populate(["Solo"])
        started = self.client.post(f"/api/classes/{busy}/live-session/start", json={})
        self.assertEqual(started.status_code, 200, started.get_json())
        self._assert_first_period(self._board())
        self.assertIsNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))

    def test_first_snapshot_after_oct_1_keeps_defined_end(self) -> None:
        """Freeze on, first snapshot taken after deploy (Oct 3): still Oct 1.

        Prod has no snapshot row, so the first read after deploy stores
        one. The first period's end is never later than Oct 1, 2026.
        """
        self._at("2026-10-03T09:00:00")
        self._two_courses()
        board = self._board()
        stored = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))
        self.assertEqual({c["through"] for c in stored["cards"]}, {"2026-10-03"})
        self._assert_first_period(board)

    def test_snapshot_before_defined_end_uses_snapshot_date(self) -> None:
        """Snapshot present and taken Sep 20: Sep 8 – Sep 20, 2026."""
        self._at("2026-09-20T16:00:00")
        self._two_courses()
        board = self._board()
        self.assertIsNotNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))
        for course in ("MCF3M", "MCR3U"):
            self.assertEqual(board[course]["period_label"], "Sep 8 – Sep 20, 2026")
            self.assertEqual(board[course]["period_end"], "2026-09-20")

    def test_defined_end_setting_override_and_garbage(self) -> None:
        """The setting overrides the constant; junk falls back; never blank."""
        os.environ[CELEBRATIONS_FROZEN_ENV] = "0"
        self._two_courses()
        self.school.set_school_setting(periods.SETTING_FIRST_AWARD_PERIOD_END, "2026-09-30")
        board = self._board()
        self.assertEqual(board["MCF3M"]["period_label"], "Sep 8 – Sep 30, 2026")
        self.school.set_school_setting(periods.SETTING_FIRST_AWARD_PERIOD_END, "not a date")
        self._assert_first_period(self._board())
        # An end before semester day 1 (a later semester) is ignored: open.
        self.school.set_school_setting(periods.SETTING_FIRST_AWARD_PERIOD_END, "2026-01-31")
        board = self._board()
        self.assertEqual(board["MCF3M"]["period_label"], "Since Sep 8, 2026")
        self.assertIsNone(board["MCF3M"]["period_end"])

    def test_label_never_blank_or_crashes(self) -> None:
        """No semester day 1, or a failing lookup, still gives a label."""
        os.environ[CELEBRATIONS_FROZEN_ENV] = "0"
        self._two_courses()
        semester = dict(self.school.get_active_semester())
        semester["instructional_first"] = None
        with patch.object(self.school, "get_active_semester", return_value=semester):
            board = self._board()
        self.assertEqual(board["MCF3M"]["period_label"], "Through Oct 1, 2026")
        self.assertIsNone(board["MCF3M"]["period_start"])
        with patch.object(periods, "semester_first_day", side_effect=RuntimeError("boom")):
            board = self._board()
        self.assertEqual(board["MCF3M"]["period_label"], "This semester")
        self.assertEqual(board["MCF3M"]["names"], ["Maple"])


if __name__ == "__main__":
    unittest.main()
