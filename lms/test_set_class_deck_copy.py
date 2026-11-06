#!/usr/bin/env python3
"""MCK-132: Set Class → Course deck copies between a teacher's own sections.

Server (S1): the From list (``sources``) and the cross-section decks are
this teacher's current, non-archived sections of the same course, sibling
sections first, then this class. Admins (IT) see only their own sections.
Decks sort naturally. A copy naming another teacher's section, an archived
section, or another course is refused. A Course deck copy onto a slot that
already has a deck needs ``replace: true`` (409 otherwise), so Next can no
longer overwrite a deck silently.

Client (S2/S3): pure helpers in ``deck_seed_help.js`` and the real
``staff_ap.js`` Set Class flow driven in node: From → Source deck →
Replace deck / Keep current / Copy deck, the Next hold, and the success
line.

Ops smoke (:8789 / :8790), teacher with MCR3U and MCR3U-2:

1. MCR3U → Run Live Class → M2 · C3 → click Course deck.
2. From = ``MCR3U-2 · N decks`` (preselected), Source deck = ``M2 C3 · …
   · same slot``. Amber strip with both question counts.
3. Press Next → red "Replace the deck or keep current first."; the class
   does not start (no /begin in devtools).
4. Keep current → strip hides. Course deck again → Replace deck → green ✓,
   chip back on Use current. Next → class opens with the copied deck.
5. Edit MCR3U-2's M2 C3 afterwards → MCR3U is unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from school_db import DeckCopyLiveConflict, DeckReplaceNotConfirmed, SchoolDB  # noqa: E402
from test_deck_seed_sticky_mode import STAFF_AP_HARNESS  # noqa: E402

NODE = shutil.which("node")


_NODE_RENDER = r"""
import { deckCopyConfirmState, deckCopySuccessText } from "./static/deck_seed_help.js";
let raw = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) raw += chunk;
const body = JSON.parse(raw);
const out = body.toast
  ? { toast: deckCopySuccessText(body.toast) }
  : deckCopyConfirmState(body);
console.log(JSON.stringify(out));
"""


def _render_deck_copy(payload: dict) -> dict:
    """Render the Set Class strip or toast through the real helper."""

    if not NODE:
        raise unittest.SkipTest("node is required")
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", _NODE_RENDER],
        cwd=str(LMS_DIR),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout.strip().splitlines()[-1])



def _without_rides(signature: list) -> list:
    """Drop engine-ride ids: deck copies skip them (MCK-443)."""

    from school_db import SchoolDB

    rides = {token.replace("-", "_") for token in SchoolDB._ENGINE_RIDE_IDS}
    return [
        pair for pair in signature if pair[0].lower().replace("-", "_") not in rides
    ]

def _question_signature(metadata: dict) -> list[tuple[str, str]]:
    """Return ``(id, text)`` pairs for merged deck questions."""

    rows = []
    for row in metadata.get("questions") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        text = str(row.get("text") or row.get("prompt") or row.get("title") or "")
        rows.append((str(row["id"]), text))
    return rows


def _deck_table_snapshot(school, class_id: int) -> str:
    """Return a stable dump of the deck tables for one class."""

    chunks = []
    for table in (
        "class_live_playlist_placements",
        "class_live_playlist_item_overrides",
        "class_live_playlist_pages",
        "class_live_media_overlays",
        "class_live_deck_seeds",
    ):
        rows = school.conn.execute(
            f"SELECT * FROM {table} WHERE class_id = ? ORDER BY id",
            (int(class_id),),
        ).fetchall()
        chunks.append(repr((table, [tuple(row) for row in rows])))
    return "\n".join(chunks)


class SetClassDeckCopyServerTests(unittest.TestCase):
    """Scoping, ordering, refusals and the replace confirm on the server."""

    def setUp(self) -> None:
        """Teacher A: MCF3M, MCF3M-2, MCF3M-3 and MCR3U. Teacher B: MCF3M."""

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school: SchoolDB = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.tid = int(self.teacher["id"])
        self.other = self.school.register_staff("other-teacher@gmail.com")
        self.oid = int(self.other["id"])
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        self.off1 = self.school.assign_course(teacher_user_id=self.tid, ontario_code="MCF3M")
        self.off2 = self.school.assign_course(
            teacher_user_id=self.tid, ontario_code="MCF3M", new_section=True
        )
        self.off3 = self.school.assign_course(
            teacher_user_id=self.tid, ontario_code="MCF3M", new_section=True
        )
        self.off_mcr = self.school.assign_course(teacher_user_id=self.tid, ontario_code="MCR3U")
        self.off_b = self.school.assign_course(teacher_user_id=self.oid, ontario_code="MCF3M")
        self.a1 = self._make_class(self.off1, self.tid)
        self.a2 = self._make_class(self.off2, self.tid)
        self.a3 = self._make_class(self.off3, self.tid)
        self.mcr = self._make_class(self.off_mcr, self.tid)
        self.b1 = self._make_class(self.off_b, self.oid)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""

        self.school.close()
        self.tmp.cleanup()

    def _make_class(self, offering: dict, teacher_id: int) -> int:
        """Create a game-show class for one offering, as the classes route does."""

        semester = self.school.get_semester(int(offering["semester_id"]))
        created = self.school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=str(offering["ontario_code"]),
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher_id),
        )
        return int(created["id"])

    def _options(self, class_id: int, module: str = "M2", slot: str = "C3") -> dict:
        """GET deck-seed-options over HTTP as teacher A."""

        got = self.client.get(
            f"/api/staff/class/{class_id}/live-lessons/{module}/{slot}/deck-seed-options"
        )
        self.assertEqual(got.status_code, 200, got.get_json())
        return got.get_json()

    def _seed(self, class_id: int, module: str, slot: str, body: dict):
        """POST deck-seed over HTTP as teacher A."""

        return self.client.post(
            f"/api/staff/class/{class_id}/live-lessons/{module}/{slot}/deck-seed",
            json=body,
        )

    def _deck(self, class_id: int, module: str, slot: str) -> list[tuple[str, str]]:
        """Question signature of one class's working deck."""

        return _question_signature(
            self.school.live_class_metadata_for_class_lesson(
                class_id, module, slot, fresh=True
            )
        )

    def _edit_first_question(self, class_id: int, module: str, slot: str, text: str) -> None:
        """Make a working copy on ``class_id`` and edit its first question."""

        self.school.apply_class_deck_seed(
            class_id, module, slot, mode="course", source_module="M1", source_slot="C2"
        )
        row = self.school.conn.execute(
            """
            SELECT id, item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND module = ? AND slot = ?
            ORDER BY id LIMIT 1
            """,
            (class_id, module, slot),
        ).fetchone()
        payload = json.loads(row["item_json"])
        payload["text"] = text
        self.school.conn.execute(
            "UPDATE class_live_playlist_placements SET item_json = ? WHERE id = ?",
            (json.dumps(payload), int(row["id"])),
        )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=class_id)

    # --- scoping -----------------------------------------------------------

    def test_sources_are_own_current_same_course_sections_sibling_first(self) -> None:
        """From lists MCF3M-2, MCF3M-3, then this class; never B or MCR3U."""

        body = self._options(self.a1)
        self.assertEqual(
            [(row["class_id"], row["section_code"], row["is_this_class"]) for row in body["sources"]],
            [
                (self.a2, "MCF3M-2", False),
                (self.a3, "MCF3M-3", False),
                (self.a1, "MCF3M", True),
            ],
        )
        deck_classes = {row["class_id"] for row in body["decks"]}
        self.assertEqual(deck_classes, {self.a1, self.a2, self.a3})
        for row in body["sources"]:
            self.assertEqual(
                row["deck_count"],
                len([d for d in body["decks"] if d["class_id"] == row["class_id"]]),
            )
        # Archive MCF3M-3: it leaves both lists.
        self.school.archive_offering(int(self.off3["id"]))
        body = self._options(self.a1)
        self.assertEqual(
            [row["class_id"] for row in body["sources"]], [self.a2, self.a1]
        )
        self.assertNotIn(self.a3, {row["class_id"] for row in body["decks"]})

    def test_section_labels_match_after_archive(self) -> None:
        """Archiving section 1 renames the rest by active rank, in every list."""

        self.school.archive_offering(int(self.off1["id"]))
        renamed = self._options(self.a2)
        codes = {row["class_id"]: row["section_code"] for row in renamed["sources"]}
        self.assertEqual(codes[self.a2], "MCF3M")
        self.assertEqual(codes[self.a3], "MCF3M-2")
        sibling_labels = [
            row["label"] for row in renamed["decks"] if row["class_id"] == self.a3
        ]
        self.assertTrue(sibling_labels)
        self.assertTrue(all(label.startswith("MCF3M-2 · ") for label in sibling_labels))
        own = next(row for row in renamed["sources"] if row["class_id"] == self.a2)
        self.assertEqual(own["section_code"], renamed["sources"][-1]["section_code"])

    def test_sibling_view_lists_section_one_first(self) -> None:
        """From MCF3M-2, the siblings are MCF3M then MCF3M-3, then this class."""

        body = self._options(self.a2)
        self.assertEqual(
            [row["class_id"] for row in body["sources"]], [self.a1, self.a3, self.a2]
        )

    def test_admin_sees_only_their_own_sections(self) -> None:
        """IT passes teacher_owns_class tenant-wide, but From is own sections only."""

        it_user = self.school.register_staff("it-admin@gmail.com")
        it_id = int(it_user["id"])
        self.school.conn.execute("UPDATE users SET role = 'it' WHERE id = ?", (it_id,))
        self.school.conn.commit()
        self.assertTrue(self.school.teacher_owns_class(it_id, self.a2))
        it_off = self.school.assign_course(teacher_user_id=it_id, ontario_code="MCF3M")
        it_class = self._make_class(it_off, it_id)
        options = self.school.deck_seed_options(it_class, "M2", "C3", teacher_user_id=it_id)
        self.assertEqual(
            [(row["class_id"], row["is_this_class"]) for row in options["sources"]],
            [(it_class, True)],
        )
        self.assertEqual({row["class_id"] for row in options["decks"]}, {it_class})
        # IT opening teacher A's class sees A's class only, not A's siblings.
        on_teacher = self.school.deck_seed_options(self.a1, "M2", "C3", teacher_user_id=it_id)
        self.assertNotIn(self.a2, {row["class_id"] for row in on_teacher["sources"]})
        self.assertNotIn(self.a2, {row["class_id"] for row in on_teacher["decks"]})
        # The admin's own section stays out of From while they are in A's class.
        self.assertNotIn(it_class, {row["class_id"] for row in on_teacher["sources"]})
        self.assertNotIn(it_class, {row["class_id"] for row in on_teacher["decks"]})
        self.assertEqual([row["class_id"] for row in on_teacher["sources"]], [self.a1])
        before = _deck_table_snapshot(self.school, it_class)
        with self.assertRaises(PermissionError):
            self.school.apply_class_deck_seed(
                it_class, "M2", "C3", mode="course", source_module="M2",
                source_slot="C3", source_class_id=self.a2, teacher_user_id=it_id,
                replace=True,
            )
        self.assertEqual(_deck_table_snapshot(self.school, it_class), before)

    def test_admin_can_copy_own_section_into_another_teachers_class(self) -> None:
        """From omits IT's section; copying that section into the class still works."""

        it_user = self.school.register_staff("it-copy@gmail.com")
        it_id = int(it_user["id"])
        self.school.conn.execute("UPDATE users SET role = 'it' WHERE id = ?", (it_id,))
        self.school.conn.commit()
        it_off = self.school.assign_course(teacher_user_id=it_id, ontario_code="MCF3M")
        it_class = self._make_class(it_off, it_id)
        self._edit_first_question(it_class, "M2", "C3", "From the IT section")
        source = self._deck(it_class, "M2", "C3")
        options = self.school.deck_seed_options(
            self.a1, "M2", "C3", teacher_user_id=it_id
        )
        self.assertNotIn(it_class, {row["class_id"] for row in options["sources"]})
        self.assertNotIn(it_class, {row["class_id"] for row in options["decks"]})
        self.assertEqual([row["class_id"] for row in options["sources"]], [self.a1])
        copied = self.school.apply_class_deck_seed(
            self.a1,
            "M2",
            "C3",
            mode="course",
            source_module="M2",
            source_slot="C3",
            source_class_id=it_class,
            teacher_user_id=it_id,
            require_replace_confirm=True,
            replace=True,
        )
        self.assertEqual(copied["source_class_id"], it_class)
        self.assertEqual(self._deck(self.a1, "M2", "C3"), source)
        self.assertEqual(self._deck(it_class, "M2", "C3"), source)

    def test_db_refuses_non_it_copy_into_another_teachers_class(self) -> None:
        """Without the route 403, the DB still refuses a cross-teacher copy."""

        other = self.school.register_staff("other-copy@gmail.com")
        other_id = int(other["id"])
        other_off = self.school.assign_course(teacher_user_id=other_id, ontario_code="MCF3M")
        own_class = self._make_class(other_off, other_id)
        self._edit_first_question(own_class, "M2", "C3", "From the other teacher")
        before = self._deck(self.a1, "M2", "C3")
        with self.assertRaises(PermissionError):
            self.school.apply_class_deck_seed(
                self.a1,
                "M2",
                "C3",
                mode="course",
                source_module="M2",
                source_slot="C3",
                source_class_id=own_class,
                teacher_user_id=other_id,
                require_replace_confirm=True,
                replace=True,
            )
        self.assertEqual(self._deck(self.a1, "M2", "C3"), before)

    # --- ordering and defaults --------------------------------------------

    def test_decks_sort_siblings_first_then_natural_module_slot(self) -> None:
        """Siblings by section, then this class; M·C as numbers, not strings."""

        decks = self._options(self.a1)["decks"]
        order = [
            (
                1 if row["same_class"] else 0,
                row["section_index"],
                int(row["module"][1:]),
                int(row["slot"][1:]),
            )
            for row in decks
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(decks[0]["class_id"], self.a2)
        self.assertEqual(decks[-1]["class_id"], self.a1)
        labels = [row["label"] for row in decks if row["class_id"] == self.a2]
        self.assertTrue(all(label.startswith("MCF3M-2 · M") for label in labels))
        key = SchoolDB._deck_natural_key
        self.assertLess(key({"module": "M2", "slot": "C3"}), key({"module": "M10", "slot": "C1"}))
        self.assertLess(key({"module": "M1", "slot": "C2"}), key({"module": "M1", "slot": "C10"}))
        self.assertEqual(key({"module": "", "slot": None}), (0, 0))

    def test_same_slot_on_sibling_and_current_question_count(self) -> None:
        """The sibling lists the target M·C; current carries its question count."""

        body = self._options(self.a1)
        self.assertTrue(
            any(
                row["class_id"] == self.a2 and (row["module"], row["slot"]) == ("M2", "C3")
                for row in body["decks"]
            )
        )
        self.assertFalse(
            any(
                row["class_id"] == self.a1 and (row["module"], row["slot"]) == ("M2", "C3")
                for row in body["decks"]
            )
        )
        self.assertTrue(body["current"]["available"])
        deck = self._deck(self.a1, "M2", "C3")
        copyable = [
            pair for pair in deck if not self.school._engine_ride_item_id(pair[0])
        ]
        self.assertEqual(body["current"]["question_count"], len(copyable))
        self.school.apply_class_deck_seed(self.a1, "M2", "C1", mode="blank")
        empty = self._options(self.a1, "M2", "C1")
        self.assertFalse(empty["current"]["available"])
        self.assertEqual(empty["current"]["question_count"], 0)

    def test_listed_question_count_is_the_working_deck(self) -> None:
        """An edited sibling deck lists its working count, not the seed count."""

        self._edit_first_question(self.a2, "M2", "C3", "Working copy")
        meta = self.school.live_class_metadata_for_class_lesson(self.a2, "M2", "C3", fresh=True)
        victim = str(meta["questions"][-1]["id"])
        self.school.remove_class_playlist_item(self.a2, "M2", "C3", victim)
        working = len(self._deck(self.a2, "M2", "C3"))
        listed = next(
            row
            for row in self._options(self.a1)["decks"]
            if row["class_id"] == self.a2 and (row["module"], row["slot"]) == ("M2", "C3")
        )
        self.assertEqual(listed["question_count"], working)

    def _listed(self, class_id: int, module: str, slot: str, *, view: str = "M2", challenge: str = "C3") -> dict:
        """Return one picker row for ``module``/``slot`` on ``class_id``."""

        return next(
            row
            for row in self._options(class_id, view, challenge)["decks"]
            if row["class_id"] == class_id
            and row["module"] == module
            and row["slot"] == slot
        )

    def _question_rows(self, metadata: dict) -> list[dict]:
        """Return question dicts from merged live metadata."""

        return [
            row
            for row in metadata.get("questions") or []
            if isinstance(row, dict)
        ]

    def test_seed_only_ride_deck_quotes_zero_and_copies_empty(self) -> None:
        """MCR3U M2 C2 is two engine rides. The quote is 0 and the copy is empty."""

        listed = self._listed(self.mcr, "M2", "C2")
        self.assertEqual(listed["question_count"], 0)
        opened = self._options(self.mcr, "M2", "C2")
        self.assertEqual(opened["current"]["question_count"], 0)
        self.assertEqual(self.school._deck_question_count(self.mcr, "M2", "C2"), 0)
        source = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M2", "C2", fresh=True
        )
        self.assertTrue(source.get("pages"))
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M3",
            "C1",
            mode="course",
            source_module="M2",
            source_slot="C2",
            replace=True,
        )
        dest = copied["live_metadata"]
        self.assertEqual(self._question_rows(dest), [])
        self.assertEqual(
            [row.get("name") for row in dest["pages"]],
            [row.get("name") for row in source["pages"]],
        )
        again = self._listed(self.mcr, "M3", "C1", view="M2", challenge="C2")
        self.assertEqual(again["question_count"], 0)

    def test_authored_seed_count_matches_the_questions_a_copy_writes(self) -> None:
        """MCR3U M1 C2 quotes the authored rows and the destination holds those."""

        source = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M1", "C2", fresh=True
        )
        authored = self.school._without_engine_rides(self._question_rows(source))
        self.assertGreater(len(authored), 0)
        self.assertLess(len(authored), len(self._question_rows(source)))
        listed = self._listed(self.mcr, "M1", "C2", view="M2", challenge="C1")
        self.assertEqual(listed["question_count"], len(authored))
        opened = self._options(self.mcr, "M1", "C2")
        self.assertEqual(opened["current"]["question_count"], len(authored))
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M3",
            "C2",
            mode="course",
            source_module="M1",
            source_slot="C2",
            replace=True,
        )
        dest_rows = self._question_rows(copied["live_metadata"])
        self.assertEqual(
            [str(row.get("id")) for row in dest_rows],
            [str(row.get("id")) for row in authored],
        )
        self.assertEqual(len(dest_rows), listed["question_count"])
        landed = self._options(self.mcr, "M3", "C2")
        self.assertEqual(landed["current"]["question_count"], len(dest_rows))

    def test_edited_deck_drops_rides_that_are_still_in_the_merge(self) -> None:
        """A page edit leaves rides in the merge. The quote and the copy omit them."""

        self.school.add_class_playlist_page(
            self.mcr, "M1", "C2", name="Extra board", after_page_id="join"
        )
        merged = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M1", "C2", fresh=True
        )
        rows = self._question_rows(merged)
        self.assertTrue(
            any(self.school._engine_ride_item_id(row.get("id"), row) for row in rows)
        )
        authored = self.school._authored_question_count(rows)
        self.assertLess(authored, len(rows))
        listed = self._listed(self.mcr, "M1", "C2", view="M2", challenge="C1")
        self.assertEqual(listed["question_count"], authored)
        opened = self._options(self.mcr, "M1", "C2")
        self.assertEqual(opened["current"]["question_count"], authored)
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M3",
            "C3",
            mode="course",
            source_module="M1",
            source_slot="C2",
            replace=True,
        )
        dest_rows = self._question_rows(copied["live_metadata"])
        self.assertEqual(len(dest_rows), authored)
        self.assertFalse(
            any(self.school._engine_ride_item_id(row.get("id"), row) for row in dest_rows)
        )

    def test_edited_deck_with_rides_removed_matches_the_copy(self) -> None:
        """Hiding the rides leaves the authored count, and the copy writes that many."""

        for ride in ("teams-spark", "meet-team", "team-challenge"):
            self.school.remove_class_playlist_item(self.mcr, "M1", "C2", ride)
        merged = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M1", "C2", fresh=True
        )
        rows = self._question_rows(merged)
        self.assertGreater(len(rows), 0)
        self.assertFalse(
            any(self.school._engine_ride_item_id(row.get("id"), row) for row in rows)
        )
        listed = self._listed(self.mcr, "M1", "C2", view="M2", challenge="C1")
        self.assertEqual(listed["question_count"], len(rows))
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M3",
            "C4",
            mode="course",
            source_module="M1",
            source_slot="C2",
            replace=True,
        )
        self.assertEqual(len(self._question_rows(copied["live_metadata"])), len(rows))

    def test_cross_section_quote_matches_the_copied_deck(self) -> None:
        """A sibling seed-only deck quotes 0. An authored sibling quotes its rows."""

        second = self.school.assign_course(
            teacher_user_id=self.tid, ontario_code="MCR3U", new_section=True
        )
        other = self._make_class(second, self.tid)
        body = self._options(self.mcr, "M3", "C1")
        rides = next(
            row
            for row in body["decks"]
            if row["class_id"] == other and (row["module"], row["slot"]) == ("M2", "C2")
        )
        authored = next(
            row
            for row in body["decks"]
            if row["class_id"] == other and (row["module"], row["slot"]) == ("M1", "C2")
        )
        self.assertEqual(rides["question_count"], 0)
        self.assertGreater(authored["question_count"], 0)
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M4",
            "C1",
            mode="course",
            source_module="M1",
            source_slot="C2",
            source_class_id=other,
            teacher_user_id=self.tid,
            replace=True,
        )
        self.assertEqual(
            len(self._question_rows(copied["live_metadata"])),
            authored["question_count"],
        )
        empty = self.school.apply_class_deck_seed(
            self.mcr,
            "M4",
            "C2",
            mode="course",
            source_module="M2",
            source_slot="C2",
            source_class_id=other,
            teacher_user_id=self.tid,
            replace=True,
        )
        self.assertEqual(self._question_rows(empty["live_metadata"]), [])

    def test_ride_only_question_list_still_copies_team_challenge(self) -> None:
        """MCR3U M1 C1 quotes 0 questions and still brings its team challenge."""

        source = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M1", "C1", fresh=True
        )
        challenge = source.get("team_challenge") or {}
        self.assertTrue(str(challenge.get("question") or "").strip())
        listed = self._listed(self.mcr, "M1", "C1", view="M2", challenge="C1")
        self.assertEqual(listed["question_count"], 0)
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M4",
            "C3",
            mode="course",
            source_module="M1",
            source_slot="C1",
            replace=True,
        )
        dest = copied["live_metadata"]
        self.assertEqual(self._question_rows(dest), [])
        self.assertEqual(
            (dest.get("team_challenge") or {}).get("question"),
            challenge["question"],
        )

    def test_overlay_only_deck_count_omits_engine_rides(self) -> None:
        """Placements with no seed file are counted by the same ride filter."""

        second = self.school.assign_course(
            teacher_user_id=self.tid, ontario_code="MCR3U", new_section=True
        )
        other = self._make_class(second, self.tid)
        self.school.conn.execute(
            "UPDATE classes SET course_code = ? WHERE id = ?",
            ("ZZZ1", other),
        )
        stamp = "2026-11-04T00:00:00Z"
        for item_id, text in (
            ("teams-spark", "Ride"),
            ("domain-check", "Which inputs are allowed?"),
        ):
            self.school.conn.execute(
                """
                INSERT INTO class_live_playlist_placements (
                    class_id, module, slot, page_number, stage, sort_order,
                    placement_key, item_id, item_json, created_at
                ) VALUES (?, 'M1', 'C1', 4, 'round', 1, ?, ?, ?, ?)
                """,
                (
                    other,
                    f"probe:{other}:{item_id}",
                    item_id,
                    json.dumps({"id": item_id, "text": text, "type": "mc"}),
                    stamp,
                ),
            )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=other)
        merged = self.school.live_class_metadata_for_class_lesson(
            other, "M1", "C1", fresh=True
        )
        ids = {str(row.get("id")) for row in self._question_rows(merged)}
        self.assertIn("teams-spark", ids)
        self.assertIn("domain-check", ids)
        body = self.school.deck_seed_options(
            self.mcr, "M2", "C3", teacher_user_id=self.tid
        )
        listed = next(
            row
            for row in body["decks"]
            if row["class_id"] == other and (row["module"], row["slot"]) == ("M1", "C1")
        )
        self.assertEqual(listed["question_count"], 1)
        self.school.conn.execute(
            "UPDATE classes SET course_code = ? WHERE id = ?",
            ("ZZZ1", self.mcr),
        )
        for item_id, text in (
            ("meet-team", "Ride"),
            ("range-check", "What outputs can you get?"),
        ):
            self.school.conn.execute(
                """
                INSERT INTO class_live_playlist_placements (
                    class_id, module, slot, page_number, stage, sort_order,
                    placement_key, item_id, item_json, created_at
                ) VALUES (?, 'M2', 'C4', 4, 'round', 1, ?, ?, ?, ?)
                """,
                (
                    self.mcr,
                    f"probe:{self.mcr}:{item_id}",
                    item_id,
                    json.dumps({"id": item_id, "text": text, "type": "mc"}),
                    stamp,
                ),
            )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=self.mcr)
        own = self.school.deck_seed_options(self.mcr, "M1", "C1")
        own_row = next(
            row
            for row in own["decks"]
            if row["class_id"] == self.mcr and (row["module"], row["slot"]) == ("M2", "C4")
        )
        self.assertEqual(own_row["question_count"], 1)

    def test_builtins_only_copy_is_an_empty_deck_with_pages(self) -> None:
        """MCK-459: a rides-only source copies, writes no questions, and keeps pages."""

        source = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M2", "C2", fresh=True
        )
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M5",
            "C1",
            mode="course",
            source_module="M2",
            source_slot="C2",
            replace=True,
        )
        dest = copied["live_metadata"]
        self.assertEqual(self._question_rows(dest), [])
        self.assertEqual(
            [row.get("id") for row in dest["pages"]],
            [row.get("id") for row in source["pages"]],
        )
        self.assertGreater(len(dest["pages"]), 0)

    def test_unserializable_authored_rows_still_refuse(self) -> None:
        """MCK-459: authored rows that cannot be stored still raise ValueError."""

        original = self.school.live_class_metadata_for_class_lesson

        def poisoned(class_id, module, slot, **kwargs):
            """Return one authored question whose payload is a set."""

            meta = dict(original(class_id, module, slot, **kwargs))
            if (
                int(class_id) == self.mcr
                and str(module).upper() == "M1"
                and str(slot).upper() == "C3"
            ):
                meta["questions"] = [
                    {
                        "id": "authored-only",
                        "text": "Cannot store this",
                        "type": "mc",
                        "stage": "round",
                        "blob": {1, 2},
                    }
                ]
            return meta

        before = _deck_table_snapshot(self.school, self.mcr)
        with patch.object(
            self.school, "live_class_metadata_for_class_lesson", side_effect=poisoned
        ):
            with self.assertRaises(ValueError) as caught:
                self.school.apply_class_deck_seed(
                    self.mcr,
                    "M5",
                    "C2",
                    mode="course",
                    source_module="M1",
                    source_slot="C3",
                    replace=True,
                )
        self.assertIn("No deck saved", str(caught.exception))
        self.assertEqual(_deck_table_snapshot(self.school, self.mcr), before)

    def test_blank_still_writes_zero_questions(self) -> None:
        """MCK-459: Blank remains a legitimate empty deck."""

        from live_class_metadata import default_math_pages

        copied = self.school.apply_class_deck_seed(self.mcr, "M5", "C3", mode="blank")
        dest = copied["live_metadata"]
        self.assertEqual(self._question_rows(dest), [])
        self.assertEqual(
            [row.get("name") for row in dest["pages"]],
            [row.get("name") for row in default_math_pages()],
        )

    # --- refusals -----------------------------------------------------------

    def test_refuses_cross_teacher_archived_and_other_course_sources(self) -> None:
        """Another teacher's section 403; archived or other course 400."""

        before_a1 = _deck_table_snapshot(self.school, self.a1)
        before_b1 = _deck_table_snapshot(self.school, self.b1)
        body = {"mode": "course", "source_module": "M2", "source_slot": "C3", "replace": True}
        foreign = self._seed(self.a1, "M2", "C3", {**body, "source_class_id": self.b1})
        self.assertEqual(foreign.status_code, 403, foreign.get_json())
        other_course = self._seed(self.a1, "M2", "C3", {**body, "source_class_id": self.mcr})
        self.assertEqual(other_course.status_code, 400, other_course.get_json())
        self.school.archive_offering(int(self.off3["id"]))
        archived = self._seed(self.a1, "M2", "C3", {**body, "source_class_id": self.a3})
        self.assertEqual(archived.status_code, 400, archived.get_json())
        self.assertIn("archived", archived.get_json()["error"])
        # Teacher A cannot write into B's class either.
        into_foreign = self._seed(self.b1, "M2", "C3", {**body, "source_class_id": self.a1})
        self.assertEqual(into_foreign.status_code, 403, into_foreign.get_json())
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before_a1)
        self.assertEqual(_deck_table_snapshot(self.school, self.b1), before_b1)

    # --- confirm, empty target, source unchanged ----------------------------

    def test_replace_needs_confirm_no_silent_overwrite(self) -> None:
        """Course deck onto a slot with a deck: 409 until replace: true."""

        self._edit_first_question(self.a2, "M2", "C3", "Section 2 only")
        source = self._deck(self.a2, "M2", "C3")
        before = _deck_table_snapshot(self.school, self.a1)
        self.assertTrue(self.school.class_has_current_live_deck(self.a1, "M2", "C3"))
        body = {
            "mode": "course",
            "source_module": "M2",
            "source_slot": "C3",
            "source_class_id": self.a2,
        }
        held = self._seed(self.a1, "M2", "C3", body)
        self.assertEqual(held.status_code, 409, held.get_json())
        self.assertTrue(held.get_json()["needs_confirm"])
        self.assertEqual(held.get_json()["error"], "Replace the deck or keep current first.")
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before)
        with self.assertRaises(DeckReplaceNotConfirmed):
            self.school.apply_class_deck_seed(
                self.a1, "M2", "C3", mode="course", source_module="M2",
                source_slot="C3", source_class_id=self.a2, teacher_user_id=self.tid,
                require_replace_confirm=True,
            )
        # keep_existing (auto pick, MCK-77) still keeps the deck, no 409.
        kept = self._seed(self.a1, "M2", "C3", {**body, "keep_existing": True})
        self.assertEqual(kept.status_code, 200, kept.get_json())
        self.assertTrue(kept.get_json()["kept_existing"])
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before)
        replaced = self._seed(self.a1, "M2", "C3", {**body, "replace": True})
        self.assertEqual(replaced.status_code, 200, replaced.get_json())
        self.assertEqual(self._deck(self.a1, "M2", "C3"), source)
        self.assertIn("Section 2 only", self._deck(self.a1, "M2", "C3")[0][1])

    def test_copy_into_empty_target_needs_no_confirm(self) -> None:
        """An empty slot loses nothing, so the copy (and Next) goes straight through."""

        self.school.apply_class_deck_seed(self.a1, "M2", "C1", mode="blank")
        self.assertFalse(self.school.class_has_current_live_deck(self.a1, "M2", "C1"))
        source = self._deck(self.a2, "M2", "C1")
        self.assertTrue(source)
        copied = self._seed(
            self.a1,
            "M2",
            "C1",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C1",
                "source_class_id": self.a2,
            },
        )
        self.assertEqual(copied.status_code, 200, copied.get_json())
        self.assertEqual(copied.get_json()["mode"], "course")
        self.assertEqual(self._deck(self.a1, "M2", "C1"), _without_rides(source))
        self.assertTrue(self.school.class_has_current_live_deck(self.a1, "M2", "C1"))

    def test_source_unchanged_after_copy_and_after_editing_the_copy(self) -> None:
        """The source section's tables never change; editing the copy stays local."""

        self._edit_first_question(self.a2, "M2", "C3", "Source edit")
        source_tables = _deck_table_snapshot(self.school, self.a2)
        source_deck = self._deck(self.a2, "M2", "C3")
        replaced = self._seed(
            self.a1,
            "M2",
            "C3",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "source_class_id": self.a2,
                "replace": True,
            },
        )
        self.assertEqual(replaced.status_code, 200, replaced.get_json())
        self.assertEqual(_deck_table_snapshot(self.school, self.a2), source_tables)
        row = self.school.conn.execute(
            """
            SELECT id, item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND module = 'M2' AND slot = 'C3' ORDER BY id LIMIT 1
            """,
            (self.a1,),
        ).fetchone()
        payload = json.loads(row["item_json"])
        payload["text"] = "Edited on the copy"
        self.school.conn.execute(
            "UPDATE class_live_playlist_placements SET item_json = ? WHERE id = ?",
            (json.dumps(payload), int(row["id"])),
        )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=self.a1)
        self.assertIn("Edited on the copy", self._deck(self.a1, "M2", "C3")[0][1])
        self.assertEqual(self._deck(self.a2, "M2", "C3"), source_deck)
        self.assertEqual(_deck_table_snapshot(self.school, self.a2), source_tables)

    def test_concurrent_copies_one_success_and_clean_409s(self) -> None:
        """Eight copies at once into one slot: one 200, the rest 409, never 500.

        Separate app instances are separate SQLite connections, the way
        gunicorn workers race. The overwrite check and the write share one
        ``BEGIN IMMEDIATE`` (MCK-150).
        """

        root = Path(self.tmp.name)
        cookie = self.client.get_cookie("session")
        self.assertIsNotNone(cookie)
        workers = [
            create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
            for _ in range(8)
        ]
        url = f"/api/staff/class/{self.a1}/live-lessons/M2/C1/deck-seed"
        body = {
            "mode": "course",
            "source_module": "M2",
            "source_slot": "C1",
            "source_class_id": self.a2,
        }
        source = self._deck(self.a2, "M2", "C1")
        self.assertTrue(source)

        def once(index: int):
            """POST one copy from one worker connection."""

            client = workers[index].test_client()
            client.set_cookie("session", cookie.value, domain=cookie.domain)
            rv = client.post(url, json=body)
            payload = rv.get_json(silent=True) or {}
            return rv.status_code, payload.get("needs_confirm"), payload.get("error")

        try:
            # A slot that already has a deck: every copy is a clean 409.
            occupied = _deck_table_snapshot(self.school, self.a1)
            self.assertTrue(self.school.class_has_current_live_deck(self.a1, "M2", "C3"))
            c3_url = f"/api/staff/class/{self.a1}/live-lessons/M2/C3/deck-seed"
            c3_body = {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "source_class_id": self.a2,
            }

            def once_c3(index: int):
                """POST a copy into the occupied M2 C3 slot."""

                client = workers[index].test_client()
                client.set_cookie("session", cookie.value, domain=cookie.domain)
                rv = client.post(c3_url, json=c3_body)
                return rv.status_code

            with ThreadPoolExecutor(max_workers=8) as pool:
                held_codes = list(pool.map(once_c3, range(8)))
            self.assertEqual(held_codes, [409] * 8, held_codes)
            self.assertEqual(_deck_table_snapshot(self.school, self.a1), occupied)

            for _round in range(8):
                self.school.apply_class_deck_seed(self.a1, "M2", "C1", mode="blank")
                self.assertFalse(
                    self.school.class_has_current_live_deck(self.a1, "M2", "C1")
                )
                with ThreadPoolExecutor(max_workers=8) as pool:
                    raced = list(pool.map(once, range(8)))
                codes = sorted(code for code, _confirm, _err in raced)
                self.assertEqual(codes, [200] + [409] * 7, raced)
                confirms = [flag for code, flag, _err in raced if code == 409]
                self.assertTrue(all(confirms), raced)
                self.assertEqual(self._deck(self.a1, "M2", "C1"), _without_rides(source))
        finally:
            for app in workers:
                app.config["SCHOOL_DB"].close()

    def test_refuses_copy_into_the_slot_being_taught(self) -> None:
        """A live class on this slot refuses the copy; join and other slots do not."""

        session = self.school.start_live_class_session(
            self.a1, self.tid, live_module="M2", live_slot="C3"
        )
        self.school.set_live_session_teacher_state(
            int(session["id"]),
            live_module="M2",
            live_slot="C3",
            class_set=True,
            stage="join",
        )
        body = {
            "mode": "course",
            "source_module": "M2",
            "source_slot": "C1",
            "source_class_id": self.a2,
            "replace": True,
        }
        # Set Class still seeds while the session is on the join stage.
        seeded = self._seed(self.a1, "M2", "C3", body)
        self.assertEqual(seeded.status_code, 200, seeded.get_json())
        self.school.set_live_session_teacher_state(
            int(session["id"]),
            live_module="M2",
            live_slot="C3",
            class_set=True,
            stage="round",
        )
        before = _deck_table_snapshot(self.school, self.a1)
        refused = self._seed(self.a1, "M2", "C3", body)
        self.assertEqual(refused.status_code, 409, refused.get_json())
        self.assertNotIn("needs_confirm", refused.get_json())
        self.assertIn("End the live class", refused.get_json()["error"])
        self.assertIn("M2 C3", refused.get_json()["error"])
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before)
        # A different slot is not the one on screen.
        self.school.apply_class_deck_seed(self.a1, "M2", "C1", mode="blank")
        other_slot = self._seed(
            self.a1,
            "M2",
            "C1",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C1",
                "source_class_id": self.a2,
            },
        )
        self.assertEqual(other_slot.status_code, 200, other_slot.get_json())
        self.school.conn.execute(
            "UPDATE live_class_sessions SET status = 'ended' WHERE id = ?",
            (int(session["id"]),),
        )
        self.school.conn.commit()
        after_end = self._seed(self.a1, "M2", "C3", body)
        self.assertEqual(after_end.status_code, 200, after_end.get_json())

    def test_slot_that_goes_live_mid_copy_is_refused(self) -> None:
        """The write re-checks liveness, including a join that stops being join."""

        session = self.school.start_live_class_session(
            self.a1, self.tid, live_module="M2", live_slot="C3"
        )
        self.school.set_live_session_teacher_state(
            int(session["id"]),
            live_module="M2",
            live_slot="C3",
            class_set=True,
            stage="join",
        )
        before = _deck_table_snapshot(self.school, self.a1)
        real = SchoolDB._refuse_deck_copy_into_live_slot
        calls = {"n": 0}

        def leave_join(db, class_id, module, slot):
            """Pass the first look, then leave join before the write re-check."""

            calls["n"] += 1
            if calls["n"] == 1:
                real(db, class_id, module, slot)
                db.set_live_session_teacher_state(
                    int(session["id"]),
                    live_module="M2",
                    live_slot="C3",
                    class_set=True,
                    stage="round",
                )
                return None
            return real(db, class_id, module, slot)

        with patch.object(SchoolDB, "_refuse_deck_copy_into_live_slot", leave_join):
            with self.assertRaises(DeckCopyLiveConflict):
                self.school.apply_class_deck_seed(
                    self.a1,
                    "M2",
                    "C3",
                    mode="course",
                    source_module="M2",
                    source_slot="C1",
                    source_class_id=self.a2,
                    teacher_user_id=self.tid,
                    require_replace_confirm=True,
                    replace=True,
                )
        self.assertGreaterEqual(calls["n"], 2)
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before)

        self.school.conn.execute(
            "UPDATE live_class_sessions SET status = 'ended' WHERE id = ?",
            (int(session["id"]),),
        )
        self.school.conn.commit()
        session = self.school.start_live_class_session(
            self.a1, self.tid, live_module="M2", live_slot="C3"
        )
        self.school.set_live_session_teacher_state(
            int(session["id"]),
            live_module="M2",
            live_slot="C3",
            class_set=True,
            stage="join",
        )
        before_item = _deck_table_snapshot(self.school, self.a1)
        calls["n"] = 0

        def activate_item(db, class_id, module, slot):
            """Pass the join exemption, then publish a question before the write."""

            calls["n"] += 1
            if calls["n"] == 1:
                real(db, class_id, module, slot)
                db.conn.execute(
                    """
                    INSERT INTO live_session_items (
                        live_session_id, placement_key, item_id, stage, kind,
                        item_json, status, created_at, updated_at
                    ) VALUES (?, 'mid-copy', 'q1', 'round', 'poll', '{}', 'active', ?, ?)
                    """,
                    (int(session["id"]), "2026-10-15T12:00:00", "2026-10-15T12:00:00"),
                )
                db.conn.commit()
                return None
            return real(db, class_id, module, slot)

        with patch.object(SchoolDB, "_refuse_deck_copy_into_live_slot", activate_item):
            with self.assertRaises(DeckCopyLiveConflict):
                self.school.apply_class_deck_seed(
                    self.a1,
                    "M2",
                    "C3",
                    mode="course",
                    source_module="M2",
                    source_slot="C1",
                    source_class_id=self.a2,
                    teacher_user_id=self.tid,
                    require_replace_confirm=True,
                    replace=True,
                )
        self.assertGreaterEqual(calls["n"], 2)
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before_item)

    def test_huge_ids_are_400_not_500(self) -> None:
        """A source or class id past SQLite's integer range is 400."""

        huge = "9" * 40
        path = self.client.post(
            f"/api/staff/class/{huge}/live-lessons/M2/C3/deck-seed",
            json={"mode": "course", "source_module": "M1", "source_slot": "C1"},
        )
        self.assertEqual(path.status_code, 400, path.get_data(as_text=True)[:300])
        body = self._seed(
            self.a1,
            "M2",
            "C3",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "source_class_id": huge,
                "replace": True,
            },
        )
        self.assertEqual(body.status_code, 400, body.get_json())
        self.assertIn("course", body.get_json()["error"])
        overflow = self._seed(
            self.a1,
            "M2",
            "C3",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "source_class_id": 2**63,
                "replace": True,
            },
        )
        self.assertEqual(overflow.status_code, 400, overflow.get_json())
        options = self.client.get(
            f"/api/staff/class/{huge}/live-lessons/M2/C3/deck-seed-options"
        )
        self.assertEqual(options.status_code, 400, options.get_data(as_text=True)[:300])
        before = _deck_table_snapshot(self.school, self.a1)
        fraction = self._seed(
            self.a1,
            "M2",
            "C3",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "source_class_id": 1.5,
                "replace": True,
            },
        )
        self.assertEqual(fraction.status_code, 400, fraction.get_json())
        self.assertIn("course", fraction.get_json()["error"])
        text_fraction = self._seed(
            self.a1,
            "M2",
            "C3",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "source_class_id": "1.5",
                "replace": True,
            },
        )
        self.assertEqual(text_fraction.status_code, 400, text_fraction.get_json())
        self.assertEqual(_deck_table_snapshot(self.school, self.a1), before)

    def _confirm(self, source: dict, current: dict, module: str, slot: str) -> dict:
        """Render the confirm strip for one real picker row and current deck."""

        name = f"{source['module']} {source['slot']}"
        section = str(source.get("section_code") or "")
        return _render_deck_copy(
            {
                "mode": "course",
                "chosen": True,
                "deck": {
                    "value": f"{source['class_id']}:{source['module']}:{source['slot']}",
                    "name": name,
                    "count": source["question_count"],
                    "has_media": bool(source.get("has_media")),
                    "has_team_challenge": bool(source.get("has_team_challenge")),
                    "has_page_changes": bool(source.get("has_page_changes")),
                },
                "source": {
                    "sectionCode": section,
                    "isThisClass": bool(source.get("same_class", True)),
                },
                "target": {"sectionCode": section, "name": f"{module} {slot}"},
                "current": current,
            }
        )

    def _require_mcr3u(self) -> None:
        """Pin a fixture to MCR3U.

        MCF3M M1 C2 has media and no team challenge. MCR3U M1 C2 is the
        only seed that is authored, media, and a real team challenge
        together, and MCR3U M1 C1 is the pair that has a team challenge
        on both sides.
        """

        self.assertEqual(self.school._course_code_for_class(self.mcr), "MCR3U")

    def _copy(self, module: str, slot: str, source_module: str, source_slot: str) -> dict:
        """Copy one MCR3U deck onto this class's slot. Untouched slots still need replace."""

        return self.school.apply_class_deck_seed(
            self.mcr,
            module,
            slot,
            mode="course",
            source_module=source_module,
            source_slot=source_slot,
            replace=True,
        )

    def _page_count(self, module: str, slot: str) -> int:
        """Overlay rows for one slot, including hidden pages."""

        row = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM class_live_playlist_pages
            WHERE class_id = ? AND module = ? AND slot = ?
            """,
            (self.mcr, module, slot),
        ).fetchone()
        return int(row["n"])

    def _add_poll(self, module: str, slot: str, text: str) -> None:
        """Add one authored poll in place, while the slot's engine rows are still merged."""

        self.school.add_staff_question_to_class_playlist(
            self.mcr,
            module,
            slot,
            question_type="poll",
            text=text,
            page_number=4,
            stage="round",
        )

    def test_zero_case_toast_on_seed_only_source(self) -> None:
        """A rides-only source toasts the zero case, not ``(0 questions)``."""

        plain = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")
        self.assertEqual(plain["question_count"], 0)
        self.assertFalse(plain["has_team_challenge"])
        toast = _render_deck_copy(
            {
                "toast": {
                    "deckName": "M2 C3",
                    "targetName": "M2 C2",
                    "count": plain["question_count"],
                    "has_team_challenge": plain["has_team_challenge"],
                }
            }
        )["toast"]
        self.assertEqual(
            toast,
            "Copied M2 C3 to M2 C2. M2 C2 has no questions yet. "
            "M2 C3's questions were only the built-in Teams Spark and Meet, "
            "which stay with each class.",
        )
        self.assertNotIn("(0 questions)", toast)
        challenge = self._listed(self.mcr, "M1", "C1", view="M2", challenge="C2")
        self.assertEqual(challenge["question_count"], 0)
        self.assertTrue(challenge["has_team_challenge"])
        with_tc = _render_deck_copy(
            {
                "toast": {
                    "deckName": "M1 C1",
                    "targetName": "M2 C2",
                    "count": challenge["question_count"],
                    "has_team_challenge": challenge["has_team_challenge"],
                }
            }
        )["toast"]
        self.assertEqual(
            with_tc,
            "Copied M1 C1 to M2 C2, with its team challenge. "
            "M2 C2 has no questions yet. "
            "M1 C1's questions were only the built-in Teams Spark and Meet, "
            "which stay with each class.",
        )

    def test_team_challenge_loss_line_when_source_lacks_it(self) -> None:
        """Destination team challenge, source without one: the loss line names it."""

        body = self._options(self.mcr, "M1", "C1")
        self.assertTrue(body["current"]["has_team_challenge"])
        self.assertTrue(body["current"]["has_media"])
        source = self._listed(self.mcr, "M2", "C1", view="M1", challenge="C1")
        self.assertTrue(source["has_media"])
        self.assertFalse(source["has_team_challenge"])
        view = self._confirm(source, body["current"], "M1", "C1")
        self.assertEqual(view["loss"], "Heads up: this removes M1 C1's team challenge.")
        self.assertNotIn("slides", view["loss"])
        self.assertEqual(view["variant"], "replace")

    def test_empty_team_challenge_dict_does_not_warn(self) -> None:
        """An all-blank team_challenge dict is not a team challenge."""

        meta = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M2", "C2", fresh=True
        )
        challenge = meta.get("team_challenge") or {}
        self.assertIsInstance(challenge, dict)
        self.assertTrue(bool(challenge))
        self.assertFalse(
            any(
                str(challenge.get(part) or "").strip()
                for part in ("context", "question", "speaker_notes")
            )
        )
        flags = SchoolDB._deck_loss_content_flags(
            {
                "team_challenge": {
                    "context": "",
                    "question": "  ",
                    "speaker_notes": "",
                },
                "media": {"file": "", "ref": ""},
                "media_file": "not-the-merged-shape.html",
                "slides": {"deck_ref": None, "page_numbers": []},
            }
        )
        self.assertFalse(flags["has_team_challenge"])
        self.assertFalse(flags["has_media"])
        body = self._options(self.mcr, "M2", "C2")
        self.assertFalse(body["current"]["has_team_challenge"])
        source = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")
        self.assertFalse(source["has_team_challenge"])
        view = self._confirm(source, body["current"], "M2", "C2")
        self.assertNotIn("team challenge", view["loss"])
        self.assertNotIn("team challenge", view["text"])
        self.assertEqual(view["loss"], "")

    def test_team_challenge_noun_is_not_gated_on_authored_count(self) -> None:
        """MCF3M M1 C1 names its team challenge even though it has no questions.

        The content flag is false on an untouched slot, true after a team
        challenge is copied in, and false again when a deck without one
        replaces it.
        """

        row = self._listed(self.a1, "M1", "C1", view="M2", challenge="C1")
        self.assertEqual(row["question_count"], 0)
        self.assertTrue(row["has_media"])
        self.assertTrue(row["has_team_challenge"])
        view = self._confirm(
            row,
            {
                "available": True,
                "question_count": 7,
                "has_media": False,
                "has_team_challenge": False,
                "has_page_changes": False,
            },
            "M2",
            "C2",
        )
        self.assertIn(
            "with the pages, media and team challenge saved on M1 C1",
            view["text"],
        )
        self.assertNotIn("with the pages and media saved on M1 C1.", view["text"])
        untouched = self._options(self.mcr, "M2", "C2")
        self.assertFalse(untouched["current"]["has_team_challenge"])
        self._copy("M2", "C2", "M1", "C1")
        brought = self._options(self.mcr, "M2", "C2")
        self.assertTrue(brought["current"]["has_team_challenge"])
        self._copy("M2", "C2", "M2", "C3")
        cleared = self._options(self.mcr, "M2", "C2")
        self.assertFalse(cleared["current"]["has_team_challenge"])

    def test_destination_count_is_authored_only_on_both_paths(self) -> None:
        """No authored rows read 0. Two added in place read 2. A copied M1 C2 reads 7."""

        fresh = self._options(self.mcr, "M2", "C2")
        self.assertEqual(fresh["current"]["question_count"], 0)
        raw = self._question_rows(
            self.school.live_class_metadata_for_class_lesson(
                self.mcr, "M2", "C2", fresh=True
            )
        )
        self.assertGreater(len(raw), 0)
        self.assertEqual(self._listed(self.mcr, "M2", "C2")["question_count"], 0)
        self._add_poll("M2", "C3", "First added question")
        self._add_poll("M2", "C3", "Second added question")
        edited = self._question_rows(
            self.school.live_class_metadata_for_class_lesson(
                self.mcr, "M2", "C3", fresh=True
            )
        )
        self.assertTrue(
            any(self.school._engine_ride_item_id(row.get("id"), row) for row in edited)
        )
        self.assertGreaterEqual(len(edited), 4)
        added = self._options(self.mcr, "M2", "C3")
        self.assertEqual(added["current"]["question_count"], 2)
        self.assertEqual(
            self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")["question_count"],
            2,
        )
        self._copy("M2", "C2", "M1", "C2")
        copied = self._options(self.mcr, "M2", "C2")
        self.assertEqual(copied["current"]["question_count"], 7)
        self.assertNotEqual(copied["current"]["question_count"], 10)

    def test_destructive_replace_quotes_the_authored_questions_it_removes(self) -> None:
        """Seven authored questions are named before the click that leaves zero."""

        self._copy("M2", "C2", "M1", "C2")
        body = self._options(self.mcr, "M2", "C2")
        self.assertEqual(body["current"]["question_count"], 7)
        source = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")
        self.assertEqual(source["question_count"], 0)
        view = self._confirm(source, body["current"], "M2", "C2")
        self.assertIn("(7 questions)", view["text"])
        self.assertIn("Can't be undone.", view["text"])
        self._copy("M2", "C2", "M2", "C3")
        landed = self._options(self.mcr, "M2", "C2")
        self.assertEqual(landed["current"]["question_count"], 0)

    def test_singular_authored_question(self) -> None:
        """One authored question renders ``1 question``."""

        self._add_poll("M2", "C4", "Only one added question")
        body = self._options(self.mcr, "M2", "C4")
        self.assertEqual(body["current"]["question_count"], 1)
        source = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C4")
        view = self._confirm(source, body["current"], "M2", "C4")
        self.assertIn("(1 question)", view["text"])
        self.assertNotIn("1 questions", view["text"])

    def test_media_and_team_challenge_named_when_each_is_lost(self) -> None:
        """MCR3U: M2 C1 isolates team challenge, M2 C3 isolates slides.

        MCF3M M1 C2 has no team challenge, so this copy has to be MCR3U
        M1 C2 (7 authored, media, team challenge) or the team-challenge
        half of the loss never happens.
        """

        self._require_mcr3u()
        self._copy("M2", "C2", "M1", "C2")
        body = self._options(self.mcr, "M2", "C2")
        self.assertEqual(body["current"]["question_count"], 7)
        self.assertTrue(body["current"]["has_media"])
        self.assertTrue(body["current"]["has_team_challenge"])
        self.assertTrue(
            SchoolDB._deck_loss_content_flags({"media": {"ref": "course/x"}})["has_media"]
        )
        self.assertTrue(
            SchoolDB._deck_loss_content_flags({"media": {"file": "/static/x.html"}})[
                "has_media"
            ]
        )
        tc_only = self._listed(self.mcr, "M2", "C1", view="M2", challenge="C2")
        self.assertTrue(tc_only["has_media"])
        self.assertFalse(tc_only["has_team_challenge"])
        tc_view = self._confirm(tc_only, body["current"], "M2", "C2")
        self.assertEqual(
            tc_view["loss"],
            "Heads up: this removes M2 C2's team challenge.",
        )
        self.assertNotIn("slides", tc_view["loss"])
        wipe = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")
        self.assertFalse(wipe["has_media"])
        self.assertFalse(wipe["has_team_challenge"])
        both = self._confirm(wipe, body["current"], "M2", "C2")
        self.assertEqual(
            both["loss"],
            "Heads up: this removes M2 C2's slides and team challenge.",
        )
        self._copy("M2", "C2", "M2", "C3")
        landed = self._options(self.mcr, "M2", "C2")
        self.assertFalse(landed["current"]["has_media"])
        self.assertFalse(landed["current"]["has_team_challenge"])
        self.assertEqual(landed["current"]["question_count"], 0)
        fresh = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M2", "C1", fresh=True
        )
        fresh_media = fresh.get("media") if isinstance(fresh.get("media"), dict) else {}
        self.assertIn("jigsawable-class-size", str(fresh_media.get("file") or ""))
        self.assertEqual(
            fresh_media.get("ref"),
            "course/MCR3U/media/jigsawable-class-size",
        )
        fresh_options = self._options(self.mcr, "M2", "C1")
        self.assertTrue(fresh_options["current"]["has_media"])
        self.assertFalse(fresh_options["current"]["has_team_challenge"])
        before_stages = self._served_stages("M2", "C1")
        self.assertEqual(
            before_stages,
            {"join": "minds_on", "teams": None, "meet": "meet-team"},
        )
        seed_wipe = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C1")
        fresh_view = self._confirm(seed_wipe, fresh_options["current"], "M2", "C1")
        self.assertEqual(fresh_view["loss"], "Heads up: this removes M2 C1's slides.")
        posted = self._seed(
            self.mcr,
            "M2",
            "C1",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "replace": True,
            },
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        wiped = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M2", "C1", fresh=True
        )
        wiped_media = wiped.get("media") if isinstance(wiped.get("media"), dict) else {}
        self.assertIsNone(wiped_media.get("file"))
        self.assertIsNone(wiped_media.get("ref"))
        after = self._options(self.mcr, "M2", "C1")
        self.assertFalse(after["current"]["has_media"])
        self.assertEqual(
            self._served_stages("M2", "C1"),
            {"join": "minds_on", "teams": "teams-spark", "meet": "meet-team"},
        )

    def test_loss_line_composes_pairs_a_four_row_table_misses(self) -> None:
        """MCR3U. Slides plus team challenge, page plus slides, page plus team challenge.

        Untouched M1 C2 replaced by untouched M2 C2 loses slides and the
        team challenge and no page changes. A table of only the four v7
        rows cannot emit ``slides and team challenge``.
        """

        self._require_mcr3u()
        dest = self._options(self.mcr, "M1", "C2")
        self.assertEqual(dest["current"]["question_count"], 7)
        self.assertTrue(dest["current"]["has_media"])
        self.assertTrue(dest["current"]["has_team_challenge"])
        self.assertFalse(dest["current"]["has_page_changes"])
        self.assertEqual(self._page_count("M1", "C2"), 0)
        source = self._listed(self.mcr, "M2", "C2", view="M1", challenge="C2")
        self.assertFalse(source["has_media"])
        self.assertFalse(source["has_team_challenge"])
        self.assertFalse(source["has_page_changes"])
        view = self._confirm(source, dest["current"], "M1", "C2")
        self.assertEqual(
            view["loss"],
            "Heads up: this removes M1 C2's slides and team challenge.",
        )
        self.assertIn("slides and team challenge", view["loss"])
        self.assertNotIn(", and", view["loss"])
        self._copy("M1", "C2", "M2", "C2")
        landed = self._options(self.mcr, "M1", "C2")
        self.assertFalse(landed["current"]["has_media"])
        self.assertFalse(landed["current"]["has_team_challenge"])
        self.assertEqual(self._page_count("M1", "C2"), 0)

        self.school.add_class_playlist_page(
            self.mcr, "M2", "C1", name="Pair board", after_page_id="join"
        )
        self.assertGreater(self._page_count("M2", "C1"), 0)
        slides = self._options(self.mcr, "M2", "C1")
        self.assertTrue(slides["current"]["has_page_changes"])
        self.assertTrue(slides["current"]["has_media"])
        self.assertFalse(slides["current"]["has_team_challenge"])
        plain = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C1")
        slides_view = self._confirm(plain, slides["current"], "M2", "C1")
        self.assertEqual(
            slides_view["loss"],
            "Heads up: this removes M2 C1's page changes and slides.",
        )

        self.school.add_class_playlist_page(
            self.mcr, "M1", "C1", name="Challenge board", after_page_id="join"
        )
        challenge = self._options(self.mcr, "M1", "C1")
        self.assertTrue(challenge["current"]["has_page_changes"])
        self.assertTrue(challenge["current"]["has_team_challenge"])
        self.assertTrue(challenge["current"]["has_media"])
        media_source = self._listed(self.mcr, "M3", "C1", view="M1", challenge="C1")
        self.assertTrue(media_source["has_media"])
        self.assertFalse(media_source["has_team_challenge"])
        self.assertFalse(media_source["has_page_changes"])
        challenge_view = self._confirm(media_source, challenge["current"], "M1", "C1")
        self.assertEqual(
            challenge_view["loss"],
            "Heads up: this removes M1 C1's page changes and team challenge.",
        )

    def test_loss_line_ignores_authored_count(self) -> None:
        """MCR3U. A zero-question deck can still lose a team challenge.

        The loss line is the three flags only. Both sides here have no
        authored questions, so a gate on ``question_count > 0`` would
        hide the line.
        """

        self._require_mcr3u()
        slides_dest = self._options(self.mcr, "M1", "C3")
        slides_source = self._listed(self.mcr, "M2", "C2", view="M1", challenge="C3")
        self.assertEqual(slides_dest["current"]["question_count"], 0)
        self.assertEqual(slides_source["question_count"], 0)
        self.assertTrue(slides_dest["current"]["has_media"])
        self.assertFalse(slides_dest["current"]["has_team_challenge"])
        self.assertFalse(slides_source["has_media"])
        self.assertFalse(slides_source["has_team_challenge"])
        slides_only = self._confirm(slides_source, slides_dest["current"], "M1", "C3")
        self.assertEqual(slides_only["loss"], "Heads up: this removes M1 C3's slides.")

        tc_dest = self._options(self.mcr, "M1", "C1")
        tc_source = self._listed(self.mcr, "M1", "C3", view="M1", challenge="C1")
        self.assertEqual(tc_dest["current"]["question_count"], 0)
        self.assertEqual(tc_source["question_count"], 0)
        self.assertTrue(tc_dest["current"]["has_media"])
        self.assertTrue(tc_dest["current"]["has_team_challenge"])
        self.assertTrue(tc_source["has_media"])
        self.assertFalse(tc_source["has_team_challenge"])
        tc_only = self._confirm(tc_source, tc_dest["current"], "M1", "C1")
        self.assertEqual(tc_only["loss"], "Heads up: this removes M1 C1's team challenge.")
        self.assertNotIn("slides", tc_only["loss"])

        both_source = self._listed(self.mcr, "M2", "C2", view="M1", challenge="C1")
        self.assertEqual(both_source["question_count"], 0)
        self.assertFalse(both_source["has_media"])
        self.assertFalse(both_source["has_team_challenge"])
        both = self._confirm(both_source, tc_dest["current"], "M1", "C1")
        self.assertEqual(
            both["loss"],
            "Heads up: this removes M1 C1's slides and team challenge.",
        )

        self._copy("M2", "C2", "M1", "C1")
        copied = self._options(self.mcr, "M2", "C2")
        self.assertEqual(copied["current"]["question_count"], 0)
        self.assertTrue(copied["current"]["has_media"])
        self.assertTrue(copied["current"]["has_team_challenge"])
        only_tc = self._listed(self.mcr, "M1", "C3", view="M2", challenge="C2")
        self.assertEqual(only_tc["question_count"], 0)
        self.assertTrue(only_tc["has_media"])
        self.assertFalse(only_tc["has_team_challenge"])
        caught = self._confirm(only_tc, copied["current"], "M2", "C2")
        self.assertEqual(caught["loss"], "Heads up: this removes M2 C2's team challenge.")
        self.assertNotIn("(0 questions)", caught["text"])
        self.assertIn("which has no questions of its own", caught["text"])

    def test_page_changes_named_when_lost(self) -> None:
        """Page changes are named on a fresh seed-media slot and on a copied deck."""

        self.school.add_class_playlist_page(
            self.mcr, "M2", "C2", name="Extra board", after_page_id="join"
        )
        self.assertEqual(self._page_count("M2", "C2"), 1)
        body = self._options(self.mcr, "M2", "C2")
        self.assertTrue(body["current"]["has_page_changes"])
        self.assertFalse(body["current"]["has_media"])
        self.assertEqual(body["current"]["question_count"], 0)
        source = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")
        self.assertFalse(source["has_page_changes"])
        view = self._confirm(source, body["current"], "M2", "C2")
        self.assertEqual(view["loss"], "Heads up: this removes M2 C2's page changes.")
        self.assertNotIn("slides", view["loss"])
        self._copy("M2", "C2", "M2", "C3")
        self.assertEqual(self._page_count("M2", "C2"), 0)
        landed = self._options(self.mcr, "M2", "C2")
        self.assertFalse(landed["current"]["has_page_changes"])

        self.school.add_class_playlist_page(
            self.mcr, "M2", "C1", name="Seed board", after_page_id="join"
        )
        fresh = self._options(self.mcr, "M2", "C1")
        self.assertTrue(fresh["current"]["has_page_changes"])
        self.assertTrue(fresh["current"]["has_media"])
        fresh_source = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C1")
        fresh_view = self._confirm(fresh_source, fresh["current"], "M2", "C1")
        self.assertEqual(
            fresh_view["loss"],
            "Heads up: this removes M2 C1's page changes and slides.",
        )
        posted = self._seed(
            self.mcr,
            "M2",
            "C1",
            {
                "mode": "course",
                "source_module": "M2",
                "source_slot": "C3",
                "replace": True,
            },
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.assertEqual(self._page_count("M2", "C1"), 0)
        fresh_landed = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M2", "C1", fresh=True
        )
        fresh_media = (
            fresh_landed.get("media")
            if isinstance(fresh_landed.get("media"), dict)
            else {}
        )
        self.assertIsNone(fresh_media.get("file"))
        self.assertIsNone(fresh_media.get("ref"))
        self.assertFalse(self._options(self.mcr, "M2", "C1")["current"]["has_media"])
        self.assertFalse(self._options(self.mcr, "M2", "C1")["current"]["has_page_changes"])

        self._copy("M4", "C1", "M1", "C2")
        self.school.add_class_playlist_page(
            self.mcr, "M4", "C1", name="Copied board", after_page_id="join"
        )
        copied = self._options(self.mcr, "M4", "C1")
        self._require_mcr3u()
        self.assertEqual(copied["current"]["question_count"], 7)
        self.assertTrue(copied["current"]["has_page_changes"])
        self.assertTrue(copied["current"]["has_media"])
        self.assertTrue(copied["current"]["has_team_challenge"])
        copied_source = self._listed(self.mcr, "M2", "C3", view="M4", challenge="C1")
        copied_view = self._confirm(copied_source, copied["current"], "M4", "C1")
        self.assertEqual(
            copied_view["loss"],
            "Heads up: this removes M4 C1's page changes, slides and team challenge.",
        )
        self._copy("M4", "C1", "M2", "C3")
        self.assertEqual(self._page_count("M4", "C1"), 0)
        cleared = self._options(self.mcr, "M4", "C1")
        self.assertFalse(cleared["current"]["has_page_changes"])
        self.assertFalse(cleared["current"]["has_media"])
        self.assertFalse(cleared["current"]["has_team_challenge"])

    def test_loss_line_absent_when_the_source_carries_it_too(self) -> None:
        """MCR3U. Only M1 C1 into M1 C2 has a team challenge on both sides.

        M1 C2 into M1 C3 and M1 C1 into M2 C1 are media guards: those
        destinations have no team challenge, so they would pass even if
        the source half of the team-challenge predicate were deleted.
        A third team-challenge pair is built by copying M1 C2 into M3 C1
        first, so the destination carries one before M1 C1 replaces it.
        """

        self._require_mcr3u()
        media_only = (
            ("M1", "C3", "M1", "C2"),
            ("M2", "C1", "M1", "C1"),
        )
        for module, slot, source_module, source_slot in media_only:
            body = self._options(self.mcr, module, slot)
            self.assertTrue(body["current"]["has_media"], f"MCR3U {module} {slot}")
            self.assertFalse(
                body["current"]["has_team_challenge"],
                f"MCR3U {module} {slot} is not a team-challenge guard",
            )
            source = self._listed(
                self.mcr, source_module, source_slot, view=module, challenge=slot
            )
            self.assertTrue(source["has_media"])
            view = self._confirm(source, body["current"], module, slot)
            self.assertEqual(view["loss"], "")
            self.assertNotIn("team challenge", view["loss"])
            self._copy(module, slot, source_module, source_slot)
            landed = self._options(self.mcr, module, slot)
            self.assertTrue(landed["current"]["has_media"])

        self._copy("M3", "C1", "M1", "C2")
        built = self._options(self.mcr, "M3", "C1")
        self.assertEqual(built["current"]["question_count"], 7)
        self.assertTrue(built["current"]["has_media"])
        self.assertTrue(built["current"]["has_team_challenge"])
        third_source = self._listed(self.mcr, "M1", "C1", view="M3", challenge="C1")
        self.assertTrue(third_source["has_team_challenge"])
        third_view = self._confirm(third_source, built["current"], "M3", "C1")
        self.assertEqual(third_view["loss"], "")
        self._copy("M3", "C1", "M1", "C1")
        third_landed = self._options(self.mcr, "M3", "C1")
        self.assertTrue(third_landed["current"]["has_media"])
        self.assertTrue(third_landed["current"]["has_team_challenge"])

        both = self._options(self.mcr, "M1", "C2")
        both_source = self._listed(self.mcr, "M1", "C1", view="M1", challenge="C2")
        self.assertEqual(both["current"]["question_count"], 7)
        self.assertTrue(both["current"]["has_media"])
        self.assertTrue(both["current"]["has_team_challenge"])
        self.assertTrue(both_source["has_media"])
        self.assertTrue(both_source["has_team_challenge"])
        both_view = self._confirm(both_source, both["current"], "M1", "C2")
        self.assertEqual(both_view["loss"], "")
        self._copy("M1", "C2", "M1", "C1")
        kept = self._options(self.mcr, "M1", "C2")
        self.assertTrue(kept["current"]["has_media"])
        self.assertTrue(kept["current"]["has_team_challenge"])

    def test_rides_only_source_names_pages_only(self) -> None:
        """A deck with no questions, media, or team challenge names pages only."""

        synthetic = _render_deck_copy(
            {
                "mode": "course",
                "chosen": True,
                "deck": {
                    "value": "1:M2:C1",
                    "name": "M2 C1",
                    "count": 0,
                    "has_media": False,
                    "has_team_challenge": False,
                    "has_page_changes": False,
                },
                "source": {"sectionCode": "MCR3U", "isThisClass": True},
                "target": {"sectionCode": "MCR3U", "name": "M2 C2"},
                "current": {
                    "available": True,
                    "question_count": 7,
                    "has_media": False,
                    "has_team_challenge": False,
                    "has_page_changes": False,
                },
            }
        )
        self.assertIn("with the pages saved on M2 C1", synthetic["text"])
        self.assertNotIn("with the questions, pages and media saved on M2 C1", synthetic["text"])
        body = self._options(self.mcr, "M1", "C2")
        source = self._listed(self.mcr, "M2", "C3", view="M1", challenge="C2")
        self.assertEqual(source["question_count"], 0)
        self.assertFalse(source["has_media"])
        self.assertFalse(source["has_team_challenge"])
        view = self._confirm(source, body["current"], "M1", "C2")
        self.assertIn("with the pages saved on M2 C3", view["text"])
        self.assertNotIn("with the questions, pages and media saved on M2 C3", view["text"])

    def _served_stages(self, module: str, slot: str) -> dict[str, str | None]:
        """Item ids a student is served at join, then teams, then meet.

        Rides are mounted by the engine when the prompt is served. A copy
        does not write them, so this reads the serve path rather than the
        placement rows.

        Args:
            module: Module token.
            slot: Challenge token.
        """

        session = self.school.start_live_class_session(
            self.mcr, self.tid, live_module=module, live_slot=slot
        )
        session_id = int(session["id"])
        found: dict[str, str | None] = {}
        for stage in ("join", "teams", "meet"):
            self.school.set_live_session_teacher_state(
                session_id,
                live_module=module,
                live_slot=slot,
                class_set=True,
                stage=stage,
            )
            payload = self.school.student_live_prompt_payload(session_id)
            prompt = payload.get("prompt") or {}
            body = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
            found[stage] = body.get("item_id")
        self.school.conn.execute(
            "UPDATE live_class_sessions SET status = 'ended' WHERE id = ?",
            (session_id,),
        )
        self.school.conn.commit()
        return found

    def test_copy_cannot_destroy_a_ride_and_blank_can(self) -> None:
        """A copy cannot destroy a ride. Blank removes all three engine stages."""

        self.assertEqual(
            self._served_stages("M2", "C1"),
            {"join": "minds_on", "teams": None, "meet": "meet-team"},
        )
        self.school.apply_class_deck_seed(self.mcr, "M2", "C1", mode="blank")
        hides = self.school.conn.execute(
            """
            SELECT item_id, removed FROM class_live_playlist_item_overrides
            WHERE class_id = ? AND module = 'M2' AND slot = 'C1'
              AND item_id IN ('minds_on', 'teams-spark', 'meet-team')
            ORDER BY item_id
            """,
            (self.mcr,),
        ).fetchall()
        self.assertEqual(
            [(row["item_id"], int(row["removed"])) for row in hides],
            [("meet-team", 1), ("minds_on", 1), ("teams-spark", 1)],
        )
        self.assertEqual(
            self._served_stages("M2", "C1"),
            {"join": None, "teams": None, "meet": None},
        )
        self._copy("M2", "C2", "M2", "C3")
        overrides = self.school.conn.execute(
            """
            SELECT item_id FROM class_live_playlist_item_overrides
            WHERE class_id = ? AND module = 'M2' AND slot = 'C2'
              AND item_id IN ('minds_on', 'teams-spark', 'meet-team')
            """,
            (self.mcr,),
        ).fetchall()
        self.assertEqual(list(overrides), [])
        self.assertEqual(
            self._served_stages("M2", "C2"),
            {"join": "minds_on", "teams": "teams-spark", "meet": "meet-team"},
        )

    def test_empty_slot_strip_after_blank(self) -> None:
        """Blank, then a copy with replace false, renders the empty-slot strip."""

        self.school.apply_class_deck_seed(self.mcr, "M2", "C2", mode="blank")
        body = self._options(self.mcr, "M2", "C2")
        self.assertFalse(body["current"]["available"])
        source = self._listed(self.mcr, "M1", "C2", view="M2", challenge="C2")
        view = self._confirm(source, body["current"], "M2", "C2")
        self.assertEqual(view["variant"], "info")
        self.assertFalse(view["replace"])
        self.assertIn("Copies the questions, pages, media and team challenge saved on M1 C2.", view["text"])
        self.assertNotIn("Can't be undone.", view["text"])
        self.assertEqual(view["loss"], "")
        copied = self.school.apply_class_deck_seed(
            self.mcr,
            "M2",
            "C2",
            mode="course",
            source_module="M1",
            source_slot="C2",
            replace=False,
        )
        self.assertEqual(copied["mode"], "course")

    def test_pristine_slot_is_the_info_shape(self) -> None:
        """A deck with nothing of its own does not say it can't be undone."""

        body = self._options(self.mcr, "M2", "C2")
        self.assertTrue(body["current"]["available"])
        self.assertEqual(body["current"]["question_count"], 0)
        self.assertFalse(body["current"]["has_media"])
        self.assertFalse(body["current"]["has_team_challenge"])
        self.assertFalse(body["current"]["has_page_changes"])
        source = self._listed(self.mcr, "M2", "C3", view="M2", challenge="C2")
        view = self._confirm(source, body["current"], "M2", "C2")
        self.assertEqual(view["variant"], "info")
        self.assertTrue(view["replace"])
        self.assertEqual(view["loss"], "")
        self.assertEqual(
            view["text"],
            "Copies the pages saved on M2 C3 over this class's M2 C2 deck. "
            "Every class has its own Teams Spark and Meet questions, so these aren't copied.",
        )
        self.assertNotIn("Can't be undone.", view["text"])

    def _count_metadata_builds(self, fn):
        """Run ``fn`` and return ``(result, [(class_id, module, slot), ...])``.

        Counts ``_build_live_metadata`` calls. That is the full class merge,
        not a seed-file read.

        Args:
            fn: Zero-argument callable.
        """

        calls: list[tuple[int, str, str]] = []
        real = self.school._build_live_metadata

        def spy(key, revision, deck_kw, **kwargs):
            """Record one full merge, then build it."""

            calls.append((int(key[0]), str(key[2]), str(key[3])))
            return real(key, revision, deck_kw, **kwargs)

        with patch.object(self.school, "_build_live_metadata", side_effect=spy):
            return fn(), calls

    def test_untouched_picker_does_not_full_merge_every_row(self) -> None:
        """Three MCR3U sections: repeated opens stay near one full merge.

        Source media and team-challenge flags used to call
        ``live_class_metadata_for_class_lesson`` for every untouched row.
        Three sections list about 95 decks and rebuilt all of them on every
        open, which filled the 64-entry shared cache and evicted the deck
        the class was already teaching. The cache cap is not the fix.
        """

        self._require_mcr3u()
        semester = self.school.get_semester(int(self.off_mcr["semester_id"]))
        for _ in range(2):
            offering = self.school.assign_course(
                teacher_user_id=self.tid, ontario_code="MCR3U", new_section=True
            )
            self._make_class(offering, self.tid)
        self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M1", "C1", fresh=True
        )
        _, warm = self._count_metadata_builds(
            lambda: self.school.live_class_metadata_for_class_lesson(
                self.mcr, "M1", "C1", fresh=False
            )
        )
        self.assertEqual(warm, [])
        opens: list[int] = []
        listed = 0
        for _ in range(3):
            body, calls = self._count_metadata_builds(
                lambda: self.school.deck_seed_options(
                    self.mcr, "M2", "C2", teacher_user_id=self.tid
                )
            )
            listed = len(body["decks"])
            opens.append(len(calls))
        self.assertGreaterEqual(listed, 90)
        self.assertEqual(opens, [1, 1, 1], opens)
        self.assertLess(len(self.school._live_metadata_cache), listed)
        _, after = self._count_metadata_builds(
            lambda: self.school.live_class_metadata_for_class_lesson(
                self.mcr, "M1", "C1", fresh=False
            )
        )
        self.assertEqual(after, [])
        listed_row = self._listed(
            self.mcr, "M1", "C1", view="M2", challenge="C2"
        )
        merged = self.school.live_class_metadata_for_class_lesson(
            self.mcr, "M1", "C1", fresh=True
        )
        truth = self.school._deck_loss_content_flags(merged)
        self.assertEqual(listed_row["has_media"], truth["has_media"])
        self.assertEqual(listed_row["has_team_challenge"], truth["has_team_challenge"])
        self.assertTrue(listed_row["has_team_challenge"])
        self._copy("M4", "C2", "M1", "C1")
        copied = self._options(self.mcr, "M4", "C2")
        self.assertEqual(copied["current"]["question_count"], 0)
        self.assertTrue(copied["current"]["has_media"])
        self.assertTrue(copied["current"]["has_team_challenge"])
        self._copy("M4", "C2", "M2", "C2")
        cleared = self._options(self.mcr, "M4", "C2")
        self.assertFalse(cleared["current"]["has_media"])
        self.assertFalse(cleared["current"]["has_team_challenge"])

    def test_page_changes_are_counted_once_for_the_whole_picker(self) -> None:
        """Page-overlay counts are one grouped query, not one query per deck."""

        calls: list[list] = []
        real = self.school._page_overlay_counts

        def spy(keys):
            """Record each grouped count, then run the real query."""

            calls.append(list(keys))
            return real(keys)

        with patch.object(self.school, "_page_overlay_counts", side_effect=spy):
            body = self.school.deck_seed_options(self.mcr, "M2", "C2")
        deck_count = len(body["decks"])
        self.assertGreater(deck_count, 8)
        self.assertLessEqual(len(calls), 2)
        self.assertTrue(any(len(batch) >= deck_count for batch in calls))
        self.assertTrue(all("has_page_changes" in row for row in body["decks"]))
        self.assertIn("has_media", body["current"])
        self.assertIn("has_team_challenge", body["current"])
        self.assertIn("has_page_changes", body["current"])


HELPER_CASES = r"""
import {
  deckCopyConfirmState,
  deckCopyHelpText,
  deckCopySources,
  deckCopySuccessText,
  deckNaturalKey,
  deckOptionsBySource,
  defaultCopyDeck,
  defaultCopySource,
} from "./static/deck_seed_help.js";

const decks = [
  { class_id: 5, module: "M1", slot: "C2", question_count: 6 },
  { class_id: 9, module: "M10", slot: "C1", question_count: 4 },
  { class_id: 9, module: "M2", slot: "C3", question_count: 9 },
  { class_id: 9, module: "M2", slot: "C10", question_count: 2 },
  { class_id: 9, module: "M2", slot: "C1", question_count: 1 },
  { class_id: 9, module: "M1", slot: "C1", question_count: 7 },
];
const sources = [
  { class_id: 5, section_code: "MCR3U", section_index: 1, is_this_class: true },
  { class_id: 12, section_code: "MCR3U-3", section_index: 3, is_this_class: false },
  { class_id: 9, section_code: "MCR3U-2", section_index: 2, is_this_class: false },
];
const out = {};
out.key = [deckNaturalKey("M2", "C3"), deckNaturalKey("M10", "C1"), deckNaturalKey("", "")];
const srcs = deckCopySources(sources, decks);
out.sources = srcs.map((s) => [s.classId, s.label]);
out.defaultSameSlot = defaultCopySource(srcs, decks, { module: "M2", slot: "C3" });
out.defaultFirstSibling = defaultCopySource(srcs, decks, { module: "M4", slot: "C4" });
out.defaultThisClass = defaultCopySource(deckCopySources([sources[0]], decks), decks, { module: "M2", slot: "C3" });
out.legacySources = deckCopySources(undefined, [{ class_id: 5, section_code: "MCR3U", same_class: true, module: "M1", slot: "C1" }]).map((s) => s.label);
out.groups = deckOptionsBySource(decks, 9, { module: "M2", slot: "C3" }).map((g) => [g.group, g.options.map((o) => o.label)]);
out.values = deckOptionsBySource(decks, 9, { module: "M2", slot: "C3" })[1].options.map((o) => o.value);
out.deckSame = defaultCopyDeck(decks, 9, { module: "M2", slot: "C3" });
out.deckNone = defaultCopyDeck(decks, 5, { module: "M2", slot: "C3" });
out.helpOnlyThis = deckCopyHelpText({ sources: deckCopySources([sources[0]], decks), chosen: "5", course: "MCR3U" });
out.helpEmptySection = deckCopyHelpText({ sources: srcs, chosen: "12", course: "MCR3U" });
out.helpNormal = deckCopyHelpText({ sources: srcs, chosen: "9", course: "MCR3U" });
const deck = { value: "9:M2:C3", name: "M2 C3", count: 9 };
const base = {
  mode: "course", chosen: true, deck,
  source: { sectionCode: "MCR3U-2", isThisClass: false },
  target: { sectionCode: "MCR3U", name: "M2 C3" },
  current: { available: true, question_count: 7 },
};
out.replace = deckCopyConfirmState(base);
out.nudge = deckCopyConfirmState({ ...base, phase: "nudge" });
out.copying = deckCopyConfirmState({ ...base, phase: "copying" });
out.error = deckCopyConfirmState({ ...base, phase: "error", error: "No deck saved for M2 C3." });
out.empty = deckCopyConfirmState({ ...base, current: { available: false } });
out.ownSource = deckCopyConfirmState({ ...base, deck: { value: "5:M1:C2", name: "M1 C2", count: 6 }, source: { sectionCode: "MCR3U", isThisClass: true } }).text;
out.notChosen = deckCopyConfirmState({ ...base, chosen: false });
out.otherMode = deckCopyConfirmState({ ...base, mode: "current" });
out.noDeck = deckCopyConfirmState({ ...base, deck: null });
out.noDeckNudge = deckCopyConfirmState({ ...base, deck: null, phase: "nudge" });
out.success = deckCopyConfirmState({ mode: "current", done: { text: deckCopySuccessText({ sourceSection: "MCR3U-2", deckName: "M2 C3", targetSection: "MCR3U", targetName: "M2 C3", count: 9 }) } });
out.stuck409 = deckCopyConfirmState({
  ...base,
  current: { available: false, question_count: 0 },
  phase: "nudge",
  needsConfirm: true,
});
out.wordingOnly = deckCopyConfirmState({
  ...base,
  current: { available: false, question_count: 0 },
  phase: "error",
  error: "Replace the deck or keep current first.",
});
const nounCases = [
  [0, false, false, "with the pages saved on M2 C1"],
  [1, false, false, "with the questions and pages saved on M2 C1"],
  [0, true, false, "with the pages and media saved on M2 C1"],
  [1, true, false, "with the questions, pages and media saved on M2 C1"],
  [0, false, true, "with the pages and team challenge saved on M2 C1"],
  [1, false, true, "with the questions, pages and team challenge saved on M2 C1"],
  [0, true, true, "with the pages, media and team challenge saved on M2 C1"],
  [1, true, true, "with the questions, pages, media and team challenge saved on M2 C1"],
];
const lossCases = [
  [true, false, false, "page changes"],
  [false, true, false, "slides"],
  [false, false, true, "team challenge"],
  [true, true, false, "page changes and slides"],
  [true, false, true, "page changes and team challenge"],
  [false, true, true, "slides and team challenge"],
  [true, true, true, "page changes, slides and team challenge"],
];
out.losses = lossCases.map(([pages, media, challenge, phrase]) => {
  const view = deckCopyConfirmState({
    mode: "course",
    chosen: true,
    deck: {
      value: "1:M2:C2",
      name: "M2 C2",
      count: 0,
      has_media: false,
      has_team_challenge: false,
      has_page_changes: false,
    },
    source: { sectionCode: "MCR3U", isThisClass: true },
    target: { sectionCode: "MCR3U", name: "M2 C2" },
    current: {
      available: true,
      question_count: 7,
      has_media: media,
      has_team_challenge: challenge,
      has_page_changes: pages,
    },
  });
  return [phrase, view.loss === `Heads up: this removes M2 C2's ${phrase}.`];
});
out.nouns = nounCases.map(([count, media, challenge, phrase]) => {
  const view = deckCopyConfirmState({
    mode: "course",
    chosen: true,
    deck: {
      value: "1:M2:C1",
      name: "M2 C1",
      count,
      has_media: media,
      has_team_challenge: challenge,
      has_page_changes: false,
    },
    source: { sectionCode: "MCR3U", isThisClass: true },
    target: { sectionCode: "MCR3U", name: "M2 C2" },
    current: {
      available: true,
      question_count: 7,
      has_media: false,
      has_team_challenge: false,
      has_page_changes: false,
    },
  });
  const sentence = String(view.text).split(". ")[0];
  return [phrase, sentence.endsWith(phrase)];
});
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is required for the deck_seed_help.js checks")
class DeckCopyHelperTests(unittest.TestCase):
    """Pure helpers: ordering, defaults, labels and the confirm strip."""

    @classmethod
    def setUpClass(cls) -> None:
        """Run the helper cases once."""

        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", HELPER_CASES],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        cls.got = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_natural_key(self) -> None:
        """Digits compare as numbers."""

        self.assertEqual(self.got["key"], [[2, 3], [10, 1], [0, 0]])

    def test_from_order_labels_and_defaults(self) -> None:
        """Siblings by section index, then this class; default prefers same M·C."""

        self.assertEqual(
            self.got["sources"],
            [
                ["9", "MCR3U-2 · 5 decks"],
                ["12", "MCR3U-3 · no decks"],
                ["5", "This class (MCR3U) · 1 deck"],
            ],
        )
        self.assertEqual(self.got["defaultSameSlot"], "9")
        self.assertEqual(self.got["defaultFirstSibling"], "9")
        self.assertEqual(self.got["defaultThisClass"], "5")
        self.assertEqual(self.got["legacySources"], ["This class (MCR3U) · 1 deck"])

    def test_decks_grouped_by_module_in_natural_order(self) -> None:
        """Module 1, 2, 10; C1, C3, C10; same slot marked; values classId:M:C."""

        self.assertEqual(
            self.got["groups"],
            [
                ["M1", ["M1 C1 · 7 questions"]],
                [
                    "M2",
                    [
                        "M2 C1 · 1 question",
                        "M2 C3 · 9 questions · same slot",
                        "M2 C10 · 2 questions",
                    ],
                ],
                ["M10", ["M10 C1 · 4 questions"]],
            ],
        )
        self.assertEqual(self.got["values"], ["9:M2:C1", "9:M2:C3", "9:M2:C10"])
        self.assertEqual(self.got["deckSame"], "9:M2:C3")
        self.assertEqual(self.got["deckNone"], "")

    def test_empty_state_help(self) -> None:
        """States F and G."""

        self.assertEqual(
            self.got["helpOnlyThis"],
            "No other current sections of MCR3U. Pick a deck from this class.",
        )
        self.assertEqual(
            self.got["helpEmptySection"],
            "MCR3U-3 has no saved decks yet. Pick another section.",
        )
        self.assertEqual(self.got["helpNormal"], "")

    def test_confirm_strip_states(self) -> None:
        """Replace holds Next; empty target does not; nudge, copying, error, success."""

        replace = self.got["replace"]
        self.assertEqual(replace["variant"], "replace")
        self.assertEqual(replace["route"], "MCR3U-2 · M2 C3 → MCR3U · M2 C3.")
        self.assertEqual(
            replace["text"],
            "Replaces this class's current M2 C3 deck (7 questions), with the questions "
            "and pages saved on M2 C3, which isn't changed. Every class has its own "
            "Teams Spark and Meet questions, so these aren't copied. Can't be undone.",
        )
        self.assertEqual(replace["loss"], "")
        self.assertEqual(replace["goLabel"], "Replace deck")
        self.assertTrue(replace["showKeep"])
        self.assertTrue(replace["holdsNext"])
        self.assertTrue(replace["replace"])
        self.assertEqual(self.got["nudge"]["variant"], "nudge")
        self.assertEqual(self.got["nudge"]["text"], "Replace the deck or keep current first.")
        self.assertTrue(self.got["nudge"]["holdsNext"])
        self.assertTrue(self.got["copying"]["busy"])
        self.assertEqual(self.got["copying"]["text"], "Copying…")
        self.assertEqual(self.got["error"]["text"], "No deck saved for M2 C3.")
        self.assertTrue(self.got["error"]["holdsNext"])
        empty = self.got["empty"]
        self.assertEqual(empty["variant"], "info")
        self.assertEqual(
            empty["text"],
            "Copies the questions and pages saved on M2 C3, which isn't changed. "
            "Every class has its own Teams Spark and Meet questions, so these aren't copied.",
        )
        self.assertEqual(empty["loss"], "")
        self.assertEqual(empty["goLabel"], "Copy deck")
        self.assertFalse(empty["showKeep"])
        self.assertFalse(empty["holdsNext"])
        self.assertNotIn("isn't changed", self.got["ownSource"])
        self.assertIn("with the questions and pages saved on M1 C2.", self.got["ownSource"])
        for key in ("notChosen", "otherMode"):
            self.assertTrue(self.got[key]["hidden"], key)
            self.assertFalse(self.got[key]["holdsNext"], key)
        self.assertTrue(self.got["noDeck"]["hidden"])
        self.assertTrue(self.got["noDeck"]["holdsNext"])
        self.assertEqual(self.got["noDeckNudge"]["text"], "Choose a deck to copy first.")
        nouns = self.got["nouns"]
        self.assertEqual(len(nouns), 8)
        for phrase, matched in nouns:
            self.assertTrue(matched, phrase)
        self.assertIn(
            "with the questions and pages saved on M2 C1",
            [phrase for phrase, _matched in nouns],
        )
        losses = self.got["losses"]
        self.assertEqual(len(losses), 7)
        for phrase, matched in losses:
            self.assertTrue(matched, phrase)
            self.assertNotIn(", and", phrase)
        self.assertIn("slides and team challenge", [phrase for phrase, _matched in losses])
        self.assertEqual(self.got["success"]["variant"], "success")
        self.assertEqual(
            self.got["success"]["text"],
            "✓ Copied M2 C3 → M2 C3 (9 questions).",
        )
        stuck = self.got["stuck409"]
        self.assertTrue(stuck["replace"])
        self.assertTrue(stuck["showKeep"])
        self.assertEqual(stuck["goLabel"], "Replace deck")
        self.assertTrue(stuck["holdsNext"])
        self.assertEqual(stuck["variant"], "nudge")
        self.assertEqual(stuck["text"], "Replace the deck or keep current first.")
        wording = self.got["wordingOnly"]
        self.assertFalse(wording["replace"])
        self.assertFalse(wording["showKeep"])
        self.assertEqual(wording["goLabel"], "Copy deck")
        self.assertEqual(wording["text"], "Replace the deck or keep current first.")
        self.assertFalse(self.got["success"]["holdsNext"])


FLOW_SCENARIO = r"""
// MCK-132 flow. Class 5 = MCR3U (this class), class 9 = MCR3U-2 (sibling).
for (const id of ['live-deck-copy-from-field', 'live-deck-copy-confirm', 'live-deck-copy-text']) el(id);
el('live-deck-copy-from', HTMLSelectElement.prototype);
el('live-deck-copy-go', HTMLButtonElement.prototype);
el('live-deck-copy-keep', HTMLButtonElement.prototype);
{
  const cls = new Set();
  els['live-deck-copy-go'].classList = {
    add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c),
    toggle: (c, on) => { const v = on === undefined ? !cls.has(c) : Boolean(on); if (v) cls.add(c); else cls.delete(c); return v; },
  };
}
el('ap-valid-date').value = '2026-10-05';
let lastFocus = '';
for (const node of Object.values(els)) node.focus = () => { lastFocus = node.id; };
document.createTextNode = (text) => ({ nodeValue: text });

const flowCurrent = { 'M2/C3': { available: true, question_count: 7 }, 'M2/C1': { available: false, question_count: 0 } };
const requests = [];
const flowPosts = [];
let flowConflict = false;
globalThis.fetch = async (url, init = {}) => {
  const target = String(url);
  requests.push(target);
  let data = { ok: true };
  const opt = target.match(/live-lessons\/(M\d+)\/(C\d+)\/deck-seed-options/);
  if (opt) {
    const key = `${opt[1]}/${opt[2]}`;
    const all = [
      { class_id: 9, section_code: 'MCR3U-2', section_index: 2, same_class: false, module: 'M2', slot: 'C3', question_count: 9 },
      { class_id: 9, section_code: 'MCR3U-2', section_index: 2, same_class: false, module: 'M1', slot: 'C1', question_count: 5 },
      { class_id: 9, section_code: 'MCR3U-2', section_index: 2, same_class: false, module: 'M2', slot: 'C1', question_count: 8 },
      { class_id: 5, section_code: 'MCR3U', section_index: 1, same_class: true, module: 'M1', slot: 'C2', question_count: 6 },
      { class_id: 5, section_code: 'MCR3U', section_index: 1, same_class: true, module: 'M2', slot: 'C3', question_count: 7 },
      { class_id: 5, section_code: 'MCR3U', section_index: 1, same_class: true, module: 'M2', slot: 'C1', question_count: 0 },
    ].filter((d) => !(d.same_class && d.module === opt[1] && d.slot === opt[2]));
    data = {
      ok: true, course: 'MCR3U', module: opt[1], slot: opt[2],
      previous: { available: false, slot: null, message: '' },
      current: { label: 'Use current', message: '', ...(flowCurrent[key] || { available: false }) },
      sources: [
        { class_id: 9, section_code: 'MCR3U-2', section_index: 2, is_this_class: false },
        { class_id: 5, section_code: 'MCR3U', section_index: 1, is_this_class: true },
      ],
      decks: all,
    };
  } else if (/\/deck-seed$/.test(target)) {
    const body = JSON.parse(init.body || '{}');
    flowPosts.push(body);
    if (flowConflict) {
      flowConflict = false;
      data = { ok: false, error: 'Replace the deck or keep current first.', needs_confirm: true };
      const conflictText = JSON.stringify(data);
      return {
        ok: false, status: 409,
        async text() { return conflictText; },
        async json() { return data; },
        headers: { get() { return 'application/json'; } },
      };
    }
    if (body.mode === 'course') flowCurrent['M2/C3'] = { available: true, question_count: 9 };
  }
  const text = JSON.stringify(data);
  return { ok: true, status: 200, async text() { return text; }, async json() { return data; },
    headers: { get() { return 'application/json'; } } };
};

let src = readFileSync(staticDir + '/staff_ap.js', 'utf8');
const staticHref = pathToFileURL(staticDir + '/').href;
const commonHref = JSON.stringify(pathToFileURL(process.env.LLOVES_COMMON_JS).href);
const editorOut = (process.env.LLOVES_HARNESS_OUT || '/tmp/staff-ap-harness.mjs').replace(/\.mjs$/, '') + '-question-editor.mjs';
writeFileSync(
  editorOut,
  readFileSync(staticDir + '/question_editor.js', 'utf8').replaceAll('"/static/common.js"', commonHref).replaceAll('"/static/', '"' + staticHref),
);
src = src.replaceAll('"/static/question_editor.js"', JSON.stringify(pathToFileURL(editorOut).href));
src = src.replaceAll('"/static/common.js"', commonHref);
src = src.replaceAll('"/static/', '"' + staticHref);
src += '\nexport { enterSetClassPhase, applyDeckSeedChoice, applyValidateDateChoice, deckCopyView };\n';
const out = process.env.LLOVES_HARNESS_OUT;
writeFileSync(out, src);
const mod = await import(pathToFileURL(out).href);

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
const checked = () => (inputs().find((i) => i.checked) || {}).value || '';
function clickChip(mode) {
  const input = els[`live-deck-seed-${mode}`];
  const wasChecked = input.checked;
  input.checked = true;
  els['live-deck-seed'].dispatch('click', input);
  if (!wasChecked) els['live-deck-seed'].dispatch('change', input);
}
const sourceGroups = () => els['live-deck-seed-source'].children.map((g) => [g.label, g.options.map((o) => o.text)]);
const r = {};

moduleSelect.value = 'M2';
slotSelect.value = 'C3';
mod.enterSetClassPhase();
await settle();
r.start = checked();
r.fromHiddenAtStart = els['live-deck-copy-from-field'].hidden;
r.stripHiddenAtStart = els['live-deck-copy-confirm'].hidden;
clickChip('course');
r.fromHidden = els['live-deck-copy-from-field'].hidden;
r.fromOptions = els['live-deck-copy-from'].options.map((o) => [o.value, o.text]);
r.fromValue = els['live-deck-copy-from'].value;
r.sourceValue = els['live-deck-seed-source'].value;
r.sourceGroups = sourceGroups();
r.replaceView = mod.deckCopyView();
r.goText = els['live-deck-copy-go'].textContent;
r.keepHidden = els['live-deck-copy-keep'].hidden;
requests.length = 0;
await mod.applyValidateDateChoice({});
r.nextRequests = requests.slice();
r.nudgeView = mod.deckCopyView();
r.nudgeFocus = lastFocus;
r.nudgeGoIsReplace = els['live-deck-copy-go'].classList.contains('is-replace');
els['live-deck-copy-keep'].dispatch('click');
r.afterKeep = checked();
r.afterKeepStripHidden = els['live-deck-copy-confirm'].hidden;
r.afterKeepHolds = mod.deckCopyView().holdsNext;
flowPosts.length = 0;
await mod.applyDeckSeedChoice();
r.afterKeepPosts = flowPosts.slice();
clickChip('course');
els['live-deck-copy-go'].dispatch('click');
await settle();
r.replacePosts = flowPosts.slice();
r.afterReplace = checked();
r.successView = mod.deckCopyView();
r.successFocus = lastFocus;
r.fromHiddenAfter = els['live-deck-copy-from-field'].hidden;
flowPosts.length = 0;
await mod.applyDeckSeedChoice();
r.nextAfterReplacePosts = flowPosts.slice();
clickChip('course');
r.successClearedOnChange = mod.deckCopyView().variant !== 'success';
els['live-deck-copy-from'].value = '5';
els['live-deck-copy-from'].dispatch('change');
r.thisClassGroups = sourceGroups();
r.thisClassValue = els['live-deck-seed-source'].value;
r.thisClassView = mod.deckCopyView();
requests.length = 0;
await mod.applyValidateDateChoice({});
r.noDeckNextRequests = requests.slice();
r.noDeckNudge = mod.deckCopyView().text;
slotSelect.value = 'C1';
slotSelect.dispatch('change');
await settle();
r.emptyChecked = checked();
r.emptyFromValue = els['live-deck-copy-from'].value;
r.emptySourceValue = els['live-deck-seed-source'].value;
r.emptyView = mod.deckCopyView();
flowPosts.length = 0;
await mod.applyDeckSeedChoice();
r.emptyNextPosts = flowPosts.slice();
// Catalog still says M2 C1 is empty, but the server refuses the copy.
flowConflict = true;
flowPosts.length = 0;
els['live-deck-copy-go'].dispatch('click');
await settle();
r.stuckView = mod.deckCopyView();
r.stuckGo = els['live-deck-copy-go'].textContent;
r.stuckKeepHidden = els['live-deck-copy-keep'].hidden;
r.stuckFocus = lastFocus;
r.stuckGoIsReplace = els['live-deck-copy-go'].classList.contains('is-replace');
r.stuckPosts = flowPosts.slice();
flowPosts.length = 0;
els['live-deck-copy-go'].dispatch('click');
await settle();
r.stuckRetryPosts = flowPosts.slice();
console.log(JSON.stringify(r));
"""


def run_flow_harness() -> dict:
    """Run the MCK-132 Set Class flow against the real staff_ap.js."""

    prelude = STAFF_AP_HARNESS[: STAFF_AP_HARNESS.index("// Class 5. M1 C1 is empty")]
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "flow.mjs"
        script.write_text(prelude + FLOW_SCENARIO, encoding="utf-8")
        env = os.environ.copy()
        env["LLOVES_LMS_STATIC"] = str(LMS_DIR / "static")
        env["LLOVES_COMMON_JS"] = str(
            REPO_ROOT / "tools" / "math-game-show" / "static" / "common.js"
        )
        env["LLOVES_HARNESS_OUT"] = str(Path(tmp) / "staff_ap_under_test.mjs")
        proc = subprocess.run(
            [NODE, str(script)], capture_output=True, text=True, env=env,
            timeout=60, check=False,
        )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is required for the staff_ap.js harness")
class SetClassDeckCopyFlowTests(unittest.TestCase):
    """Drive From → Source deck → confirm in the real staff_ap.js."""

    @classmethod
    def setUpClass(cls) -> None:
        """Run the flow once; each test checks one step."""

        cls.r = run_flow_harness()

    def test_closed_row_then_two_step_pickers(self) -> None:
        """Use current at start; Course deck shows From then Source deck."""

        r = self.r
        self.assertEqual(r["start"], "current")
        self.assertTrue(r["fromHiddenAtStart"])
        self.assertTrue(r["stripHiddenAtStart"])
        self.assertFalse(r["fromHidden"])
        self.assertEqual(
            r["fromOptions"],
            [["9", "MCR3U-2 · 3 decks"], ["5", "This class (MCR3U) · 2 decks"]],
        )
        self.assertEqual(r["fromValue"], "9")
        self.assertEqual(r["sourceValue"], "9:M2:C3")
        self.assertEqual(
            r["sourceGroups"],
            [
                ["M1", ["M1 C1 · 5 questions"]],
                ["M2", ["M2 C1 · 8 questions", "M2 C3 · 9 questions · same slot"]],
            ],
        )

    def test_replace_strip_and_next_is_held(self) -> None:
        """Amber strip; Next sends no /begin and no deck-seed, then nudges."""

        r = self.r
        self.assertEqual(r["replaceView"]["variant"], "replace")
        self.assertTrue(r["replaceView"]["holdsNext"])
        self.assertIn(
            "(7 questions), with the questions and pages saved on M2 C3, which isn't changed.",
            r["replaceView"]["text"],
        )
        self.assertEqual(r["replaceView"]["loss"], "")
        self.assertEqual(r["goText"], "Replace deck")
        self.assertFalse(r["keepHidden"])
        self.assertEqual(r["nextRequests"], [])
        self.assertEqual(r["nudgeView"]["variant"], "nudge")
        self.assertEqual(r["nudgeView"]["text"], "Replace the deck or keep current first.")
        # MED-1: a second Enter/Space on Next lands on Keep current, not Replace.
        self.assertEqual(r["nudgeFocus"], "live-deck-copy-keep")
        self.assertTrue(r["nudgeGoIsReplace"])

    def test_keep_current_writes_nothing(self) -> None:
        """Keep current hides the strip and Next sends no deck-seed."""

        r = self.r
        self.assertEqual(r["afterKeep"], "current")
        self.assertTrue(r["afterKeepStripHidden"])
        self.assertFalse(r["afterKeepHolds"])
        self.assertEqual(r["afterKeepPosts"], [])

    def test_replace_posts_once_then_success_and_next_writes_nothing(self) -> None:
        """One deck-seed with replace + source_class_id; chip back on Use current."""

        r = self.r
        self.assertEqual(
            r["replacePosts"],
            [
                {
                    "mode": "course",
                    "source_module": "M2",
                    "source_slot": "C3",
                    "source_class_id": "9",
                    "replace": True,
                }
            ],
        )
        self.assertEqual(r["afterReplace"], "current")
        self.assertEqual(r["successView"]["variant"], "success")
        self.assertEqual(
            r["successView"]["text"],
            "✓ Copied M2 C3 → M2 C3 (9 questions).",
        )
        self.assertEqual(r["successFocus"], "live-deck-seed-current")
        self.assertTrue(r["fromHiddenAfter"])
        self.assertEqual(r["nextAfterReplacePosts"], [])
        self.assertTrue(r["successClearedOnChange"])

    def test_this_class_regroups_and_unpicked_deck_holds_next(self) -> None:
        """From = this class: target slot absent, placeholder, Next held."""

        r = self.r
        self.assertEqual(
            r["thisClassGroups"],
            [["M1", ["M1 C2 · 6 questions"]], ["M2", ["M2 C1 · no questions of its own"]]],
        )
        self.assertEqual(r["thisClassValue"], "")
        self.assertTrue(r["thisClassView"]["holdsNext"])
        self.assertEqual(r["noDeckNextRequests"], [])
        self.assertEqual(r["noDeckNudge"], "Choose a deck to copy first.")

    def test_empty_target_copies_on_next_without_replace(self) -> None:
        """Empty M2 C1: info strip, Next is not held and copies without replace."""

        r = self.r
        self.assertEqual(r["emptyChecked"], "course")
        self.assertEqual(r["emptyFromValue"], "9")
        self.assertEqual(r["emptySourceValue"], "9:M2:C1")
        self.assertEqual(r["emptyView"]["variant"], "info")
        self.assertFalse(r["emptyView"]["holdsNext"])
        self.assertEqual(
            r["emptyNextPosts"],
            [
                {
                    "mode": "course",
                    "source_module": "M2",
                    "source_slot": "C1",
                    "source_class_id": "9",
                }
            ],
        )

    def test_refused_copy_on_empty_slot_offers_replace_or_keep(self) -> None:
        """A 409 while the catalog says empty becomes Replace / Keep, then replace: true."""

        r = self.r
        self.assertEqual(
            r["stuckPosts"],
            [
                {
                    "mode": "course",
                    "source_module": "M2",
                    "source_slot": "C1",
                    "source_class_id": "9",
                }
            ],
        )
        self.assertEqual(r["stuckView"]["variant"], "nudge")
        self.assertEqual(r["stuckView"]["text"], "Replace the deck or keep current first.")
        self.assertTrue(r["stuckView"]["showKeep"])
        self.assertTrue(r["stuckView"]["replace"])
        self.assertEqual(r["stuckGo"], "Replace deck")
        self.assertFalse(r["stuckKeepHidden"])
        self.assertEqual(r["stuckFocus"], "live-deck-copy-keep")
        self.assertTrue(r["stuckGoIsReplace"])
        self.assertEqual(
            r["stuckRetryPosts"],
            [
                {
                    "mode": "course",
                    "source_module": "M2",
                    "source_slot": "C1",
                    "source_class_id": "9",
                    "replace": True,
                }
            ],
        )


class SetClassDeckCopyMarkupTests(unittest.TestCase):
    """Template ids, the kept MCK-15 labels, and copy: Wonder tags."""

    def test_markup_and_copy_tags(self) -> None:
        """From field before Source deck; confirm strip after the row."""

        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        panel = html[html.index('id="ap-panel-validate"') : html.index('id="live-shell-left"')]
        self.assertLess(panel.index('id="live-deck-copy-from"'), panel.index('id="live-deck-seed-source"'))
        self.assertIn('aria-label="Copy from section"', panel)
        self.assertIn('aria-label="Deck to copy"', panel)
        self.assertIn(">Source deck</span>", panel)
        self.assertIn('name="live-deck-seed"', panel)
        strip = panel[panel.index('id="live-deck-copy-confirm"') :]
        self.assertIn('role="group"', strip[:200])
        self.assertIn('aria-live="polite"', strip)
        self.assertIn('id="live-deck-copy-go"', strip)
        self.assertIn('id="live-deck-copy-keep"', strip)
        self.assertGreater(panel.index('id="live-deck-copy-confirm"'), panel.index('id="live-deck-seed-picker"'))
        self.assertIn("copy: Wonder", panel)
        help_js = (LMS_DIR / "static" / "deck_seed_help.js").read_text(encoding="utf-8")
        for text in (
            "Replace the deck or keep current first.",
            "Can't be undone.",
            "which has no questions of its own",
            "Heads up: this removes",
            "which isn't changed",
            "no questions of its own",
            "Every class has its own Teams Spark and Meet questions, so these aren't copied.",
            "has no questions yet.",
            "with its team challenge",
            "No other current sections of ${course}. Pick a deck from this class.",
            "has no saved decks yet. Pick another section.",
            "✓ Copied",
            'PLACEHOLDER = "Choose a deck…"',
        ):
            line = next(row for row in help_js.splitlines() if text in row)
            self.assertIn("copy: Wonder", line, text)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn('.live-deck-copy-confirm[data-variant="replace"]', css)
        self.assertIn(".live-deck-copy-from[hidden] { display: none !important; }", css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        start = js.index("async function applyValidateDateChoice")
        guard = js[start:]
        self.assertLess(
            guard.index("holdSetClassNextForDeckCopy(opts)"), guard.index("gridSelectedIso(")
        )
        self.assertLess(
            start + guard.index("holdSetClassNextForDeckCopy(opts)"),
            js.index("/api/classes/${classId}/begin"),
        )


if __name__ == "__main__":
    unittest.main()
