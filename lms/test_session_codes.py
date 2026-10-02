#!/usr/bin/env python3
"""MCK-117: 4-character live-session join codes that grow to 5, then 6.

Students type the session code from the projector, so it is short and
drops look-alikes (0/O, 1/I/L). Sessions started before the change keep
their 8-character code until they end, and durable offering codes stay 8.
"""

from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from typing import Any
from unittest.mock import patch

import codes
import test_auth
from codes import (
    ALPHABET,
    SESSION_CODE_ALPHABET,
    SESSION_CODE_GROW_AT_ACTIVE,
    SESSION_CODE_LENGTHS,
    SESSION_CODE_TRIES_PER_LENGTH,
    normalize_live_access_code,
    pick_session_code,
    session_code_start_length,
)


class SessionCodeUnitTests(unittest.TestCase):
    """Generation, growth, and validation without the app."""

    def test_alphabet_has_no_look_alikes(self) -> None:
        for ch in "0O1IL":
            self.assertNotIn(ch, SESSION_CODE_ALPHABET)
        self.assertEqual(len(SESSION_CODE_ALPHABET), 31)
        self.assertEqual(len(set(SESSION_CODE_ALPHABET)), 31)
        self.assertTrue(set(SESSION_CODE_ALPHABET) <= set(ALPHABET))

    def test_default_pick_is_four_unambiguous_characters(self) -> None:
        picks = {pick_session_code(lambda _c: False) for _ in range(300)}
        for code in picks:
            self.assertEqual(len(code), 4)
            self.assertTrue(set(code) <= set(SESSION_CODE_ALPHABET), code)
            self.assertEqual(normalize_live_access_code(code), code)
        self.assertGreater(len(picks), 250)

    def test_start_length_grows_with_active_sessions(self) -> None:
        self.assertEqual(SESSION_CODE_LENGTHS, (4, 5, 6))
        grow_5 = SESSION_CODE_GROW_AT_ACTIVE[4]
        grow_6 = SESSION_CODE_GROW_AT_ACTIVE[5]
        self.assertEqual(session_code_start_length(0), 4)
        self.assertEqual(session_code_start_length(grow_5 - 1), 4)
        self.assertEqual(session_code_start_length(grow_5), 5)
        self.assertEqual(session_code_start_length(grow_6 - 1), 5)
        self.assertEqual(session_code_start_length(grow_6), 6)
        self.assertEqual(len(pick_session_code(lambda _c: False, grow_5)), 5)
        self.assertEqual(len(pick_session_code(lambda _c: False, grow_6 + 50)), 6)

    def test_collisions_retry_then_grow(self) -> None:
        tried: list[str] = []

        def taken(code: str) -> bool:
            tried.append(code)
            return len(code) < 6

        code = pick_session_code(taken)
        self.assertEqual(len(code), 6)
        lengths = [len(c) for c in tried]
        tries = SESSION_CODE_TRIES_PER_LENGTH
        self.assertEqual(lengths, [4] * tries + [5] * tries + [6])

    def test_one_collision_retries_same_length(self) -> None:
        seen: list[str] = []
        first = iter(["AAAA", "BBBB"])

        def make(length: int) -> str:
            self.assertEqual(length, 4)
            return next(first)

        code = pick_session_code(
            lambda c: seen.append(c) or c == "AAAA", generate=make
        )
        self.assertEqual(code, "BBBB")
        self.assertEqual(seen, ["AAAA", "BBBB"])

    def test_every_length_full_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            pick_session_code(lambda _c: True)

    def test_normalize_accepts_supported_lengths(self) -> None:
        self.assertEqual(normalize_live_access_code(" ab2c "), "AB2C")
        self.assertEqual(normalize_live_access_code("ab 2c"), "AB2C")
        self.assertEqual(normalize_live_access_code("ab2cd"), "AB2CD")
        self.assertEqual(normalize_live_access_code("ab2cde"), "AB2CDE")
        # Legacy 8-character codes may contain L.
        self.assertEqual(normalize_live_access_code("lmnp2345"), "LMNP2345")
        self.assertEqual(normalize_live_access_code("LMNP 2345"), "LMNP2345")
        for bad in ("", "ab2", "ab2cdef", "ab2cdefgh", "AB0C", "ABOC", "AB1C", "ABIC"):
            with self.assertRaises(ValueError, msg=bad):
                normalize_live_access_code(bad)


class _InputFinder(HTMLParser):
    """Collect attributes of ``<input id="code">``."""

    def __init__(self) -> None:
        super().__init__()
        self.attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "input" and found.get("id") == "code":
            self.attrs = {k: v or "" for k, v in found.items()}


class SessionCodeJoinTests(test_auth.AuthTests):
    """Over HTTP. Only the tests below run here."""

    def setUp(self) -> None:
        super().setUp()
        self.live = self._boot_live_class(["Maple"])

    def _join(self, code: str) -> Any:
        return self.app.test_client().post(
            "/auth/student-code",
            data={"code": code, "name": "Maple"},
            follow_redirects=False,
        )

    def test_started_session_gets_four_character_code(self) -> None:
        code = str(self.live["session_code"])
        self.assertEqual(len(code), 4)
        self.assertTrue(set(code) <= set(SESSION_CODE_ALPHABET), code)
        offering = self.school.get_offering(int(self.live["offering_id"]))
        self.assertEqual(len(offering["live_access_code"]), 8)

    def test_join_with_four_character_code(self) -> None:
        code = str(self.live["session_code"])
        typed = f" {code[:2].lower()} {code[2:].lower()} "
        rv = self._join(typed)
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])

    def _assert_joins_with(self, code: str) -> None:
        self._set_code(code)
        rv = self._join(code.lower())
        self.assertEqual(rv.status_code, 302, (code, rv.get_data(as_text=True)[:300]))

    def test_join_with_five_character_code(self) -> None:
        self._assert_joins_with("AB2CD")

    def test_join_with_six_character_code(self) -> None:
        self._assert_joins_with("AB2CDE")

    def test_legacy_eight_character_session_still_joins(self) -> None:
        """A session minted before MCK-117 keeps its 8-character code."""
        self._set_code("LMNP2345")
        found = self.school.get_active_live_session_by_code("lmnp 2345")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(int(found["id"]), int(self.live["id"]))
        rv = self._join("lmnp2345")
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])

    def test_wrong_short_code_is_rejected(self) -> None:
        code = str(self.live["session_code"])
        wrong = next(c for c in ("AAAA", "BBBB") if c != code)
        rv = self._join(wrong)
        self.assertEqual(rv.status_code, 401)

    def test_mint_skips_active_code(self) -> None:
        taken = str(self.live["session_code"])
        picks = iter([taken, "ZZ22"])
        with patch.object(
            codes, "generate_live_access_code", side_effect=lambda *_a: next(picks)
        ):
            self.assertEqual(self.school.mint_unique_active_session_code(), "ZZ22")

    def test_insert_repicks_code_taken_by_another_worker(self) -> None:
        """The write-locked insert re-picks a code that went active meanwhile."""
        live = self.live
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_class_sessions SET status = 'ended' WHERE id = ?",
                (int(live["id"]),),
            )
            # Another worker's class started with "QR7K" after our pick.
            self.school.conn.execute(
                """
                INSERT INTO live_class_sessions (
                    class_id, offering_id, teacher_user_id, session_code,
                    status, started_at
                ) VALUES (9999, 9999, 9999, 'QR7K', 'active', '2026-10-02T13:00:00Z')
                """
            )
            self.school.conn.commit()
        new_id, clash = self.school._insert_active_live_session(
            int(live["class_id"]),
            int(live["teacher_user_id"]),
            int(live["offering_id"]),
            "QR7K",
            live.get("meeting_date"),
        )
        self.assertIsNone(clash)
        assert new_id is not None
        row = self.school.get_live_session(new_id)
        assert row is not None
        self.assertNotEqual(row["session_code"], "QR7K")
        self.assertEqual(len(row["session_code"]), 4)

    def test_landing_form_accepts_every_length(self) -> None:
        page = self.app.test_client().get("/").get_data(as_text=True)
        finder = _InputFinder()
        finder.feed(page)
        attrs = finder.attrs
        self.assertEqual(attrs.get("minlength"), "4")
        self.assertEqual(attrs.get("maxlength"), "8")
        self.assertNotIn("8-character", page)
        pattern = re.compile(attrs["pattern"])
        for ok in ("AB2C", "ab2c", "AB2CD", "AB2CDE", "LMNP2345", "AB 2C"):
            self.assertIsNotNone(pattern.fullmatch(ok), ok)
        for bad in ("AB2", "AB-2C", ""):
            self.assertIsNone(pattern.fullmatch(bad), bad)

    def _set_code(self, code: str) -> None:
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_class_sessions SET session_code = ? WHERE id = ?",
                (code, int(self.live["id"])),
            )
            self.school.conn.commit()


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited auth ones."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(SessionCodeUnitTests))
    names = [
        n for n, v in vars(SessionCodeJoinTests).items()
        if n.startswith("test_") and callable(v)
    ]
    suite.addTests(SessionCodeJoinTests(n) for n in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
