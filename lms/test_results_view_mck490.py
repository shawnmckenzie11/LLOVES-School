#!/usr/bin/env python3
"""MCK-490: student result privacy, one toggle postcard, live teacher bars.

The student graph is counts and percents. Show answer and bar order are
one class-wide postcard. Teacher bars move with the answered counter,
before Close.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_news_wire import NewsAudience, event_visible, log_for, reset_logs  # noqa: E402

# Keys that name a person, a pick, or the answer. The answer keys are
# allowed only while Show answer is on.
_PRIVATE = {
    "name",
    "names",
    "student_id",
    "student_name",
    "email",
    "codename",
    "picks",
    "status_board",
    "member_answers",
    "vote_summary",
    "last_submitter",
    "roster",
    "attendees",
    "responses",
    "who",
    "prompt",
    "prompt_ref",
    "prompt_id",
    "source",
    "response_seq",
}
_ANSWER = {
    "correct",
    "correct_answer",
    "correct_value",
    "correct_ids",
    "key",
    "answer_key",
    "tolerance",
    "tolerance_kind",
}
_CHOICE_KEYS = {"id", "label", "count", "pct", "other", "correct"}


def _keys(value: Any, found: set[str] | None = None) -> set[str]:
    """Collect every dict key under ``value``.

    Args:
        value: JSON-like student results.
        found: Accumulator. Created when omitted.

    Returns:
        The set of keys.
    """
    bag = found if found is not None else set()
    if isinstance(value, dict):
        for key, item in value.items():
            bag.add(str(key))
            _keys(item, bag)
    elif isinstance(value, list):
        for item in value:
            _keys(item, bag)
    return bag


def _function_source(js: str, name: str) -> str:
    """Return one top-level function, including its body.

    Args:
        js: Script text.
        name: Function name.
    """
    start = js.index(f"\nfunction {name}(") + 1
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        char = js[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


class ResultsViewTests(unittest.TestCase):
    """Student payload, the toggle postcard, and bars before Close."""

    def setUp(self) -> None:
        """One live class, two seats, and a signed-in teacher."""

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["S1", "S2"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.session_id = int(
            self.school.start_live_class_session(
                self.class_id, int(teacher["id"])
            )["id"]
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_id="round_1",
            page_number=4,
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        self.ids = [int(row["id"]) for row in rows]
        for student_id in self.ids:
            self.school.join_live_class_session(
                self.session_id, student_id, codename=f"S{student_id}"
            )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        """Close sqlite and drop the temp news tape."""

        logging.disable(logging.NOTSET)
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _add(self, **kwargs: Any) -> dict[str, Any]:
        """Add one question on the current page and return its lifecycle row."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            page_number=4,
            stage="round",
            **kwargs,
        )
        token = str(placed.get("item_id") or "")
        return next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == token
        )

    def _student_item(self, student_id: int, live_item_id: int) -> dict[str, Any]:
        """Return one student's copy of a lifecycle item."""

        payload = self.school.student_live_items_payload(self.session_id, student_id)
        for bucket in ("active_questions", "closed_results", "live_items"):
            for row in payload.get(bucket) or []:
                if int(row.get("id") or 0) == int(live_item_id):
                    return row
        self.fail(f"item {live_item_id} missing from the student payload")

    def _tip(self) -> int:
        """Highest postcard id, or 0."""

        rows = log_for(self.school.data_dir).since(self.session_id, 0)
        return int(rows[-1]["id"]) if rows else 0

    def test_student_results_omit_private_keys_until_reveal(self) -> None:
        """Counts and percents only. The answer appears only while revealed."""

        mc = self._add(
            question_type="mc",
            text="Pick one",
            options=["kite", "128", "moth", "lark"],
            correct_index=0,
        )
        numeric = self._add(
            question_type="numeric",
            text="A number",
            correct_answer="3.14159",
            tolerance=0.01,
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(mc["id"]), publish_mode="individual"
        )
        self.school.publish_live_session_item(
            self.session_id, int(numeric["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[0], {"choice": "B"}
        )
        hidden = self._student_item(self.ids[0], int(mc["id"]))
        results = hidden.get("results") or {}
        self.assertEqual(_keys(results) & (_PRIVATE | _ANSWER), set())
        for choice in results.get("choices") or []:
            self.assertTrue(set(choice) <= _CHOICE_KEYS)
            self.assertIn("count", choice)
            self.assertIn("pct", choice)
        self.assertEqual(
            {row["label"]: row["count"] for row in results["choices"]}.get("128"),
            1,
        )

        self.school.update_live_session_item_settings(
            self.session_id, int(mc["id"]), show_answer=True
        )
        shown = self._student_item(self.ids[0], int(mc["id"]))
        shown_keys = _keys(shown.get("results"))
        self.assertIn("correct", shown_keys)
        self.assertEqual(shown_keys & _PRIVATE, set())
        marked = [
            row
            for row in (shown.get("results") or {}).get("choices") or []
            if row.get("correct")
        ]
        self.assertEqual([row.get("label") for row in marked], ["kite"])

        number_row = self.school.get_live_session_item(
            self.session_id, int(numeric["id"])
        )
        number_prompt = self.school._prompt_for_live_item(number_row)
        assert number_prompt is not None
        self.school.submit_live_prompt_response(
            int(number_prompt["id"]), self.ids[0], {"value": 3.14}
        )
        open_numbers = self._student_item(self.ids[0], int(numeric["id"]))
        self.assertEqual(_keys(open_numbers.get("results")) & (_PRIVATE | _ANSWER), set())
        self.assertNotIn("3.14159", json.dumps(open_numbers.get("results")))

        self.school.update_live_session_item_settings(
            self.session_id, int(numeric["id"]), show_answer=True
        )
        revealed = self._student_item(self.ids[0], int(numeric["id"]))
        self.assertEqual(str(revealed["results"].get("correct_value")), "3.14159")
        self.assertEqual(_keys(revealed.get("results")) & _PRIVATE, set())
        self.school.update_live_session_item_settings(
            self.session_id, int(numeric["id"]), show_answer=False
        )
        concealed = self._student_item(self.ids[0], int(numeric["id"]))
        self.assertEqual(
            _keys(concealed.get("results")) & (_PRIVATE | _ANSWER), set()
        )
        self.assertNotIn("3.14159", json.dumps(concealed.get("results")))

    def test_toggle_emits_one_class_wide_postcard(self) -> None:
        """Show answer and order are one event. The answer rides reveal only."""

        mc = self._add(
            question_type="mc",
            text="Pick one",
            options=["kite", "128", "moth", "lark"],
            correct_index=0,
        )
        self.school.publish_live_session_item(
            self.session_id, int(mc["id"]), publish_mode="individual"
        )
        live_id = int(mc["id"])
        cursor = self._tip()
        shown = self.staff.patch(
            f"/api/live-sessions/{self.session_id}/items/{live_id}/settings",
            json={"show_answer": True, "results_order": "ranked"},
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        fresh = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, cursor)
            if row.get("type") == "results_view"
        ]
        self.assertEqual(len(fresh), 1)
        event = fresh[0]
        self.assertEqual(
            set(event) - {"id"},
            {"type", "item_id", "order", "reveal", "answer"},
        )
        self.assertEqual(event.get("order"), "ranked")
        self.assertEqual(event.get("reveal"), 1)
        self.assertEqual(event.get("answer"), "kite")
        self.assertNotIn("student_id", event)
        self.assertFalse(
            any(
                row.get("type") == "state_seq"
                for row in log_for(self.school.data_dir).since(self.session_id, cursor)
            )
        )
        for student_id in self.ids:
            self.assertTrue(
                event_visible(event, NewsAudience("student", student_id=student_id))
            )

        cursor = self._tip()
        ordered = self.staff.patch(
            f"/api/live-sessions/{self.session_id}/items/{live_id}/settings",
            json={"results_order": "default"},
        )
        self.assertEqual(ordered.status_code, 200, ordered.get_data(as_text=True))
        order_events = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, cursor)
            if row.get("type") == "results_view"
        ]
        self.assertEqual(len(order_events), 1)
        self.assertNotIn("answer", order_events[0])
        self.assertEqual(order_events[0].get("reveal"), 1)
        self.assertEqual(order_events[0].get("order"), "default")

        cursor = self._tip()
        hidden = self.staff.patch(
            f"/api/live-sessions/{self.session_id}/items/{live_id}/settings",
            json={"show_answer": False},
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_data(as_text=True))
        hide_events = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, cursor)
            if row.get("type") == "results_view"
        ]
        self.assertEqual(len(hide_events), 1)
        self.assertNotIn("answer", hide_events[0])
        self.assertEqual(hide_events[0].get("reveal"), 0)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        handler = portal.split('event.type === "results_view"')[1].split(
            'event.type === "surface_show"'
        )[0]
        self.assertIn("applyResultsView(event)", handler)
        self.assertIn("return;", handler)
        self.assertNotIn("void tick()", handler)
        self.assertNotIn("scheduleNewsState()", handler)

    def test_teacher_bars_move_before_close(self) -> None:
        """The light poll carries bar counts while the question is still open."""

        mc = self._add(
            question_type="mc",
            text="Pick one",
            options=["kite", "128", "moth", "lark"],
            correct_index=0,
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(mc["id"]), publish_mode="individual"
        )
        live_id = int(mc["id"])
        self.assertEqual(
            str(self.school.get_live_session_item(self.session_id, live_id).get("status")),
            "active",
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[0], {"choice": "B"}
        )
        state = self.school.get_live_session_state(self.session_id, light=True)
        counts = state.get("lifecycle_response_counts") or {}
        tallies = state.get("lifecycle_choice_tallies") or {}
        self.assertEqual(int(counts.get(live_id) or counts.get(str(live_id)) or 0), 1)
        tally = tallies.get(live_id) or tallies.get(str(live_id))
        self.assertIsInstance(tally, dict)
        assert isinstance(tally, dict)
        choices = tally.get("choices") or []
        self.assertEqual(sum(int(row.get("count") or 0) for row in choices), 1)
        marked = [row for row in choices if row.get("correct")]
        self.assertEqual([row.get("label") for row in marked], ["kite"])
        self.assertEqual(
            self.school.get_live_session_item(self.session_id, live_id).get("status"),
            "active",
        )
        self.assertEqual(_keys(tally) & {"name", "student_id", "picks", "email"}, set())

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_light_tick_paints_teacher_bars_without_a_results_fetch(self) -> None:
        """The counter's event inserts bars. Close and /results are not involved."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        src = "\n".join(
            _function_source(js, name)
            for name in (
                "lifecycleRowIsRank",
                "adoptLifecycleResponseCounts",
                "adoptLifecycleChoiceTallies",
                "paintLifecycleBarsInPlace",
                "paintLifecycleProgressInPlace",
            )
        )
        harness = r"""
const vm = require("vm");
const src = require("fs").readFileSync(0, "utf8");
class Node {
  constructor(cls, attrs, text) {
    this.className = cls;
    this.attrs = Object.assign({}, attrs);
    this.children = [];
    this.textContent = text || "";
    this.parent = null;
  }
  getAttribute(key) { return this.attrs[key] ?? null; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  matches(sel) {
    const card = sel.match(/^\.([\w-]+)\[data-live-item-id="(\d+)"\]$/);
    if (card) {
      return this.className.split(" ").includes(card[1]) && this.attrs["data-live-item-id"] === card[2];
    }
    const simple = sel.match(/^\.([\w-]+)$/);
    return Boolean(simple) && this.className.split(" ").includes(simple[1]);
  }
  querySelector(sel) {
    for (const child of this.children) {
      if (child.matches(sel)) return child;
      const deep = child.querySelector(sel);
      if (deep) return deep;
    }
    return null;
  }
  insertAdjacentHTML(where, html) {
    const node = new Node("mc-results-graph");
    node.textContent = html;
    node.parent = this.parent;
    const index = this.parent.children.indexOf(this);
    this.parent.children.splice(where === "beforebegin" ? index : index + 1, 0, node);
  }
}
const host = new Node("live-question-list");
const card = new Node("live-question-card", { "data-live-item-id": "7" });
const progress = new Node("live-question-progress", {}, "0 / 4 answered");
card.children.push(progress);
progress.parent = card;
card.parent = host;
host.children.push(card);
let fetched = 0;
const ctx = {
  $: (id) => (id === "live-question-list" ? host : null),
  lifecycleResults: new Map([[7, { response_count: 0, eligible_count: 4, tally: {} }]]),
  lastLiveItems: [{ id: 7, response_mode: "individual", item: { type: "mc" } }],
  mcGraphItemKey: () => "item:7",
  mcGraphPref: () => ({ order: "default", showKey: false }),
  individualLifecycleResultsHtml: (tally) => {
    const bits = tally.choices.map((row) => `${row.label}:${row.count}${row.correct ? "*" : ""}`).join(",");
    return `<section class="mc-results-graph">${bits}</section>`;
  },
  refreshLifecycleResults: async () => { fetched += 1; },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
ctx.adoptLifecycleResponseCounts(
  { "7": 1 },
  {},
  { "7": { kind: "mc", response_count: 1, choices: [
    { id: "A", label: "kite", count: 0, pct: 0, correct: true },
    { id: "B", label: "128", count: 1, pct: 100, correct: false },
  ] } }
);
const graph = card.querySelector(".mc-results-graph");
console.log(JSON.stringify({
  progress: progress.textContent,
  graph: graph && graph.textContent,
  fetched,
}));
"""
        result = subprocess.run(
            ["node", "-e", harness],
            input=src,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out["progress"], "1 / 4 answered")
        self.assertIn("128:1", out["graph"])
        self.assertIn("kite:0*", out["graph"])
        self.assertEqual(out["fetched"], 0)



class SharePercentTests(unittest.TestCase):
    """One count always rounds to one percent, on both payloads."""

    def test_equal_counts_share_a_percent(self) -> None:
        """1, 3, 1, 1 of 6 is 17, 50, 17, 17, not a last row nudged to 16."""

        from live_mc import student_results_graph_payload, teacher_live_bar_tally

        raw = {
            "kind": "mc",
            "choices": [
                {"id": "A", "label": "north", "count": 1, "pct": 17},
                {"id": "B", "label": "128", "count": 3, "pct": 50},
                {"id": "C", "label": "4096", "count": 1, "pct": 17},
                {"id": "D", "label": "east", "count": 1, "pct": 16},
            ],
        }
        student = student_results_graph_payload(raw, reveal=False)
        teacher = teacher_live_bar_tally(raw)
        self.assertEqual([row["pct"] for row in student["choices"]], [17, 50, 17, 17])
        self.assertEqual([row["pct"] for row in teacher["choices"]], [17, 50, 17, 17])

    def test_student_payload_keeps_the_answer_total(self) -> None:
        """The class answer total stays on the student tally.

        Waiting-room and live cards read ``response_count`` to know
        answers arrived. Names, picks, and the answer fingerprint stay off.
        """

        from live_mc import student_results_graph_payload

        raw = {
            "kind": "numeric",
            "response_count": 1,
            "responded": 1,
            "choices": [{"id": "7", "label": "7", "count": 1, "pct": 100}],
            "other_count": 0,
            "prompt_ref": "spark",
            "response_seq": "abc",
        }
        student = student_results_graph_payload(raw, reveal=False)
        self.assertEqual(student["response_count"], 1)
        self.assertNotIn("prompt_ref", student)
        self.assertNotIn("response_seq", student)


if __name__ == "__main__":
    unittest.main()
