"""MCK-184: Take turns rank on a seeded bank item froze the picker's phone.

Root cause (client): a card the student lifted (one touch on the phone
resize pill, or a title-bar drag from 720px up) became ``is-floating``, and
``paintLifecycleQuestionStack`` never rebuilt floating cards. The tap
greyed the buttons, the server took the pick (the teammate saw it), and
the picker's card kept its old DOM: no spot, "Your turn. Place one item.",
no later turns, no console error. The browser case is in
``test_floating_card_mck184``.

Here: two players on a real #236 seeded item (disguised ids, shuffle),
two rotations with undo and re-place, Undo only for the last placer, and
the waiting phone's poll after a missed push. Also the poll stamp follows
presence while a turns item is open (a leave or rejoin changes whose turn
it is and used to answer "unchanged").
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any

from test_rank_challenge_mck171 import ChallengeHarness

LMS_DIR = Path(__file__).resolve().parent


class TurnsHarness(ChallengeHarness):
    """Team 0 is Ava, Cy, Eli; team 1 is Ben, Dee, Fay (4 options)."""

    race = False

    def _turns_item(self) -> dict[str, Any]:
        row = self._rank_row()
        body: dict[str, Any] = {"group_rank_mode": "turns"}
        if self.race:
            body["group_rank_race"] = True
        rv = self._settings(row, body)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return self._publish(row)

    def _poll(self, name: str, seq: Any = None, stamp: Any = None) -> dict[str, Any]:
        query = {}
        if seq is not None:
            query["seq"] = seq
        if stamp is not None:
            query["stamp"] = stamp
        rv = self.students[name].get("/api/student/state", query_string=query)
        self.assertEqual(rv.status_code, 200)
        return rv.get_json()

    def _turns(self, payload: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
        for pool in ("active_questions", "live_items"):
            for row in payload.get(pool) or []:
                if int(row.get("id") or 0) == int(item["id"]):
                    return (row.get("group_submit") or {}).get("turns")
        return None

    def _phone(self, name: str, item: dict[str, Any]) -> dict[str, Any]:
        turns = self._turns(self._poll(name), item)
        assert turns is not None
        return turns

    def _place(self, name: str, item: dict[str, Any], token: str, code: int = 200):
        rv = self._post(name, item, "rank-turn", {"option_id": token})
        self.assertEqual(rv.status_code, code, rv.get_data(as_text=True)[:300])
        return rv.get_json()

    def _undo(self, name: str, item: dict[str, Any], code: int = 200):
        rv = self._post(name, item, "rank-turn", {"undo": True})
        self.assertEqual(rv.status_code, code, rv.get_data(as_text=True)[:300])
        return rv.get_json()

    def _missed_push_then_poll(self, waiter: str, item: dict[str, Any], move) -> dict[str, Any]:
        """``waiter`` holds its last seq+stamp, ``move`` runs with no push
        delivered, then the waiter's fallback poll must rebuild its card."""
        before = self._poll(waiter)
        seq, stamp = before.get("state_seq"), before.get("stamp")
        self.assertTrue(stamp)
        move()
        after = self._poll(waiter, seq, stamp)
        self.assertFalse(after.get("unchanged"), "the waiting phone got 'unchanged' after a turn change")
        turns = self._turns(after, item)
        assert turns is not None
        return turns


SEED = json.loads((LMS_DIR / "seeds" / "rank_items" / "MCR3U.json").read_text(encoding="utf-8"))
SEED_ITEM = next(row for row in SEED["items"] if row["id"] == "MCR3U-M1-inverse-interchange")


class SeededTurnsHarness(TurnsHarness):
    """A real #236 seeded item (MCK-169), imported from the course bank as
    Take turns (MCK-177 preset), with the MCK-176 shuffle and disguised ids.
    Phones send exactly what their own ``/state`` list shows."""

    def _turns_item(self) -> dict[str, Any]:
        row = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
        ).fetchone()
        library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.school.attach_library(int(row["offering_id"]), library_id)
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search", query_string={"type": "rank"}
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        prompt = SEED_ITEM["payload"]["text"]
        hit = next(r for r in rv.get_json()["items"] if r.get("prompt") == prompt)
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": int(hit["question_id"]), "page_number": 4, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        placed = rv.get_json()["placement"]["item"]
        self.assertEqual(placed["group_rank_mode"], "turns")
        self.school.ensure_live_session_items(self.session_id)
        live = next(
            r
            for r in self.school.list_live_session_items(self.session_id)
            if r["placement_key"] == placed["placement_key"]
        )
        if self.race:
            rv = self._settings(live, {"group_rank_race": True})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = self._publish(live)
        stored = self.school.get_live_session_item(self.session_id, int(item["id"]))["item"]
        # The real seeded keys: o1..o5, math labels, the answer order.
        self.assertEqual(sorted(o["id"] for o in stored["rank_options"]), ["o1", "o2", "o3", "o4", "o5"])
        self.assertEqual(stored["rank_key"], SEED_ITEM["payload"]["rank_key"])
        self.assertIn("Write f⁻¹(x) = (x + 2)/5", [o["label"] for o in stored["rank_options"]])
        return item

    def _row(self, name: str, item: dict[str, Any]) -> dict[str, Any]:
        for row in self._poll(name).get("active_questions") or []:
            if int(row.get("id") or 0) == int(item["id"]):
                return row
        raise AssertionError(f"{name} has no card {item['id']}")

    def _shown(self, name: str, item: dict[str, Any]) -> dict[str, str]:
        """label -> id exactly as this phone's list shows it."""
        return {o["label"]: o["id"] for o in self._row(name, item)["content"]["rank_options"]}

    def _tap(self, name: str, item: dict[str, Any], label: str, code: int = 200) -> dict[str, Any]:
        token = self._shown(name, item)[label]
        rv = self.students[name].post(f"/api/student/live-items/{item['id']}/rank-turn", json={"option_id": token})
        self.assertEqual(rv.status_code, code, rv.get_data(as_text=True)[:300])
        body = rv.get_json()
        if code == 200:
            self._assert_reply_matches_list(name, item, body)
        return body

    def _assert_reply_matches_list(self, name: str, item: dict[str, Any], body: dict[str, Any]) -> None:
        """The picker's own reply: every spot id is in its own list (what
        the phone reconciles against), and it is never its turn again."""
        turns = body["group_submit"]["turns"]
        shown = set(self._shown(name, item).values())
        self.assertTrue(all(s["option_id"] in shown for s in turns["spots"]), (turns, shown))
        self.assertTrue(turns["spots"][-1]["mine"])
        self.assertFalse(turns["can_place"])


class TwoPlayerRotationTests(SeededTurnsHarness):
    """Two present teammates (Eli away), like Eva and Iggy, on a seeded item."""

    def setUp(self) -> None:
        super().setUp()
        self._leave("Eli")

    def _assert_turn(self, item: dict[str, Any], mover: str, other: str) -> None:
        mine, theirs = self._phone(mover, item), self._phone(other, item)
        self.assertTrue(mine["can_place"], (mover, mine))
        self.assertFalse(theirs["can_place"], (other, theirs))

    def _assert_undo_only(self, item: dict[str, Any], last: str, other: str) -> None:
        self.assertTrue(self._phone(last, item)["can_undo"])
        self.assertFalse(self._phone(other, item)["can_undo"])

    def test_two_full_rotations_with_undo_replace_and_missed_pushes(self) -> None:
        item = self._turns_item()
        L = [o["label"] for o in SEED_ITEM["payload"]["rank_options"]]
        # Both phones show the same disguised ids; none is a real o1..o5.
        self.assertEqual(self._shown("Ava", item), self._shown("Cy", item))
        self.assertFalse(set(self._shown("Ava", item).values()) & {"o1", "o2", "o3", "o4", "o5"})
        # A places first; only A has Undo; B's Undo is refused server-side.
        self._tap("Ava", item, L[4])
        self._assert_undo_only(item, "Ava", "Cy")
        rev = self._phone("Ava", item)["rev"]
        self.assertEqual(self._undo("Cy", item, 409)["reason"], "no_undo")
        self.assertEqual(self._phone("Ava", item)["rev"], rev)  # nothing changed
        # A undoes and re-places (prod item 126 had two such cycles).
        self._undo("Ava", item)
        ava, cy = self._phone("Ava", item), self._phone("Cy", item)
        self.assertTrue(ava["can_place"])
        self.assertEqual((ava["can_undo"], cy["can_undo"]), (False, False))
        self.assertEqual(ava["spots"], [])
        self._tap("Ava", item, L[3])
        self._assert_turn(item, "Cy", "Ava")
        # B places with A's phone missing the push: A's turn 2 still arrives.
        turns = self._missed_push_then_poll("Ava", item, lambda: self._tap("Cy", item, L[4]))
        self.assertTrue(turns["can_place"])
        self.assertFalse(turns["can_undo"])
        self._assert_undo_only(item, "Cy", "Ava")
        # Mid-game undo hands the turn back to B, never to A; B re-places.
        self._undo("Ava", item, 409)
        self._undo("Cy", item)
        self._assert_turn(item, "Cy", "Ava")
        # Ava's pick is the last spot again, so only Ava's phone has Undo.
        self._assert_undo_only(item, "Ava", "Cy")
        self._tap("Cy", item, L[0])
        self._assert_turn(item, "Ava", "Cy")
        # Rotation 2: A, B, each waiting phone missing the push.
        turns = self._missed_push_then_poll("Cy", item, lambda: self._tap("Ava", item, L[2]))
        self.assertTrue(turns["can_place"])
        self._assert_undo_only(item, "Ava", "Cy")
        self._tap("Ava", item, L[1], 409)  # out of turn
        turns = self._missed_push_then_poll("Ava", item, lambda: self._tap("Cy", item, L[4]))
        self.assertFalse(turns["done"])
        self.assertTrue(turns["can_place"])
        # Rotation 3 starts: A places the last step and the order is in.
        self._tap("Ava", item, L[1])
        turns = self._phone("Cy", item)
        self.assertTrue(turns["done"])
        self.assertEqual((turns["can_place"], turns["can_undo"]), (False, False))
        self.assertFalse(self._phone("Ava", item)["can_undo"])  # no Undo once the order is in
        self.assertEqual([s["by_name"] for s in turns["spots"]], ["Ava", "Cy", "Ava", "Cy", "Ava"])
        self.assertEqual([s["label"] for s in turns["spots"]], [L[3], L[0], L[2], L[4], L[1]])
        self._undo("Ava", item, 409)

class TwoPlayerRotationRaceTests(TwoPlayerRotationTests):
    """The same rotation as a Team challenge (MCK-171) Take turns."""

    race = True


class PresenceTests(TurnsHarness):
    """The stuck case: a teammate's presence changes while a phone waits."""

    def test_teammate_leaving_enables_the_waiting_turn(self) -> None:
        item = self._turns_item()
        self._place("Ava", item, "o1")
        self._place("Cy", item, "o2")
        ava = self._phone("Ava", item)
        self.assertFalse(ava["can_place"])  # waiting on Eli, who never moved
        self.assertEqual(ava["next_names"], ["Eli"])
        # Eli's tab closes (swept as left). No item row changes and no push
        # reaches Ava. Before the fix this poll answered "unchanged" forever.
        turns = self._missed_push_then_poll("Ava", item, lambda: self._leave("Eli"))
        self.assertTrue(turns["can_place"])
        turns = self._missed_push_then_poll("Cy", item, lambda: self._place("Ava", item, "o3"))
        self.assertTrue(turns["can_place"])

    def test_teammate_rejoining_reaches_the_waiting_phones(self) -> None:
        self._leave("Eli")
        item = self._turns_item()
        self._place("Ava", item, "o1")
        self._place("Cy", item, "o2")
        self.assertTrue(self._phone("Ava", item)["can_place"])

        def rejoin() -> None:
            code = self.school.get_live_session(self.session_id)["session_code"]
            self.students["Eli"].post("/auth/student-code", data={"code": str(code), "name": "Eli"})

        # Eli is back and hasn't had a turn: Ava now waits on Eli.
        turns = self._missed_push_then_poll("Ava", item, rejoin)
        self.assertFalse(turns["can_place"])
        self.assertIn("Eli", turns["next_names"])
        self.assertTrue(self._phone("Eli", item)["can_place"])

    def test_stamp_ignores_presence_when_no_turns_item_is_open(self) -> None:
        """No extra presence read for ordinary questions."""
        stamp = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self._leave("Eli")
        self.assertEqual(self.school.live_student_poll_stamp(self.session_id, self.class_id), stamp)


def _pg_admin_url() -> str:
    return os.environ.get("LIVE_PRESENCE_TEST_URL", "").strip()


class PostgresPresenceMixin:
    """Live presence on a throwaway Postgres database (prod's setup)."""

    def _presence_url(self) -> str:
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_t184_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, ""))

    def _drop_pg(self, admin: str) -> None:
        import psycopg

        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class PresencePostgresTests(PostgresPresenceMixin, PresenceTests):
    """The presence cases with Postgres presence."""

    def test_presence_is_on_postgres(self) -> None:
        self.assertEqual(self.school.live_presence_status(), "postgres")


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class TwoPlayerRotationPostgresTests(PostgresPresenceMixin, TwoPlayerRotationTests):
    """Two rotations with Postgres presence."""


class TurnsClientTests(unittest.TestCase):
    """Phone self-heal and Undo visibility (node harness + statics)."""

    def test_group_flows_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "group_flows.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_student_portal_repaints_lifted_cards_and_heals_after_a_tap(self) -> None:
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        paint = js[js.index("function paintLifecycleQuestionStack"):][:20000]
        # Lifted (floating) cards are rebuilt in place, not skipped.
        self.assertIn("refreshFloatingCard(card, renderLiveCard(item))", paint)
        flow = js[js.index("async function postGroupFlow"):js.index("function reenableTurnButtons")]
        self.assertIn("} finally {", flow)
        self.assertIn("reenableTurnButtons(itemId);", flow)
        self.assertIn("queueGroupFlowResync();", flow)
        self.assertIn("!turnsCardIsStale(row.group_submit, data.group_submit)", flow)
        resync = js[js.index("function queueGroupFlowResync"):]
        self.assertIn('lastPollStamp = "";', resync[:600])
        # A touch never lifts a card on a phone; a lift needs a real move.
        rz = js[js.index('resizeHandle?.addEventListener("pointerdown"'):js.index("const endResize")]
        self.assertIn("if (window.innerWidth < 720) return;", rz)
        self.assertLess(rz.index("< 4) return;"), rz.index("floatPaneAtCurrentPosition(pane, host)"))
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".live-question-stack-body > .student-live-card.student-floating-pane {\n    max-height: 100%;", css)
        # The resize pill only shows from 48rem up.
        self.assertNotRegex(css, r"(?m)^\.student-live-card \.student-pane-resize \{\n  display: block;")


if __name__ == "__main__":
    unittest.main()
