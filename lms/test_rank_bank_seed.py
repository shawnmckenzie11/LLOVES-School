#!/usr/bin/env python3
"""MCK-169 part B: the 60 reviewed rank items are stored in per-module course banks."""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import rank_bank_seed as seed  # noqa: E402
import test_group_mc_pick_turns_mck155 as turns_base  # noqa: E402
from app import create_app  # noqa: E402
from bank_mc_normalize import normalize_bank_rank  # noqa: E402

EXPECTED = {
    "MCR3U": {1: 4, 2: 8, 3: 6, 4: 7, 5: 5, 6: 3},
    "MCF3M": {1: 3, 2: 4, 3: 3, 4: 4, 5: 3, 6: 1, 7: 4, 8: 5},
}


def _ensure_in_process(db_path: str, data_dir: str, library_id: int, barrier: Any, out: Any) -> None:
    """One 'gunicorn worker': its own SchoolDB connection on the shared file."""
    sys.path.insert(0, str(LMS_DIR))
    sys.path.insert(0, str(LMS_DIR.parent))
    from school_db import SchoolDB

    school = SchoolDB(Path(db_path), Path(data_dir), live_database_url="")
    try:
        barrier.wait(timeout=60)
        summary = school.ensure_rank_bank(library_id)
        out.put(("ok", (summary or {}).get("inserted", 0)))
    except Exception as exc:  # pragma: no cover - reported to the parent
        out.put(("error", repr(exc)))
    finally:
        school.close()


class RankCatalogueTests(unittest.TestCase):
    """The git-tracked catalogue matches the reviewed file's shape."""

    def test_sixty_items_valid_and_placed(self) -> None:
        """33 MCR3U + 27 MCF3M, unique ids, C1–C4 slots, each normalizes with its key."""
        total = 0
        ids: set[str] = set()
        notes = keys = 0
        for code, modules in EXPECTED.items():
            version, items = seed.load_rank_catalogue(code)
            self.assertTrue(version)
            counts: dict[int, int] = {}
            for index, item in enumerate(items):
                counts[item["module"]] = counts.get(item["module"], 0) + 1
                self.assertNotIn(item["id"], ids)
                ids.add(item["id"])
                self.assertIn(item["live_class_slot"], {"C1", "C2", "C3", "C4"})
                self.assertIn(item["md_status"], {"approve", "fixed"})
                title, payload = seed.stored_row(item)
                self.assertTrue(seed.row_is_pristine(title, payload))
                live, reason = normalize_bank_rank(question_id=index + 1, bank_id=1, title=title, payload=payload)
                self.assertIsNotNone(live, f"{item['id']}: {reason}")
                self.assertEqual(live["rank_key"], payload["rank_key"], item["id"])
                notes += bool(payload.get("teacher_note"))
                keys += bool(payload.get("teacher_key"))
            self.assertEqual(counts, modules, code)
            total += len(items)
        self.assertEqual(total, 60)
        self.assertEqual((notes, keys), (4, 4))
        self.assertEqual(seed.load_rank_catalogue("MHF4U"), ("", []))


class RankBankSeedTests(unittest.TestCase):
    """Seeding is idempotent, keeps teacher edits, retires, and is concurrency-safe."""

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "lloves.sqlite"
        self.app = create_app(db_path=self.db_path, data_dir=self.root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.libs = {
            code: int(self.school.create_library(code, origin="upload")["id"]) for code in ("MCR3U", "MCF3M", "MHF4U")
        }

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    # helpers -------------------------------------------------------------
    def _rows(self, library_id: int) -> list[dict[str, Any]]:
        rows = self.school.conn.execute(
            """
            SELECT q.id, q.import_key, q.title, q.payload_json, q.item_type, b.import_key AS bank_key
            FROM questions q JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ? AND b.import_key LIKE 'rank-bank:%'
            ORDER BY q.id
            """,
            (library_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _banks(self, library_id: int) -> list[str]:
        rows = self.school.conn.execute(
            "SELECT import_key FROM question_banks WHERE library_id = ? AND import_key LIKE 'rank-bank:%' ORDER BY import_key",
            (library_id,),
        ).fetchall()
        return [str(row["import_key"]) for row in rows]

    def _custom_catalogue(self, code: str, edit: Any) -> Path:
        """Copy the real catalogue to a temp dir and apply ``edit(items)``."""
        out = self.root / f"cat-{len(list(self.root.glob('cat-*')))}"
        out.mkdir()
        shutil.copy(seed.RANK_ITEMS_DIR / f"{code}.json", out / f"{code}.json")
        doc = json.loads((out / f"{code}.json").read_text(encoding="utf-8"))
        doc["items"] = edit(doc["items"])
        (out / f"{code}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return out

    def _ids_by_module(self, code: str) -> dict[int, set[int]]:
        lib = self.libs[code]
        by_key = {row["import_key"]: int(row["id"]) for row in self._rows(lib)}
        _v, items = seed.load_rank_catalogue(code)
        out: dict[int, set[int]] = {}
        for item in items:
            out.setdefault(int(item["module"]), set()).add(by_key[seed.rank_item_key(item["id"])])
        return out

    # tests ---------------------------------------------------------------
    def test_seed_twice_no_duplicates(self) -> None:
        """Second seed is all unchanged; one bank per module; rows are essay ranks."""
        lib = self.libs["MCR3U"]
        first = seed.seed_rank_bank(self.school, lib, "MCR3U")
        self.assertEqual(first["inserted"], 33)
        again = seed.seed_rank_bank(self.school, lib, "MCR3U")
        self.assertEqual((again["inserted"], again["updated"], again["unchanged"]), (0, 0, 33))
        rows = self._rows(lib)
        self.assertEqual(len(rows), 33)
        self.assertEqual(len({r["import_key"] for r in rows}), 33)
        self.assertTrue(all(r["item_type"] == "essay_question" for r in rows))
        self.assertTrue(all(r["import_key"].startswith("rank:MCR3U-M") for r in rows))
        self.assertEqual(self._banks(lib), [f"rank-bank:MCR3U:M{n}" for n in range(1, 7)])
        payload = json.loads(rows[0]["payload_json"])
        for field in ("rank_key", "rank_options", "text", "live_class_slot", "catalogue_id", "expectation_codes", "md_status"):
            self.assertIn(field, payload)
        self.assertEqual(payload["type"], "rank")
        self.assertEqual(payload["bank_kind"], "")
        self.assertTrue(self.school.conn.execute(
            "SELECT 1 FROM course_module_bank_links WHERE library_id = ? AND module_number = 1", (lib,)
        ).fetchone())
        self.assertEqual(seed.seed_rank_bank(self.school, self.libs["MHF4U"], "MHF4U")["inserted"], 0)

    def test_teacher_edit_survives_reseed_and_others_update(self) -> None:
        """A changed catalogue updates pristine rows and never an edited one."""
        lib = self.libs["MCR3U"]
        seed.seed_rank_bank(self.school, lib, "MCR3U")
        rows = self._rows(lib)
        edited, other = rows[0], rows[1]
        payload = json.loads(edited["payload_json"])
        payload["text"] = "Teacher reworded this"
        self.school.conn.execute(
            "UPDATE questions SET payload_json = ? WHERE id = ?", (json.dumps(payload), edited["id"])
        )
        ids = {edited["import_key"], other["import_key"]}

        def bump(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for item in items:
                if seed.rank_item_key(item["id"]) in ids:
                    item["payload"]["text"] += " (v2)"
            return items

        cat = self._custom_catalogue("MCR3U", bump)
        summary = seed.seed_rank_bank(self.school, lib, "MCR3U", items_dir=cat)
        self.assertEqual((summary["updated"], summary["kept_edited"], summary["unchanged"]), (1, 1, 31))
        after = {r["import_key"]: json.loads(r["payload_json"]) for r in self._rows(lib)}
        self.assertEqual(after[edited["import_key"]]["text"], "Teacher reworded this")
        self.assertTrue(after[other["import_key"]]["text"].endswith("(v2)"))
        again = seed.seed_rank_bank(self.school, lib, "MCR3U", items_dir=cat)
        self.assertEqual((again["updated"], again["kept_edited"], again["unchanged"]), (0, 1, 32))

    def test_retired_rows_flagged_hidden_and_restored(self) -> None:
        """Dropped items are flagged (not deleted), hidden from search and import; they come back."""
        lib = self.libs["MCF3M"]
        seed.seed_rank_bank(self.school, lib, "MCF3M")
        _v, items = seed.load_rank_catalogue("MCF3M")
        gone = next(i for i in items if i["module"] == 2)
        gone_key = seed.rank_item_key(gone["id"])
        cat = self._custom_catalogue("MCF3M", lambda rows: [r for r in rows if r["id"] != gone["id"]])
        summary = seed.seed_rank_bank(self.school, lib, "MCF3M", items_dir=cat)
        self.assertEqual(summary["retired"], 1)
        row = next(r for r in self._rows(lib) if r["import_key"] == gone_key)
        self.assertTrue(json.loads(row["payload_json"])["retired"])
        self.assertEqual(len(self._rows(lib)), 27)
        self.school.__dict__["_rank_bank_memo"] = {lib: ("MCF3M", seed.catalogue_version("MCF3M"))}
        found = {int(r["question_id"]) for r in self.school.search_module_bank_mcs(lib, 2, "")["items"]}
        self.assertNotIn(int(row["id"]), found)
        self.assertEqual(len(found), 3)
        with self.assertRaisesRegex(ValueError, "retired"):
            self.school.import_mc_to_class_playlist(1, "M2", "C1", int(row["id"]), library_id=lib, page_number=1)
        back = seed.seed_rank_bank(self.school, lib, "MCF3M")
        self.assertEqual(back["restored"], 1)
        row = next(r for r in self._rows(lib) if r["import_key"] == gone_key)
        self.assertNotIn("retired", json.loads(row["payload_json"]))

    def test_ensure_is_cheap_when_current_and_never_relinks(self) -> None:
        """Memo skips the DB; a stale version reseeds; an unconfirmed bank stays unconfirmed."""
        lib = self.libs["MCR3U"]
        self.assertEqual(self.school.ensure_rank_bank(lib)["inserted"], 33)
        with mock.patch.object(seed, "rank_bank_is_current", side_effect=AssertionError("queried")):
            self.assertIsNone(self.school.ensure_rank_bank(lib))
            self.assertIsNone(self.school.ensure_rank_bank(self.libs["MHF4U"]))
            self.assertIsNone(self.school.ensure_rank_bank(self.libs["MHF4U"]))
        self.school.__dict__["_rank_bank_memo"] = {}
        self.assertIsNone(self.school.ensure_rank_bank(lib))  # one query, already current
        m1_bank = next(
            int(r["bank_id"]) for r in self.school.list_module_bank_links(lib, 1) if r["import_key"] == "rank-bank:MCR3U:M1"
        )
        self.school.confirm_module_bank_links(lib, 1, [])
        self.school.conn.execute(
            "UPDATE question_banks SET settings_json = '{}' WHERE library_id = ? AND import_key LIKE 'rank-bank:%'", (lib,)
        )
        self.school.__dict__["_rank_bank_memo"] = {}
        summary = self.school.ensure_rank_bank(lib)
        self.assertEqual(summary["unchanged"], 33)
        self.assertNotIn(m1_bank, {int(r["bank_id"]) for r in self.school.list_module_bank_links(lib, 1)})
        self.assertTrue(seed.rank_bank_is_current(self.school, lib, "MCR3U"))

    def test_concurrent_ensure_threads_and_processes_no_duplicates(self) -> None:
        """Separate connections (threads and spawned worker processes) seed once between them."""
        lib = self.libs["MCF3M"]
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(4)
        out = ctx.Queue()
        procs = [
            ctx.Process(target=_ensure_in_process, args=(str(self.db_path), str(self.root), lib, barrier, out))
            for _ in range(4)
        ]
        for proc in procs:
            proc.start()
        results = [out.get(timeout=120) for _ in procs]
        for proc in procs:
            proc.join(timeout=60)
        self.assertTrue(all(kind == "ok" for kind, _ in results), results)
        self.assertEqual(sum(n for _, n in results), 27)
        lib2 = self.libs["MCR3U"]
        from school_db import SchoolDB

        schools = [SchoolDB(self.db_path, self.root, live_database_url="") for _ in range(4)]
        gate = threading.Barrier(4)
        errors: list[str] = []

        def run(school: Any) -> None:
            try:
                gate.wait(timeout=30)
                school.ensure_rank_bank(lib2)
            except Exception as exc:  # pragma: no cover
                errors.append(repr(exc))

        threads = [threading.Thread(target=run, args=(s,)) for s in schools]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        for s in schools:
            s.close()
        self.assertEqual(errors, [])
        for code, library_id, n_banks in (("MCF3M", lib, 8), ("MCR3U", lib2, 6)):
            rows = self._rows(library_id)
            self.assertEqual(len(rows), 60 - 33 if code == "MCF3M" else 33)
            self.assertEqual(len({r["import_key"] for r in rows}), len(rows))
            self.assertEqual(len(self._banks(library_id)), n_banks)
            links = self.school.conn.execute(
                "SELECT module_number, COUNT(*) AS n FROM course_module_bank_links l JOIN question_banks b ON b.id = l.bank_id "
                "WHERE l.library_id = ? AND b.import_key LIKE 'rank-bank:%' GROUP BY module_number",
                (library_id,),
            ).fetchall()
            self.assertEqual({int(r["module_number"]): int(r["n"]) for r in links}, {m: 1 for m in EXPECTED[code]})

    def test_all_sixty_searchable_in_their_module_only(self) -> None:
        """Bank search (which ensures) finds each item in its module, nowhere else; Course Wide finds all."""
        for code in ("MCR3U", "MCF3M"):
            lib = self.libs[code]
            self.school.search_module_bank_mcs(lib, 1, "")  # first search seeds
            expected = self._ids_by_module(code)
            every = set().union(*expected.values())
            for number in range(1, 9):
                found = {
                    int(r["question_id"])
                    for r in self.school.search_module_bank_mcs(lib, number, "", limit=500)["items"]
                    if r.get("type") == "rank"
                }
                self.assertEqual(found, expected.get(number, set()), f"{code} M{number}")
                rows = [r for r in self.school.search_module_bank_mcs(lib, number, "")["items"] if r.get("type") == "rank"]
                self.assertTrue(all(r.get("rank_key") and r.get("question_type") == "rank" for r in rows))
            course = {
                int(r["question_id"])
                for r in self.school.search_bank_scope_mcs(lib, "course", 1, "", limit=500)["items"]
                if r.get("type") == "rank"
            }
            self.assertEqual(course, every)
        self.assertEqual(self._rows(self.libs["MHF4U"]), [])


class RankBankImportPublishTests(unittest.TestCase):
    """A seeded item from each course imports and publishes as a keyed live rank."""

    def _class_for(self, code: str) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        self.teacher_id = int(teacher["id"])
        offering = self.school.assign_course(teacher_user_id=self.teacher_id, ontario_code=code)
        self.library_id = int(self.school.create_library(code, origin="upload")["id"])
        self.school.attach_library(int(offering["id"]), self.library_id)
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27", semester="Semester 1", course_code=code, days_preset="M/W/F",
                time_label="2:00pm", codenames=["Maple"], offering_id=int(offering["id"]),
                teacher_user_id=self.teacher_id,
            )["id"]
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _import_and_publish(self, code: str, candidate_id: str) -> None:
        self._class_for(code)
        _v, items = seed.load_rank_catalogue(code)
        item = next(i for i in items if i["id"] == candidate_id)
        module = f"M{item['module']}"
        slot = item["live_class_slot"]
        body = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/{module}/mc-search", query_string={"q": item["payload"]["text"][:30]}
        ).get_json()
        row = next(r for r in body["items"] if r.get("type") == "rank")
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/import-mc",
            json={"question_id": row["question_id"], "page_number": 1, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        placed = rv.get_json()["placement"]["item"]
        self.assertEqual(placed["rank_key"], item["payload"]["rank_key"])
        self.assertNotIn("live_class_slot", placed)
        session_id = int(self.school.start_live_class_session(self.class_id, self.teacher_id)["id"])
        self.client.post(
            f"/api/live-sessions/{session_id}/teacher-state",
            json={"live_module": module, "live_slot": slot, "stage": "round", "page_id": "round_1"},
        )
        self.school.ensure_live_session_items(session_id)
        live = next(r for r in self.school.list_live_session_items(session_id) if r["item_id"] == placed["id"])
        rv = self.client.post(
            f"/api/live-sessions/{session_id}/items/{live['id']}/publish", json={"publish_mode": "individual"}
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        prompt = self.school._prompt_for_live_item(self.school.get_live_session_item(session_id, int(live["id"])))
        self.assertEqual(prompt["kind"], "rank")
        self.assertEqual(prompt["payload"]["rank_key"], item["payload"]["rank_key"])
        self.assertEqual(
            [o["label"] for o in prompt["payload"]["rank_options"]],
            [o["label"] for o in item["payload"]["rank_options"]],
        )

    def test_mcr3u_seed_item(self) -> None:
        """The MCR3U M2 seed item (factor by grouping) plays as a keyed rank."""
        self._import_and_publish("MCR3U", "MCR3U-M2-SEED-factor-grouping")

    def test_mcf3m_item(self) -> None:
        """An MCF3M M5 item plays as a keyed rank."""
        self._import_and_publish("MCF3M", "MCF3M-M5-flagpole-distance")


class OpsGateFindingsTests(unittest.TestCase):
    """Ops gate on 6047e1d: HIGH-1 picker linking, MED-1 cap, MED-2 edits."""

    _class_for = RankBankImportPublishTests._class_for
    tearDown = RankBankImportPublishTests.tearDown

    def setUp(self) -> None:
        self._class_for("MCR3U")

    # helpers -------------------------------------------------------------
    def _bank(self, title: str, key: str) -> int:
        cur = self.school.conn.execute(
            "INSERT INTO question_banks (library_id, import_key, title, settings_json, created_at) VALUES (?, ?, ?, '{}', '2026-10-03')",
            (self.library_id, key, title),
        )
        return int(cur.lastrowid)

    def _mc(self, bank_id: int, key: str, stem: str) -> int:
        payload = {
            "stem_html": f"<p>{stem}</p>",
            "choices": [
                {"id": "a", "html": "Right", "correct": True},
                {"id": "b", "html": "Wrong", "correct": False},
                {"id": "c", "html": "Other", "correct": False},
            ],
        }
        cur = self.school.conn.execute(
            "INSERT INTO questions (bank_id, import_key, item_type, title, payload_json, created_at) "
            "VALUES (?, ?, 'multiple_choice_question', ?, ?, '2026-10-03')",
            (bank_id, key, stem[:80], json.dumps(payload)),
        )
        return int(cur.lastrowid)

    def _open_import(self, module: str) -> dict[str, Any]:
        """What bank_mc_picker.js does on open: status → auto-confirm → status → search."""
        base = f"/api/staff/class/{self.class_id}/module-banks"
        status = self.client.get(f"{base}?module={module}").get_json()
        needs = bool(status["needs_confirmation"])
        if needs:
            source = status.get("recommended") or status.get("suggested") or []
            ids = list(dict.fromkeys([int(r["bank_id"]) for r in status["confirmed"]] + [int(r["bank_id"]) for r in source]))
            if ids:
                rv = self.client.post(f"{base}/confirm", json={"module": module, "bank_ids": ids})
                self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        after = self.client.get(f"{base}?module={module}").get_json()
        search = self.client.get(f"{base}/{module}/mc-search").get_json()
        by_type: dict[str, int] = {}
        for row in search["items"]:
            by_type[row.get("type") or "mc"] = by_type.get(row.get("type") or "mc", 0) + 1
        return {
            "needs_on_open": needs,
            "needs_after": bool(after["needs_confirmation"]),
            "linked": sorted(r["title"] for r in after["confirmed"]),
            "suggested": [r["import_key"] for r in after["suggested"]],
            "by_type": by_type,
            "search": search,
        }

    # HIGH-1 --------------------------------------------------------------
    def test_high1_picker_still_links_teacher_module_banks(self) -> None:
        """Ops repro: Module 5/6 Quiz banks (3 MC each), open M6 then M5; base behaviour holds plus rank items."""
        quiz5 = self._bank("Module 5 Quiz", "bank:m5quiz")
        quiz6 = self._bank("Module 6 Quiz", "bank:m6quiz")
        for n in range(3):
            self._mc(quiz5, f"q5-{n}", f"Module five question {n}")
            self._mc(quiz6, f"q6-{n}", f"Module six question {n}")
        m6 = self._open_import("M6")
        self.assertTrue(m6["needs_on_open"])
        self.assertIn("Module 6 Quiz", m6["linked"])
        self.assertEqual(m6["by_type"], {"rank": 3, "mc": 3})
        self.assertFalse(m6["needs_after"])
        # Opening M6 seeded every module's rank bank; M5 must still prompt and link Quiz 5.
        m5 = self._open_import("M5")
        self.assertTrue(m5["needs_on_open"])
        self.assertEqual(m5["linked"], ["MCR3U Module 5 rank items", "Module 5 Quiz"])
        self.assertEqual(m5["by_type"], {"rank": 5, "mc": 3})
        self.assertFalse(m5["needs_after"])
        self.assertFalse(any(key.startswith("rank-bank:") for key in m5["suggested"] + m6["suggested"]))
        # A module with only its rank bank still reads as "nothing linked" (base wording).
        m1 = self.client.get(f"/api/staff/class/{self.class_id}/module-banks?module=M1").get_json()
        self.assertTrue(m1["needs_confirmation"])
        self.assertEqual(m1["suggested"], [])
        self.assertEqual([r["import_key"] for r in m1["confirmed"]], ["rank-bank:MCR3U:M1"])

    # MED-1 ---------------------------------------------------------------
    def test_med1_empty_search_lists_rank_rows_first_within_the_cap(self) -> None:
        """250 MC rows sorting before the rank bank: the default 200 still shows all 8 M2 rank items first."""
        big = self._bank("MCR3U Module 2 curriculum bank", "bank:m2big")
        for n in range(250):
            self._mc(big, f"big-{n}", f"Big bank stem {n:03d}")
        self.school.confirm_module_bank_links(self.library_id, 2, [big])
        self.school.ensure_rank_bank(self.library_id)
        rank_bank = next(
            int(r["id"]) for r in self.school.conn.execute(
                "SELECT id FROM question_banks WHERE library_id = ? AND import_key = 'rank-bank:MCR3U:M2'", (self.library_id,)
            ).fetchall()
        )
        self.school.confirm_module_bank_links(self.library_id, 2, [big, rank_bank])
        body = self.client.get(f"/api/staff/class/{self.class_id}/module-banks/M2/mc-search").get_json()
        types = [row.get("type") for row in body["items"]]
        self.assertEqual(len(types), 200)
        self.assertEqual(types[:8], ["rank"] * 8)
        self.assertNotIn("rank", types[8:])
        self.assertEqual((body["total"], body["filtered"]), (258, 258))
        mc_order = [row["text"] for row in body["items"][8:]]
        self.assertEqual(mc_order, [f"Big bank stem {n:03d}" for n in range(192)])
        hit = self.client.get(f"/api/staff/class/{self.class_id}/module-banks/M2/mc-search", query_string={"q": "big bank stem 01"}).get_json()
        self.assertEqual([r["text"] for r in hit["items"]], [f"Big bank stem {n:03d}" for n in range(10, 20)])
        course = self.client.get(f"/api/staff/class/{self.class_id}/module-banks/course/mc-search").get_json()
        ctypes = [row.get("type") for row in course["items"]]
        n_rank = ctypes.count("rank")
        self.assertEqual(n_rank, 33)
        self.assertEqual(ctypes[:n_rank], ["rank"] * n_rank)
        self.assertEqual(course["total"], 250 + 33)

    # MED-2 ---------------------------------------------------------------
    def test_med2_rank_rows_cannot_be_edited(self) -> None:
        """Bank PATCH and overlay PATCH on rank rows are 400; nothing is written; MC edits still save."""
        self.school.ensure_rank_bank(self.library_id)
        row = self.school.conn.execute(
            "SELECT q.id, q.bank_id, q.title, q.payload_json FROM questions q JOIN question_banks b ON b.id = q.bank_id "
            "WHERE b.library_id = ? AND q.import_key = 'rank:MCR3U-M1-point-mapping-chain'",
            (self.library_id,),
        ).fetchone()
        qid, bank_id = int(row["id"]), int(row["bank_id"])
        rv = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{bank_id}/questions/{qid}",
            json={"stem_text": "EDITED BY TEACHER", "points": 2},
        )
        self.assertEqual(rv.status_code, 400, rv.get_data(as_text=True))
        self.assertIn("Rank items can't be edited", rv.get_json()["error"])
        rv = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-overlays/{qid}",
            json={"stem_text": "EDITED", "options": ["a", "b", "c"], "correct_answer": "A"},
        )
        self.assertEqual(rv.status_code, 400, rv.get_data(as_text=True))
        self.assertIn("Rank items can't be edited", rv.get_json()["error"])
        self.assertIsNone(self.school.conn.execute(
            "SELECT 1 FROM library_question_overlays WHERE question_id = ?", (qid,)
        ).fetchone())
        after = self.school.conn.execute("SELECT title, payload_json FROM questions WHERE id = ?", (qid,)).fetchone()
        self.assertEqual((after["title"], after["payload_json"]), (row["title"], row["payload_json"]))
        # Staff-authored rank rows (live bank) are blocked the same way.
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={"bank_scope": "M1", "type": "rank", "stem_text": "Mine", "options": ["a", "b", "c"]},
        )
        mine = rv.get_json()["question"]
        rv = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{mine['bank_id']}/questions/{mine['id']}",
            json={"stem_text": "Changed"},
        )
        self.assertEqual(rv.status_code, 400)
        # MC edits are unchanged.
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={"bank_scope": "M1", "type": "mc", "stem_text": "Slope?", "options": ["1", "2"], "correct_answer": "A"},
        )
        mc = rv.get_json()["question"]
        rv = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{mc['bank_id']}/questions/{mc['id']}",
            json={"stem_text": "Slope of y = x?", "options": ["1", "2"], "correct_answer": "A"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        picker = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn('<span class="hint compact">Read-only</span>', picker)
        banks_tab = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        self.assertIn("editMode && isRank(selected)", banks_tab)


class TeacherNoteNeverReachesStudentsTests(unittest.TestCase):
    """teacher_note / teacher_key stay off every student surface (state, SSE, after Close)."""

    setUp_base = turns_base.PickThenAgreeAndTurnsTests.setUp
    tearDown = turns_base.PickThenAgreeAndTurnsTests.tearDown

    def setUp(self) -> None:
        self.setUp_base()
        offering = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
        ).fetchone()
        self.library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.school.attach_library(int(offering["offering_id"]), self.library_id)

    def _student_surfaces(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, client in self.students.items():
            out[f"{name} state"] = client.get("/api/student/state").get_data(as_text=True)
            out[f"{name} live-prompt"] = client.get("/api/student/live-prompt").get_data(as_text=True)
            out[f"{name} page"] = client.get("/student").get_data(as_text=True)
            out[f"{name} sse"] = client.get(f"/api/live/session/{self.session_id}/events").get_data(as_text=True)
        guest = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, None, participant_uuid="guest-zed", codename="Zed", unmatched=True
        )
        out["guest"] = json.dumps(guest, default=str)
        out["metadata"] = json.dumps(self.school.student_live_class_metadata_for_session(self.session_id), default=str)
        for name, sid in self.ids.items():
            out[f"{name} items"] = json.dumps(self.school.student_live_items_payload(self.session_id, sid), default=str)
        return out

    def test_note_and_key_hidden_through_publish_answer_and_close(self) -> None:
        """Real seeded M1 item with a teacher_note, plus a keyed item, on M1 C2."""
        _v, items = seed.load_rank_catalogue("MCR3U")
        noted = next(i for i in items if i["id"] == "MCR3U-M1-point-mapping-chain")
        note = noted["payload"]["teacher_note"]
        self.assertTrue(note)
        self.school.ensure_rank_bank(self.library_id)
        qid = int(self.school.conn.execute(
            "SELECT q.id FROM questions q JOIN question_banks b ON b.id = q.bank_id WHERE b.library_id = ? AND q.import_key = ?",
            (self.library_id, seed.rank_item_key(noted["id"])),
        ).fetchone()["id"])
        keyed = json.loads(self.school.conn.execute("SELECT payload_json FROM questions WHERE id = ?", (qid,)).fetchone()[0])
        keyed["teacher_key"] = {"check": "KEY-SECRET (x, y) -> (x/k + d, ay + c)"}
        keyed_title, keyed_payload = "keyed copy", dict(keyed, text="Keyed copy: order the moves")
        bank_id = int(self.school.conn.execute("SELECT bank_id FROM questions WHERE id = ?", (qid,)).fetchone()[0])
        cur = self.school.conn.execute(
            "INSERT INTO questions (bank_id, import_key, item_type, title, payload_json, created_at) "
            "VALUES (?, 'test:keyed', 'essay_question', ?, ?, '2026-10-03')",
            (bank_id, keyed_title, json.dumps(keyed_payload)),
        )
        keyed_qid = int(cur.lastrowid)
        secrets = [note[:40], "KEY-SECRET", "teacher_note", "teacher_key", "rank_key"]
        published = []
        for page, question_id in ((4, qid), (4, keyed_qid)):
            rv = self.client.post(
                f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
                json={"question_id": question_id, "page_number": page, "stage": "round"},
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            placed = rv.get_json()["placement"]["item"]
            self.assertIn("teacher_note", placed)
            self.school.ensure_live_session_items(self.session_id)
            row = next(r for r in self.school.list_live_session_items(self.session_id) if r["item_id"] == placed["id"])
            rv = self.client.post(
                f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish", json={"publish_mode": "individual"}
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            published.append(rv.get_json()["item"])
            staff = self.school.get_live_session_item(self.session_id, int(row["id"]))
            self.assertEqual(staff["item"]["teacher_note"], note)
            surfaces = self._student_surfaces()
            self.assertIn("order", surfaces["Ava live-prompt"].lower())
            for where, text in surfaces.items():
                for secret in secrets:
                    self.assertNotIn(secret, text, f"published q{question_id}: {where}")
            prompt = self.school._prompt_for_live_item(staff)
            rv = self.students["Ava"].post(
                "/api/student/live-prompt/response",
                json={"prompt_id": prompt["id"], "response": {"order": [o["id"] for o in prompt["payload"]["rank_options"]]}},
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            for secret in secrets:
                self.assertNotIn(secret, rv.get_data(as_text=True), "answer response")
            rv = self.client.post(f"/api/live-sessions/{self.session_id}/items/{row['id']}/close")
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            for where, text in self._student_surfaces().items():
                for secret in secrets:
                    self.assertNotIn(secret, text, f"closed q{question_id}: {where}")


if __name__ == "__main__":
    unittest.main()
