#!/usr/bin/env python3
"""MCK-42: keep one unsent live answer and resend it without a duplicate row."""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any

import test_live_class_backend_state as backend

LMS_DIR = backend.Path(backend.__file__).resolve().parent
UNSENT_JS = LMS_DIR / "static" / "unsent_answer.js"
PORTAL_JS = LMS_DIR / "static" / "student-portal.js"


class UnsentAnswerServerTests(backend.LiveBackendStateTests):
    """Same answer twice is one row. A closed question rejects the retry."""

    def _open_prompt(self) -> int:
        """Publish q-one and return its prompt id."""
        self.school.set_live_session_teacher_state(
            self.session_id, stage="round", student_view={"questions": "student"}
        )
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "q-one")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.item_id = int(published["id"])
        return int(prompt["id"])

    def _rows(self, prompt_id: int) -> list[dict[str, Any]]:
        """Return stored answers for one prompt."""
        with self.school._lock:
            found = self.school.conn.execute(
                """
                SELECT id, student_id, participant_uuid, response_json, updated_at
                FROM live_session_responses
                WHERE prompt_id = ?
                ORDER BY id ASC
                """,
                (int(prompt_id),),
            ).fetchall()
        return [dict(row) for row in found]

    def _answer_kinds(self) -> list[str]:
        """Engagement kinds that mean an individual answer."""
        rows = self.school.list_engagement_events(
            self.class_id, live_session=self.session_id
        )
        return [
            str(row["kind"])
            for row in rows
            if str(row["kind"]) in {"answer.submit", "answer.change"}
        ]

    def test_same_answer_is_one_row_and_one_event(self) -> None:
        """A reconnect replay does not insert a second answer or a second event."""
        self._begin_and_join(1)
        prompt_id = self._open_prompt()
        sid = self.student_ids[0]
        first = self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        self.assertNotIn("idempotent_replay", first)
        stamp = self._rows(prompt_id)[0]["updated_at"]
        again = self.school.submit_live_prompt_response(
            prompt_id, sid, {"choice": "A"}, participant_uuid="person-a"
        )
        self.assertTrue(again.get("idempotent_replay"))
        rows = self._rows(prompt_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["updated_at"], stamp)
        self.assertEqual(json.loads(rows[0]["response_json"])["choice"], "A")
        self.assertEqual(self._answer_kinds(), ["answer.submit"])

    def test_changed_answer_updates_the_same_row(self) -> None:
        """A real change while the question is open still replaces that one row."""
        self._begin_and_join(1)
        prompt_id = self._open_prompt()
        sid = self.student_ids[0]
        self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        changed = self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "B"})
        self.assertNotIn("idempotent_replay", changed)
        rows = self._rows(prompt_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["response_json"])["choice"], "B")
        self.assertEqual(self._answer_kinds(), ["answer.submit", "answer.change"])

    def test_two_students_keep_two_rows(self) -> None:
        """Idempotency is per student, not per question."""
        self._begin_and_join(2)
        prompt_id = self._open_prompt()
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[0], {"choice": "A"}
        )
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[1], {"choice": "A"}
        )
        rows = self._rows(prompt_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {int(row["student_id"]) for row in rows},
            {self.student_ids[0], self.student_ids[1]},
        )

    def test_guest_replay_stays_one_row(self) -> None:
        """A guest keyed only by participant uuid does not fork a second row."""
        self._begin_and_join(1)
        prompt_id = self._open_prompt()
        self.school.submit_live_prompt_response(
            prompt_id, None, {"choice": "A"}, participant_uuid="guest-maple"
        )
        again = self.school.submit_live_prompt_response(
            prompt_id, None, {"choice": "A"}, participant_uuid="guest-maple"
        )
        self.assertTrue(again.get("idempotent_replay"))
        rows = self._rows(prompt_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["participant_uuid"], "guest-maple")

    def test_closed_question_rejects_the_retry(self) -> None:
        """Once the question is closed, a resend does not add or change a row."""
        self._begin_and_join(1)
        prompt_id = self._open_prompt()
        sid = self.student_ids[0]
        self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        self.school.close_live_session_item(self.session_id, self.item_id)
        before = self._rows(prompt_id)
        with self.assertRaisesRegex(ValueError, "not accepting"):
            self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "B"})
        self.assertEqual(self._rows(prompt_id), before)

    def test_unique_race_collapses_to_one_row(self) -> None:
        """A lost insert race updates the row that won instead of raising."""
        self._begin_and_join(1)
        prompt_id = self._open_prompt()
        sid = self.student_ids[0]
        self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        real = type(self.school)._lookup_live_response_row
        calls = {"n": 0}

        def hide_first(
            db: Any, looked_up: int, *, participant_uuid: str, student_id: int | None
        ) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return real(
                db,
                looked_up,
                participant_uuid=participant_uuid,
                student_id=student_id,
            )

        type(self.school)._lookup_live_response_row = hide_first  # type: ignore[method-assign]
        try:
            again = self.school.submit_live_prompt_response(prompt_id, sid, {"choice": "A"})
        finally:
            type(self.school)._lookup_live_response_row = real  # type: ignore[method-assign]
        self.assertTrue(again.get("idempotent_replay"))
        self.assertEqual(len(self._rows(prompt_id)), 1)
        self.assertGreaterEqual(calls["n"], 2)

    def test_http_replay_returns_ok_and_one_row(self) -> None:
        """The student route accepts the same POST twice and stores one answer."""
        prompt_id = self._open_prompt()
        live = self.school.get_live_session(self.session_id)
        assert live is not None
        client = self.app.test_client()
        joined = client.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302, joined.headers.get("Location"))
        self.assertEqual(client.post("/student/mood", data={"mood": "good"}).status_code, 302)
        self.assertEqual(
            client.post("/student/character", data={"character": "fox"}).status_code, 302
        )
        body = {"prompt_id": prompt_id, "response": {"choice": "A"}}
        first = client.post("/api/student/live-prompt/response", json=body)
        second = client.post("/api/student/live-prompt/response", json=body)
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertNotIn("idempotent_replay", second.get_json())
        self.assertEqual(len(self._rows(prompt_id)), 1)
        self.assertEqual(self._answer_kinds().count("answer.submit"), 1)
        self.assertEqual(self._answer_kinds().count("answer.change"), 0)
        self.school.close_live_session_item(self.session_id, self.item_id)
        blocked = client.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"choice": "B"}},
        )
        self.assertIn(blocked.status_code, {400, 409}, blocked.get_json())
        error = str(blocked.get_json().get("error") or "").lower()
        self.assertTrue(
            "not accepting" in error or "no longer active" in error or "no active prompt" in error,
            blocked.get_json(),
        )
        self.assertEqual(len(self._rows(prompt_id)), 1)
        self.assertEqual(json.loads(self._rows(prompt_id)[0]["response_json"])["choice"], "A")


class UnsentAnswerClientTests(unittest.TestCase):
    """The browser keeps one answer per student and does not resend a closed one."""

    def test_portal_wires_the_calm_hold_and_reconnect_flush(self) -> None:
        """Student portal keeps the answer and flushes it when the link returns."""
        portal = PORTAL_JS.read_text(encoding="utf-8")
        module = UNSENT_JS.read_text(encoding="utf-8")
        self.assertIn('from "/static/unsent_answer.js"', portal)
        self.assertIn("Saved, will send when you're back online", module)
        self.assertIn("Could not submit that answer.", portal)
        self.assertIn("function holdUnsentLiveAnswer(", portal)
        self.assertIn("function flushUnsentAnswers(", portal)
        self.assertIn("void flushUnsentAnswers();", portal)
        online = portal.split('addEventListener("online"', 1)[1][:500]
        self.assertIn("studentOutageStartedAt = 0", online)
        self.assertIn("flushUnsentAnswers()", online)
        self.assertIn("reissueStudentStateOnce()", online)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".student-live-unsent-pending", css)
        self.assertIn(".student-live-unsent-pending", portal)

    def test_outbox_and_resend_rules(self) -> None:
        """localStorage is per student and question. A closed card is not resent."""
        script = r"""
import {
  UNSENT_ANSWER_COPY,
  classifyUnsentFailure,
  forgetUnsentAnswer,
  listUnsentAnswers,
  markUnsentClosed,
  rememberUnsentAnswer,
  unsentResendDecision,
  unsentStudentKey,
} from "./static/unsent_answer.js";

function memory() {
  const data = new Map();
  return {
    getItem(key) { return data.has(key) ? data.get(key) : null; },
    setItem(key, value) { data.set(key, String(value)); },
    removeItem(key) { data.delete(key); },
  };
}

const store = memory();
const maple = { studentId: 4, visitToken: "visit-maple" };
const aspen = { studentId: 9, visitToken: "visit-aspen" };
if (unsentStudentKey(maple) !== "s:4") throw new Error("roster id should win");
if (unsentStudentKey({ visitToken: "visit-maple" }) !== "v:visit-maple") {
  throw new Error("visit token is the guest key");
}
rememberUnsentAnswer(store, {
  studentKey: unsentStudentKey({ visitToken: "visit-maple" }),
  visitToken: "visit-maple",
  promptId: 15,
  itemId: 3,
  sessionId: 8,
  response: { choice: "A" },
  body: { prompt_id: 15, response: { choice: "A" } },
});
rememberUnsentAnswer(store, {
  studentKey: unsentStudentKey(maple),
  studentId: 4,
  visitToken: "visit-maple",
  promptId: 15,
  itemId: 3,
  sessionId: 8,
  response: { choice: "B" },
  body: { prompt_id: 15, response: { choice: "B" } },
});
rememberUnsentAnswer(store, {
  studentKey: unsentStudentKey(aspen),
  studentId: 9,
  visitToken: "visit-aspen",
  promptId: 15,
  response: { choice: "A" },
});
const mapleRows = listUnsentAnswers(store, maple);
const aspenRows = listUnsentAnswers(store, aspen);
if (mapleRows.length !== 1 || mapleRows[0].response.choice !== "B") {
  throw new Error("maple should keep the latest answer only");
}
if (aspenRows.length !== 1) throw new Error("aspen must keep a separate answer");
if (classifyUnsentFailure({ message: "Could not submit that answer.", status: 0 }) !== "offline") {
  throw new Error("a dropped response is retryable");
}
if (classifyUnsentFailure({ message: "Failed to fetch", status: 0 }) !== "offline") {
  throw new Error("a network throw is retryable");
}
if (classifyUnsentFailure({ error: "This item is not accepting responses.", status: 400 }) !== "closed") {
  throw new Error("a closed item must not be resent");
}
if (classifyUnsentFailure({ error: "Poll is closed.", pollClosed: true, status: 409 }) !== "closed") {
  throw new Error("a closed poll must not be resent");
}
if (classifyUnsentFailure({ message: "Choose an answer before submitting.", status: 0 }) !== "rejected") {
  throw new Error("validation is not an outbox item");
}
const open = {
  live_session_id: 8,
  active_questions: [{ id: 3, status: "active", can_submit: true, prompt: { id: 15 } }],
};
const closed = {
  live_session_id: 8,
  active_questions: [{ id: 3, status: "closed", prompt: { id: 15 } }],
};
const landed = {
  live_session_id: 8,
  prompt: { id: 15 },
  my_response: { response: { choice: "B" } },
};
if (unsentResendDecision(null, mapleRows[0]) !== "wait") throw new Error("wait until state");
if (unsentResendDecision(open, mapleRows[0]) !== "send") throw new Error("open question sends");
if (unsentResendDecision(closed, mapleRows[0]) !== "closed") throw new Error("closed does not send");
if (unsentResendDecision(landed, mapleRows[0]) !== "landed") throw new Error("landed is dropped");
if (unsentResendDecision({ ...open, live_session_id: 99 }, mapleRows[0]) !== "forget") {
  throw new Error("a new session drops the old answer");
}
if (unsentResendDecision({ live_session_id: 8, prompt: { id: 15 }, poll_closed: true }, mapleRows[0]) !== "closed") {
  throw new Error("poll closed does not send");
}
markUnsentClosed(store, mapleRows[0]);
if (listUnsentAnswers(store, maple).length !== 0) throw new Error("closed answers leave the send list");
const held = listUnsentAnswers(store, maple, { includeHeld: true });
if (!held[0] || !held[0].doNotSend || held[0].response.choice !== "B") {
  throw new Error("the closed note still has the typed answer");
}
if (unsentResendDecision(open, held[0]) !== "closed") throw new Error("doNotSend never sends");
forgetUnsentAnswer(store, { studentId: 9, visitToken: "visit-aspen", promptId: 15 });
if (listUnsentAnswers(store, aspen).length !== 0) throw new Error("forget drops aspen only");
if (listUnsentAnswers(store, maple, { includeHeld: true }).length !== 1) {
  throw new Error("forgetting aspen must leave maple");
}
if (UNSENT_ANSWER_COPY !== "Saved, will send when you're back online") {
  throw new Error("calm copy drifted");
}
console.log("ok");
"""
        done = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=LMS_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
        self.assertIn("ok", done.stdout)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited backend suite."""
    suite = unittest.TestSuite()
    names = [
        name
        for name, value in vars(UnsentAnswerServerTests).items()
        if name.startswith("test_") and callable(value)
    ]
    suite.addTests(UnsentAnswerServerTests(name) for name in sorted(names))
    suite.addTests(loader.loadTestsFromTestCase(UnsentAnswerClientTests))
    return suite


if __name__ == "__main__":
    unittest.main()
