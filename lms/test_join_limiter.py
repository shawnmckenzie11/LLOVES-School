#!/usr/bin/env python3
"""MCK-120: Student Code join limiter.

The limiter used to key on the client-chosen leftmost X-Forwarded-For hop,
answered a locked IP differently for the real code (an oracle), counted
only wrong codes, and had no per-session or site-wide limit.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import test_auth
from auth import (
    STUDENT_JOIN_LOCKED_MSG,
    STUDENT_JOIN_SESSION_FAIL_LIMIT,
    STUDENT_JOIN_SESSION_PER_IP_LIMIT,
    STUDENT_JOIN_WRONG_CODE_LIMIT,
    join_limit_ip_key,
    join_limit_ok_key,
    join_limit_pair_key,
    join_limit_session_key,
    trusted_client_ip,
)

WRONG = "ZZZZZZZZ"


class ClientIpTests(unittest.TestCase):
    """``trusted_client_ip`` and ``join_limit_ip_key`` without the app."""

    def _ip(self, headers: dict[str, str], remote: str = "10.9.9.9", proxyfix: bool = False) -> str:
        app = Flask(__name__)
        if proxyfix:
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        seen: dict[str, str] = {}

        @app.route("/")
        def probe() -> str:
            seen["ip"] = trusted_client_ip()
            return "ok"

        app.test_client().get("/", headers=headers, environ_base={"REMOTE_ADDR": remote})
        return seen["ip"]

    def test_never_reads_xff(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLY_APP_NAME", None)
            self.assertEqual(self._ip({"X-Forwarded-For": "1.2.3.4"}), "10.9.9.9")
            self.assertEqual(
                self._ip({"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}, proxyfix=True), "10.9.9.9"
            )

    def test_fly_client_ip_only_on_fly(self) -> None:
        headers = {"Fly-Client-IP": "203.0.113.7", "X-Forwarded-For": "6.6.6.6, 203.0.113.7, 66.241.1.1"}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLY_APP_NAME", None)
            self.assertEqual(self._ip(headers), "10.9.9.9")
        with patch.dict(os.environ, {"FLY_APP_NAME": "lloves-lms"}):
            self.assertEqual(self._ip(headers, proxyfix=True), "203.0.113.7")
            # Missing or junk header on Fly: the socket peer, still not XFF.
            self.assertEqual(
                self._ip({"X-Forwarded-For": "6.6.6.6, 66.241.1.1"}, proxyfix=True), "10.9.9.9"
            )
            self.assertEqual(self._ip({"Fly-Client-IP": "nope"}), "10.9.9.9")

    def test_key_groups_ipv6_by_64(self) -> None:
        self.assertEqual(join_limit_ip_key("203.0.113.7"), "ip:203.0.113.7")
        self.assertEqual(
            join_limit_ip_key("2001:db8:1:2:aaaa::1"), join_limit_ip_key("2001:db8:1:2:ffff::9")
        )
        self.assertEqual(join_limit_ip_key("2001:db8:1:2::1"), "ip:2001:db8:1:2::/64")
        self.assertNotEqual(join_limit_ip_key("2001:db8:1:2::1"), join_limit_ip_key("2001:db8:1:3::1"))
        self.assertEqual(join_limit_ip_key("::ffff:198.51.100.4"), "ip:198.51.100.4")
        self.assertEqual(join_limit_ip_key(""), "ip:unknown")


class JoinLimiterTests(test_auth.AuthTests):
    """Over HTTP. Only the tests below run here."""

    def _client(self, remote: str = "127.0.0.1") -> Any:
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = remote
        return client

    def _post(self, code: str, name: str, *, remote: str = "127.0.0.1", json: bool = False,
              headers: dict[str, str] | None = None) -> Any:
        client = self._client(remote)
        if json:
            return client.post("/auth/student-code", json={"code": code, "name": name},
                               headers=headers or {}, follow_redirects=False)
        return client.post("/auth/student-code", data={"code": code, "name": name},
                           headers=headers or {}, follow_redirects=False)

    def _fails(self, key: str) -> int:
        return self.school.count_join_failures(key, seconds=600)

    def _lock(self, remote: str = "127.0.0.1", n: int = STUDENT_JOIN_WRONG_CODE_LIMIT) -> None:
        for _ in range(n):
            self._post(WRONG, "X", remote=remote)

    def test_xff_spoof_does_not_dodge_the_limit(self) -> None:
        """Rotating X-Forwarded-For (any position) keeps one key."""
        self._boot_live_class(["Maple"])
        for i in range(STUDENT_JOIN_WRONG_CODE_LIMIT):
            rv = self._post(WRONG, "X", headers={"X-Forwarded-For": f"10.0.{i // 250}.{i % 250}, 9.9.9.{i % 250}"})
            self.assertEqual(rv.status_code, 401)
        rv = self._post(WRONG, "X", headers={"X-Forwarded-For": "10.77.0.1"})
        self.assertEqual(rv.status_code, 429)
        self.assertEqual(self._fails("ip:127.0.0.1"), STUDENT_JOIN_WRONG_CODE_LIMIT + 1)
        self.assertEqual(self.school.count_join_failures(prefix="ip:10.", seconds=600), 0)

    def test_spoofed_fly_client_ip_ignored_off_fly(self) -> None:
        self._boot_live_class(["Maple"])
        os.environ.pop("FLY_APP_NAME", None)
        for i in range(STUDENT_JOIN_WRONG_CODE_LIMIT):
            self._post(WRONG, "X", headers={"Fly-Client-IP": f"10.1.0.{i % 250}"})
        rv = self._post(WRONG, "X", headers={"Fly-Client-IP": "10.1.9.9"})
        self.assertEqual(rv.status_code, 429)

    def test_fly_client_ip_keys_on_fly(self) -> None:
        self._boot_live_class(["Maple"])
        with patch.dict(os.environ, {"FLY_APP_NAME": "lloves-lms"}):
            self._lock()  # no header: socket peer 127.0.0.1
            for _ in range(STUDENT_JOIN_WRONG_CODE_LIMIT):
                self._post(WRONG, "X", headers={"Fly-Client-IP": "203.0.113.7"})
            locked = self._post(WRONG, "X", headers={"Fly-Client-IP": "203.0.113.7"})
            fresh = self._post(WRONG, "X", headers={"Fly-Client-IP": "198.51.100.4"})
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(fresh.status_code, 401)

    def test_distinct_remote_addrs_get_distinct_keys(self) -> None:
        """How Ops tests on the box tip hosts: one source address per client."""
        self._boot_live_class(["Maple"])
        self._lock("127.0.0.2")
        self.assertEqual(self._post(WRONG, "X", remote="127.0.0.2").status_code, 429)
        self.assertEqual(self._post(WRONG, "X", remote="127.0.0.3").status_code, 401)
        # IPv6 clients share their /64.
        self._lock("2001:db8:1:2::1")
        self.assertEqual(self._post(WRONG, "X", remote="2001:db8:1:2::abcd").status_code, 429)
        self.assertEqual(self._post(WRONG, "X", remote="2001:db8:1:3::1").status_code, 401)

    def test_locked_answer_identical_for_valid_and_invalid_codes(self) -> None:
        """A quiet IP learns nothing: same status and same bytes."""
        live = self._boot_live_class(["Maple", "Birch"])
        code = str(live["session_code"])
        joined = self._post(code, "Birch", remote="127.0.0.9")
        self.assertEqual(joined.status_code, 302)
        offering = self.school.get_offering(int(live["offering_id"]))
        self._lock()
        for json_mode in (False, True):
            wrong = self._post(WRONG, "X", json=json_mode)
            cases = {
                "valid code, wrong name": self._post(code, "Nobody", json=json_mode),
                "valid code, name already in class": self._post(code, "Birch", json=json_mode),
                "valid code, no name": self._post(code, "", json=json_mode),
                "permanent class code": self._post(str(offering["live_access_code"]), "Maple", json=json_mode),
                "lowercase wrong": self._post(WRONG.lower(), "Maple", json=json_mode),
            }
            self.assertEqual(wrong.status_code, 429)
            for label, rv in cases.items():
                self.assertEqual(rv.status_code, 429, label)
                self.assertEqual(rv.get_data(), wrong.get_data(), label)
            if json_mode:
                self.assertEqual(wrong.get_json(), {"ok": False, "error": STUDENT_JOIN_LOCKED_MSG})
            else:
                body = wrong.get_data(as_text=True)
                self.assertIn(STUDENT_JOIN_LOCKED_MSG, body)
                self.assertNotIn(code, body)
        # The safe path: right code + roster name still joins.
        self.assertEqual(self._post(code, "Maple").status_code, 302)

    def test_every_failure_counts(self) -> None:
        """Name misses, empty names, "already in class", and class codes count."""
        live = self._boot_live_class(["Maple"])
        code = str(live["session_code"])
        offering = self.school.get_offering(int(live["offering_id"]))
        session_key = join_limit_session_key(live)
        steps = [
            (code, "Nobody", 401),
            (code, "", 401),
            (str(offering["live_access_code"]), "Maple", 401),
            (WRONG, "Maple", 401),
        ]
        for n, (typed, name, status) in enumerate(steps, start=1):
            rv = self._post(typed, name)
            self.assertEqual(rv.status_code, status, (typed, name))
            self.assertEqual(self._fails("ip:127.0.0.1"), n)
        self.assertEqual(self._fails(session_key), 2)
        self.assertEqual(self._post(code, "Maple", remote="127.0.0.5").status_code, 302)
        dup = self._post(code, "Maple")
        self.assertEqual(dup.status_code, 409)
        self.assertIn("already in class", dup.get_data(as_text=True))
        self.assertEqual(self._fails("ip:127.0.0.1"), len(steps) + 1)
        self.assertEqual(self._fails(session_key), 3)
        # Successful joins never count.
        self.assertEqual(self._fails("ip:127.0.0.5"), 0)

    def test_class_code_gets_its_own_message(self) -> None:
        live = self._boot_live_class(["Maple"])
        offering = self.school.get_offering(int(live["offering_id"]))
        durable = str(offering["live_access_code"])
        rv = self._post(f" {durable[:4].lower()} {durable[4:]} ", "Maple", json=True)
        self.assertEqual(rv.status_code, 401)
        self.assertIn("class code, not the live code", rv.get_json()["error"])
        self.assertEqual(self._fails("ip:127.0.0.1"), 1)

    def test_presence_hint_needs_valid_code(self) -> None:
        """"Already in class" only appears with the right code and a roster name."""
        live = self._boot_live_class(["Maple"])
        code = str(live["session_code"])
        self.assertEqual(self._post(code, "Maple", remote="127.0.0.5").status_code, 302)
        wrong = self._post(WRONG, "Maple", json=True)
        self.assertNotIn("already", wrong.get_json()["error"])
        miss = self._post(code, "Nobody", json=True)
        self.assertNotIn("already", miss.get_json()["error"])

    def test_class_of_thirty_on_one_ip_all_join(self) -> None:
        """30 students behind one school IP, each with typos, all get in."""
        names = [f"Stu{n:02d}" for n in range(30)]
        live = self._boot_live_class(names)
        code = str(live["session_code"])
        for name in names:
            self._post(code[:-1] + ("A" if code[-1] != "A" else "B"), name)  # code typo
            self._post(code, name + "x")  # name typo
            self._post(code, "")  # empty name
            rv = self._post(code, name)
            self.assertEqual(rv.status_code, 302, name)
        self.assertGreater(self._fails("ip:127.0.0.1"), STUDENT_JOIN_WRONG_CODE_LIMIT)
        self.assertLess(self._fails("ip:127.0.0.1"), auth.STUDENT_JOIN_IP_BLOCK_LIMIT)

    def test_ip_block_refuses_even_the_right_code(self) -> None:
        live = self._boot_live_class(["Maple", "Birch"])
        code = str(live["session_code"])
        with patch.object(auth, "STUDENT_JOIN_IP_BLOCK_LIMIT", 90):
            self._lock(n=95)
            self.assertEqual(self._fails("ip:127.0.0.1"), 90)  # stops counting at the block
            blocked = self._post(code, "Maple")
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.get_data(), self._post(WRONG, "X").get_data())
            self.assertEqual(self._post(code, "Birch", remote="127.0.0.3").status_code, 302)

    def test_session_limit_blocks_new_joins_but_not_rejoins(self) -> None:
        live = self._boot_live_class(["Maple", "Birch"])
        code = str(live["session_code"])
        first = self._client("127.0.0.20")
        rv = first.post("/auth/student-code", data={"code": code, "name": "Maple"})
        self.assertEqual(rv.status_code, 302)
        with first.session_transaction() as sess:
            token = sess.get("student_visit_token")
        self.assertTrue(token)
        with patch.object(auth, "STUDENT_JOIN_SESSION_FAIL_LIMIT", 6):
            # Name guessing spread over many IPs, none of them quiet.
            for i in range(6):
                self.assertEqual(self._post(code, f"Guess{i}", remote=f"127.0.1.{i}").status_code, 401)
            # A blocked session answers exactly like an invalid code.
            blocked = self._post(code, "Birch", remote="127.0.2.1", json=True)
            wrong = self._post(WRONG, "Birch", remote="127.0.2.1", json=True)
            self.assertEqual(blocked.status_code, 401)
            self.assertEqual(blocked.get_data(), wrong.get_data())
            self._lock("127.0.2.3")
            quiet_blocked = self._post(code, "Birch", remote="127.0.2.3")
            self.assertEqual(quiet_blocked.status_code, 429)
            self.assertEqual(quiet_blocked.get_data(), self._post(WRONG, "X", remote="127.0.2.3").get_data())
            back = self._post(code, "Maple", remote="127.0.2.2", headers={"X-Student-Visit-Token": token})
            self.assertEqual(back.status_code, 302, back.get_data(as_text=True)[:300])

    def test_session_block_clears_on_end_and_start(self) -> None:
        """Ops #217 HIGH: End then Start reuses the row id; the new run starts clean."""
        live = self._boot_live_class(["Maple", "Birch"])
        code = str(live["session_code"])
        old_key = join_limit_session_key(live)
        ips = STUDENT_JOIN_SESSION_FAIL_LIMIT // STUDENT_JOIN_SESSION_PER_IP_LIMIT
        for n in range(ips):
            for i in range(STUDENT_JOIN_SESSION_PER_IP_LIMIT):
                self._post(code, f"Guess{i}", remote=f"127.0.5.{n}")
        self.assertEqual(self._fails(old_key), STUDENT_JOIN_SESSION_FAIL_LIMIT)
        blocked = self._post(code, "Maple", remote="127.0.6.1", json=True)
        self.assertEqual(blocked.status_code, 401)
        # Teacher presses End, then Start (the real routes).
        class_id = int(live["class_id"])
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.assertEqual(self.client.post(f"/staff/class/{class_id}/end-live").status_code, 302)
        self.client.post(f"/staff/class/{class_id}/run-live")
        self.client.get("/logout")
        fresh = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(fresh)
        self.assertEqual(int(fresh["id"]), int(live["id"]))  # the row id is reused
        self.assertNotEqual(str(fresh["session_code"]), code)
        self.assertNotEqual(join_limit_session_key(fresh), old_key)
        joined = self._post(str(fresh["session_code"]), "Maple", remote="127.0.6.1")
        self.assertEqual(joined.status_code, 302, joined.get_data(as_text=True)[:300])
        self.assertEqual(self._fails(join_limit_session_key(fresh)), 0)

    def test_one_ip_cannot_block_a_session_alone(self) -> None:
        """One IP (or one school NAT) adds at most 50 to a run's count."""
        live = self._boot_live_class(["Maple"])
        code = str(live["session_code"])
        key = join_limit_session_key(live)
        for i in range(STUDENT_JOIN_SESSION_PER_IP_LIMIT + 20):
            self.assertEqual(self._post(code, f"Guess{i}").status_code, 401)
        self.assertEqual(self._fails("ip:127.0.0.1"), STUDENT_JOIN_SESSION_PER_IP_LIMIT + 20)
        self.assertEqual(self._fails(key), STUDENT_JOIN_SESSION_PER_IP_LIMIT)
        self.assertEqual(
            self._fails(join_limit_pair_key(key, "ip:127.0.0.1")), STUDENT_JOIN_SESSION_PER_IP_LIMIT
        )
        # Other sources still add, and the class still joins.
        self._post(code, "Nobody", remote="127.0.0.8")
        self.assertEqual(self._fails(key), STUDENT_JOIN_SESSION_PER_IP_LIMIT + 1)
        self.assertEqual(self._post(code, "Maple").status_code, 302)

    def _boot_another_class(self, email: str, codenames: list[str]) -> dict:
        """A second teacher's live class, after ``_boot_live_class``."""
        teacher = self.school.register_staff(email)
        offering = self.school.assign_course(teacher_user_id=int(teacher["id"]), ontario_code="MCF3M")
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=T")
        client.post("/verify-email", data={"code": self.school.get_user_by_email(email)["verification_code"]})
        created = client.post(
            "/api/staff/classes",
            json={"offering_id": offering["id"], "days": "M/W/F", "time": "2:00pm", "codenames": codenames},
        )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True)[:300])
        class_id = int(created.get_json()["class"]["id"])
        client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        return live

    def test_school_nat_three_classes_all_join(self) -> None:
        """Ops #213-on-#217: 3 classes x 28 at the bell on one NAT, 3 typos each."""
        rosters = [[f"C{c}Kid{n:02d}" for n in range(28)] for c in range(3)]
        lives = [self._boot_live_class(rosters[0])]
        lives += [self._boot_another_class(f"t{c}@gmail.com", rosters[c]) for c in (1, 2)]
        codes = [str(live["session_code"]) for live in lives]
        self.assertEqual(len(set(codes)), 3)
        for n in range(28):
            for c in range(3):
                code, name = codes[c], rosters[c][n]
                self._post(code[:-1] + ("A" if code[-1] != "A" else "B"), name)  # code typo
                self._post(code, name + "x")  # name typo
                self._post(code, "")  # empty name
                rv = self._post(code.lower(), name)
                self.assertEqual(rv.status_code, 302, (c, n, rv.get_data(as_text=True)[:200]))
        self.assertEqual(self._fails("ip:127.0.0.1"), 252)
        self.assertGreater(252, auth.STUDENT_JOIN_IP_BLOCK_LIMIT)
        self.assertEqual(self._fails(join_limit_ok_key("ip:127.0.0.1")), 84)

    def test_hard_block_depends_on_successful_joins(self) -> None:
        """No joins: blocked at 240. Any join: only the 600 backstop blocks."""
        live = self._boot_live_class(["Maple", "Birch", "Cedar"])
        code = str(live["session_code"])
        with patch.object(auth, "STUDENT_JOIN_IP_BLOCK_LIMIT", 90), \
                patch.object(auth, "STUDENT_JOIN_IP_JOINED_BLOCK_LIMIT", 120):
            # A guessing IP with no joins is blocked at 90.
            self._lock("127.0.7.1", n=95)
            self.assertEqual(self._fails("ip:127.0.7.1"), 90)
            self.assertEqual(self._post(code, "Maple", remote="127.0.7.1").status_code, 429)
            # A school IP whose students get in keeps going past 90 ...
            self.assertEqual(self._post(code, "Birch", remote="127.0.7.2").status_code, 302)
            self._lock("127.0.7.2", n=100)
            self.assertEqual(self._post(code, "Maple", remote="127.0.7.2").status_code, 302)
            # ... up to the backstop.
            self._lock("127.0.7.2", n=30)
            self.assertEqual(self._fails("ip:127.0.7.2"), 120)
            self.assertEqual(self._post(code, "Cedar", remote="127.0.7.2").status_code, 429)

    def test_global_limit_quiets_every_ip(self) -> None:
        live = self._boot_live_class(["Maple"])
        code = str(live["session_code"])
        with patch.object(auth, "STUDENT_JOIN_GLOBAL_FAIL_LIMIT", 20):
            for i in range(20):
                self._post(WRONG, "X", remote=f"127.0.3.{i}")
            wrong = self._post(WRONG, "X", remote="127.0.4.1")
            miss = self._post(code, "Nobody", remote="127.0.4.2")
            self.assertEqual(wrong.status_code, 429)
            self.assertEqual(miss.status_code, 429)
            self.assertEqual(wrong.get_data(), miss.get_data())
            self.assertEqual(self._post(code, "Maple", remote="127.0.4.3").status_code, 302)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited auth ones."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(ClientIpTests))
    names = [
        n for n, v in vars(JoinLimiterTests).items()
        if n.startswith("test_") and callable(v)
    ]
    suite.addTests(JoinLimiterTests(n) for n in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
