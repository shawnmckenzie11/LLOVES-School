#!/usr/bin/env python3
"""MCK-46: export saved End-of-class answers to CSV.

The file reads the End results snapshot per run (``run_key``), so a reused
session id never mixes runs. Only the class's own staff can download it,
and cells that a spreadsheet would run as formulas are neutralised.
"""

from __future__ import annotations

import csv
import io
import unittest
from typing import Any

import test_live_shell as shell
from live_results_csv import COLUMNS, answer_text, live_results_csv, safe_cell


def _parse(body: str) -> list[dict[str, str]]:
    """Parse the CSV text (BOM included) into dict rows."""
    assert body.startswith("\ufeff")
    return list(csv.DictReader(io.StringIO(body[1:])))


class CsvFormatTests(unittest.TestCase):
    """Pure formatting helpers."""

    def test_formula_cells_are_neutralised(self) -> None:
        """=, +, -, @ text gets an apostrophe; numbers stay numbers."""
        self.assertEqual(safe_cell("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(safe_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(safe_cell("+1+cmd"), "'+1+cmd")
        self.assertEqual(safe_cell("-3"), "-3")
        self.assertEqual(safe_cell("2.5"), "2.5")
        self.assertEqual(safe_cell(None), "")

    def test_answer_text_flattens_saved_answers(self) -> None:
        """Choice, numeric and multi-select answers read as text."""
        self.assertEqual(answer_text({"choice": "B", "question_view": "student"}), "B")
        self.assertEqual(answer_text({"value": 4.5}), "4.5")
        self.assertEqual(answer_text({"choices": ["A", "C"]}), "A; C")
        self.assertEqual(answer_text({"status": "finalized"}), '{"status": "finalized"}')
        self.assertEqual(answer_text({"question_view": "student"}), "")

    def test_header_only_when_no_runs(self) -> None:
        """No saved runs still gives a valid file with the header."""
        body = live_results_csv([])
        self.assertEqual(body[1:].strip().split(","), COLUMNS)


class ResultsCsvRouteTests(shell.LiveShellTests):
    """Reuse the live-shell fixture. Only the tests below run here."""

    def _run_once(self) -> tuple[str, int]:
        """Start, answer as Aspen, End. Returns ``(run_key, aspen_id)``."""
        sid, aspen = self._open_live_with_aspen_answers()
        run_key = str(self.school.get_live_session(sid)["run_key"])
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live", follow_redirects=False
        )
        self.assertEqual(ended.status_code, 302)
        return run_key, aspen

    def _get(self, query: str = "") -> Any:
        return self.client.get(f"/staff/class/{self.class_id}/results.csv{query}")

    def test_export_has_every_run_and_keeps_them_apart(self) -> None:
        """Two runs on a reused session id export as two run_keys."""
        key1, aspen = self._run_once()
        key2, _ = self._run_once()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.mimetype == "text/csv", resp.mimetype)
        self.assertIn("attachment; filename=lloves-results-", resp.headers["Content-Disposition"])
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        rows = _parse(resp.get_data(as_text=True))
        first = self.school.live_result_snapshot_rows(self.class_id, run_key=key1)
        second = self.school.live_result_snapshot_rows(self.class_id, run_key=key2)
        self.assertEqual(len(rows), len(first) + len(second))
        # Newest run first, each run's rows together.
        keys = [row["run_key"] for row in rows]
        self.assertEqual(keys, [key2] * len(second) + [key1] * len(first))
        mine = [row for row in rows if row["student_id"] == str(aspen)]
        self.assertTrue(mine)
        self.assertTrue(all(row["name"] == "Aspen" for row in mine))
        answers = {row["answer"] for row in mine}
        self.assertIn(shell.MINDS_ON_CHOICES[0], answers)
        self.assertIn("This sparks something", answers)
        self.assertTrue(all(row["question"] for row in mine))

        one = self._get(f"?run_key={key1}")
        self.assertEqual(one.status_code, 200)
        only = _parse(one.get_data(as_text=True))
        self.assertEqual({row["run_key"] for row in only}, {key1})
        self.assertEqual(len(only), len(first))
        self.assertEqual(self._get("?run_key=nope").status_code, 404)

    def test_empty_class_downloads_header(self) -> None:
        """Before any End, the file is just the header."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_parse(resp.get_data(as_text=True)), [])

    def test_other_teacher_cannot_download(self) -> None:
        """Another staff member gets 403 and no names."""
        self._run_once()
        other = self.school.register_staff("other@gmail.com")
        self.assertIsNotNone(other)
        self.client.get("/logout")
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=other@gmail.com&name=O")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("other@gmail.com")[
                    "verification_code"
                ]
            },
        )
        resp = self._get()
        self.assertEqual(resp.status_code, 403, resp.status_code)
        self.assertNotIn("Aspen", resp.get_data(as_text=True))

    def test_participation_page_links_the_export(self) -> None:
        """The Participation toolbar has the Export results button."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=participation")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('id="export-results-csv"', html)
        self.assertIn(f"/staff/class/{self.class_id}/results.csv", html)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited live-shell ones."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(CsvFormatTests))
    names = [
        name
        for name, value in vars(ResultsCsvRouteTests).items()
        if name.startswith("test_") and callable(value)
    ]
    suite.addTests(ResultsCsvRouteTests(name) for name in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()
