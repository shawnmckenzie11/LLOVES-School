#!/usr/bin/env python3
"""MCK-273: the catch-up code is the live join code from that run."""

from __future__ import annotations

import json
import os
import secrets
import sys
import unittest
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import codes  # noqa: E402
from catchup_landing import set_landing_session  # noqa: E402
from catchup_portal_code import (  # noqa: E402
    decode_portal_join_code,
    mint_portal_join_code,
)
from catchup_landing import link_still_open  # noqa: E402
from codes import (  # noqa: E402
    SESSION_CODE_ALPHABET,
    SESSION_CODE_FALLBACK_LENGTH,
    SESSION_CODE_LENGTHS,
    SESSION_CODE_TRIES_PER_LENGTH,
    SessionCodeUnavailable,
    is_offensive_code,
    session_code_charset_ok,
    session_code_start_length,
)
from test_participation_mck198 import Base  # noqa: E402


def _same(left: str, right: str) -> bool:
    """True when two join codes are present and equal."""
    return bool(left) and bool(right) and left == right


class OneCodeTests(Base):
    """End stores the live join code; sign-in checks live before catch-up."""

    def _publish_one_answer(self, session_id: int, module: str, slot: str) -> None:
        """Publish one individual item and one roster answer."""
        now = "2026-10-14T13:20:00"
        conn = self.school.conn
        conn.execute(
            """
            UPDATE live_class_sessions
            SET teacher_state_json = ?
            WHERE id = ?
            """,
            (json.dumps({"live_module": module, "live_slot": slot}), int(session_id)),
        )
        prompt_id = conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 1, 'poll', '{}', ?, ?)
            """,
            (int(session_id), now, now),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, item_json,
                prompt_id, status, publish_mode, response_mode, published_at,
                created_at, updated_at
            ) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed',
                      'individual', 'individual', ?, ?, ?)
            """,
            (
                int(session_id),
                json.dumps({"text": "q0", "type": "mc", "options": [{"text": "A"}]}),
                prompt_id,
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', ?, ?)
            """,
            (int(prompt_id), int(self.students[0]), now, now),
        )
        conn.commit()

    def _end_with_answer(self, module: str, slot: str, *, is_test: bool = False) -> tuple[str, str]:
        """Start, publish one answer, End. Return the live code and the stored catch-up code."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=is_test
        )
        self._publish_one_answer(int(live["id"]), module, slot)
        join_code = str(live["session_code"] or "")
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=True,
        )
        link = self.school.conn.execute(
            """
            SELECT portal_join_code FROM catchup_links
            WHERE class_id = ? AND live_module = ? AND live_slot = ?
              AND COALESCE(is_test, 0) = ?
            """,
            (self.class_id, module, slot, 1 if is_test else 0),
        ).fetchone()
        stored = str(link["portal_join_code"] or "") if link is not None else ""
        return join_code, stored

    def _session_shaped_portal_code(self) -> str:
        """A structured catch-up code that is also a legal live join code."""
        for module in ("M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"):
            for slot in ("C2", "C3", "C4"):
                for _ in range(12):
                    code = mint_portal_join_code("MCR3U", module, slot)
                    if (
                        decode_portal_join_code(code) is not None
                        and all(ch in SESSION_CODE_ALPHABET for ch in code)
                        and not is_offensive_code(code)
                    ):
                        return code
        self.fail("could not mint a session-shaped catch-up code")
        return ""

    def test_end_stores_the_live_join_code(self) -> None:
        """After End, catch-up uses the code students typed to join live."""
        join_code, stored = self._end_with_answer("M1", "C1")
        self.assertTrue(_same(join_code, stored), "catch-up code did not match the live join code")
        self.assertTrue(session_code_charset_ok(join_code))
        resolved = self.school.resolve_catchup_link_by_portal_code(join_code.lower())
        self.assertIsNotNone(resolved)
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": join_code.lower(), "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/catchup", rv.headers.get("Location") or "")

    def test_rerun_replaces_the_catchup_code(self) -> None:
        """A second End of the same slot stores that run's join code."""
        first, stored_first = self._end_with_answer("M1", "C2")
        self.assertTrue(_same(first, stored_first), "first catch-up code did not match")
        second, stored_second = self._end_with_answer("M1", "C2")
        self.assertTrue(_same(second, stored_second), "rerun catch-up code did not match")
        self.assertNotEqual(first, second)
        self.assertIsNone(self.school.resolve_catchup_link_by_portal_code(first))
        self.assertIsNotNone(self.school.resolve_catchup_link_by_portal_code(second))

    def test_mint_skips_an_open_catchup_code(self) -> None:
        """Start will not hand out a code an open catch-up link still holds."""
        reserved, stored = self._end_with_answer("M2", "C1")
        self.assertTrue(_same(reserved, stored), "open catch-up code was not stored")
        self.assertTrue(self.school.catchup_join_code_reserved(reserved))
        calls = {"n": 0}

        def generate(length: int, alphabet: str = "") -> str:
            """Return the reserved code once, then a different free code."""
            calls["n"] += 1
            if calls["n"] == 1:
                return reserved
            while True:
                nxt = "".join(secrets.choice(SESSION_CODE_ALPHABET) for _ in range(int(length)))
                if (
                    nxt != reserved
                    and session_code_charset_ok(nxt)
                    and not is_offensive_code(nxt)
                ):
                    return nxt

        with mock.patch.object(codes, "generate_live_access_code", side_effect=generate):
            minted = self.school.mint_unique_active_session_code()
        self.assertNotEqual(minted, reserved)
        self.assertGreaterEqual(calls["n"], 2)

    def test_live_code_wins_when_it_also_decodes_as_catchup(self) -> None:
        """A live join code is not treated as a catch-up code at sign-in."""
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        shaped = self._session_shaped_portal_code()
        self.school.conn.execute(
            "UPDATE live_class_sessions SET session_code = ? WHERE id = ?",
            (shaped, int(live["id"])),
        )
        self.school.conn.commit()
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": shaped, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        location = rv.headers.get("Location") or ""
        self.assertIn("/student/mood", location)
        self.assertNotIn("/student/catchup", location)

    def test_live_code_wins_over_an_open_catchup_row(self) -> None:
        """The same value on an active session joins live, not catch-up."""
        join_code, stored = self._end_with_answer("M3", "C1")
        self.assertTrue(_same(join_code, stored), "catch-up code was not stored")
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        self.school.conn.execute(
            "UPDATE live_class_sessions SET session_code = ? WHERE id = ?",
            (join_code, int(live["id"])),
        )
        self.school.conn.commit()
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": join_code, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        location = rv.headers.get("Location") or ""
        self.assertIn("/student/mood", location)
        self.assertNotIn("/student/catchup", location)

    def test_stored_legacy_catchup_code_still_resolves(self) -> None:
        """An older structured code already on a row still opens catch-up."""
        self._end_with_answer("M4", "C1")
        legacy = mint_portal_join_code("MCR3U", "M4", "C2")
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET portal_join_code = ?
            WHERE class_id = ? AND live_module = 'M4' AND live_slot = 'C1'
              AND COALESCE(is_test, 0) = 0
            """,
            (legacy, self.class_id),
        )
        self.school.conn.commit()
        self.assertIsNotNone(self.school.resolve_catchup_link_by_portal_code(legacy))
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": legacy, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/catchup", rv.headers.get("Location") or "")

    def test_test_class_code_works_and_delete_revokes_it(self) -> None:
        """A test run's code joins catch-up; deleting the test class revokes it."""
        join_code, stored = self._end_with_answer("M5", "C1", is_test=True)
        self.assertTrue(_same(join_code, stored), "test catch-up code did not match")
        link = self.school.conn.execute(
            """
            SELECT run_key, is_test FROM catchup_links
            WHERE class_id = ? AND COALESCE(is_test, 0) = 1
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNotNone(link)
        self.assertEqual(int(link["is_test"]), 1)
        answers = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM participation_answers
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, str(link["run_key"])),
        ).fetchone()
        self.assertGreater(int(answers["n"]), 0)
        resolved = self.school.resolve_catchup_link_by_portal_code(join_code)
        self.assertIsNotNone(resolved)
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": join_code, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/catchup", rv.headers.get("Location") or "")
        session_id = int(
            self.school.conn.execute(
                """
                SELECT id FROM live_class_sessions
                WHERE class_id = ? AND COALESCE(is_test, 0) = 1
                ORDER BY id DESC LIMIT 1
                """,
                (self.class_id,),
            ).fetchone()["id"]
        )
        self.school.delete_test_live_class(self.class_id, session_id)
        self.assertIsNone(self.school.resolve_catchup_link_by_portal_code(join_code))
        self.assertFalse(self.school.catchup_join_code_reserved(join_code))

    def test_sign_out_does_not_put_the_code_on_the_url(self) -> None:
        """Catch-up sign-out returns to the landing page without the join code."""
        join_code, stored = self._end_with_answer("M6", "C1")
        self.assertTrue(_same(join_code, stored), "catch-up code was not stored")
        link = self.school.list_catchup_links(self.class_id)[0]
        client = self.app.test_client()
        with client.session_transaction() as sess:
            set_landing_session(
                sess,
                class_id=self.class_id,
                student_id=self.students[0],
                matched_token=str(link["token"]),
                portal_code=join_code,
                display_name=self.first,
            )
        rv = client.get("/student/catchup/sign-out", follow_redirects=False)
        self.assertEqual(rv.status_code, 302)
        location = rv.headers.get("Location") or ""
        parsed = urllib.parse.urlparse(location)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query.get("signed_out"), ["1"])
        self.assertNotIn("student_code", query)
        self.assertNotIn("code", query)
        values = [item for group in query.values() for item in group]
        self.assertFalse(
            any(item.strip().upper() == join_code for item in values),
            "sign-out URL contains a join code",
        )
        landing = client.get(location)
        self.assertEqual(landing.status_code, 200)
        page = landing.get_data(as_text=True)
        self.assertRegex(page, r'id="code"[^>]*value=""')

    def test_join_form_submits_stored_code_lengths(self) -> None:
        """The join form sends 4, 5, 6, and 8 character codes without a live class."""
        page = self.app.test_client().get("/").get_data(as_text=True)
        gate = page.split('id="student-code-panel"', 1)[1].split(
            'fetch("/api/student/live-available"', 1
        )[0]
        self.assertIn("/^[A-Za-z0-9]{4,6}$/", gate)
        self.assertIn("/^[A-Za-z0-9]{8}$/", gate)

    def test_expired_catchup_code_stays_reserved(self) -> None:
        """Start never reissues a code on an expired or closed catch-up row."""
        code, stored = self._end_with_answer("M9", "C4")
        self.assertTrue(_same(code, stored), "catch-up code was not stored")
        self.assertTrue(session_code_charset_ok(code))
        row = self.school.conn.execute(
            """
            SELECT run_key FROM catchup_links
            WHERE class_id = ? AND live_module = 'M9' AND live_slot = 'C4'
            """,
            (self.class_id,),
        ).fetchone()
        run_key = str(row["run_key"])
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET ended_at = ?
            WHERE class_id = ? AND run_key = ?
            """,
            ("2020-01-15T15:00:00", self.class_id, run_key),
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET open_until = ?
            WHERE class_id = ? AND run_key = ?
            """,
            ("2020-01-22T15:00:00-05:00", self.class_id, run_key),
        )
        self.school.conn.commit()
        expired = dict(
            self.school.conn.execute(
                "SELECT * FROM catchup_links WHERE class_id = ? AND run_key = ?",
                (self.class_id, run_key),
            ).fetchone()
        )
        self.assertFalse(link_still_open(self.school, expired))
        self.assertTrue(self.school.catchup_join_code_reserved(code))
        self.school.conn.execute(
            "UPDATE catchup_links SET is_open = 0 WHERE class_id = ? AND run_key = ?",
            (self.class_id, run_key),
        )
        self.school.conn.commit()
        self.assertTrue(self.school.catchup_join_code_reserved(code))
        calls = {"n": 0}

        def generate(length: int, alphabet: str = "") -> str:
            """Always offer the reserved code. Length is ignored."""
            del length, alphabet
            calls["n"] += 1
            return code

        start = session_code_start_length(0)
        lengths = [n for n in SESSION_CODE_LENGTHS if n >= start]
        lengths.append(SESSION_CODE_LENGTHS[-1])
        lengths.append(SESSION_CODE_FALLBACK_LENGTH)
        expected = len(lengths) * SESSION_CODE_TRIES_PER_LENGTH
        with self.assertRaises(SessionCodeUnavailable) as caught:
            with mock.patch.object(codes, "generate_live_access_code", side_effect=generate):
                self.school.mint_unique_active_session_code()
        self.assertIn("Press Start again", str(caught.exception))
        self.assertEqual(calls["n"], expected)

    def test_closed_or_other_class_digit_code_still_signs_in(self) -> None:
        """PIN setting and sign-in ignore a closed or other-class all-digit code."""
        self._end_with_answer("M7", "C2")
        sign_in = self.school.conn.execute(
            """
            SELECT token FROM catchup_links
            WHERE class_id = ? AND live_module = 'M7' AND live_slot = 'C2'
            """,
            (self.class_id,),
        ).fetchone()
        token = str(sign_in["token"])
        digits = "".join(ch for ch in SESSION_CODE_ALPHABET if ch.isdigit())
        numeric = "".join(secrets.choice(digits) for _ in range(4))
        now = "2026-10-14T13:20:00"
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, is_open,
                portal_join_code, created_at, updated_at, is_test
            ) VALUES (?, ?, ?, 'M1', 'C1', 1, ?, ?, ?, 0)
            """,
            ("other-class-row", 99991, "other-class-run", numeric, now, now),
        )
        self.school.conn.commit()
        self.school.set_student_pin(self.class_id, self.students[0], numeric)
        signed = self.school.catchup_sign_in(token, self.first, numeric)
        self.assertTrue(signed.get("ok"), signed.get("reason"))
        checked = self.school.catchup_verify_student_pin(
            self.class_id, self.students[0], numeric
        )
        self.assertTrue(checked.get("ok"), checked.get("reason"))
        self.school.conn.execute("DELETE FROM catchup_links WHERE class_id = ?", (99991,))
        self._end_with_answer("M7", "C3")
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET portal_join_code = ?
            WHERE class_id = ? AND live_module = 'M7' AND live_slot = 'C3'
            """,
            (numeric, self.class_id),
        )
        run_key = str(
            self.school.conn.execute(
                """
                SELECT run_key FROM catchup_links
                WHERE class_id = ? AND live_module = 'M7' AND live_slot = 'C3'
                """,
                (self.class_id,),
            ).fetchone()["run_key"]
        )
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET ended_at = ?
            WHERE class_id = ? AND run_key = ?
            """,
            ("2020-01-15T15:00:00", self.class_id, run_key),
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET open_until = ?
            WHERE class_id = ? AND run_key = ?
            """,
            ("2020-01-22T15:00:00-05:00", self.class_id, run_key),
        )
        self.school.conn.commit()
        expired = dict(
            self.school.conn.execute(
                "SELECT * FROM catchup_links WHERE class_id = ? AND run_key = ?",
                (self.class_id, run_key),
            ).fetchone()
        )
        self.assertFalse(link_still_open(self.school, expired))
        self.school.set_student_pin(self.class_id, self.students[0], numeric)
        still = self.school.catchup_sign_in(token, self.first, numeric)
        self.assertTrue(still.get("ok"), still.get("reason"))
        # Two days ago stays inside the seven-day legacy window.
        ended = (
            datetime.now(ZoneInfo("America/Toronto")) - timedelta(days=2)
        ).replace(microsecond=0, tzinfo=None)
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET ended_at = ?
            WHERE class_id = ? AND run_key = ?
            """,
            (ended.isoformat(timespec="seconds"), self.class_id, run_key),
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET open_until = NULL
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        )
        self.school.conn.commit()
        reopened = dict(
            self.school.conn.execute(
                "SELECT * FROM catchup_links WHERE class_id = ? AND run_key = ?",
                (self.class_id, run_key),
            ).fetchone()
        )
        self.assertTrue(link_still_open(self.school, reopened))
        with self.assertRaises(ValueError) as caught:
            self.school.set_student_pin(self.class_id, self.students[0], numeric)
        self.assertIn("join code", str(caught.exception).lower())
        refused = self.school.catchup_sign_in(token, self.first, numeric)
        self.assertEqual(refused["reason"], "looks_like_join_code")
        landing = self.school.catchup_verify_student_pin(
            self.class_id, self.students[0], numeric
        )
        self.assertEqual(landing["reason"], refused["reason"])

    def test_digit_catchup_code_is_refused_as_a_pin(self) -> None:
        """The stored catch-up code is not accepted on the PIN screen."""
        self._end_with_answer("M7", "C1")
        digits = "".join(ch for ch in SESSION_CODE_ALPHABET if ch.isdigit())
        numeric = "".join(secrets.choice(digits) for _ in range(4))
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET portal_join_code = ?
            WHERE class_id = ? AND live_module = 'M7' AND live_slot = 'C1'
            """,
            (numeric, self.class_id),
        )
        self.school.conn.commit()
        token = str(self.school.list_catchup_links(self.class_id)[0]["token"])
        result = self.school.catchup_sign_in(token, self.first, numeric)
        self.assertEqual(result["reason"], "looks_like_join_code")

    def test_closed_code_does_not_join_the_next_live_class(self) -> None:
        """A closed or expired catch-up code does not join the next live class."""
        self.school.set_student_pin(self.class_id, self.students[0], "4321")
        self._assert_stored_code_stays_out_of_live("M8", "C1", close="expired")
        self._assert_stored_code_stays_out_of_live("M8", "C2", close="closed")

    def _assert_stored_code_stays_out_of_live(
        self, module: str, slot: str, *, close: str
    ) -> None:
        """Store a catch-up code, close it, and refuse it at the next live class.

        The code stays on the row and stays reserved. The class's new join
        code still reaches the live class. After the PIN, the student sees
        the usual catch-up-closed heading.

        Args:
            module: Live module key, such as ``M8``.
            slot: Live slot key, such as ``C1``.
            close: ``expired`` backdates the run; ``closed`` sets ``is_open`` to 0.
        """
        from catchup_landing import COPY_CLOSED_HEADING, COPY_LIVE_JOINING

        old, stored = self._end_with_answer(module, slot)
        self.assertTrue(_same(old, stored), "catch-up code was not stored")
        run_key = str(
            self.school.conn.execute(
                """
                SELECT run_key FROM catchup_links
                WHERE class_id = ? AND live_module = ? AND live_slot = ?
                  AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, module, slot),
            ).fetchone()["run_key"]
        )
        if close == "expired":
            # Start wipes a celebrating session. The open-until date then
            # comes from the result snapshot, so both have to be in the past.
            self.school.conn.execute(
                """
                UPDATE live_class_sessions
                SET ended_at = ?
                WHERE class_id = ? AND run_key = ?
                """,
                ("2020-01-15T15:00:00", self.class_id, run_key),
            )
            self.school.conn.execute(
                """
                UPDATE live_result_snapshots
                SET snapshot_at = ?
                WHERE class_id = ? AND run_key = ?
                """,
                ("2020-01-15T15:00:00", self.class_id, run_key),
            )
            self.school.conn.execute(
                """
                UPDATE catchup_links
                SET open_until = ?
                WHERE class_id = ? AND run_key = ?
                """,
                ("2020-01-22T15:00:00-05:00", self.class_id, run_key),
            )
        elif close == "closed":
            self.school.conn.execute(
                """
                UPDATE catchup_links
                SET is_open = 0
                WHERE class_id = ? AND live_module = ? AND live_slot = ?
                  AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, module, slot),
            )
        else:
            raise AssertionError("close must be expired or closed")
        self.school.conn.commit()
        link = dict(
            self.school.conn.execute(
                """
                SELECT * FROM catchup_links
                WHERE class_id = ? AND live_module = ? AND live_slot = ?
                  AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, module, slot),
            ).fetchone()
        )
        self.assertFalse(link_still_open(self.school, link))
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        new_code = str(live["session_code"] or "")
        self.assertNotEqual(old.upper(), new_code.upper())
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": old.lower(), "name": self.first},
            environ_base={"REMOTE_ADDR": f"10.8.0.{1 if close == 'expired' else 2}"},
            follow_redirects=False,
        )
        self.assertFalse(
            link_still_open(
                self.school,
                dict(
                    self.school.conn.execute(
                        """
                        SELECT * FROM catchup_links
                        WHERE class_id = ? AND live_module = ? AND live_slot = ?
                          AND COALESCE(is_test, 0) = 0
                        """,
                        (self.class_id, module, slot),
                    ).fetchone()
                ),
            )
        )
        self.assertEqual(rv.status_code, 302)
        location = rv.headers.get("Location") or ""
        self.assertIn("/student/catchup", location)
        self.assertIn("state=closed", location)
        self.assertNotIn("/student/mood", location)
        self.assertNotIn(COPY_LIVE_JOINING, rv.get_data(as_text=True))
        seated = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM live_session_attendees
            WHERE live_session_id = ? AND student_id = ?
            """,
            (int(live["id"]), int(self.students[0])),
        ).fetchone()["n"]
        self.assertEqual(seated, 0)
        self.assertTrue(self.school.catchup_join_code_reserved(old))
        kept = self.school.resolve_catchup_link_by_portal_code(old)
        self.assertIsNotNone(kept)
        self.assertTrue(
            _same(str(kept["portal_join_code"] or ""), old),
            "closed catch-up code was cleared",
        )
        pin_page = client.get(location).get_data(as_text=True)
        self.assertIn('id="landing_pin"', pin_page)
        self.assertNotIn(COPY_CLOSED_HEADING, pin_page)
        after_pin = client.post(
            "/student/catchup/landing",
            data={"action": "pin", "pin": "4321"},
            environ_base={"REMOTE_ADDR": f"10.8.0.{1 if close == 'expired' else 2}"},
            follow_redirects=True,
        )
        self.assertIn(COPY_CLOSED_HEADING, after_pin.get_data(as_text=True))
        self.assertNotIn(COPY_LIVE_JOINING, after_pin.get_data(as_text=True))
        fresh = self.app.test_client()
        live_rv = fresh.post(
            "/auth/student-code",
            data={"code": new_code.lower(), "name": self.first},
            environ_base={"REMOTE_ADDR": f"10.8.1.{1 if close == 'expired' else 2}"},
            follow_redirects=False,
        )
        self.assertEqual(live_rv.status_code, 302)
        self.assertIn("/student/mood", live_rv.headers.get("Location") or "")
        self.school.end_live_class_session(int(live["id"]))


if __name__ == "__main__":
    unittest.main()
