#!/usr/bin/env python3
"""MCK-178: seeded rank items never show a currency ``$`` (it renders as math).

- the catalogue has no unbalanced or currency-style ``$``;
- the builder writes money in words;
- a fixed catalogue reaches the stored bank rows and unedited class imports
  (never a teacher-edited one), including through the staff deck load.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

import rank_bank_seed as seed  # noqa: E402
import test_rank_bank_seed as seed_tests  # noqa: E402
from question_math import format_math_html  # noqa: E402

#: A ``$`` not escaped as ``\\$``.
_BARE_DOLLAR = re.compile(r"(?<!\\)\$")
#: Currency style: ``$`` before a digit, or a lone ``$`` word ("of $)").
_CURRENCY = re.compile(r"(?<!\\)\$\s*\d|(?:^|[\s(])\$(?:[\s).,]|$)")
CHANGED = {
    "MCR3U": {
        "MCR3U-M2-cts-max-profit",
        "MCR3U-M4-annuity-interest",
        "MCR3U-M4-present-value",
        "MCR3U-M4-rank-interest-rates",
        "MCR3U-M4-solve-for-rate",
        "MCR3U-M4-two-stage-compound",
    },
    "MCF3M": {
        "MCF3M-M8-annuity-interest",
        "MCF3M-M8-present-value",
        "MCF3M-M8-rank-interest-rates",
        "MCF3M-M8-stereo-payment",
        "MCF3M-M8-two-stage-compound",
    },
}
OLD_PROFIT_STEM = (
    "P(x) = −5x² + 550x − 5000 is profit (thousands of $) when x thousand $ is spent on "
    "advertising. Put the completing-the-square steps in order to find the maximum profit."
)
OLD_PROFIT_OPTION = "Maximum profit is $10 125 000 when $55 000 is spent on advertising"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k), *_strings(v)]]
    return []


def _shown_together(payload: dict[str, Any]) -> list[str]:
    """Text the renderer sees in one host: the stem plus the option preview."""
    labels = [str(row.get("label") or "") for row in payload.get("rank_options") or []]
    return [
        " · ".join([str(payload.get("text") or ""), *labels]),
        " · ".join([str(payload.get("stem_html") or ""), *(payload.get("options") or [])]),
    ]


def _builder() -> Any:
    spec = importlib.util.spec_from_file_location("build_rank_items", REPO_ROOT / "scripts" / "build_rank_items.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class SeedDollarLintTests(unittest.TestCase):
    """Fails when a seeded rank item has an unbalanced or currency ``$``."""

    def test_no_currency_or_unbalanced_dollar(self) -> None:
        seen = 0
        for code in ("MCR3U", "MCF3M"):
            _v, items = seed.load_rank_catalogue(code)
            for item in items:
                payload = item["payload"]
                for text in _strings(payload) + _shown_together(payload):
                    seen += 1
                    where = f"{item['id']}: {text[:90]!r}"
                    self.assertIsNone(_CURRENCY.search(text), where)
                    self.assertEqual(len(_BARE_DOLLAR.findall(text)) % 2, 0, where)
        self.assertGreater(seen, 600)

    def test_changed_items_render_without_math(self) -> None:
        """The money items have no TeX: no math span anywhere they are shown."""
        for code, ids in CHANGED.items():
            _v, items = seed.load_rank_catalogue(code)
            by_id = {item["id"]: item for item in items}
            self.assertEqual(ids - set(by_id), set(), code)
            for cid in ids:
                payload = by_id[cid]["payload"]
                for text in _strings(payload) + _shown_together(payload):
                    self.assertNotIn("math-latex", format_math_html(text), f"{cid}: {text[:80]!r}")
                self.assertIn("dollar", json.dumps(payload, ensure_ascii=False), cid)

    def test_lint_catches_the_old_profit_text(self) -> None:
        self.assertIsNotNone(_CURRENCY.search(OLD_PROFIT_STEM))
        self.assertIsNotNone(_CURRENCY.search(OLD_PROFIT_OPTION))
        self.assertEqual(len(_BARE_DOLLAR.findall("cost $5")) % 2, 1)
        self.assertIsNone(_CURRENCY.search("Solve $x^2 = 4$ for x"))
        self.assertIn("math-latex", format_math_html(OLD_PROFIT_STEM))  # the MCK-178 bug


class MoneyWordsTests(unittest.TestCase):
    """scripts/build_rank_items.py writes money in words."""

    def test_rewrites(self) -> None:
        words = _builder().money_words
        self.assertEqual(
            words(OLD_PROFIT_STEM),
            OLD_PROFIT_STEM.replace("(thousands of $)", "(thousands of dollars)").replace(
                "thousand $ is", "thousand dollars is"
            ),
        )
        self.assertEqual(
            words(OLD_PROFIT_OPTION),
            "Maximum profit is 10 125 000 dollars when 55 000 dollars is spent on advertising",
        )
        self.assertEqual(words("Interest = FV − 50($650) ≈ $27 337.37"), "Interest = FV − 50(650) ≈ 27 337.37 dollars")
        self.assertEqual(words("PV ≈ $5586.46"), "PV ≈ 5586.46 dollars")
        self.assertEqual(words("A $1300 stereo is bought"), "A 1300-dollar stereo is bought")
        self.assertEqual(words("Rico wants $15 000 in 10 years"), "Rico wants 15 000 dollars in 10 years")
        self.assertEqual(words("y = $x$"), "y = $x$")  # no currency: untouched


class ExistingRowsGetTheFixTests(unittest.TestCase):
    """Rows seeded before MCK-178 (old text) are fixed in place."""

    _class_for = seed_tests.RankBankImportPublishTests._class_for
    tearDown = seed_tests.RankBankImportPublishTests.tearDown

    def setUp(self) -> None:
        self._class_for("MCR3U")
        self.old_dir = Path(self.tmp.name) / "old-catalogue"
        self.old_dir.mkdir()
        doc = json.loads((seed.RANK_ITEMS_DIR / "MCR3U.json").read_text(encoding="utf-8"))
        for item in doc["items"]:
            if item["id"] == "MCR3U-M2-cts-max-profit":
                payload = item["payload"]
                payload["text"] = OLD_PROFIT_STEM
                payload["stem_html"] = f"<p>{OLD_PROFIT_STEM}</p>"
                for field in ("options", "choices"):
                    payload[field][1] = OLD_PROFIT_OPTION
                payload["rank_options"][1]["label"] = OLD_PROFIT_OPTION
        (self.old_dir / "MCR3U.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        # What prod holds: the pre-MCK-178 catalogue, seeded.
        seed.seed_rank_bank(self.school, self.library_id, "MCR3U", items_dir=self.old_dir)
        self.qid = int(
            self.school.conn.execute(
                "SELECT id FROM questions WHERE import_key = ?", (seed.rank_item_key("MCR3U-M2-cts-max-profit"),)
            ).fetchone()["id"]
        )

    def _import(self, page: int) -> dict[str, Any]:
        placed = self.school.import_mc_to_class_playlist(
            self.class_id, "M2", "C4", self.qid, library_id=self.library_id, page_number=page, stage="round"
        )
        self.assertEqual(placed["item"]["text"], OLD_PROFIT_STEM)
        return placed

    def _placement(self, placement_id: int) -> dict[str, Any]:
        row = self.school.conn.execute(
            "SELECT item_json FROM class_live_playlist_placements WHERE id = ?", (placement_id,)
        ).fetchone()
        return json.loads(row["item_json"])

    def _bank_row(self) -> dict[str, Any]:
        return json.loads(
            self.school.conn.execute("SELECT payload_json FROM questions WHERE id = ?", (self.qid,)).fetchone()[0]
        )

    def test_reseed_fixes_bank_row_and_unedited_import_only(self) -> None:
        plain = self._import(1)
        edited = self._import(2)
        item = self._placement(int(edited["id"]))
        item["text"] = "My own wording: profit is in thousands of $."
        self.school.conn.execute(
            "UPDATE class_live_playlist_placements SET item_json = ? WHERE id = ?",
            (json.dumps(item, ensure_ascii=False), int(edited["id"])),
        )
        before_plain = self._placement(int(plain["id"]))
        summary = seed.seed_rank_bank(self.school, self.library_id, "MCR3U")
        self.assertEqual((summary["updated"], summary["imports_updated"], summary["imports_kept_edited"]), (1, 1, 1))
        bank = self._bank_row()
        self.assertNotIn("$", bank["text"])
        self.assertTrue(seed.row_is_pristine(bank["text"][:80], bank))
        fixed = self._placement(int(plain["id"]))
        self.assertIn("(thousands of dollars)", fixed["text"])
        self.assertEqual(fixed["prompt"], fixed["text"])
        self.assertNotIn("$", json.dumps(fixed, ensure_ascii=False))
        self.assertEqual(
            fixed["rank_options"][1],
            {"id": "o2", "label": "Maximum profit is 10 125 000 dollars when 55 000 dollars is spent on advertising"},
        )
        self.assertEqual(fixed["rank_key"], before_plain["rank_key"])
        # Class-owned fields are kept.
        for field in set(before_plain) - set(seed.IMPORT_CONTENT_FIELDS):
            self.assertEqual(fixed.get(field), before_plain[field], field)
        self.assertEqual(self._placement(int(edited["id"]))["text"], "My own wording: profit is in thousands of $.")
        again = seed.seed_rank_bank(self.school, self.library_id, "MCR3U")
        self.assertEqual((again["updated"], again["imports_updated"]), (0, 0))

    def test_deck_load_applies_the_fix_without_a_bank_search(self) -> None:
        plain = self._import(1)
        rv = self.client.get(f"/api/staff/class/{self.class_id}/live-lessons/M2/C4/deck")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertIn("(thousands of dollars)", self._placement(int(plain["id"]))["text"])
        self.assertNotIn("$", self._bank_row()["text"])
        body = rv.get_data(as_text=True)
        self.assertNotIn("thousands of $", body)
        self.assertIn("(thousands of dollars)", body)


if __name__ == "__main__":
    unittest.main()
