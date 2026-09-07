#!/usr/bin/env python3
"""Unit tests for Grades 11–12 math PDF specific-expectation extraction."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from math_expectations_pdf import (  # noqa: E402
    drive_upload_text,
    extract_math_11_12,
    extract_math_11_12_from_pages,
    write_course_json_files,
)

DOWNLOAD_PDF = Path("/Users/shawnscomputer/Downloads/math1112currb (2).pdf")


class MathExpectationsPdfTests(unittest.TestCase):
    """Window of MCF3M specifics must match the Ministry PDF, not invented text."""

    def test_mcf3m_a11_from_synthetic_pages(self) -> None:
        """Strand heading + 1.1 on one page become A1.1 with PDF wording."""
        pages = [""]
        pages.extend(["x"] * 58)
        pages.append(
            "Functions and Applications,\nGrade 11\nUniversity/College Preparation\nMCF3M\n"
            "This course introduces basic features.\n"
        )
        pages.append(
            "1.1 pose problems involving quadratic relations arising from real-world "
            "applications and represented by tables of values and graphs, and solve "
            "these and other such problems\n"
            "A.  QUADRATIC FUNCTIONS\n"
            "OVERALL EXPECTATIONS\n"
            "By the end of this course, students will:\n"
            "1. expand and simplify quadratic expressions, solve quadratic equations, "
            "and relate the roots of a quadratic equation to the corresponding graph;\n"
            "SPECIFIC EXPECTATIONS\n"
        )
        courses = extract_math_11_12_from_pages(pages)
        self.assertIn("MCF3M", courses)
        specs = {
            s["code"]: s["statement"]
            for st in courses["MCF3M"]["strands"]
            for s in st["specific"]
        }
        self.assertIn("A1.1", specs)
        self.assertIn("quadratic relations", specs["A1.1"])
        self.assertNotIn("invented", specs["A1.1"].lower())

    @unittest.skipUnless(DOWNLOAD_PDF.is_file(), "Ministry PDF not on this machine")
    def test_real_pdf_ten_courses_and_mcf3m_a11(self) -> None:
        """Attached PDF yields all 10 Gr 11–12 math codes and MCF3M A1.1."""
        courses = extract_math_11_12(DOWNLOAD_PDF)
        self.assertEqual(
            set(courses),
            {
                "MAP4C",
                "MBF3C",
                "MCF3M",
                "MCR3U",
                "MCT4C",
                "MCV4U",
                "MDM4U",
                "MEL3E",
                "MEL4E",
                "MHF4U",
            },
        )
        mcf = courses["MCF3M"]
        self.assertGreaterEqual(mcf["specific_count"], 20)
        a11 = next(
            s
            for st in mcf["strands"]
            for s in st["specific"]
            if s["code"] == "A1.1"
        )
        self.assertTrue(
            a11["statement"].lower().startswith("pose problems involving quadratic")
        )
        seed = json.loads(
            (LMS_DIR / "seeds" / "mcf3m_expectations.json").read_text(encoding="utf-8")
        )
        seed_a11 = next(
            s["statement"]
            for st in seed["strands"]
            for s in st["specific"]
            if s["code"] == "A1.1"
        )
        self.assertIn("quadratic relations", a11["statement"])
        self.assertIn("quadratic relations", seed_a11)

    def test_write_json_splits_specific_file(self) -> None:
        """Per-course specific JSON lists codes at the top level."""
        import tempfile

        courses = {
            "MCF3M": {
                "course_code": "MCF3M",
                "course_title": "Functions and Applications",
                "source_pdf": "x",
                "source_pages_pdf": "59-68",
                "verification_status": "test",
                "notes": "n",
                "specific_count": 1,
                "overall_count": 0,
                "strands": [
                    {
                        "code": "A",
                        "name": "Quadratic Functions",
                        "overall": [],
                        "specific": [
                            {
                                "code": "A1.1",
                                "overall": "A1",
                                "statement": "pose problems involving quadratic relations",
                                "examples": [],
                            }
                        ],
                    }
                ],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_course_json_files(courses, Path(tmp))
            spec = Path(tmp) / "MCF3M" / "MCF3M-specific-expectations.json"
            self.assertTrue(spec.is_file())
            payload = json.loads(spec.read_text(encoding="utf-8"))
            self.assertEqual(payload["specific"][0]["code"], "A1.1")
            self.assertTrue((Path(tmp) / "MCF3M" / "MCF3M-specific-expectations.md").is_file())
            self.assertTrue(any(p.name == "index.json" for p in paths))

    def test_load_local_specific_rows_from_dump(self) -> None:
        """Local MCF3M dump supplies A1.1 wording for runtime matching."""
        from math_expectations_pdf import load_local_specific_rows

        rows = load_local_specific_rows("MCF3M")
        if not rows:
            self.skipTest("local MCF3M extract is missing")
        a11 = next(r for r in rows if r["code"] == "A1.1")
        self.assertIn("quadratic", a11["statement"].lower())

    def test_drive_upload_text_posts_multipart(self) -> None:
        """Drive helper posts metadata plus body to the upload endpoint."""

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"id": "file1", "name": "x.json"}

        class _Http:
            def __init__(self) -> None:
                self.calls = []

            def post(self, url, params=None, headers=None, data=None, timeout=None):
                self.calls.append(
                    {"url": url, "params": params, "headers": headers, "data": data}
                )
                return _Resp()

        http = _Http()
        out = drive_upload_text(
            access_token="tok",
            parent_id="folder1",
            title="x.json",
            text='{"ok": true}',
            http=http,
        )
        self.assertEqual(out["id"], "file1")
        self.assertEqual(len(http.calls), 1)
        self.assertIn("upload/drive/v3/files", http.calls[0]["url"])
        self.assertIn(b"folder1", http.calls[0]["data"])


if __name__ == "__main__":
    unittest.main()
