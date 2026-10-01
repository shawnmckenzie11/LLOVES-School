#!/usr/bin/env python3
"""MCK-110: students with accented or punctuated names can join by code.

SQLite ``lower()`` folds ASCII only, so a roster "Élodie" or "Ó'Brien" got
401 "Double-check the code" while "Zoë" (lowercase accent) worked. Names
now fold with NFKC + casefold, apostrophe/quote/hyphen look-alikes fold,
and multi-word Codenames match as a whole.
"""

from __future__ import annotations

import unicodedata
import unittest
from typing import Any

import test_auth
from name_match import loose_name_key, name_key, same_name

ROSTER = [
    "Élodie",
    "Ó'Brien",
    "O’Brien",
    "Zoë",
    "Chloé",
    "Jean-Luc",
    "Maple",
    'Ana "Q"',
    "ÅSA",
]


class NameKeyTests(unittest.TestCase):
    """The folding rules on their own."""

    def test_case_and_unicode_forms_fold(self) -> None:
        self.assertTrue(same_name("Élodie", "élodie"))
        self.assertTrue(same_name("ÉLODIE", "élodie"))
        self.assertTrue(same_name("Ó'Brien", "ó'brien"))
        self.assertTrue(same_name("Zoë", "ZOË"))
        decomposed = unicodedata.normalize("NFD", "Chloé")
        self.assertNotEqual(decomposed, "Chloé")
        self.assertTrue(same_name(decomposed, "Chloé"))
        self.assertTrue(same_name("Straße", "STRASSE"))
        self.assertTrue(same_name("Maple", "  MAPLE "))

    def test_apostrophes_quotes_hyphens_spaces_fold(self) -> None:
        self.assertTrue(same_name("O’Brien", "O'Brien"))
        self.assertTrue(same_name("O‘Brien", "oʼbrien"))
        self.assertTrue(same_name("Jean-Luc", "jean luc"))
        self.assertTrue(same_name("Jean–Luc", "Jean‑Luc"))
        self.assertTrue(same_name("Jean  Luc", "Jean Luc"))
        self.assertTrue(same_name('Ana “Q”', 'ana "q"'))

    def test_different_names_stay_different(self) -> None:
        self.assertFalse(same_name("Zoë", "Zoe"))
        self.assertFalse(same_name("Élodie", "Melodie"))
        self.assertFalse(same_name("", ""))
        self.assertEqual(loose_name_key("Élodie"), "elodie")
        self.assertEqual(loose_name_key("Ó’Brien"), "obrien")
        self.assertEqual(name_key("ÅSA"), "åsa")


class JoinUnicodeNameTests(test_auth.AuthTests):
    """Join by code over HTTP. Only the tests below run here."""

    def setUp(self) -> None:
        super().setUp()
        self.live = self._boot_live_class(ROSTER)

    def _join(self, typed: str) -> Any:
        client = self.app.test_client()
        return client.post(
            "/auth/student-code",
            data={"code": self.live["session_code"], "name": typed},
            follow_redirects=False,
        )

    def _joined(self, typed: str, expect_codename: str) -> None:
        rv = self._join(typed)
        self.assertEqual(rv.status_code, 302, (typed, rv.get_data(as_text=True)[:300]))
        self.assertIn("/student/mood", rv.headers.get("Location", ""))
        names = [
            row["codename"]
            for row in self.school.list_live_session_attendees(
                int(self.live["id"]), present_only=True
            )
        ]
        self.assertIn(expect_codename.split()[0], names, (typed, names))

    def test_accented_capitals_join(self) -> None:
        """Élodie and ÅSA join typed as on the roster, or in another case."""
        self._joined("Élodie", "Élodie")
        self._joined("åsa", "ÅSA")

    def test_lowercase_accents_still_join(self) -> None:
        """Zoë and Chloé (worked before) still join."""
        self._joined("Zoë", "Zoë")
        self._joined("chloé", "Chloé")

    def test_decomposed_input_joins(self) -> None:
        """A phone that sends NFD "Chloé" still matches."""
        self._joined(unicodedata.normalize("NFD", "Chloé"), "Chloé")

    def test_hyphen_and_plain_ascii(self) -> None:
        self._joined("Jean-Luc", "Jean-Luc")
        self._joined("MAPLE", "Maple")

    def test_multi_word_codename_joins_whole(self) -> None:
        """A Codename with a space or quotes matches as a whole."""
        self._joined('Ana "Q"', 'Ana "Q"')

    def test_apostrophe_variants_pick_between_the_two(self) -> None:
        """Ó'Brien and O’Brien are different students; each is found.

        Typing the exact one joins it. Typing "OBrien" (no accent, no
        apostrophe) matches both loosely, so the student gets the picker.
        """
        rv = self._join("ó’brien")
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])
        rv = self._join("O'Brien")
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])
        both = self.school.list_roster_matches_for_live_session(
            int(self.live["id"]), "OBrien"
        )
        self.assertEqual(sorted(r["codename"] for r in both), sorted(["Ó'Brien", "O’Brien"]))

    def test_unaccented_typing_falls_back(self) -> None:
        """No accent key: "Elodie" still finds Élodie (only fallback)."""
        self._joined("Elodie", "Élodie")
        exact = self.school.list_roster_matches_for_live_session(
            int(self.live["id"]), "Zoë"
        )
        self.assertEqual([r["codename"] for r in exact], ["Zoë"])

    def test_wrong_name_is_still_rejected(self) -> None:
        rv = self._join("Nobody")
        self.assertEqual(rv.status_code, 401)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited auth ones."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(NameKeyTests))
    names = [
        n for n, v in vars(JoinUnicodeNameTests).items()
        if n.startswith("test_") and callable(v)
    ]
    suite.addTests(JoinUnicodeNameTests(n) for n in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
