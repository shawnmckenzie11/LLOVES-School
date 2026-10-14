#!/usr/bin/env python3
"""MCK-204: backfill catch-up links from snapshots + default PINs."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_backfill import backfill_catchup_from_snapshots  # noqa: E402
from default_pin import (  # noqa: E402
    SLIP_TEMPLATE,
    decrypt_pin,
    generate_unique_default_pin,
    pin_conflicts_class_code,
)
from participation import check_pin  # noqa: E402
from test_participation_mck198 import Base, catchup_data  # noqa: E402


class DefaultPinTests(unittest.TestCase):
    def test_rejects_class_code_match(self) -> None:
        self.assertTrue(pin_conflicts_class_code("1234", "1234"))
        self.assertFalse(pin_conflicts_class_code("5678", "ABCD12XY"))

    def test_generate_avoids_class_code(self) -> None:
        pin = generate_unique_default_pin(class_code="9999", used=())
        self.assertNotEqual(pin, "9999")


class BackfillTests(Base):
    def _insert_snapshot_run(
        self,
        *,
        module: str,
        slot: str,
        run_key: str,
        questions: list[tuple[str, bool]],
        answers: dict[tuple[str, int], str],
    ) -> None:
        """Write snapshot rows without a live session (backfill source)."""
        stamp = "2026-10-14T10:30:00"
        sid = 9000 + len(run_key)
        for qkey, answered in questions:
            if answered:
                for (key, stu), ans in answers.items():
                    if key != qkey:
                        continue
                    self.school.conn.execute(
                        """
                        INSERT INTO live_result_snapshots (
                            live_session_id, class_id, session_code, run_key,
                            live_module, live_slot, source, question_key, question_json,
                            student_id, participant_uuid, codename, team_id,
                            answer_json, awarded_points, answered_at, snapshot_at
                        ) VALUES (?, ?, 'TST', ?, ?, ?, 'prompt', ?, ?, ?, NULL, '', NULL, ?, NULL, ?, ?)
                        """,
                        (
                            sid,
                            self.class_id,
                            run_key,
                            module,
                            slot,
                            f"item:{qkey}",
                            json.dumps({"text": f"Q {qkey}", "type": "mc"}),
                            stu,
                            json.dumps({"value": ans}),
                            stamp,
                            stamp,
                        ),
                    )
            else:
                # Published in class but nobody answered — must not count as offered.
                self.school.conn.execute(
                    """
                    INSERT INTO live_result_snapshots (
                        live_session_id, class_id, session_code, run_key,
                        live_module, live_slot, source, question_key, question_json,
                        student_id, participant_uuid, codename, team_id,
                        answer_json, awarded_points, answered_at, snapshot_at
                    ) VALUES (?, ?, 'TST', ?, ?, ?, 'prompt', ?, ?, NULL, NULL, '', NULL, '{}', NULL, NULL, ?)
                    """,
                    (
                        sid,
                        self.class_id,
                        run_key,
                        module,
                        slot,
                        f"item:{qkey}",
                        json.dumps({"text": f"Silent {qkey}", "type": "mc"}),
                        stamp,
                    ),
                )
        self.school.conn.commit()

    def test_backfill_offered_set_matches_answered_only(self) -> None:
        s0 = self.students[0]
        run_key = "backfill:m2c1"
        self._insert_snapshot_run(
            module="M2",
            slot="C1",
            run_key=run_key,
            questions=[("a", True), ("b", False), ("c", True)],
            answers={("a", s0): "A", ("c", s0): "B"},
        )
        report = backfill_catchup_from_snapshots(self.school, module="M2", slot="C1", apply=True)
        self.assertTrue(any("participation" in r.action for r in report.runs))
        keys = [
            r["question_key"]
            for r in self.school.conn.execute(
                "SELECT question_key FROM participation_items WHERE class_id = ? AND run_key = ?",
                (self.class_id, run_key),
            ).fetchall()
        ]
        self.assertEqual(sorted(keys), ["item:a", "item:c"])

    def test_backfill_idempotent(self) -> None:
        s0 = self.students[0]
        run_key = "backfill:idem"
        self._insert_snapshot_run(
            module="M2",
            slot="C1",
            run_key=run_key,
            questions=[("x", True)],
            answers={("x", s0): "A"},
        )
        backfill_catchup_from_snapshots(self.school, apply=True)
        links = self.school.list_catchup_links(self.class_id)
        n_links = len(links)
        n_items = self.school.conn.execute(
            "SELECT COUNT(*) FROM participation_items WHERE class_id = ?", (self.class_id,)
        ).fetchone()[0]
        report = backfill_catchup_from_snapshots(self.school, apply=True)
        self.assertEqual(len(self.school.list_catchup_links(self.class_id)), n_links)
        link = self.school.list_catchup_links(self.class_id)[0]
        self.assertTrue(int(link["is_open"]))
        self.assertEqual(
            sum(
                1
                for r in report.runs
                if r.run_key == run_key and "participation" in r.action
            ),
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM participation_items WHERE class_id = ?", (self.class_id,)
            ).fetchone()[0],
            n_items,
        )

    def test_dry_run_default(self) -> None:
        s0 = self.students[0]
        self._insert_snapshot_run(
            module="M2",
            slot="C1",
            run_key="backfill:dry",
            questions=[("d", True)],
            answers={("d", s0): "A"},
        )
        report = backfill_catchup_from_snapshots(self.school, module="M2", slot="C1", apply=False)
        self.assertTrue(report.dry_run)
        self.assertFalse(self.school.list_catchup_links(self.class_id))


class DefaultPinDbTests(Base):
    def test_unique_pins_and_no_regeneration(self) -> None:
        secret = "test-secret-mck204"
        self.school.game.conn.execute(
            "UPDATE classes SET live_access_code = 'ZZZZZZZZ' WHERE id = ?", (self.class_id,)
        )
        first = self.school.ensure_default_pins_for_class(self.class_id, secret)
        self.assertEqual(first["assigned"], len(self.students))
        rows = self.school.game.conn.execute(
            "SELECT id, pin_hash, default_pin_ciphertext FROM students WHERE class_id = ?",
            (self.class_id,),
        ).fetchall()
        pins = [decrypt_pin(str(r["default_pin_ciphertext"]), secret) for r in rows]
        self.assertEqual(len(pins), len(set(pins)))
        for pin in pins:
            self.assertFalse(pin_conflicts_class_code(pin, "ZZZZZZZZ"))
        second = self.school.ensure_default_pins_for_class(self.class_id, secret)
        self.assertEqual(second["assigned"], 0)
        self.assertEqual(second["skipped_existing"], len(self.students))

    def test_never_overwrites_existing_pin_hash(self) -> None:
        secret = "test-secret-mck204b"
        target = self.students[0]
        self.school.set_student_pin(self.class_id, target, "4242")
        before = self.school.game.conn.execute(
            "SELECT pin_hash, default_pin_ciphertext FROM students WHERE id = ?", (target,)
        ).fetchone()
        self.school.ensure_default_pins_for_class(self.class_id, secret)
        after = self.school.game.conn.execute(
            "SELECT pin_hash, default_pin_ciphertext FROM students WHERE id = ?", (target,)
        ).fetchone()
        self.assertEqual(before["pin_hash"], after["pin_hash"])
        self.assertIsNone(after["default_pin_ciphertext"])

    def test_teacher_reset_clears_ciphertext(self) -> None:
        secret = "test-secret-mck204c"
        self.school.ensure_default_pins_for_class(self.class_id, secret)
        sid = self.students[1]
        self.school.set_student_pin(self.class_id, sid, "5151")
        row = self.school.game.conn.execute(
            "SELECT default_pin_ciphertext FROM students WHERE id = ?", (sid,)
        ).fetchone()
        self.assertIsNone(row["default_pin_ciphertext"])

    def test_slip_copy_verbatim(self) -> None:
        secret = "test-secret-slip"
        self.school.ensure_default_pins_for_class(self.class_id, secret)
        slips = self.school.teacher_default_pin_slips(self.class_id, secret)
        self.assertTrue(slips)
        for slip in slips:
            self.assertRegex(slip["slip"], r"^Your catch-up PIN: \d{4} \(not the join code\)$")


class CatchupWithDefaultPinTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.secret = str(self.app.secret_key or "")
        self.run_class("M2", "C1", individual=3, group=0, answers={self.students[0]: 2})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.school.ensure_default_pins_for_class(self.class_id, self.secret)
        row = self.school.game.conn.execute(
            "SELECT default_pin_ciphertext FROM students WHERE id = ?", (self.students[0],)
        ).fetchone()
        self.default_pin = decrypt_pin(str(row["default_pin_ciphertext"]), self.secret)

    def test_student_sign_in_with_default_pin(self) -> None:
        self.assertTrue(
            self.school.catchup_sign_in(self.token, self.first, self.default_pin)["ok"]
        )
        client = self.app.test_client()
        client.post(f"/catchup/{self.token}", data={"action": "who", "codename": self.first})
        rv = client.post(
            f"/catchup/{self.token}", data={"action": "pin", "pin": self.default_pin},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/dashboard", rv.headers.get("Location") or "")
        play = client.get(
            self.school.catchup_canonical_path_for_link(
                {**self.school.list_catchup_links(self.class_id)[0], "class_id": self.class_id}
            )
            + "?play=1"
        )
        html = play.get_data(as_text=True)
        self.assertIn('id="catchup-data"', html)
        data = catchup_data(html)
        self.assertTrue(data["questions"])

    def test_teacher_only_pin_slips_on_roster_tab(self) -> None:
        client = self.app.test_client()
        html = client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        self.assertNotIn("Default catch-up PINs", html)
        staff = self.app.test_client()
        staff.get("/auth/google?portal=staff")
        staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        staff.post("/verify-email", data={"code": code})
        page = staff.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        self.assertIn("Default catch-up PINs", page)
        self.assertIn(SLIP_TEMPLATE.format(pin=self.default_pin), page)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
