#!/usr/bin/env python3
"""MCK-436: module-bank search reuses a rebuilt question list.

The picker used to parse every row on every call. The rebuilt list is
cached in-process by library and module. Keyword, Kind, and type filters
run on that list. Edits drop it. A short TTL drops it even when this
process never saw the write.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
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
from school_db import SchoolDB, _now  # noqa: E402

TIMING_ROWS = 250


def _insert_bank(school: Any, library_id: int, *, title: str, import_key: str) -> int:
    """Insert one question bank row for tests."""
    cur = school.conn.execute(
        """
        INSERT INTO question_banks (
            library_id, import_key, title, settings_json, created_at
        ) VALUES (?, ?, ?, '{}', ?)
        """,
        (int(library_id), import_key, title, _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


def _mc_payload(stem: str) -> str:
    """Return a minimal multiple-choice payload for one synthetic stem."""
    return json.dumps(
        {
            "stem_html": stem,
            "points_possible": 1.0,
            "choices": [
                {"id": "a", "html": "First", "correct": True},
                {"id": "b", "html": "Second", "correct": False},
            ],
            "correct_ids": ["a"],
        }
    )


def _signature(result: dict[str, Any]) -> dict[str, Any]:
    """Stable search snapshot: counts plus the rows a teacher would see."""
    rows = []
    for item in result.get("items") or []:
        options = item.get("options") or []
        rows.append(
            {
                "question_id": item.get("question_id"),
                "text": item.get("text"),
                "correct_answer": item.get("correct_answer"),
                "type": item.get("type"),
                "question_type": item.get("question_type"),
                "kind": item.get("kind"),
                "bank_scope": item.get("bank_scope"),
                "options": list(options) if isinstance(options, list) else options,
            }
        )
    return {
        "total": result.get("total"),
        "filtered": result.get("filtered"),
        "type_counts": result.get("type_counts"),
        "rows": rows,
    }


class ModuleBankMcCacheTests(unittest.TestCase):
    """Cache hits, edit invalidation, and parity with the cache off."""

    def setUp(self) -> None:
        """One library, one confirmed module bank, and a handful of MCs."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.school = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        ).config["SCHOOL_DB"]
        self.library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.bank_id = _insert_bank(
            self.school,
            self.library_id,
            title="Module 1 Quiz",
            import_key="bank:m1-cache",
        )
        self.question_ids: list[int] = []
        for index in range(12):
            cur = self.school.conn.execute(
                """
                INSERT INTO questions (
                    bank_id, import_key, item_type, title, payload_json, created_at
                ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
                """,
                (
                    self.bank_id,
                    f"q-cache-{index}",
                    f"Cache stem {index:02d}",
                    _mc_payload(f"Cache stem {index:02d}"),
                    _now(),
                ),
            )
            self.question_ids.append(int(cur.lastrowid))
        self.school.conn.commit()
        self.school.ensure_rank_bank(self.library_id)
        self.school._ensure_module_bank_link(self.library_id, 1, self.bank_id)
        self.school.invalidate_module_bank_mc_cache(self.library_id)
        self.school._module_bank_mc_cache_hits = 0
        self.school._module_bank_mc_cache_misses = 0

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _search(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        """Search module 1 and return the result dict."""
        return self.school.search_module_bank_mcs(
            self.library_id, 1, query, limit=500, **kwargs
        )

    def test_second_search_hits_and_filters_the_cached_list(self) -> None:
        """A different keyword does not rebuild; it filters the cached rows."""
        with self.assertLogs("school_db", level="DEBUG") as logs:
            first = self._search("")
        self.assertTrue(any("module bank mc cache miss" in line for line in logs.output))
        misses_after_first = self.school._module_bank_mc_cache_misses
        hits_after_first = self.school._module_bank_mc_cache_hits
        self.assertGreaterEqual(misses_after_first, 1)
        self.assertEqual(hits_after_first, 0)
        second = self._search("cache stem 01")
        self.assertEqual(self.school._module_bank_mc_cache_misses, misses_after_first)
        self.assertGreater(self.school._module_bank_mc_cache_hits, hits_after_first)
        stems = [row.get("text") for row in second["items"]]
        self.assertTrue(stems)
        self.assertTrue(all("cache stem 01" in str(stem).lower() for stem in stems))
        self.assertLess(second["filtered"], first["filtered"])
        self.assertEqual(second["total"], first["total"])

    def test_results_match_with_the_cache_disabled(self) -> None:
        """The same queries return the same rows whether the cache is on or off."""
        queries = ("", "cache stem 0", "no-such-stem")
        kinds = ("", "warmup", "standard")
        cached: list[dict[str, Any]] = []
        for kind in kinds:
            for query in queries:
                cached.append(_signature(self._search(query, kind=kind)))
        cached.append(
            _signature(
                self.school.search_bank_scope_mcs(
                    self.library_id, "course", 1, "cache stem", limit=500
                )
            )
        )
        self.school._module_bank_mc_cache_enabled = False
        self.school.invalidate_module_bank_mc_cache(self.library_id)
        plain: list[dict[str, Any]] = []
        for kind in kinds:
            for query in queries:
                plain.append(_signature(self._search(query, kind=kind)))
        plain.append(
            _signature(
                self.school.search_bank_scope_mcs(
                    self.library_id, "course", 1, "cache stem", limit=500
                )
            )
        )
        self.assertEqual(plain, cached)

    def test_edit_import_delete_and_promote_drop_the_cache(self) -> None:
        """A bank edit, insert, delete, or scope change is visible on the next search."""
        self._search("")
        misses = self.school._module_bank_mc_cache_misses
        target = self.question_ids[0]
        row = self.school.get_library_question(self.library_id, target)
        assert row is not None
        payload = dict(row["payload"])
        payload["stem_html"] = "Edited cache stem"
        self.school.update_library_question_payload(
            self.library_id,
            target,
            title="Edited cache stem",
            payload=payload,
        )
        self.assertEqual(self._school_cache_for_library(), {})
        edited = self._search("edited cache")
        self.assertGreater(self.school._module_bank_mc_cache_misses, misses)
        self.assertIn(target, {int(item["question_id"]) for item in edited["items"]})
        self.assertNotIn(
            target,
            {int(item["question_id"]) for item in self._search("cache stem 00")["items"]},
        )

        misses = self.school._module_bank_mc_cache_misses
        new_id = self.school.insert_library_bank_question(
            self.library_id,
            self.bank_id,
            item_type="multiple_choice_question",
            title="Imported cache stem",
            payload=json.loads(_mc_payload("Imported cache stem")),
        )
        self.assertEqual(self._school_cache_for_library(), {})
        imported = self._search("imported cache")
        self.assertGreater(self.school._module_bank_mc_cache_misses, misses)
        self.assertIn(new_id, {int(item["question_id"]) for item in imported["items"]})

        misses = self.school._module_bank_mc_cache_misses
        self.school.delete_library_bank_question(self.library_id, self.bank_id, new_id)
        self.assertEqual(self._school_cache_for_library(), {})
        after_delete = self._search("imported cache")
        self.assertGreater(self.school._module_bank_mc_cache_misses, misses)
        self.assertNotIn(
            new_id, {int(item["question_id"]) for item in after_delete["items"]}
        )

        misses = self.school._module_bank_mc_cache_misses
        self.school.set_question_bank_scope(self.library_id, target, "course")
        self.assertEqual(self._school_cache_for_library(), {})
        hidden = self._search("", include_course_placed=False)
        self.assertGreater(self.school._module_bank_mc_cache_misses, misses)
        self.assertNotIn(target, {int(item["question_id"]) for item in hidden["items"]})
        course_only = self.school.search_module_bank_mcs(
            self.library_id,
            1,
            "",
            limit=500,
            scope_policy="course_only",
        )
        self.assertIn(
            target, {int(item["question_id"]) for item in course_only["items"]}
        )
        self.school._module_bank_mc_cache_enabled = False
        self.assertEqual(_signature(hidden), _signature(self._search("", include_course_placed=False)))
        self.assertEqual(
            _signature(course_only),
            _signature(
                self.school.search_module_bank_mcs(
                    self.library_id, 1, "", limit=500, scope_policy="course_only"
                )
            ),
        )

    def test_expired_entry_rebuilds_after_a_write_this_process_missed(self) -> None:
        """A raw write is stale only until the TTL. The rebuild then matches."""
        self._search("")
        self.assertGreater(len(self._school_cache_for_library()), 0)
        target = self.question_ids[3]
        payload = json.loads(_mc_payload("TTL refresh stem"))
        self.school.conn.execute(
            "UPDATE questions SET title = ?, payload_json = ? WHERE id = ?",
            ("TTL refresh stem", json.dumps(payload), target),
        )
        self.school.conn.commit()
        stale = self._search("ttl refresh")
        self.assertNotIn(target, {int(item["question_id"]) for item in stale["items"]})
        self._expire_cache()
        misses = self.school._module_bank_mc_cache_misses
        fresh = self._search("ttl refresh")
        self.assertGreater(self.school._module_bank_mc_cache_misses, misses)
        self.assertIn(target, {int(item["question_id"]) for item in fresh["items"]})
        self.school._module_bank_mc_cache_enabled = False
        self.assertEqual(
            _signature(fresh),
            _signature(self._search("ttl refresh")),
        )

    def test_other_connection_drops_a_cached_list_after_a_write(self) -> None:
        """A write through one SchoolDB misses the other connection's cache."""
        self._search("")
        other = SchoolDB(self.school.db_path, self.school.data_dir)
        try:
            warm = other.search_module_bank_mcs(self.library_id, 1, "", limit=500)
            self.assertGreater(warm["total"], 0)
            again = other.search_module_bank_mcs(self.library_id, 1, "cache stem 01", limit=500)
            self.assertGreater(other._module_bank_mc_cache_hits, 0)
            target = self.question_ids[0]
            row = self.school.get_library_question(self.library_id, target)
            assert row is not None
            payload = dict(row["payload"])
            payload["stem_html"] = "Edited on the other connection"
            misses = other._module_bank_mc_cache_misses
            self.school.update_library_question_payload(
                self.library_id,
                target,
                title="Edited on the other connection",
                payload=payload,
            )
            fresh = other.search_module_bank_mcs(
                self.library_id, 1, "edited on the other", limit=500
            )
            self.assertGreater(other._module_bank_mc_cache_misses, misses)
            self.assertIn(target, {int(item["question_id"]) for item in fresh["items"]})
            stale = other.search_module_bank_mcs(
                self.library_id, 1, "cache stem 00", limit=500
            )
            self.assertNotIn(target, {int(item["question_id"]) for item in stale["items"]})
            self.assertEqual(again["total"], warm["total"])
        finally:
            other.close()

    def test_store_drops_a_rebuild_when_the_stamp_moved(self) -> None:
        """An edit during a rebuild must not put the old list back."""
        key = (int(self.library_id), 1, "module", False, True, 0)
        with self.school._lock:
            stamp = self.school._read_module_bank_mc_db_stamp(int(self.library_id))
        self.school._store_module_bank_mc_cache(key, [{"text": "fresh"}], stamp)
        self.assertIn(key, self.school._module_bank_mc_cache)
        self.school.invalidate_module_bank_mc_cache(self.library_id)
        self.school._store_module_bank_mc_cache(key, [{"text": "stale"}], stamp)
        self.assertNotIn(key, self.school._module_bank_mc_cache)

    def test_cache_stays_within_its_cap(self) -> None:
        """The process keeps a bounded number of rebuilt lists."""
        lib = int(self.library_id)
        with self.school._lock:
            stamp = self.school._read_module_bank_mc_db_stamp(lib)
        for index in range(self.school.MODULE_BANK_MC_CACHE_MAX + 12):
            key = (lib, 100 + index, "module", False, True, 0)
            self.school._store_module_bank_mc_cache(key, [{"n": index}], stamp)
        self.assertLessEqual(
            len(self.school._module_bank_mc_cache),
            self.school.MODULE_BANK_MC_CACHE_MAX,
        )

    def _school_cache_for_library(self) -> dict[Any, Any]:
        """Return cache entries for this test library."""
        return {
            key: value
            for key, value in self.school._module_bank_mc_cache.items()
            if int(key[0]) == int(self.library_id)
        }

    def _expire_cache(self) -> None:
        """Mark every cached list for this library as older than the TTL."""
        ttl = float(self.school._module_bank_mc_cache_ttl)
        for key, (_stored_at, db_stamp, items) in list(self._school_cache_for_library().items()):
            self.school._module_bank_mc_cache[key] = (
                time.monotonic() - ttl - 1.0,
                db_stamp,
                items,
            )


class ModuleBankMcCacheTimingTests(unittest.TestCase):
    """Before/after timings on a seeded module bank."""

    def setUp(self) -> None:
        """Seed one module with enough rows that a rebuild is measurable."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.school = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        ).config["SCHOOL_DB"]
        self.library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.bank_id = _insert_bank(
            self.school,
            self.library_id,
            title="Module 1 Quiz",
            import_key="bank:m1-timing",
        )
        rows = [
            (
                self.bank_id,
                f"q-time-{index}",
                f"Timing stem {index:03d}",
                _mc_payload(f"Timing stem {index:03d}"),
                _now(),
            )
            for index in range(TIMING_ROWS)
        ]
        self.school.conn.executemany(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
            """,
            rows,
        )
        self.school.conn.commit()
        self.school.ensure_rank_bank(self.library_id)
        self.school._ensure_module_bank_link(self.library_id, 1, self.bank_id)
        # Course-wide search rebuilds every module. Give the other modules
        # a real bank so the uncached course time is not just module 1.
        for module_number in range(2, 9):
            bank_id = _insert_bank(
                self.school,
                self.library_id,
                title=f"Module {module_number} Quiz",
                import_key=f"bank:m{module_number}-timing",
            )
            extra = [
                (
                    bank_id,
                    f"q-m{module_number}-{index}",
                    f"Module {module_number} stem {index:03d}",
                    _mc_payload(f"Module {module_number} stem {index:03d}"),
                    _now(),
                )
                for index in range(80)
            ]
            self.school.conn.executemany(
                """
                INSERT INTO questions (
                    bank_id, import_key, item_type, title, payload_json, created_at
                ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
                """,
                extra,
            )
            self.school.conn.commit()
            self.school._ensure_module_bank_link(
                self.library_id, module_number, bank_id
            )
        self.school.invalidate_module_bank_mc_cache(self.library_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _elapsed_ms(self, fn: Any) -> float:
        """Return how long ``fn`` took, in milliseconds."""
        started = time.perf_counter()
        fn()
        return (time.perf_counter() - started) * 1000.0

    def test_cached_search_is_faster_and_matches(self) -> None:
        """Report uncached, cold, and warm times. Warm matches uncached rows."""
        def module_search() -> dict[str, Any]:
            return self.school.search_module_bank_mcs(
                self.library_id, 1, "", limit=500
            )

        def module_query() -> dict[str, Any]:
            return self.school.search_module_bank_mcs(
                self.library_id, 1, "timing stem 01", limit=500
            )

        def course_search() -> dict[str, Any]:
            return self.school.search_bank_scope_mcs(
                self.library_id, "course", 1, "", limit=500
            )

        self.school._module_bank_mc_cache_enabled = False
        uncached_ms = self._elapsed_ms(module_search)
        uncached = _signature(module_search())
        uncached_course = _signature(course_search())
        course_uncached_ms = self._elapsed_ms(course_search)

        self.school._module_bank_mc_cache_enabled = True
        self.school.invalidate_module_bank_mc_cache(self.library_id)
        self.school._module_bank_mc_cache_hits = 0
        self.school._module_bank_mc_cache_misses = 0
        cold_ms = self._elapsed_ms(module_search)
        warm_ms = self._elapsed_ms(module_query)
        self.assertGreater(self.school._module_bank_mc_cache_hits, 0)
        course_cold_ms = self._elapsed_ms(course_search)
        hits_before_second_course = self.school._module_bank_mc_cache_hits
        course_warm_ms = self._elapsed_ms(course_search)
        self.assertGreater(
            self.school._module_bank_mc_cache_hits, hits_before_second_course
        )

        self.school._module_bank_mc_cache_enabled = False
        self.assertEqual(_signature(module_search()), uncached)
        self.assertEqual(_signature(course_search()), uncached_course)

        report = (
            f"rows={TIMING_ROWS}+7x80 "
            f"module_uncached_ms={uncached_ms:.1f} "
            f"module_cold_ms={cold_ms:.1f} "
            f"module_warm_ms={warm_ms:.1f} "
            f"course_uncached_ms={course_uncached_ms:.1f} "
            f"course_cold_ms={course_cold_ms:.1f} "
            f"course_warm_ms={course_warm_ms:.1f} "
            f"hits={self.school._module_bank_mc_cache_hits} "
            f"misses={self.school._module_bank_mc_cache_misses}"
        )
        print(f"MCK-436 timings {report}")
        artifact = Path("/opt/cursor/artifacts")
        if artifact.is_dir():
            (artifact / "mck-436-timings.txt").write_text(report + "\n", encoding="utf-8")
        self.assertLess(warm_ms, uncached_ms)
        self.assertLess(course_warm_ms, course_cold_ms)


class BankPickerParallelRequestTests(unittest.TestCase):
    """The picker starts bank status and the first search together."""

    def test_refresh_starts_status_and_search_before_awaiting(self) -> None:
        """Neither call waits for the other to finish before it is sent."""
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        fn = src.split("async function refreshSearch()", 1)[1]
        fn = fn.split("const revealSearchResults", 1)[0]
        status_at = fn.find("loadModuleBankStatus")
        search_at = fn.find("searchModuleMcs")
        await_at = fn.find("await ")
        self.assertGreaterEqual(status_at, 0)
        self.assertGreaterEqual(search_at, 0)
        self.assertLess(status_at, await_at)
        self.assertLess(search_at, await_at)
        self.assertIn("await refreshSearch();\n  queryEl?.focus();", src)


if __name__ == "__main__":
    unittest.main()
