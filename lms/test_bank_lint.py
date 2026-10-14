#!/usr/bin/env python3
"""Bank linter (lms/bank_lint.py): checks, storage, and import hooks.

Fixtures are trimmed copies of prod rows from the 2026-10-14 bank audit
(question ids in ``bank-audit/report.md``).
"""

from __future__ import annotations

import base64
import gzip
import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import bank_lint  # noqa: E402
from bank_lint import ImageResolver, KatexChecker, lint_question  # noqa: E402


def _subtypes(findings) -> set[tuple[str, str]]:
    return {(f.code, f.subtype) for f in findings}


def _wiris(mml: str) -> str:
    gz = base64.b64encode(gzip.compress(mml.encode())).decode()
    return "https://www.wiris.net/demo/plugins/app/showimage?" + urllib.parse.urlencode({"mmlgz": gz})


class _Resolver(ImageResolver):
    """Offline resolver with a fixed set of good relative paths."""

    def __init__(self, good: set[str] | None = None) -> None:
        super().__init__(None)
        self.good = good or set()

    def _library_file(self, library_id: int, relpath: str) -> bool:
        return relpath in self.good


def lint(payload, **kw):
    kw.setdefault("image_resolver", _Resolver())
    return lint_question(payload, library_id=1, **kw)


class CheckTests(unittest.TestCase):
    """Pure payload checks."""

    def test_equatio_image_is_raw_equation_with_tex_proposal(self) -> None:
        """q 23021."""
        tex = r"T\left(t\right)=65\left(\frac{1}{2}\right)^{\frac{t}{22}}"
        src = "https://equatio-api.texthelp.com/svg/" + urllib.parse.quote(tex)
        found = lint({"stem_html": f'<p>Half-life model <img src="{src}"></p>'})
        hit = [f for f in found if f.subtype == "math_as_image_equatio"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].proposed, "$" + tex + "$")

    def test_wiris_is_dead_image_and_raw_equation(self) -> None:
        src = _wiris('<math xmlns="http://www.w3.org/1998/Math/MathML"><mfrac><mn>1</mn><mi>x</mi></mfrac></math>')
        found = lint({"stem_html": f'<p>Simplify <img class="Wirisformula" src="{src}"></p>'})
        subs = _subtypes(found)
        self.assertIn(("broken_image", "wiris_image_unavailable"), subs)
        self.assertIn(("raw_equation", "math_as_image_wiris"), subs)
        wiris = next(f for f in found if f.subtype == "math_as_image_wiris")
        self.assertEqual(wiris.proposed, r"$\frac{1}{x}$")

    def test_dead_gyazo_is_broken_image(self) -> None:
        found = lint({"stem_html": '<p>Graph <img src="https://i.gyazo.com/02d0c02e.png"></p>'})
        self.assertIn(("broken_image", "gyazo_image_unavailable"), _subtypes(found))

    def test_mirrored_remote_is_ok(self) -> None:
        resolver = _Resolver()
        resolver._memo[(1, "https://i.gyazo.com/ok.png")] = (True, "mirror cache ok")
        found = lint({"stem_html": '<p>Graph of f <img src="https://i.gyazo.com/ok.png"></p>'}, image_resolver=resolver)
        self.assertNotIn("broken_image", {f.code for f in found})

    def test_network_check_needs_image_content_type(self) -> None:
        resolver = ImageResolver(None, network=True, fetch=lambda u: (200, "text/plain"))
        self.assertFalse(resolver.check(1, "https://example.org/a.png")[0])
        resolver = ImageResolver(None, network=True, fetch=lambda u: (200, "image/png"))
        self.assertTrue(resolver.check(1, "https://example.org/a.png")[0])

    def test_two_dollar_amounts_parse_as_math(self) -> None:
        """q 23371; a single $500 is fine."""
        found = lint({"stem_html": "<p>Ann saves $500 and then spends $600 on a bike.</p>"})
        self.assertIn(("raw_equation", "currency_dollar_parsed_as_math"), _subtypes(found))
        self.assertEqual(lint({"stem_html": "<p>Ann saves $500 for a new bike today.</p>"}), [])

    def test_prose_exponents(self) -> None:
        """q 28709 (x2 - 3x + 5) and q 23298 (6x^2 - x - 2)."""
        self.assertIn(
            ("raw_equation", "x2_style_exponent"),
            _subtypes(lint({"stem_html": "<p>Factor x2 - 3x + 5 completely.</p>"})),
        )
        self.assertIn(
            ("raw_equation", "caret_in_prose"),
            _subtypes(lint({"stem_html": "<p>Factor 6x^2 - x - 2 completely.</p>"})),
        )
        self.assertEqual(lint({"stem_html": "<p>Factor $6x^2 - x - 2$ completely.</p>"}), [])

    def test_empty_img_tag(self) -> None:
        """q 23148."""
        found = lint({"stem_html": '<p>Which graph? <img alt=""></p>'})
        self.assertIn(("broken_image", "empty_img_tag"), _subtypes(found))

    def test_option_image_in_stem(self) -> None:
        """q 24684."""
        img = '<img src="$IMS-CC-FILEBASE$/g.png">'
        payload = {"stem_html": f"<p>Pick the graph {img}</p>", "choices": [{"html": img, "correct": True}, {"html": "B"}]}
        found = lint(payload, image_resolver=_Resolver({"web_resources/g.png"}))
        self.assertIn(("answer_in_stem", "option_image_in_stem"), _subtypes(found))

    def test_option_text_in_stem_and_rank_skip(self) -> None:
        stem = "<p>Solve 2x + 3 = 7. The answer is x = 2.5</p>"
        choices = [{"html": "x = 2.5", "correct": True}, {"html": "x = 5"}]
        found = lint({"stem_html": stem, "choices": choices})
        self.assertTrue(any(f.code == "answer_in_stem" for f in found))
        rank = {"stem_html": "<p>Order: first step, second step here</p>",
                "rank_options": [{"label": "first step, second step"}]}
        self.assertFalse(any(f.code == "answer_in_stem" for f in lint(rank)))

    def test_empty_stem(self) -> None:
        self.assertIn(("empty_stem", "empty_stem"), _subtypes(lint({"stem_html": "<p> </p>", "choices": ["a"]})))
        self.assertEqual(lint({"stem_html": "", "image_url": "/static/alc-logo.png"}, item_type="x")
                         and [f for f in lint({"stem_html": ""}, item_type="text_only_question") if f.code == "empty_stem"], [])

    def test_imscc_filebase_resolves_against_library_files(self) -> None:
        stem = '<p>Graph of f <img src="$IMS-CC-FILEBASE$/x.png"></p>'
        self.assertEqual(lint({"stem_html": stem}, image_resolver=_Resolver({"web_resources/x.png"})), [])
        self.assertIn(("broken_image", "unresolvable"), _subtypes(lint({"stem_html": stem})))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_katex_parse_error(self) -> None:
        good = '<p>Evaluate <img class="equation_image" title="\\frac{1}{2}" src="/equation_images/x"></p>'
        bad = '<p>Evaluate <img class="equation_image" title="\\frac{1}{" src="/equation_images/x"></p>'
        self.assertEqual(lint({"stem_html": good}, katex=KatexChecker()), [])
        self.assertTrue(any(f.subtype.startswith("katex_parse_error") for f in lint({"stem_html": bad}, katex=KatexChecker())))

    def test_katex_skipped_without_node(self) -> None:
        checker = KatexChecker()
        with mock.patch.object(bank_lint.shutil, "which", return_value=None):
            self.assertIsNone(checker.errors(["x^"]))
        self.assertEqual(checker.status, "skipped")

    def test_whitespace_only_dollars_do_not_raise(self) -> None:
        """Ops gate PR #314: ``$ $`` raised IndexError."""
        for stem in ("$ $", "cost $ $ more", "<p>$  $ and $ $</p>"):
            lint({"stem_html": stem})
            lint({"prompt": stem})
            lint({"stem_html": "<p>Pick one</p>", "choices": [{"html": stem}]})

    def test_odd_payloads_never_raise(self) -> None:
        for payload in ({}, {"stem_html": None}, {"choices": [None, 3, {"x": 1}]}, {"blanks": ["x"]}, "nope"):
            lint(payload)  # type: ignore[arg-type]


class StorageAndHookTests(unittest.TestCase):
    """question_lint rows, safe hooks, and the import entry point."""

    def setUp(self) -> None:
        import test_ingest

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = test_ingest.LovesDB(self.root / "lloves.sqlite")
        self.library = self.db.create_library("SBI3U", origin="upload")
        self.unpacked = test_ingest.build_cartridge(self.root / "unpacked")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _ingest(self):
        from ingest import ingest_library

        return ingest_library(self.db, self.root, int(self.library["id"]), self.unpacked)

    def test_ingest_writes_lint_rows_and_counts(self) -> None:
        summary = self._ingest()
        self.assertIn("lint", summary)
        checked = self.db.conn.execute("SELECT COUNT(*) FROM question_lint_checked").fetchone()[0]
        self.assertEqual(checked, summary["questions"])
        counts = bank_lint.lint_counts(self.db, int(self.library["id"]))
        self.assertEqual(set(counts), set(bank_lint.CODES))

    def test_ingest_flags_a_broken_question(self) -> None:
        self._ingest()
        qid, payload = self.db.conn.execute("SELECT id, payload_json FROM questions ORDER BY id LIMIT 1").fetchone()
        data = json.loads(payload)
        data["stem_html"] = '<p>Which graph? <img alt=""></p>'
        self.db.conn.execute("UPDATE questions SET payload_json = ? WHERE id = ?", (json.dumps(data), qid))
        self.db.conn.commit()
        self._ingest_lint_only()
        self.assertIn("broken_image", bank_lint.fix_codes(self.db, [qid]).get(int(qid), []))

    def _ingest_lint_only(self) -> None:
        bank_lint.lint_library(self.db, int(self.library["id"]))

    def test_lint_failure_never_blocks_import(self) -> None:
        with mock.patch.object(bank_lint, "lint_library", side_effect=RuntimeError("boom")):
            summary = self._ingest()
        self.assertGreater(summary["questions"], 0)
        self.assertNotIn("lint", summary)

    def test_decorators_keep_return_and_swallow_errors(self) -> None:
        @bank_lint.lints_library_after
        def seed(school, library_id):
            return {"ok": 1}

        with mock.patch.object(bank_lint, "lint_unchecked", side_effect=RuntimeError("boom")):
            self.assertEqual(seed(self.db, int(self.library["id"])), {"ok": 1})

        @bank_lint.lints_question_after
        def save(school, question_id):
            return 7

        with mock.patch.object(bank_lint, "lint_questions", side_effect=RuntimeError("boom")):
            self.assertEqual(save(self.db, 1), 7)

    def test_one_item_raising_does_not_abort_the_run(self) -> None:
        self._ingest()
        ids = [r[0] for r in self.db.conn.execute("SELECT id FROM questions ORDER BY id")]
        self.assertGreaterEqual(len(ids), 2)
        real = bank_lint.lint_question
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise IndexError("bad item")
            return real(*a, **kw)

        with mock.patch.object(bank_lint, "lint_question", side_effect=flaky), \
                mock.patch.object(bank_lint, "_report_item_error") as report:
            summary = bank_lint.lint_library(self.db, int(self.library["id"]))
        report.assert_called_once()
        self.assertEqual(report.call_args[0][0], ids[0])
        self.assertEqual(summary["questions"], len(ids) - 1)
        checked = {r[0] for r in self.db.conn.execute("SELECT question_id FROM question_lint_checked")}
        self.assertTrue(set(ids[1:]) <= checked)

    def test_lint_unchecked_only_lints_new_rows(self) -> None:
        self._ingest()
        lib = int(self.library["id"])
        self.assertEqual(bank_lint.lint_unchecked(self.db, lib), {})

    def test_cli_is_read_only(self) -> None:
        self._ingest()
        self.db.conn.commit()
        out = self.root / "out.json"
        with mock.patch("sys.stdout"):
            rc = bank_lint.main(["--library-id", str(self.library["id"]), "--db",
                                 str(self.root / "lloves.sqlite"), "--json", str(out)])
        self.assertEqual(rc, 0)
        self.assertIn("summary", json.loads(out.read_text()))


class SchoolSaveHookTests(unittest.TestCase):
    """A single-question save writes lint rows synchronously."""

    import test_module_bank_import as _base

    setUp = _base.ModuleBankImportApiTests.setUp
    tearDown = _base.ModuleBankImportApiTests.tearDown
    _login = _base.ModuleBankImportApiTests._login

    def test_insert_library_bank_question_lints(self) -> None:
        qid = self.school.insert_library_bank_question(
            self.library_id, self.m1_bank, item_type="multiple_choice_question",
            title="t", payload={"stem_html": "<p>Factor x2 - 3x + 5 now.</p>", "choices": []},
        )
        self.assertIn("raw_equation", bank_lint.fix_codes(self.school, [qid]).get(qid, []))


if __name__ == "__main__":
    unittest.main()
