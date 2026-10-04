"""MCK-171 Team challenge: the points payout under real concurrency.

Ops gate HIGH-1 (693363c): parallel Next, a double-click, Next racing End
Game, and End Game pressed twice all over-paid or lost the step. These
tests hammer the real routes from threads, spread over several app
instances opened on the same database file (separate SQLite connections,
like gunicorn workers; threads inside one app, like gthread), and check
that every member is paid exactly once, nothing 500s, and the step is
never lost or skipped.

Run on SQLite (always) and with live presence on Postgres when
``LIVE_PRESENCE_TEST_URL`` is set (as CI runs the suite).
"""

from __future__ import annotations

import logging
import os
import threading
import unittest
from pathlib import Path
from typing import Any, Callable

import rank_challenge
from app import create_app
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness
from test_rank_challenge_results_mck171 import SWAP, PaysOnMixin, StepMixin

POINTS_STEP = rank_challenge.points_step(4)
PODIUM_STEP = rank_challenge.podium_step(4)

#: Ava's team (Ava, Cy, Eli) places the key: 4 right = 8 each. Ben's team
#: (Ben, Dee, Fay) swaps two: 2 right = 4 each.
EXPECT = {"Ava": 8, "Cy": 8, "Eli": 8, "Ben": 4, "Dee": 4, "Fay": 4}

#: App instances on the one database file ("workers").
WORKERS = 4


class ConcurrencyHarness(PaysOnMixin, StepMixin, ChallengeHarness):
    """Several app instances on the same file; the teacher cookie on each.

    Pays-once checks need the payout on (``PaysOnMixin``): MCK-185 option B
    switched it off by default."""

    def setUp(self) -> None:
        super().setUp()
        root = Path(self.tmp.name)
        cookie = self.client.get_cookie("session")
        assert cookie is not None
        self._cookie = (cookie.value, cookie.domain)
        self.workers = []
        for _ in range(WORKERS):
            app = create_app(
                db_path=root / "lloves.sqlite",
                data_dir=root,
                testing=True,
                live_database_url=self._worker_presence_url(),
            )
            self.workers.append(app)
        logging.disable(logging.WARNING)

    def tearDown(self) -> None:
        for app in self.workers:
            app.config["SCHOOL_DB"].close()
        super().tearDown()

    def _worker_presence_url(self) -> str | None:
        return None

    # helpers -------------------------------------------------------------
    def _worker_client(self, index: int):
        client = self.workers[index % len(self.workers)].test_client()
        value, domain = self._cookie
        client.set_cookie("session", value, domain=domain)
        return client

    def _ready(self) -> dict[str, Any]:
        """A closed challenge with both teams scored."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._order("Ben", item, SWAP)
        return self._close(item)

    def _start_game(self) -> None:
        rv = self.client.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 30}]},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])

    def _ledger(self) -> dict[str, tuple[int, int]]:
        """Per student: (sum of point events, number of non-zero events)."""
        game = self.school.game
        with game._lock:
            rows = game.conn.execute(
                "SELECT to_id, COALESCE(SUM(amount), 0) AS s, "
                "SUM(CASE WHEN amount != 0 THEN 1 ELSE 0 END) AS n "
                "FROM point_events WHERE to_kind = 'student' GROUP BY to_id"
            ).fetchall()
        by_id = {int(r["to_id"]): (int(r["s"] or 0), int(r["n"] or 0)) for r in rows}
        return {name: by_id.get(self.ids[name], (0, 0)) for name in EXPECT}

    def _burst(self, calls: list[Callable[[Any], Any]]) -> list[tuple[int, str]]:
        """Run each call on its own thread (own worker client), all at once."""
        out: list[tuple[int, str]] = [(-1, "")] * len(calls)
        clients = [self._worker_client(i) for i in range(len(calls))]
        barrier = threading.Barrier(len(calls))

        def run(index: int, fn: Callable[[Any], Any]) -> None:
            try:
                barrier.wait(timeout=30)
                rv = fn(clients[index])
                data = rv.get_json(silent=True) or {}
                step = data.get("step") if isinstance(data, dict) else None
                out[index] = (rv.status_code, f"step={step} {rv.get_data(as_text=True)[:120]}")
            except Exception as exc:  # noqa: BLE001 - reported by the asserts
                out[index] = (-1, repr(exc))

        threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        return out

    def _next(self, item: dict[str, Any], step: int) -> Callable[[Any], Any]:
        path = f"/api/live-sessions/{self.session_id}/items/{item['id']}/race-step"
        return lambda c: c.post(path, json={"step": step})

    def _end_game(self, *, preserve: bool) -> Callable[[Any], Any]:
        path = f"/api/classes/{self.class_id}/game/end"
        body = {"preserve_live_session": True} if preserve else {}
        return lambda c: c.post(path, json=body)

    def _end_live(self) -> Callable[[Any], Any]:
        path = f"/staff/class/{self.class_id}/end-live"
        return lambda c: c.post(path, data={})

    def _server_step(self, item: dict[str, Any]) -> int:
        with self.school._lock:
            row = self.school.conn.execute(
                "SELECT step FROM live_rank_race_steps WHERE live_item_id = ?", (int(item["id"]),)
            ).fetchone()
        return int(row["step"]) if row is not None else 0

    def _assert_paid_once(self, before: dict[str, tuple[int, int]], codes: list[tuple[int, str]]) -> None:
        after = self._ledger()
        for code, body in codes:
            self.assertGreater(code, 0, body)
            self.assertLess(code, 500, body)
        delta = {n: after[n][0] - before[n][0] for n in EXPECT}
        events = {n: after[n][1] - before[n][1] for n in EXPECT}
        self.assertEqual(delta, EXPECT, codes)
        self.assertEqual(events, {n: 1 for n in EXPECT}, codes)


class ConcurrencyTests(ConcurrencyHarness):
    """Exactly once under parallel Next / End Game / End Live Class."""

    def test_parallel_next_at_the_points_step_pays_once(self) -> None:
        for n in (8, 16):
            with self.subTest(parallel=n):
                item = self._ready()
                self._step(item, POINTS_STEP - 1)
                before = self._ledger()
                codes = self._burst([self._next(item, POINTS_STEP)] * n)
                self._assert_paid_once(before, codes)
                self.assertEqual(sorted(c for c, _ in codes), [200] + [409] * (n - 1), codes)
                self.assertEqual(self._server_step(item), POINTS_STEP)

    def test_double_click_moves_one_screen_and_pays_once(self) -> None:
        item = self._ready()
        self._step(item, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst([self._next(item, POINTS_STEP)] * 2)
        self._assert_paid_once(before, codes)
        self.assertEqual(sorted(c for c, _ in codes), [200, 409])
        self.assertEqual(self._server_step(item), POINTS_STEP)  # no screen skipped

    def test_parallel_mixed_steps_never_skip_or_lose_a_step(self) -> None:
        item = self._ready()
        self._step(item, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst(
            [self._next(item, POINTS_STEP), self._next(item, PODIUM_STEP)] * 3
        )
        self._assert_paid_once(before, codes)
        ok = [body for code, body in codes if code == 200]
        # Points is accepted exactly once; the podium at most once, and only
        # after points (otherwise it would have skipped a screen).
        self.assertEqual(sum(b.startswith(f"step={POINTS_STEP} ") for b in ok), 1, codes)
        self.assertEqual(sum(b.startswith(f"step={PODIUM_STEP} ") for b in ok), len(ok) - 1, codes)
        self.assertLessEqual(len(ok), 2)
        self.assertEqual(self._server_step(item), POINTS_STEP + len(ok) - 1)

    def test_steps_before_points_stay_monotonic(self) -> None:
        item = self._ready()
        for target in range(1, POINTS_STEP):
            codes = self._burst([self._next(item, target), self._next(item, target), self._next(item, target - 1)])
            self.assertEqual(sorted(c for c, _ in codes), [200, 409, 409], codes)
            self.assertEqual(self._server_step(item), target)
        self.assertEqual(self._ledger()["Ava"], (0, 0))

    def test_next_racing_end_game_pays_once(self) -> None:
        item = self._ready()
        self._start_game()
        self._step(item, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst([self._next(item, POINTS_STEP)] * 4 + [self._end_game(preserve=True)])
        self._assert_paid_once(before, codes)
        nexts = sorted(c for c, _ in codes[:4])
        self.assertEqual(nexts, [200, 409, 409, 409], codes)
        self.assertEqual(self._server_step(item), POINTS_STEP)  # step not lost

    def test_next_racing_end_game_that_ends_the_session_pays_once(self) -> None:
        item = self._ready()
        self._start_game()
        self._step(item, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst([self._next(item, POINTS_STEP)] * 2 + [self._end_game(preserve=False)])
        self._assert_paid_once(before, codes)
        # Next either won (200, then 409) or found the class ended (4xx).
        self.assertLessEqual(sum(c == 200 for c, _ in codes[:2]), 1, codes)
        self.assertIn(self._server_step(item), (POINTS_STEP - 1, POINTS_STEP))

    def test_end_game_twice_pays_once(self) -> None:
        self._ready()
        self._start_game()
        before = self._ledger()
        codes = self._burst([self._end_game(preserve=True)] * 2)
        self._assert_paid_once(before, codes)

    def test_end_game_twice_after_the_points_step_pays_nothing_more(self) -> None:
        item = self._ready()
        self._start_game()
        self._step(item, POINTS_STEP)
        paid = self._ledger()
        codes = self._burst([self._end_game(preserve=True)] * 2)
        for code, body in codes:
            self.assertLess(code, 500, body)
        self.assertEqual(self._ledger(), paid)

    def test_end_live_class_twice_pays_once(self) -> None:
        item = self._ready()
        self._step(item, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst([self._end_live()] * 2)
        self._assert_paid_once(before, codes)
        self.assertEqual(self.school.get_live_session(self.session_id)["status"], "ended")

    def test_next_racing_end_live_class_pays_once(self) -> None:
        item = self._ready()
        self._step(item, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst([self._next(item, POINTS_STEP)] * 3 + [self._end_live()])
        self._assert_paid_once(before, codes)
        self.assertLessEqual(sum(c == 200 for c, _ in codes[:3]), 1, codes)

    def test_two_challenges_in_parallel_each_pay_once(self) -> None:
        first = self._ready()
        second = self._ready()
        self._step(first, POINTS_STEP - 1)
        self._step(second, POINTS_STEP - 1)
        before = self._ledger()
        codes = self._burst([self._next(first, POINTS_STEP), self._next(second, POINTS_STEP)] * 4)
        after = self._ledger()
        for code, body in codes:
            self.assertLess(code, 500, body)
        self.assertEqual({n: after[n][0] - before[n][0] for n in EXPECT}, {n: 2 * v for n, v in EXPECT.items()})
        self.assertEqual({n: after[n][1] - before[n][1] for n in EXPECT}, {n: 2 for n in EXPECT})
        self.assertEqual((self._server_step(first), self._server_step(second)), (POINTS_STEP, POINTS_STEP))


def _pg_admin_url() -> str:
    return os.environ.get("LIVE_PRESENCE_TEST_URL", "").strip()


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class ConcurrencyPostgresPresenceTests(ConcurrencyTests):
    """The same hammering with live presence on Postgres (as CI runs it)."""

    def _presence_url(self) -> str:
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_rc_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        self.pg_url = urlunsplit((parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, ""))
        return self.pg_url

    def _worker_presence_url(self) -> str:
        return self.pg_url

    def _drop_pg(self, admin: str) -> None:
        import psycopg

        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def test_presence_is_on_postgres(self) -> None:
        self.assertEqual(self.school.live_presence_status(), "postgres")
        for app in self.workers:
            self.assertEqual(app.config["SCHOOL_DB"].live_presence_status(), "postgres")


if __name__ == "__main__":
    unittest.main()
