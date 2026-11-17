#!/usr/bin/env python3
"""MCK-481: soft-remove bank questions and approve ranking drafts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
import bank_bot  # noqa: E402
from bank_bot import (  # noqa: E402
    NOT_CONFIGURED_MESSAGE,
    allow_generate,
    content_for_model,
    prompt_from_contents,
    reset_generate_limit,
)
from bank_edit import list_staff_bank_questions  # noqa: E402
from components import list_questions_matching_codes  # noqa: E402
from school_db import _now  # noqa: E402
from slide_builder import collect_consolidation_items  # noqa: E402


def _user(school, token: str, role: str) -> int:
    """Insert a staff or admin row without an address."""
    cur = school.conn.execute(
        """
        INSERT INTO users (email, display_name, role, tenant_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (token, token, role, int(school.default_tenant_id()), _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


class BankBotTests(unittest.TestCase):
    """Soft delete, undo, drafts, the unset-key path, and course scoping."""

    def setUp(self) -> None:
        """One course library and a signed-in owner. No model key."""
        self._saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        reset_generate_limit()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.owner_id = _user(self.school, "staff-owner", "staff")
        self.other_id = _user(self.school, "staff-other", "staff")
        self.admin_id = _user(self.school, "staff-admin", "it")
        self.learner_id = _user(self.school, "staff-learner", "student")
        self.offering = self.school.assign_course(
            teacher_user_id=self.owner_id, ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Seat"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=self.owner_id,
        )
        self.class_id = int(created["id"])
        self._session(self.owner_id)

    def tearDown(self) -> None:
        """Close sqlite and put the model key back."""
        self.school.close()
        self.tmp.cleanup()
        if self._saved_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._saved_key

    def _session(self, user_id: int, portal: str = "staff") -> None:
        """Sign the client in as an existing user."""
        with self.client.session_transaction() as sess:
            sess.clear()
            sess["logged_in"] = True
            sess["user_id"] = int(user_id)
            sess["portal"] = portal

    def _add_mc(self, stem: str) -> dict:
        """Insert one multiple-choice question into this course bank."""
        response = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": stem,
                "options": ["flat", "gentle", "steep", "vertical"],
                "correct_answer": "C",
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["question"]

    def _search(self, needle: str) -> list[dict]:
        """Picker search for this course, the same path Import uses."""
        response = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search"
            f"?staff_types=1&q={needle}"
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return list(response.get_json().get("items") or [])

    def _payload(self, question_id: int) -> str:
        """Stored payload text. Soft-remove must not rewrite it."""
        row = self.school.conn.execute(
            "SELECT payload_json, removed_at FROM questions WHERE id = ?",
            (int(question_id),),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row["payload_json"]), row["removed_at"]

    def test_prompt_keeps_question_content_only(self) -> None:
        """Extra stored fields never become part of the model prompt."""
        content = content_for_model(
            {
                "item_type": "multiple_choice_question",
                "title": "Slope",
                "bank_scope": "M1",
                "payload": {
                    "type": "mc",
                    "stem_html": "Slope of a line",
                    "private_note": "DO-NOT-SEND",
                    "choices": [
                        {"html": "flat", "correct": False},
                        {"html": "steep", "correct": True},
                    ],
                },
            }
        )
        self.assertIsNotNone(content)
        prompt = prompt_from_contents([content])
        self.assertIn("Slope of a line", prompt)
        self.assertNotIn("DO-NOT-SEND", prompt)
        self.assertNotIn("private_note", prompt)

    def test_claude_reads_the_key_when_the_call_runs(self) -> None:
        """The key is taken from the environment at the HTTP call, not import."""
        with self.assertRaises(KeyError):
            bank_bot.request_claude("Slope of a line")
        os.environ["ANTHROPIC_API_KEY"] = "call-time-key"
        seen: dict[str, str] = {}

        class _Body:
            """Minimal response for ``urlopen``."""

            def read(self) -> bytes:
                """Return one empty ranking payload."""
                return b'{"content":[{"type":"text","text":"{}"}]}'

            def __enter__(self) -> "_Body":
                """Enter the ``urlopen`` context."""
                return self

            def __exit__(self, *exc: object) -> bool:
                """Leave the ``urlopen`` context."""
                return False

        def fake_urlopen(request: object, timeout: float = 0) -> _Body:
            """Capture the key header and skip the network."""
            del timeout
            header = getattr(request, "get_header")
            seen["key"] = str(header("X-api-key") or "")
            return _Body()

        with patch("bank_bot.urllib.request.urlopen", fake_urlopen):
            bank_bot.request_claude("Slope of a line")
        self.assertEqual(seen["key"], "call-time-key")

    def test_soft_delete_hides_from_picker_and_undo_restores(self) -> None:
        """Remove hides the row. Undo puts it back. The stored payload stays."""
        question = self._add_mc("Slope of a line")
        question_id = int(question["id"])
        before, _removed = self._payload(question_id)
        self.assertTrue(self._search("Slope of a line"))
        removed = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "remove",
                "questions": [
                    {"bank_id": question["bank_id"], "question_id": question_id}
                ],
            },
        )
        self.assertEqual(removed.status_code, 200, removed.get_data(as_text=True))
        after, stamp = self._payload(question_id)
        self.assertEqual(after, before)
        self.assertTrue(stamp)
        self.assertEqual(self._search("Slope of a line"), [])
        blocked = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/import-mc",
            json={"question_id": question_id, "page_number": 1},
        )
        self.assertEqual(blocked.status_code, 404)
        listed = self.client.get(f"/api/staff/class/{self.class_id}/bank-bot/removed")
        self.assertEqual(listed.status_code, 200, listed.get_data(as_text=True))
        ids = [int(row["question_id"]) for row in listed.get_json()["questions"]]
        self.assertIn(question_id, ids)
        restored = self.client.post(
            f"/api/staff/class/{self.class_id}/bank-bot/restore",
            json={"question_ids": [question_id]},
        )
        self.assertEqual(restored.status_code, 200, restored.get_data(as_text=True))
        again, cleared = self._payload(question_id)
        self.assertEqual(again, before)
        self.assertIsNone(cleared)
        self.assertTrue(self._search("Slope of a line"))

    def test_past_results_stay_after_soft_delete(self) -> None:
        """A copied live item and a saved answer are unchanged by remove."""
        question = self._add_mc("Rise over run")
        question_id = int(question["id"])
        imported = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/import-mc",
            json={"question_id": question_id, "page_number": 1},
        )
        self.assertEqual(imported.status_code, 200, imported.get_data(as_text=True))
        placement = self.school.conn.execute(
            """
            SELECT item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND source_question_id = ?
            """,
            (self.class_id, question_id),
        ).fetchone()
        self.assertIsNotNone(placement)
        item_json = str(placement["item_json"])
        stamp = _now()
        session_code = uuid.uuid4().hex
        cur = self.school.conn.execute(
            """
            INSERT INTO live_class_sessions (
                class_id, offering_id, teacher_user_id, session_code,
                status, started_at, ended_at
            ) VALUES (?, ?, ?, ?, 'ended', ?, ?)
            """,
            (
                self.class_id,
                int(self.offering["id"]),
                self.owner_id,
                session_code,
                stamp,
                stamp,
            ),
        )
        session_id = int(cur.lastrowid)
        answer = json.dumps({"choice": "C"})
        cur = self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, kind, payload, active, created_at, updated_at
            ) VALUES (?, 'mc', ?, 0, ?, ?)
            """,
            (session_id, item_json, stamp, stamp),
        )
        prompt_id = int(cur.lastrowid)
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, response_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (prompt_id, answer, stamp, stamp),
        )
        self.school.conn.commit()
        removed = self.client.delete(
            f"/api/staff/class/{self.class_id}/question-bank/"
            f"{question['bank_id']}/questions/{question_id}"
        )
        self.assertEqual(removed.status_code, 200, removed.get_data(as_text=True))
        kept_item = self.school.conn.execute(
            """
            SELECT item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND source_question_id = ?
            """,
            (self.class_id, question_id),
        ).fetchone()
        kept_prompt = self.school.conn.execute(
            "SELECT payload FROM live_session_prompts WHERE id = ?",
            (prompt_id,),
        ).fetchone()
        kept_answer = self.school.conn.execute(
            "SELECT response_json FROM live_session_responses WHERE prompt_id = ?",
            (prompt_id,),
        ).fetchone()
        self.assertEqual(str(kept_item["item_json"]), item_json)
        self.assertEqual(str(kept_prompt["payload"]), item_json)
        self.assertEqual(str(kept_answer["response_json"]), answer)
        still = self.school.conn.execute(
            "SELECT id, removed_at FROM questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        self.assertIsNotNone(still)
        self.assertTrue(still["removed_at"])

    def test_drafts_stay_hidden_until_approved(self) -> None:
        """A mocked model reply is a draft. Approval is what enters the bank."""
        question = self._add_mc("Slope of a line")
        self.school.conn.execute(
            """
            UPDATE questions
            SET payload_json = json_set(payload_json, '$.private_note', 'DO-NOT-SEND')
            WHERE id = ?
            """,
            (int(question["id"]),),
        )
        self.school.conn.commit()
        other_library = self.school.create_library("MCF3M", origin="upload")
        other_bank = self.school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, 'bank:other', 'Other', '{}', ?)
            """,
            (int(other_library["id"]), _now()),
        )
        self.school.conn.commit()
        other_id = int(
            self.school.conn.execute(
                """
                INSERT INTO questions (
                    bank_id, import_key, item_type, title, payload_json, created_at
                ) VALUES (?, 'q-other', 'multiple_choice_question', 'Other course stem', ?, ?)
                """,
                (
                    int(other_bank.lastrowid),
                    json.dumps(
                        {
                            "stem_html": "OTHER-COURSE-STEM",
                            "choices": [
                                {"id": "A", "html": "one", "correct": True},
                                {"id": "B", "html": "two", "correct": False},
                            ],
                        }
                    ),
                    _now(),
                ),
            ).lastrowid
        )
        self.school.conn.commit()
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        seen: list[str] = []

        def fake_claude(prompt: str) -> str:
            """Return two ranking drafts and keep the prompt for the assertion."""
            seen.append(prompt)
            return json.dumps(
                {
                    "items": [
                        {
                            "stem": "Order these slopes",
                            "options": ["flat", "gentle", "steep"],
                            "rank_key": [0, 1, 2],
                        },
                        {
                            "stem": "Order these widths",
                            "options": ["narrow", "medium", "wide"],
                            "rank_key": [0, 1, 2],
                        },
                    ]
                }
            )

        with patch("bank_bot.request_claude", side_effect=fake_claude):
            generated = self.client.post(
                f"/api/staff/class/{self.class_id}/bank-bot/generate",
                json={
                    "question_ids": [int(question["id"]), other_id],
                    "bank_scope": "M1",
                },
            )
        self.assertEqual(generated.status_code, 200, generated.get_data(as_text=True))
        body = generated.get_json()
        self.assertTrue(body["configured"])
        self.assertEqual(len(body["drafts"]), 2)
        self.assertEqual(seen[0].count("Slope of a line"), 1)
        self.assertNotIn("DO-NOT-SEND", seen[0])
        self.assertNotIn("OTHER-COURSE-STEM", seen[0])
        self.assertNotIn("test-key", seen[0])
        self.assertEqual(self._search("Order these slopes"), [])
        self.assertEqual(self._search("Order these widths"), [])
        drafts = self.client.get(f"/api/staff/class/{self.class_id}/bank-bot/drafts")
        self.assertEqual(len(drafts.get_json()["drafts"]), 2)
        first, second = body["drafts"]
        edited = self.client.post(
            f"/api/staff/class/{self.class_id}/bank-bot/drafts/{first['id']}/approve",
            json={
                "stem": "Order these slopes now",
                "options": ["flat", "gentle", "steep"],
                "rank_key": [2, 1, 0],
            },
        )
        self.assertEqual(edited.status_code, 200, edited.get_data(as_text=True))
        payload = edited.get_json()["question"]["payload"]
        self.assertEqual(payload.get("type"), "rank")
        self.assertIn("Order these slopes now", str(payload.get("stem_html") or payload.get("text") or ""))
        self.assertTrue(self._search("Order these slopes now"))
        self.assertEqual(self._search("Order these widths"), [])
        discarded = self.client.post(
            f"/api/staff/class/{self.class_id}/bank-bot/drafts/{second['id']}/discard"
        )
        self.assertEqual(discarded.status_code, 200, discarded.get_data(as_text=True))
        left = self.client.get(f"/api/staff/class/{self.class_id}/bank-bot/drafts")
        self.assertEqual(left.get_json()["drafts"], [])
        self.assertEqual(self._search("Order these widths"), [])
        again = self.client.post(
            f"/api/staff/class/{self.class_id}/bank-bot/drafts/{first['id']}/approve",
            json={},
        )
        self.assertEqual(again.status_code, 404)

    def test_filter_generates_without_a_selection(self) -> None:
        """A multiple-choice module filter is enough when nothing is checked."""
        self._add_mc("Filter source slope")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"

        def fake_claude(prompt: str) -> str:
            """One draft, so the filter path can be told from a selection."""
            self.assertIn("Filter source slope", prompt)
            return json.dumps(
                {
                    "items": [
                        {
                            "stem": "Order the filter slopes",
                            "options": ["flat", "gentle", "steep"],
                            "rank_key": [0, 1, 2],
                        }
                    ]
                }
            )

        with patch("bank_bot.request_claude", side_effect=fake_claude):
            generated = self.client.post(
                f"/api/staff/class/{self.class_id}/bank-bot/generate",
                json={"bank_scope": "M1", "question_type": "mc"},
            )
        self.assertEqual(generated.status_code, 200, generated.get_data(as_text=True))
        self.assertEqual(self._search("Order the filter slopes"), [])

    def test_missing_key_explains_and_delete_still_works(self) -> None:
        """No model key. Generate says so. Remove still hides the question."""
        question = self._add_mc("Keyless slope")
        generated = self.client.post(
            f"/api/staff/class/{self.class_id}/bank-bot/generate",
            json={"question_ids": [int(question["id"])]},
        )
        self.assertEqual(generated.status_code, 200, generated.get_data(as_text=True))
        body = generated.get_json()
        self.assertFalse(body["configured"])
        self.assertEqual(body["error"], NOT_CONFIGURED_MESSAGE)
        self.assertNotIn("drafts", body)
        removed = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "remove",
                "questions": [
                    {"bank_id": question["bank_id"], "question_id": question["id"]}
                ],
            },
        )
        self.assertEqual(removed.status_code, 200, removed.get_data(as_text=True))
        self.assertEqual(self._search("Keyless slope"), [])

    def test_auth_is_teacher_or_admin_and_scoped(self) -> None:
        """Students and other teachers are refused. An admin in the school is not."""
        question = self._add_mc("Scoped slope")
        anon = self.app.test_client()
        refused = anon.post(
            f"/api/staff/class/{self.class_id}/bank-bot/generate",
            json={"question_ids": [int(question["id"])]},
        )
        self.assertEqual(refused.status_code, 401)
        self._session(self.learner_id, portal="staff")
        learner = self.client.get(f"/api/staff/class/{self.class_id}/bank-bot/removed")
        self.assertEqual(learner.status_code, 403)
        self._session(self.other_id)
        other = self.client.post(
            f"/api/staff/class/{self.class_id}/bank-bot/restore",
            json={"question_ids": [int(question["id"])]},
        )
        self.assertEqual(other.status_code, 403)
        self._session(self.admin_id, portal="it")
        admin = self.client.get(f"/api/staff/class/{self.class_id}/bank-bot/removed")
        self.assertEqual(admin.status_code, 200, admin.get_data(as_text=True))
        self._session(self.owner_id)
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        with patch("bank_bot.request_claude", return_value='{"items":[]}'):
            wrong_type = self.client.post(
                f"/api/staff/class/{self.class_id}/bank-bot/generate",
                json={"bank_scope": "M1", "question_type": "rank"},
            )
        self.assertEqual(wrong_type.status_code, 400)

    def test_pages_expose_the_controls(self) -> None:
        """The bank tab and the live class both offer remove and generate."""
        bank = self.client.get(f"/staff/class/{self.class_id}?tab=question-banks")
        live = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(bank.status_code, 200)
        self.assertEqual(live.status_code, 200)
        bank_html = bank.get_data(as_text=True)
        live_html = live.get_data(as_text=True)
        self.assertIn('id="qbank-generate"', bank_html)
        self.assertIn('id="qbank-removed"', bank_html)
        self.assertIn("Remove from bank?", bank_html)
        self.assertIn('id="live-bank-bot-btn"', live_html)
        self.assertIn('id="live-bank-bot-confirm"', live_html)
        self.assertIn("/static/bank_bot.js", live_html)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        teacher_poll = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertNotIn("bank-bot", student)
        self.assertNotIn("bank-bot", teacher_poll)

    def test_removed_question_stays_out_of_new_slide_decks(self) -> None:
        """A removed multiple-choice row is not a consolidation candidate."""
        kept = self._add_mc("Keep the A2.2 practice item")
        removed = self._add_mc("Drop the A2.1 practice item")
        self.school.soft_remove_library_bank_questions(
            self.library_id,
            [(int(removed["bank_id"]), int(removed["id"]))],
        )
        matched = list_questions_matching_codes(
            self.school, self.library_id, ["A2.1"], module_number=1, strand="A"
        )
        matched_ids = {int(row["id"]) for row in matched}
        self.assertNotIn(int(removed["id"]), matched_ids)
        self.assertNotIn(int(kept["id"]), matched_ids)
        still = list_questions_matching_codes(
            self.school, self.library_id, ["A2.2"], module_number=1, strand="A"
        )
        self.assertIn(int(kept["id"]), {int(row["id"]) for row in still})
        cache = Path(self.tmp.name) / "empty-curriculum"
        cache.mkdir()
        rows = collect_consolidation_items(
            self.school,
            course_code="MCR3U",
            module_number=1,
            strand="A",
            expectation_codes={"A2.1"},
            library_id=self.library_id,
            cache_root=cache,
        )
        blob = " ".join(str(row.get("stem") or "") for row in rows)
        self.assertNotIn("A2.1 practice", blob)

    def test_removing_the_canonical_copy_shows_the_twin(self) -> None:
        """The surviving duplicate stays on the bank list, and undo hides it again."""
        first = self._add_mc("Duplicate slope stem")
        second = self._add_mc("Duplicate slope stem")
        bank_id = int(first["bank_id"])
        shown = [
            int(row["id"])
            for row in list_staff_bank_questions(self.school, self.library_id, bank_id)
        ]
        self.assertEqual(shown, [int(first["id"])])
        self.school.soft_remove_library_bank_questions(
            self.library_id, [(bank_id, int(first["id"]))]
        )
        shown = [
            int(row["id"])
            for row in list_staff_bank_questions(self.school, self.library_id, bank_id)
        ]
        self.assertEqual(shown, [int(second["id"])])
        self.school.restore_removed_library_questions(
            self.library_id, [int(first["id"])]
        )
        shown = [
            int(row["id"])
            for row in list_staff_bank_questions(self.school, self.library_id, bank_id)
        ]
        self.assertEqual(shown, [int(first["id"])])

    def test_generate_is_limited_per_teacher(self) -> None:
        """A fifth generate in the same minute does not call Claude."""
        self._add_mc("Limited slope")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        self.assertTrue(all(allow_generate(9001, now=100.0) for _ in range(4)))
        self.assertFalse(allow_generate(9001, now=130.0))
        self.assertTrue(allow_generate(9002, now=130.0))
        self.assertTrue(allow_generate(9001, now=160.0))
        reset_generate_limit()
        calls = {"n": 0}

        def fake_claude(prompt: str) -> str:
            """Count calls and return one ranking draft."""
            del prompt
            calls["n"] += 1
            return json.dumps(
                {
                    "items": [
                        {
                            "stem": "Order the limited slopes",
                            "options": ["flat", "gentle", "steep"],
                            "rank_key": [0, 1, 2],
                        }
                    ]
                }
            )

        with patch("bank_bot.request_claude", side_effect=fake_claude):
            codes = [
                self.client.post(
                    f"/api/staff/class/{self.class_id}/bank-bot/generate",
                    json={"bank_scope": "M1", "question_type": "mc"},
                ).status_code
                for _ in range(5)
            ]
        self.assertEqual(codes, [200, 200, 200, 200, 429])
        self.assertEqual(calls["n"], 4)
        self.assertTrue(allow_generate(self.other_id, now=10.0))


if __name__ == "__main__":
    unittest.main()
