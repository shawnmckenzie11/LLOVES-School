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
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from school_db import DeckReplaceNotConfirmed, SchoolDB  # noqa: E402
from test_deck_seed_sticky_mode import STAFF_AP_HARNESS  # noqa: E402

NODE = shutil.which("node")


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
        before = _deck_table_snapshot(self.school, it_class)
        with self.assertRaises(PermissionError):
            self.school.apply_class_deck_seed(
                it_class, "M2", "C3", mode="course", source_module="M2",
                source_slot="C3", source_class_id=self.a2, teacher_user_id=it_id,
                replace=True,
            )
        self.assertEqual(_deck_table_snapshot(self.school, it_class), before)

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
        self.assertEqual(body["current"]["question_count"], len(self._deck(self.a1, "M2", "C3")))
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
        self.assertEqual(self._deck(self.a1, "M2", "C1"), source)
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
                ["Module 1", ["M1 C1 · 7 questions"]],
                [
                    "Module 2",
                    [
                        "M2 C1 · 1 question",
                        "M2 C3 · 9 questions · same slot",
                        "M2 C10 · 2 questions",
                    ],
                ],
                ["Module 10", ["M10 C1 · 4 questions"]],
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
            "Replaces this class's current M2 C3 deck (7 questions) with 9 questions, "
            "pages, media and team challenge. MCR3U-2 is not changed. Can't be undone.",
        )
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
        self.assertEqual(empty["text"], "Copies 9 questions, pages, media and team challenge.")
        self.assertEqual(empty["goLabel"], "Copy deck")
        self.assertFalse(empty["showKeep"])
        self.assertFalse(empty["holdsNext"])
        self.assertIn("MCR3U · M1 C2 is not changed.", self.got["ownSource"])
        for key in ("notChosen", "otherMode"):
            self.assertTrue(self.got[key]["hidden"], key)
            self.assertFalse(self.got[key]["holdsNext"], key)
        self.assertTrue(self.got["noDeck"]["hidden"])
        self.assertTrue(self.got["noDeck"]["holdsNext"])
        self.assertEqual(self.got["noDeckNudge"]["text"], "Choose a deck to copy first.")
        self.assertEqual(self.got["success"]["variant"], "success")
        self.assertEqual(
            self.got["success"]["text"],
            "✓ Copied MCR3U-2 · M2 C3 → MCR3U · M2 C3 (9 questions).",
        )
        self.assertFalse(self.got["success"]["holdsNext"])


FLOW_SCENARIO = r"""
// MCK-132 flow. Class 5 = MCR3U (this class), class 9 = MCR3U-2 (sibling).
for (const id of ['live-deck-copy-from-field', 'live-deck-copy-confirm', 'live-deck-copy-text']) el(id);
el('live-deck-copy-from', HTMLSelectElement.prototype);
el('live-deck-copy-go', HTMLButtonElement.prototype);
el('live-deck-copy-keep', HTMLButtonElement.prototype);
el('ap-valid-date').value = '2026-10-05';
let lastFocus = '';
for (const node of Object.values(els)) node.focus = () => { lastFocus = node.id; };
document.createTextNode = (text) => ({ nodeValue: text });

const flowCurrent = { 'M2/C3': { available: true, question_count: 7 }, 'M2/C1': { available: false, question_count: 0 } };
const requests = [];
const flowPosts = [];
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
    if (body.mode === 'course') flowCurrent['M2/C3'] = { available: true, question_count: 9 };
  }
  const text = JSON.stringify(data);
  return { ok: true, status: 200, async text() { return text; }, async json() { return data; },
    headers: { get() { return 'application/json'; } } };
};

let src = readFileSync(staticDir + '/staff_ap.js', 'utf8');
const staticHref = pathToFileURL(staticDir + '/').href;
src = src.replaceAll('"/static/common.js"', JSON.stringify(pathToFileURL(process.env.LLOVES_COMMON_JS).href));
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
                ["Module 1", ["M1 C1 · 5 questions"]],
                ["Module 2", ["M2 C1 · 8 questions", "M2 C3 · 9 questions · same slot"]],
            ],
        )

    def test_replace_strip_and_next_is_held(self) -> None:
        """Amber strip; Next sends no /begin and no deck-seed, then nudges."""

        r = self.r
        self.assertEqual(r["replaceView"]["variant"], "replace")
        self.assertTrue(r["replaceView"]["holdsNext"])
        self.assertIn("(7 questions) with 9 questions", r["replaceView"]["text"])
        self.assertEqual(r["goText"], "Replace deck")
        self.assertFalse(r["keepHidden"])
        self.assertEqual(r["nextRequests"], [])
        self.assertEqual(r["nudgeView"]["variant"], "nudge")
        self.assertEqual(r["nudgeView"]["text"], "Replace the deck or keep current first.")
        self.assertEqual(r["nudgeFocus"], "live-deck-copy-go")

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
            "✓ Copied MCR3U-2 · M2 C3 → MCR3U · M2 C3 (9 questions).",
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
            [["Module 1", ["M1 C2 · 6 questions"]], ["Module 2", ["M2 C1 · 0 questions"]]],
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
            "Copies ${qs}, pages, media and team challenge.",
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
