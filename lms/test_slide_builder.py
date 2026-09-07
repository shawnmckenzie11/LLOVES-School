#!/usr/bin/env python3
"""Lesson Slides pacing, keyword filter, and index-based fill."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_constants import (  # noqa: E402
    M1C1_TEAM_CHALLENGE_CONTEXT,
    M1C1_TEAM_CHALLENGE_QUESTION,
)
from live_class_slides import strand_from_module  # noqa: E402
from slide_builder import (  # noqa: E402
    build_index_fill_requests,
    collect_consolidation_items,
    filter_async_lessons,
    load_local_bank_items,
    load_official_example_stems,
    lesson_fill_values,
    live_class_lesson_window,
    match_expectations,
    pick_consolidation_questions,
    preview_live_class,
)


class SlideBuilderMathTests(unittest.TestCase):
    """Pure window math, keyword filter, and unlabeled-shape fill."""

    def test_window_six_lessons_four_lives(self) -> None:
        """M1C1 with k=6 L=4 is Lessons 1-2; adjacent lives overlap."""
        self.assertEqual(live_class_lesson_window(6, 4, 1), (1, 2))
        self.assertEqual(live_class_lesson_window(6, 4, 2), (2, 3))
        self.assertEqual(live_class_lesson_window(6, 4, 3), (4, 5))
        self.assertEqual(live_class_lesson_window(6, 4, 4), (5, 6))
        covered: set[int] = set()
        for live in range(1, 5):
            start, end = live_class_lesson_window(6, 4, live)
            covered.update(range(start, end + 1))
        self.assertEqual(covered, {1, 2, 3, 4, 5, 6})

    def test_keyword_filter_orders_lessons(self) -> None:
        """Only titles containing Lesson are numbered 1...k."""
        items = [
            {"title": "Welcome"},
            {"title": "Lesson 1 — Functions"},
            {"title": "Quiz"},
            {"title": "Lesson 2 factoring"},
            {"title": "lesson review homework"},
        ]
        lessons = filter_async_lessons(items, "Lesson")
        self.assertEqual([row["lesson_number"] for row in lessons], [1, 2, 3])
        self.assertEqual(lessons[0]["title"], "Lesson 1 — Functions")

    def test_match_uses_seeded_codes_not_invented_wording(self) -> None:
        """Explicit A2.1 plus official statement tokens; no invented code."""
        expects = [
            {"code": "A2.1", "statement": "factor polynomial expressions involving quadratics"},
            {"code": "C1.1", "statement": "periodic functions and sine"},
        ]
        hits = match_expectations("Lesson 1 introduces A2.1 and factoring quadratics", expects)
        self.assertEqual([row["code"] for row in hits], ["A2.1"])
        self.assertIn("factor polynomial", hits[0]["statement"])

    def test_code_re_matches_strand_d(self) -> None:
        """Grade 12 data-management codes use strand D."""
        from slide_builder import CODE_RE

        found = {m.group(1).upper() for m in CODE_RE.finditer("MAP4C D1.1 and A2.3")}
        self.assertIn("D1.1", found)
        self.assertIn("A2.3", found)

    def test_index_fill_writes_unlabeled_shapes_without_tokens(self) -> None:
        """Seven branded slides with no {{placeholders}} still get insertText."""

        def slide(oid: str, shape_id: str, *, notes_id: str | None = None) -> dict:
            page = {
                "objectId": oid,
                "pageElements": [
                    {
                        "objectId": shape_id,
                        "size": {
                            "width": {"magnitude": 4000000, "unit": "EMU"},
                            "height": {"magnitude": 2000000, "unit": "EMU"},
                        },
                        "shape": {
                            "text": {
                                "textElements": [{"textRun": {"content": "Brand lockup\n"}}]
                            }
                        },
                    }
                ],
            }
            if notes_id:
                page["slideProperties"] = {
                    "notesPage": {
                        "pageElements": [
                            {
                                "objectId": notes_id,
                                "shape": {
                                    "placeholder": {"type": "BODY"},
                                    "text": {
                                        "textElements": [{"textRun": {"content": "old\n"}}]
                                    },
                                },
                            }
                        ]
                    }
                }
            return page

        presentation = {
            "slides": [
                slide("s1", "t1"),
                slide("s2", "t2"),
                slide("s3", "t3"),
                slide("s4", "t4", notes_id="n4"),
                slide("s5", "t5"),
                slide("s6", "t6"),
                slide("s7", "t7"),
            ]
        }
        values = lesson_fill_values(
            ontario_code="MCF3M",
            preview={"lesson_key": "M1C1"},
            context=M1C1_TEAM_CHALLENGE_CONTEXT,
            question=M1C1_TEAM_CHALLENGE_QUESTION,
            speaker_notes="Explain jigsaw grouping.",
            consolidation_text="1. Equal-group factorizations",
        )
        requests = build_index_fill_requests(presentation, values)
        self.assertTrue(requests)
        blob = str(requests)
        self.assertNotIn("replaceAllText", blob)
        self.assertIn("MCF3M: M1C1", blob)
        self.assertIn("jigsaw", blob.lower())
        self.assertIn("nicely jigsaw-able", blob)
        self.assertIn("Explain jigsaw grouping.", blob)
        self.assertIn("What strategy did your team try first?", blob)
        object_ids = {
            req.get("insertText", {}).get("objectId")
            for req in requests
            if "insertText" in req
        }
        self.assertIn("t1", object_ids)
        self.assertIn("t4", object_ids)
        self.assertIn("n4", object_ids)
        self.assertNotIn("t3", object_ids)


class ConsolidationLookupTests(unittest.TestCase):
    """Keyed local cache lookup without Drive and without inventing stems."""

    def setUp(self) -> None:
        """Isolated cache root."""
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self) -> None:
        """Remove cache root."""
        self.tmp.cleanup()

    def test_missing_banks_and_index_return_empty(self) -> None:
        """Absent ``items.json`` / ``examples-index.json`` must not raise."""
        rows = collect_consolidation_items(
            None,
            course_code="MCF3M",
            module_number=1,
            strand="A",
            expectation_codes={"A2.1"},
            library_id=None,
            cache_root=self.cache,
        )
        self.assertEqual(rows, [])

    def test_reads_only_course_module_strand_path(self) -> None:
        """MCF3M/M1/A is used; other course folders are ignored."""
        hit = (
            self.cache
            / "MCF3M"
            / "banks"
            / "M1"
            / "A"
            / "items.json"
        )
        hit.parent.mkdir(parents=True)
        hit.write_text(
            json.dumps(
                {
                    "course_code": "MCF3M",
                    "module_number": 1,
                    "strand": "A",
                    "items": [
                        {
                            "title": "Equal groups",
                            "stem": "How can 16 students split into equal groups?",
                            "item_type": "short_answer",
                            "expectation_codes": ["A2.1"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        other = (
            self.cache
            / "MCR3U"
            / "banks"
            / "M1"
            / "A"
            / "items.json"
        )
        other.parent.mkdir(parents=True)
        other.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "Wrong course",
                            "stem": "Do not pick this stem",
                            "expectation_codes": ["A2.1"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        rows = collect_consolidation_items(
            None,
            course_code="MCF3M",
            module_number=1,
            strand="A",
            expectation_codes={"A2.1"},
            cache_root=self.cache,
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("equal groups", rows[0]["stem"].lower())
        self.assertEqual(rows[0]["source"], "local_bank")
        self.assertNotIn("Wrong course", str(rows))

    def test_examples_index_is_last_resort_keyed_by_code(self) -> None:
        """Only codes present in examples-index.json are used as stems."""
        index = self.cache / "MCF3M" / "examples-index.json"
        index.parent.mkdir(parents=True)
        index.write_text(
            json.dumps(
                {
                    "course_code": "MCF3M",
                    "by_code": {
                        "A2.1": {
                            "stem": "Sample: factor x^2 - 16.",
                            "title": "Ministry sample A2.1",
                        },
                        "C1.1": {"stem": "Ignore other strand sample."},
                    },
                }
            ),
            encoding="utf-8",
        )
        rows = collect_consolidation_items(
            None,
            course_code="MCF3M",
            module_number=1,
            strand="A",
            expectation_codes={"A2.1"},
            cache_root=self.cache,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "official_example")
        self.assertIn("x^2 - 16", rows[0]["stem"])
        self.assertNotIn("Ignore other", rows[0]["stem"])

    def test_specifics_examples_list_becomes_stem(self) -> None:
        """curriculum-drive-author specifics[].examples is the official stem."""
        index = self.cache / "MCF3M" / "examples-index.json"
        index.parent.mkdir(parents=True)
        index.write_text(
            json.dumps(
                {
                    "course_code": "MCF3M",
                    "specifics": [
                        {"code": "A1.3", "examples": ["Factor 2x^2 − 12x + 10."]},
                        {"code": "A1.4", "examples": []},
                    ],
                }
            ),
            encoding="utf-8",
        )
        rows = load_official_example_stems(
            "MCF3M",
            {"A1.3"},
            cache_root=self.cache,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "official_example")
        self.assertIn("Factor 2x^2 − 12x + 10.", rows[0]["stem"])
        self.assertEqual(
            load_official_example_stems("MCF3M", {"A1.4"}, cache_root=self.cache),
            [],
        )

    def test_empty_expectation_codes_kept_for_module_strand(self) -> None:
        """Nelson stems with no codes stay in the M{n}/{strand} pool."""
        path = self.cache / "MCF3M" / "banks" / "M5" / "C" / "items.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "course_code": "MCF3M",
                    "module_number": 5,
                    "strand": "C",
                    "items": [
                        {
                            "smart_id": "nelson-m5-c-1",
                            "source": "nelson",
                            "stem": "Sketch one cycle of a sinusoidal graph.",
                            "expectation_codes": [],
                            "verified": True,
                        },
                        {
                            "smart_id": "nelson-m5-c-skip",
                            "source": "nelson",
                            "stem": "This coded item is another strand.",
                            "expectation_codes": ["A2.1"],
                            "verified": False,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        rows = load_local_bank_items(
            "MCF3M",
            5,
            "C",
            {"C1.1"},
            cache_root=self.cache,
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("sinusoidal", rows[0]["stem"].lower())
        self.assertNotIn("another strand", str(rows))

    def test_non_overlapping_codes_excluded(self) -> None:
        """Items whose listed codes miss the connected set are dropped."""
        path = self.cache / "MCF3M" / "banks" / "M2" / "A" / "items.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "source": "nelson",
                            "stem": "Solve using the quadratic formula.",
                            "expectation_codes": ["C1.2"],
                            "verified": True,
                        },
                        {
                            "source": "nelson",
                            "stem": "Factor x^2 - 16.",
                            "expectation_codes": ["A2.1"],
                            "verified": False,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        rows = load_local_bank_items(
            "MCF3M",
            2,
            "A",
            {"A2.1"},
            cache_root=self.cache,
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("x^2 - 16", rows[0]["stem"])
        self.assertNotIn("quadratic formula", rows[0]["stem"].lower())

    def test_verified_nelson_before_unverified_then_examples(self) -> None:
        """Verified Nelson stems rank above unverified, then examples-index."""
        bank = self.cache / "MCF3M" / "banks" / "M1" / "A" / "items.json"
        bank.parent.mkdir(parents=True)
        bank.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "source": "nelson",
                            "stem": "Unverified Nelson stem about groups.",
                            "expectation_codes": [],
                            "verified": False,
                        },
                        {
                            "source": "nelson",
                            "stem": "Verified Nelson stem about equal groups.",
                            "expectation_codes": ["A2.1"],
                            "verified": True,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        index = self.cache / "MCF3M" / "examples-index.json"
        index.write_text(
            json.dumps(
                {
                    "by_code": {
                        "A2.1": {"stem": "Official sample: factor x^2 - 16."},
                    }
                }
            ),
            encoding="utf-8",
        )
        rows = collect_consolidation_items(
            None,
            course_code="MCF3M",
            module_number=1,
            strand="A",
            expectation_codes={"A2.1"},
            cache_root=self.cache,
        )
        self.assertGreaterEqual(len(rows), 3)
        self.assertIn("Verified Nelson", rows[0]["stem"])
        self.assertIn("Unverified Nelson", rows[1]["stem"])
        self.assertEqual(rows[2]["source"], "official_example")


class StrandFromModuleTests(unittest.TestCase):
    """ELC MCF3M packing plus optional module-strand-map.json."""

    def setUp(self) -> None:
        """Empty cache so missing maps use the documented fallback."""
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self) -> None:
        """Remove temp cache."""
        self.tmp.cleanup()

    def test_mcf3m_elc_pack_modules(self) -> None:
        """M2→A quadratic, M5→C trig, M7→B exponential when titles are empty."""
        self.assertEqual(
            strand_from_module("", 2, "MCF3M", cache_root=self.cache),
            "A",
        )
        self.assertEqual(
            strand_from_module("", 5, "MCF3M", cache_root=self.cache),
            "C",
        )
        self.assertEqual(
            strand_from_module("", 7, "MCF3M", cache_root=self.cache),
            "B",
        )

    def test_title_keywords_win(self) -> None:
        """Module title trig/quadratic/exponential beat the numeric fallback."""
        self.assertEqual(
            strand_from_module("Sinusoidal models", 2, "MCF3M", cache_root=self.cache),
            "C",
        )
        self.assertEqual(
            strand_from_module("Quadratic relations", 7, "MCF3M", cache_root=self.cache),
            "A",
        )
        self.assertEqual(
            strand_from_module("Exponential growth", 1, "MCF3M", cache_root=self.cache),
            "B",
        )

    def test_mcr3u_reads_module_strand_map(self) -> None:
        """MCR3U uses the same map file: modules[].module_number → strand."""
        path = self.cache / "MCR3U" / "module-strand-map.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "course_code": "MCR3U",
                    "modules": [
                        {"module_number": 1, "strand": "A"},
                        {"module_number": 5, "strand": "C"},
                        {"module_number": 7, "strand": "B"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            strand_from_module("", 5, "MCR3U", cache_root=self.cache),
            "C",
        )
        self.assertEqual(
            strand_from_module("", 7, "MCR3U", cache_root=self.cache),
            "B",
        )
        self.assertEqual(
            strand_from_module("", 2, "MCR3U", cache_root=self.cache),
            "A",
        )


class LessonSlidesHttpTests(unittest.TestCase):
    """Staff tab, preview API, and mock build for M1C1."""

    def setUp(self) -> None:
        """Isolated sqlite + Flask client as the IT operator."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.client.get("/auth/google?portal=it")
        self.client.get("/auth/google/callback?email=solutions@mckenzian.com&name=Shawn")
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        self.offering = self.school.assign_course(
            teacher_user_id=int(user["id"]),
            ontario_code="MCF3M",
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch", "Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        self.class_id = int(created.get_json()["class"]["id"])
        self._seed_six_lessons()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _seed_six_lessons(self) -> None:
        """Attach a module with six Lesson pages so M1C1 maps to 1-2."""
        lib = self.school.create_library("MCF3M", origin="upload")
        self.school.conn.execute(
            "UPDATE course_offerings SET library_id = ? WHERE id = ?",
            (int(lib["id"]), int(self.offering["id"])),
        )
        cur = self.school.conn.execute(
            """
            INSERT INTO module_outlines (library_id, import_key, title, position, created_at)
            VALUES (?, 'm1', 'Quadratic Relations', 1, datetime('now'))
            """,
            (int(lib["id"]),),
        )
        outline_id = int(cur.lastrowid)
        for index in range(1, 7):
            html = f"<p>Lesson {index} A2.1 factor polynomial expressions</p>"
            page_cur = self.school.conn.execute(
                """
                INSERT INTO pages (
                    library_id, import_key, kind, title, html_text, created_at
                ) VALUES (?, ?, 'html', ?, ?, datetime('now'))
                """,
                (int(lib["id"]), f"p{index}", f"Lesson {index} — practice", html),
            )
            page_id = int(page_cur.lastrowid)
            self.school.conn.execute(
                """
                INSERT INTO module_items (
                    outline_id, import_key, title, position, component_type,
                    component_id, source_type, created_at
                ) VALUES (?, ?, ?, ?, 'page', ?, 'html', datetime('now'))
                """,
                (
                    outline_id,
                    f"i{index}",
                    f"Lesson {index} — practice",
                    index,
                    page_id,
                ),
            )
        self.school.conn.commit()

    def test_preview_m1c1_names_lessons_one_and_two(self) -> None:
        """GET lesson-slides preview lists Lessons 1-2 and M1C1 defaults."""
        rv = self.client.get(
            f"/api/classes/{self.class_id}/lesson-slides"
            "?module=1&live_index=1&lives_per_module=4&keyword=Lesson"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        preview = body["preview"]
        self.assertEqual(preview["lesson_key"], "M1C1")
        self.assertEqual(preview["window_start"], 1)
        self.assertEqual(preview["window_end"], 2)
        nums = [row["lesson_number"] for row in preview["connected_lessons"]]
        self.assertEqual(nums, [1, 2])
        self.assertIn("Lesson 1", preview["summary"])
        self.assertIn("Lesson 2", preview["summary"])
        self.assertIn("A2.1", preview["expectation_codes"])
        self.assertIn("jigsaw", preview["team_challenge"]["context"].lower())

        offering = self.school.get_offering(int(self.offering["id"]))
        local = preview_live_class(
            self.school, offering=offering, module_number=1, live_index=1
        )
        self.assertEqual(local["window_end"], 2)

    def test_build_mock_deck_is_non_empty(self) -> None:
        """POST writes an HTML mock that includes context and question copy."""
        self.client.get("/auth/google/slides")
        rv = self.client.post(
            f"/api/classes/{self.class_id}/lesson-slides",
            json={"module": 1, "live_index": 1},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertTrue(body.get("presentation_url"))
        html = self.client.get(body["presentation_url"])
        self.assertEqual(html.status_code, 200)
        text = html.get_data(as_text=True)
        self.assertIn("MCF3M: M1C1", text)
        self.assertIn("jigsaw", text.lower())
        self.assertIn("TEAM_CHALLENGE_ROUND_CONTEXT", text)
        tab = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertIn("ls-preview", tab.get_data(as_text=True))
        live = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertNotIn("ap-connect-slides", live.get_data(as_text=True))

    def _insert_bank_question(self, *, title: str, stem: str, bank_title: str) -> None:
        """Insert one ingested question into the offering library."""
        offering = self.school.get_offering(int(self.offering["id"]))
        library_id = int(offering["library_id"])
        cur = self.school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, ?, ?, '{}', datetime('now'))
            """,
            (library_id, f"bank:{bank_title}", bank_title),
        )
        bank_id = int(cur.lastrowid)
        self.school.conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, 'q1', 'essay', ?, ?, datetime('now'))
            """,
            (
                bank_id,
                title,
                json.dumps({"stem": stem, "position": 1}),
            ),
        )
        self.school.conn.commit()

    def test_build_prefers_sqlite_questions_matching_codes(self) -> None:
        """Keyed sqlite hit beats local cache; other-course files are unused."""
        self._insert_bank_question(
            title="Jigsaw factors A2.1",
            stem="Which factorizations of 16 give equal group sizes?",
            bank_title="MCF3M M1/A",
        )
        cache = Path(self.tmp.name) / "curriculum"
        local = cache / "MCF3M" / "banks" / "M1" / "A" / "items.json"
        local.parent.mkdir(parents=True)
        local.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "Local only",
                            "stem": "Should lose to sqlite",
                            "expectation_codes": ["A2.1"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        offering = self.school.get_offering(int(self.offering["id"]))
        preview = preview_live_class(
            self.school, offering=offering, module_number=1, live_index=1
        )
        self.assertEqual(preview.get("strand"), "A")
        picked = pick_consolidation_questions(
            self.school,
            offering=offering,
            preview=preview,
            cache_root=cache,
        )
        self.assertTrue(picked)
        self.assertEqual(picked[0]["source"], "sqlite")
        self.assertIn("equal group", picked[0]["stem"].lower())
        self.assertNotIn("Should lose", picked[0]["stem"])


if __name__ == "__main__":
    unittest.main()
