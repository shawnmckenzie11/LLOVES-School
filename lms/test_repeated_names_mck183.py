#!/usr/bin/env python3
"""MCK-183: repeated first names ("Sam", "Sam 2") join as themselves.

Welcome's names step tells teachers to add a number to a repeated first
name. ``first_name_only`` used to drop the number, so "Sam 2" showed in
class as "Sam", and a bare "Sam" joined the first Sam without a picker.
"""

from __future__ import annotations

import unittest
from typing import Any

import test_auth
from school_db import first_name_only

ROSTER = ["Sam", "Sam 2", "Ana", 'Ana "Q"', "Jean Luc"]


class FirstNameOnlyTests(unittest.TestCase):
    """A trailing 1-2 digit number stays; last names still go."""

    def test_numbers_stay_last_names_go(self) -> None:
        self.assertEqual(first_name_only("Sam 2"), "Sam 2")
        self.assertEqual(first_name_only("  sam   12 "), "sam 12")
        self.assertEqual(first_name_only("Sam Smith"), "Sam")
        self.assertEqual(first_name_only("Sam 123"), "Sam")
        self.assertEqual(first_name_only("Sam 2 Smith"), "Sam 2")
        self.assertEqual(first_name_only("Sam"), "Sam")
        self.assertEqual(first_name_only(""), "")


class RepeatedNameJoinTests(test_auth.AuthTests):
    """Join by code over HTTP. Only the tests below run here."""

    def setUp(self) -> None:
        super().setUp()
        self.live = self._boot_live_class(ROSTER)

    def _join(self, typed: str, student_id: int | None = None) -> Any:
        data: dict[str, Any] = {"code": self.live["session_code"], "name": typed}
        if student_id is not None:
            data["student_id"] = student_id
        return self.app.test_client().post("/auth/student-code", json=data)

    def _present(self) -> list[str]:
        return [
            row["codename"]
            for row in self.school.list_live_session_attendees(
                int(self.live["id"]), present_only=True
            )
        ]

    def test_bare_name_gets_the_picker(self) -> None:
        """Typing "Sam" offers Sam and Sam 2."""
        rv = self._join("Sam")
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True)[:300])
        labels = sorted(c["label"] for c in rv.get_json()["candidates"])
        self.assertEqual(labels, ["Sam", "Sam 2"])

    def test_picked_sam_2_shows_as_sam_2(self) -> None:
        """Picking Sam 2 joins as "Sam 2", not "Sam"."""
        picker = self._join("sam").get_json()["candidates"]
        sam2 = next(c for c in picker if c["label"] == "Sam 2")
        rv = self._join("sam", sam2["student_id"])
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])
        self.assertEqual(self._present(), ["Sam 2"])

    def test_typed_number_joins_directly(self) -> None:
        """Typing "Sam 2" joins Sam 2 with no picker; "Sam" can still join."""
        self.assertEqual(self._join("Sam 2").status_code, 302)
        picker = self._join("Sam").get_json()["candidates"]
        sam = next(c for c in picker if c["label"] == "Sam")
        self.assertEqual(self._join("Sam", sam["student_id"]).status_code, 302)
        self.assertEqual(sorted(self._present()), ["Sam", "Sam 2"])

    def test_other_names_unchanged(self) -> None:
        """Multi-word Codenames still match whole and keep the first word."""
        self.assertEqual(self._join("Jean Luc").status_code, 302)
        self.assertIn("Jean", self._present())


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited auth ones."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(FirstNameOnlyTests))
    names = [
        n for n, v in vars(RepeatedNameJoinTests).items()
        if n.startswith("test_") and callable(v)
    ]
    suite.addTests(RepeatedNameJoinTests(n) for n in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
