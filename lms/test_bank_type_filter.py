#!/usr/bin/env python3
"""MCK-170: filter bank questions by type on mc-search, with per-type counts."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from shutil import which

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from bank_question_types import (  # noqa: E402
    bank_question_type,
    filter_by_type,
    parse_type_filter,
    type_counts,
)
from school_db import _now  # noqa: E402
import test_live_bank_tab  # noqa: E402

NODE = which("node")


class TypeClassifierTests(unittest.TestCase):
    """Pure type mapping, aliases and counts."""

    def test_parse_type_filter_aliases(self) -> None:
        """Empty/all mean no filter; aliases map; unknown is None."""
        for raw in (None, "", "all", " ANY "):
            self.assertEqual(parse_type_filter(raw), "")
        self.assertEqual(parse_type_filter("order"), "rank")
        self.assertEqual(parse_type_filter("Rank"), "rank")
        self.assertEqual(parse_type_filter("poll"), "open")
        self.assertEqual(parse_type_filter("tf"), "true_false")
        self.assertEqual(parse_type_filter("numerical_question"), "numeric")
        self.assertIsNone(parse_type_filter("essay-ish"))

    def test_bank_question_type(self) -> None:
        """Rank wins, then item_type, then poll and True/False options, else mc."""
        self.assertEqual(bank_question_type({"type": "mc"}, payload={"rank_options": ["a"]}), "rank")
        self.assertEqual(bank_question_type({"type": "mc"}, payload={"type": "order"}), "rank")
        self.assertEqual(
            bank_question_type({}, item_type="numerical_question", payload={}), "numeric"
        )
        self.assertEqual(bank_question_type({"type": "poll"}, payload={}), "open")
        self.assertEqual(
            bank_question_type({"type": "mc", "options": ["True", "False"]}, payload={}),
            "true_false",
        )
        self.assertEqual(
            bank_question_type({"type": "mc", "options": ["True", "False", "Maybe"]}, payload={}),
            "mc",
        )

    def test_counts_order_and_filter(self) -> None:
        """Counts follow chip order, skip zero, and the filter keeps only one type."""
        items = [
            {"question_type": "open"},
            {"question_type": "mc"},
            {"question_type": "rank"},
            {"question_type": "mc"},
        ]
        self.assertEqual(
            type_counts(items),
            [
                {"type": "mc", "label": "Multiple choice", "count": 2},
                {"type": "rank", "label": "Rank", "count": 1},
                {"type": "open", "label": "Open-ended", "count": 1},
            ],
        )
        self.assertEqual(len(filter_by_type(items, "mc")), 2)
        self.assertEqual(filter_by_type(items, ""), items)


class BankTypeFilterEndpointTests(unittest.TestCase):
    """mc-search ``type`` param and ``type_counts`` over a seeded live bank."""

    _login_staff = test_live_bank_tab.LiveBankTabTests._login_staff
    tearDown = test_live_bank_tab.LiveBankTabTests.tearDown

    def setUp(self) -> None:
        """Live bank rows on M1 and a separate M2-only bank."""
        test_live_bank_tab.LiveBankTabTests.setUp(self)
        self._add("Factor x^2 - 9", ["(x-3)(x+3)", "(x-9)(x+1)"])
        self._add("Expand (x+1)^2", ["x^2+2x+1", "x^2+1", "2x+1"])
        self._add("Every function is a relation", ["True", "False"])
        # MCK-169 is not merged: no staff path yet stores a rank row that
        # search can import. Simulate one by marking a stored MC as rank.
        rank = self._add("Order the factor steps", ["Common", "Difference", "Check"])
        payload = dict(rank["payload"])
        payload["type"] = "rank"
        payload["rank_options"] = [{"label": label} for label in payload.get("options") or []]
        self.school.update_library_question_payload(
            self.library_id, int(rank["id"]), title=str(rank.get("title") or ""), payload=payload
        )
        cur = self.school.conn.execute(
            """
            INSERT INTO question_banks (library_id, import_key, title, settings_json, created_at)
            VALUES (?, 'bank:m2', 'Module 2 pool', '{}', ?)
            """,
            (self.library_id, _now()),
        )
        m2_bank = int(cur.lastrowid)
        for key, stem, options in (
            ("m2-tf", "Vertex form shows the vertex", ["True", "False"]),
            ("m2-mc", "Vertex of y=(x-2)^2", ["(2,0)", "(-2,0)"]),
        ):
            self.school.conn.execute(
                """
                INSERT INTO questions (bank_id, import_key, item_type, title, payload_json, created_at)
                VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
                """,
                (
                    m2_bank,
                    key,
                    stem,
                    json.dumps(
                        {
                            "stem_html": stem,
                            "choices": [
                                {"id": str(i), "html": text, "correct": i == 0}
                                for i, text in enumerate(options)
                            ],
                        }
                    ),
                    _now(),
                ),
            )
        self.school.conn.commit()
        self.school._ensure_module_bank_link(self.library_id, 2, m2_bank)

    def _add(self, stem: str, options: list[str]) -> dict:
        """POST one M1 MC (answer A) to the live bank and return the question."""
        res = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": stem,
                "options": options,
                "correct_answer": "A",
            },
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return res.get_json()["question"]

    def _search(self, scope: str = "M1", **params: str) -> dict:
        """GET mc-search and return its JSON."""
        res = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/{scope}/mc-search",
            query_string=params,
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return res.get_json()

    @staticmethod
    def _counts(body: dict) -> dict[str, int]:
        return {row["type"]: row["count"] for row in body["type_counts"]}

    @staticmethod
    def _stems(body: dict) -> list[str]:
        return sorted(str(item.get("text") or item.get("question_title") or "") for item in body["items"])

    def test_no_param_matches_all_and_reports_counts(self) -> None:
        """Without ``type`` the result is unchanged; ``type=`` and ``type=all`` match it."""
        base = self._search()
        for extra in ({"type": ""}, {"type": "all"}):
            same = self._search(**extra)
            self.assertEqual(same["items"], base["items"])
            self.assertEqual((same["total"], same["filtered"]), (base["total"], base["filtered"]))
        self.assertEqual(base["type"], "")
        self.assertEqual(base["filtered"], base["total"])
        self.assertEqual(self._counts(base), {"mc": 2, "true_false": 1, "rank": 1})
        self.assertEqual(sum(self._counts(base).values()), base["filtered"])
        self.assertEqual(
            [row["label"] for row in base["type_counts"]],
            ["Multiple choice", "True/false", "Rank"],
        )

    def test_each_type_filters(self) -> None:
        """Each type keeps only its rows; counts still describe every type."""
        base = self._search()
        seen = 0
        for qtype, want in (("mc", 2), ("true_false", 1), ("rank", 1)):
            body = self._search(type=qtype)
            self.assertEqual(body["type"], qtype)
            self.assertEqual(body["filtered"], want, qtype)
            self.assertEqual(len(body["items"]), want, qtype)
            self.assertTrue(all(item["question_type"] == qtype for item in body["items"]))
            self.assertEqual(body["total"], base["total"])
            self.assertEqual(body["type_counts"], base["type_counts"])
            seen += want
        self.assertEqual(seen, base["filtered"])
        rank = self._search(type="order")
        self.assertEqual(self._stems(rank), ["Order the factor steps"])
        self.assertEqual(self._search(type="numeric")["items"], [])

    def test_open_type_with_warmup_kind(self) -> None:
        """Warmup polls classify as Open-ended; Kind and type AND together."""
        warm = self._search(kind="warmup")
        counts = self._counts(warm)
        self.assertGreater(counts.get("open", 0), 0)
        self.assertNotIn("rank", counts)
        body = self._search(kind="warmup", type="open")
        self.assertEqual(body["filtered"], counts["open"])
        self.assertTrue(all(item["question_type"] == "open" for item in body["items"]))
        self.assertEqual(self._search(kind="warmup", type="rank")["items"], [])

    def test_combines_with_module(self) -> None:
        """M2 counts and filters only M2 rows."""
        m2 = self._search("M2")
        self.assertEqual(self._counts(m2), {"mc": 1, "true_false": 1})
        tf = self._search("M2", type="true_false")
        self.assertEqual(self._stems(tf), ["Vertex form shows the vertex"])
        self.assertEqual(self._search("M2", type="rank")["items"], [])

    def test_combines_with_search(self) -> None:
        """Counts follow the search text; type narrows within it."""
        hit = self._search(q="factor")
        self.assertEqual(self._counts(hit), {"mc": 1, "rank": 1})
        self.assertEqual(hit["filtered"], 2)
        only = self._search(q="factor", type="rank")
        self.assertEqual(self._stems(only), ["Order the factor steps"])
        self.assertEqual(only["filtered"], 1)
        self.assertEqual(self._search(q="factor", type="true_false")["items"], [])

    def test_course_scope_counts(self) -> None:
        """Course Wide counts span modules and the filter applies across them."""
        course = self._search("course")
        self.assertEqual(self._counts(course), {"mc": 3, "true_false": 2, "rank": 1})
        tf = self._search("course", type="true_false")
        self.assertEqual(
            self._stems(tf), ["Every function is a relation", "Vertex form shows the vertex"]
        )
        hit = self._search("course", q="vertex", type="mc")
        self.assertEqual(self._stems(hit), ["Vertex of y=(x-2)^2"])
        self.assertEqual(self._counts(hit), {"mc": 1, "true_false": 1})

    def test_unknown_type_is_400(self) -> None:
        """An unknown type is refused instead of silently returning everything."""
        res = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search?type=bogus"
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json()["ok"])


class TypeChipWiringTests(unittest.TestCase):
    """One chip component, mounted in both bank views."""

    def test_both_views_mount_the_chip_row(self) -> None:
        """Question banks tab and Import from bank share bank_type_chips.js."""
        picker = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        banks = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        for src in (picker, banks):
            self.assertIn('import { mountTypeChips } from "/static/bank_type_chips.js"', src)
        self.assertIn('view: "question-banks"', banks)
        self.assertIn('params.set("type", qtype)', banks)
        self.assertIn("&type=${encodeURIComponent(qtype)}", picker)
        self.assertIn('<div data-bank-type-chips></div>\n    </div>', picker)
        self.assertIn('id="live-bank-types" data-bank-type-chips', course)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        row = css.split("body.staff-shell .bank-type-chip-row {", 1)[1].split("}", 1)[0]
        self.assertIn("flex-wrap: nowrap", row)
        self.assertIn("overflow: hidden", row)

    @unittest.skipUnless(NODE, "node not installed")
    def test_chip_helper_node_cases(self) -> None:
        """bank_type_chips.test.mjs: model, fit with More, session storage."""
        proc = subprocess.run(
            [NODE, str(LMS_DIR / "static" / "bank_type_chips.test.mjs")],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)



if __name__ == "__main__":
    unittest.main()
