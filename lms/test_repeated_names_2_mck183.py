#!/usr/bin/env python3
"""MCK-183 #260 gate follow-up: hyphenated, two-word and glued repeated names.

- "Mary-Jo" with "Mary-Jo 2" on the roster gets the picker (it used to
  take the seat, then block the real Mary-Jo with "already in class").
- "Jean Luc" and "Jean Luc 2" show as "Jean" and "Jean Luc 2" (kept whole, #269 gate).
- "Sam2" is Sam 2.
- A seat reclaimed after Leave by another device gets a fresh visit token.
"""

from __future__ import annotations

import unittest
from typing import Any

import test_auth
from school_db import first_name_only

ROSTER = ["Mary-Jo", "Mary-Jo 2", "Jean Luc", "Jean Luc 2", "Sam", "Sam 2", "R2D2", "Ana"]


class NumberRuleTests(unittest.TestCase):
    """``first_name_only`` keeps the repeat number in every shape."""

    def test_shapes(self) -> None:
        cases = {
            "Jean Luc 2": "Jean Luc 2",
            "Jean  Luc   2": "Jean Luc 2",
            "Jean Luc": "Jean",
            "Mary-Jo 2": "Mary-Jo 2",
            "Mary-Jo2": "Mary-Jo 2",
            "Sam2": "Sam 2",
            " sam12 ": "sam 12",
            "Sam 2 Smith": "Sam 2",
            "Sam Smith": "Sam",
            "Sam 123": "Sam",
            "Sam123": "Sam123",
            "R2D2": "R2D2",
            "Agent47": "Agent 47",
        }
        for raw, want in cases.items():
            self.assertEqual(first_name_only(raw), want, raw)


class RepeatedNameShapesJoinTests(test_auth.AuthTests):
    """Join by code over HTTP. Only the tests below run here."""

    def setUp(self) -> None:
        super().setUp()
        self.live = self._boot_live_class(ROSTER)

    def _join(self, typed: str, student_id: int | None = None, client: Any = None) -> Any:
        data: dict[str, Any] = {"code": self.live["session_code"], "name": typed}
        if student_id is not None:
            data["student_id"] = student_id
        return (client or self.app.test_client()).post("/auth/student-code", json=data)

    def _labels(self, typed: str) -> list[str]:
        rv = self._join(typed)
        self.assertEqual(rv.status_code, 409, (typed, rv.get_data(as_text=True)[:300]))
        return sorted(c["label"] for c in rv.get_json()["candidates"])

    def _pick(self, typed: str, label: str) -> Any:
        rv = self._join(typed)
        cand = next(c for c in rv.get_json()["candidates"] if c["label"] == label)
        return self._join(typed, cand["student_id"])

    def _present(self) -> list[str]:
        return sorted(
            row["codename"]
            for row in self.school.list_live_session_attendees(
                int(self.live["id"]), present_only=True
            )
        )

    def _attendee(self, codename: str) -> dict:
        rows = self.school.list_live_session_attendees(int(self.live["id"]))
        return next(r for r in rows if r["codename"] == codename)

    def test_hyphenated_name_gets_the_picker(self) -> None:
        """'Mary-Jo' / 'Mary Jo' offer both; each Mary-Jo gets her own seat."""
        self.assertEqual(self._labels("Mary-Jo"), ["Mary-Jo", "Mary-Jo 2"])
        self.assertEqual(self._labels("mary jo"), ["Mary-Jo", "Mary-Jo 2"])
        self.assertEqual(self._join("Mary-Jo 2").status_code, 302)
        self.assertEqual(self._pick("Mary-Jo", "Mary-Jo").status_code, 302)
        self.assertEqual(self._present(), ["Mary-Jo", "Mary-Jo 2"])

    def test_two_word_names_show_apart(self) -> None:
        """'Jean Luc' gets the picker; the two show as 'Jean' and 'Jean Luc 2'."""
        self.assertEqual(self._labels("Jean Luc"), ["Jean", "Jean Luc 2"])
        self.assertEqual(self._join("Jean Luc 2").status_code, 302)
        self.assertEqual(self._pick("Jean Luc", "Jean").status_code, 302)
        self.assertEqual(self._present(), ["Jean", "Jean Luc 2"])

    def test_glued_number_is_name_plus_number(self) -> None:
        """'Sam2' joins Sam 2 directly; 'R2D2' is still a plain name."""
        self.assertEqual(self._join("Sam2").status_code, 302)
        self.assertEqual(self._join("R2D2").status_code, 302)
        self.assertEqual(self._present(), ["R2D2", "Sam 2"])

    def test_typed_number_skips_the_picker(self) -> None:
        """A name that already ends in a number never gets the picker."""
        self.assertEqual(self._join("Sam 2").status_code, 302)
        self.assertEqual(self._join("Ana").status_code, 302)

    def test_seat_reclaimed_after_leave_gets_a_fresh_token(self) -> None:
        """Leave, then another device types the name: new token, old one dead."""
        self.assertEqual(self._join("Sam 2").status_code, 302)
        first = self._attendee("Sam 2")
        old_token = str(first["visit_token"])
        self.school.mark_live_session_attendee_left(int(self.live["id"]), int(first["student_id"]))
        self.assertEqual(self._join("Sam 2").status_code, 302)
        again = self._attendee("Sam 2")
        self.assertNotEqual(str(again["visit_token"]), old_token)
        self.assertEqual(int(again["id"]), int(first["id"]))
        self.assertEqual(again["participant_uuid"], first["participant_uuid"])
        self.assertIsNone(self.school.resolve_student_visit_token(old_token))

    def test_same_device_rejoin_after_leave_keeps_its_token(self) -> None:
        """The phone that left (still holding its token) resumes the same seat."""
        phone = self.app.test_client()
        self.assertEqual(self._join("Sam 2", client=phone).status_code, 302)
        first = self._attendee("Sam 2")
        self.school.mark_live_session_attendee_left(int(self.live["id"]), int(first["student_id"]))
        rv = phone.post(
            "/auth/student-code",
            json={"code": self.live["session_code"], "name": "Sam 2", "visit_token": first["visit_token"]},
            headers={"X-Student-Visit-Token": str(first["visit_token"])},
        )
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])
        self.assertEqual(str(self._attendee("Sam 2")["visit_token"]), str(first["visit_token"]))


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited auth ones."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(NumberRuleTests))
    names = [
        n for n, v in vars(RepeatedNameShapesJoinTests).items()
        if n.startswith("test_") and callable(v)
    ]
    suite.addTests(RepeatedNameShapesJoinTests(n) for n in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
