#!/usr/bin/env python3
"""Set Class chooses how the live deck is seeded.

Ops smoke (localhost ``http://127.0.0.1:8787``, ``LOCAL_DEV_LOGIN=1``):

1. Open the staff portal and sign in with the Teacher card
   (``shawnmckenzie11.sm@gmail.com``). Do not type a random Google email.
2. Open the demo MCF3M class and choose Run Live Class so Set Class shows.
3. Confirm the three choices: Copy previous challenge deck, Copy another
   deck from this course, and Blank 7-page template.
4. Module M1, live class C1: previous is disabled and says there is no
   previous challenge. Forward with Blank 7-page template. The lesson
   rail is Join, Welcome, Meet, Round 1, Round 2, Round 3, Summary, with
   no authored questions.
5. Run Live Class again. Module M1, live class C3. Previous is enabled.
   Choose it and Forward. Questions match M1 C2, and opening M1 C2 again
   still shows the original C2 deck.
6. Run Live Class again. Module M2, live class C1. Choose Copy another
   deck from this course. The list is MCF3M decks only (M1 C2, M1 C3, …),
   not another course. Pick M1 C2 and Forward. The new deck matches that
   source, and M1 C2 is unchanged.
"""

from __future__ import annotations

import json
import os
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
from live_class_metadata import (  # noqa: E402
    default_math_pages,
    metadata_path,
    previous_challenge_slot,
)


SEED_C2 = metadata_path("MCF3M", "M1", "C2")
SEED_C3 = metadata_path("MCF3M", "M1", "C3")


def _question_signature(metadata: dict) -> list[tuple[str, str]]:
    """Return ``(id, text)`` pairs for merged deck questions."""

    rows = []
    for row in metadata.get("questions") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        text = str(
            row.get("text") or row.get("prompt") or row.get("title") or ""
        )
        rows.append((str(row["id"]), text))
    return rows


class PreviousChallengeSlotTests(unittest.TestCase):
    """Previous challenge follows module order, not the calendar."""

    def test_c3_previous_is_c2_and_c1_has_none(self) -> None:
        """M2 C3 copies M2 C2. C1 does not invent a deck."""

        self.assertEqual(previous_challenge_slot("C3"), "C2")
        self.assertEqual(previous_challenge_slot("c2"), "C1")
        self.assertEqual(previous_challenge_slot("C4"), "C3")
        self.assertIsNone(previous_challenge_slot("C1"))
        self.assertIsNone(previous_challenge_slot("C9"))
        self.assertIsNone(previous_challenge_slot(""))


class LiveDeckSeedTests(unittest.TestCase):
    """Staff Set Class seeds a working copy or the blank 7-page template."""

    def setUp(self) -> None:
        """Teacher-owned MCF3M class signed in through the staff portal."""

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
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
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
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])
        self.c2_bytes = SEED_C2.read_bytes()
        self.c3_bytes = SEED_C3.read_bytes()

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""

        self.school.close()
        self.tmp.cleanup()

    def _meta(self, module: str, slot: str) -> dict:
        """Return a fresh merged deck for this class."""

        return self.school.live_class_metadata_for_class_lesson(
            self.class_id, module, slot, fresh=True
        )

    def test_set_class_shows_three_deck_choices(self) -> None:
        """The calendar step offers the three staff labels."""

        page = self.client.get(f"/staff/class/{self.class_id}?tab=live&run=1")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        panel = html[
            html.index('id="ap-panel-validate"') : html.index('id="live-shell-left"')
        ]
        self.assertIn("Copy previous challenge deck", panel)
        self.assertIn("Copy another deck from this course", panel)
        self.assertIn("Blank 7-page template", panel)
        self.assertIn('id="live-deck-seed-blank" value="blank" checked', panel)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("async function applyDeckSeedChoice()", js)
        self.assertIn("deck-seed-options", js)

    def test_previous_copies_prior_challenge_without_touching_source(self) -> None:
        """M1 C3 copies M1 C2. Editing C3 leaves C2 and the seed file alone."""

        source_before = _question_signature(self._meta("M1", "C2"))
        dest_before = _question_signature(self._meta("M1", "C3"))
        self.assertTrue(source_before)
        self.assertNotEqual(source_before, dest_before)
        seeded = self.school.apply_class_deck_seed(
            self.class_id, "M1", "C3", mode="previous"
        )
        self.assertEqual(seeded["mode"], "previous")
        self.assertEqual(seeded["source_module"], "M1")
        self.assertEqual(seeded["source_slot"], "C2")
        self.assertEqual(
            _question_signature(seeded["live_metadata"]), source_before
        )
        self.assertEqual(_question_signature(self._meta("M1", "C2")), source_before)
        self.assertEqual(SEED_C2.read_bytes(), self.c2_bytes)
        self.assertEqual(SEED_C3.read_bytes(), self.c3_bytes)
        source_rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(source_rows, [])
        dest_rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C3"
        )
        self.assertTrue(dest_rows)
        self.assertTrue(
            all(str(row["placement_key"]).startswith("deck-copy:") for row in dest_rows)
        )
        first = dest_rows[0]
        payload = dict(first["item"])
        payload["text"] = "Edited only on the C3 copy"
        self.school.conn.execute(
            """
            UPDATE class_live_playlist_placements
            SET item_json = ?
            WHERE id = ?
            """,
            (json.dumps(payload), int(first["id"])),
        )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=self.class_id)
        edited = _question_signature(self._meta("M1", "C3"))
        self.assertIn("Edited only on the C3 copy", edited[0][1])
        self.assertEqual(_question_signature(self._meta("M1", "C2")), source_before)
        self.assertEqual(SEED_C2.read_bytes(), self.c2_bytes)

    def test_c1_and_missing_previous_do_not_invent_a_deck(self) -> None:
        """C1 has no previous challenge. A course with no C2 file is refused."""

        refused = self.school.deck_seed_options(self.class_id, "M1", "C1")
        self.assertFalse(refused["previous"]["available"])
        self.assertIn("No previous challenge", refused["previous"]["message"])
        with self.assertRaises(ValueError) as caught:
            self.school.apply_class_deck_seed(
                self.class_id, "M1", "C1", mode="previous"
            )
        self.assertIn("No previous challenge", str(caught.exception))
        self.assertEqual(SEED_C2.read_bytes(), self.c2_bytes)

        self.school.conn.execute(
            "UPDATE classes SET course_code = ? WHERE id = ?",
            ("ABC1", self.class_id),
        )
        self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=self.class_id)
        missing = self.school.deck_seed_options(self.class_id, "M1", "C3")
        self.assertFalse(missing["previous"]["available"])
        self.assertIn("No deck saved for M1 C2", missing["previous"]["message"])
        self.assertEqual(missing["decks"], [])
        with self.assertRaises(ValueError) as missing_copy:
            self.school.apply_class_deck_seed(
                self.class_id, "M1", "C3", mode="previous"
            )
        self.assertIn("No deck saved for M1 C2", str(missing_copy.exception))
        self.assertFalse((LMS_DIR / "seeds" / "live_classes" / "ABC1").exists())
        self.assertEqual(SEED_C2.read_bytes(), self.c2_bytes)

    def test_course_picker_is_this_course_only(self) -> None:
        """Another-deck list follows the class course and skips the open slot."""

        mcf = self.school.deck_seed_options(self.class_id, "M2", "C3")
        self.assertEqual(mcf["course"], "MCF3M")
        self.assertTrue(mcf["previous"]["available"])
        self.assertEqual(mcf["previous"]["slot"], "C2")
        pairs = {(row["module"], row["slot"]) for row in mcf["decks"]}
        self.assertNotIn(("M2", "C3"), pairs)
        self.assertIn(("M1", "C2"), pairs)
        for row in mcf["decks"]:
            path = metadata_path("MCF3M", row["module"], row["slot"])
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.parent.parent.name, "MCF3M")

        mcr_offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": mcr_offering["id"],
                "days": "T/Th/F",
                "time": "10:40am",
                "codenames": ["Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        mcr_id = int(created.get_json()["class"]["id"])
        mcr = self.school.deck_seed_options(mcr_id, "M2", "C3")
        self.assertEqual(mcr["course"], "MCR3U")
        mcr_c2 = next(
            row
            for row in mcr["decks"]
            if row["module"] == "M1" and row["slot"] == "C2"
        )
        from live_class_metadata import list_live_lesson_summaries

        expected = next(
            row["question_count"]
            for row in list_live_lesson_summaries("MCR3U")
            if row["module"] == "M1" and row["live_class"] == "C2"
        )
        self.assertEqual(mcr_c2["question_count"], expected)
        for row in mcr["decks"]:
            path = metadata_path("MCR3U", row["module"], row["slot"])
            self.assertTrue(path.is_file() and path.parent.parent.name == "MCR3U")

    def test_blank_template_is_seven_empty_pages(self) -> None:
        """Blank ignores the authored seed and keeps the seven math pages."""

        authored = _question_signature(self._meta("M1", "C3"))
        self.assertTrue(authored)
        seeded = self.school.apply_class_deck_seed(
            self.class_id, "M1", "C3", mode="blank"
        )
        meta = seeded["live_metadata"]
        self.assertEqual(_question_signature(meta), [])
        self.assertEqual(
            [row["name"] for row in meta["pages"]],
            [row["name"] for row in default_math_pages()],
        )
        self.assertEqual(len(meta["pages"]), 7)
        self.assertEqual(meta.get("schema_version"), 2)
        self.assertEqual(SEED_C3.read_bytes(), self.c3_bytes)

    def test_blank_hides_waiting_room_questions(self) -> None:
        """Blank stays empty after a session has already minted Minds On."""

        started = self.client.post(
            f"/api/classes/{self.class_id}/live-session/start",
            json={"live_module": "M1", "live_slot": "C1"},
        )
        self.assertEqual(started.status_code, 200, started.get_json())
        session_id = int(started.get_json()["live_session_id"])
        self.school.ensure_live_session_items(session_id)
        self.school.ensure_waiting_room_minds_on(session_id)
        seeded = self.school.apply_class_deck_seed(
            self.class_id, "M1", "C1", mode="blank"
        )
        meta = seeded["live_metadata"]
        self.assertEqual(_question_signature(meta), [])
        self.assertEqual(
            [row["name"] for row in meta["pages"]],
            [row["name"] for row in default_math_pages()],
        )
        self.assertTrue(
            self.school.schema_v2_owns_live_stage_questions(session_id, "join")
        )
        cards = self.school.live_session_question_cards(session_id)
        blob = " ".join(str(card.get("text") or "") for card in cards).lower()
        self.assertNotIn("straight-line", blob)
        self.assertNotIn("teammate", blob)
        questions = [
            row
            for row in self.school.list_live_session_items(session_id)
            if str(row.get("kind") or "") == "question"
        ]
        self.assertEqual(questions, [])

    def test_course_copy_and_http_round_trip(self) -> None:
        """POST seeds from a chosen same-course deck and GET lists it."""

        options = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/M2/C1/deck-seed-options"
        )
        self.assertEqual(options.status_code, 200)
        body = options.get_json()
        self.assertFalse(body["previous"]["available"])
        self.assertEqual(body["blank"]["page_count"], 7)
        self.assertTrue(
            any(row["module"] == "M1" and row["slot"] == "C2" for row in body["decks"])
        )
        source = _question_signature(self._meta("M1", "C2"))
        copied = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M2/C1/deck-seed",
            json={"mode": "course", "source_module": "M1", "source_slot": "C2"},
        )
        self.assertEqual(copied.status_code, 200, copied.get_json())
        self.assertEqual(
            _question_signature(copied.get_json()["live_metadata"]), source
        )
        self.assertEqual(_question_signature(self._meta("M1", "C2")), source)
        self.assertEqual(SEED_C2.read_bytes(), self.c2_bytes)
        missing = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M2/C1/deck-seed",
            json={"mode": "course", "source_module": "M9", "source_slot": "C2"},
        )
        self.assertEqual(missing.status_code, 400)


class DeckSeedPickerUiTests(unittest.TestCase):
    """Chip helper copy, stale replies, and the empty-course chip."""

    def test_set_class_keeps_full_labels_on_chip_titles(self) -> None:
        """Short chip text stays, and the old labels remain in title."""

        page_html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        panel = page_html[
            page_html.index('id="ap-panel-validate"') : page_html.index(
                'id="live-shell-left"'
            )
        ]
        self.assertLess(panel.index('id="live-class-select"'), panel.index('id="live-deck-seed"'))
        fieldset = panel[
            panel.index('<fieldset class="live-deck-seed"') : panel.index(
                'id="live-deck-seed-picker"'
            )
        ]
        self.assertIn("</fieldset>", fieldset)
        self.assertIn('title="Copy previous challenge deck"', panel)
        self.assertIn('title="Copy another deck from this course"', panel)
        self.assertIn('title="Blank 7-page template"', panel)
        self.assertIn(">Previous</span>", panel)
        self.assertIn(">Course deck</span>", panel)
        self.assertIn(">Blank</span>", panel)
        self.assertIn(">Source deck</span>", panel)
        self.assertIn('id="live-deck-seed-picker"', panel)
        self.assertIn("hidden", panel[panel.index('id="live-deck-seed-picker"') : panel.index('id="live-deck-seed-picker"') + 120])
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(".live-deck-seed-picker[hidden] { display: none !important; }", css)
        self.assertIn('.live-deck-seed-seg input[type="radio"]', css)
        lloves = (LMS_DIR / "static" / "lloves.css").read_text(encoding="utf-8")
        self.assertIn("input, select, textarea {", lloves)

    def test_helper_stale_guard_and_course_chip(self) -> None:
        """Wonder copy, a mismatched class/challenge, and no other decks."""

        script = r"""
import {
  courseDeckChoiceDisabled,
  deckSeedHelpText,
  deckSeedResponseIsCurrent,
} from "./static/deck_seed_help.js";

const helpCases = [
  [
    { mode: "previous", previousAvailable: true, previousLabel: "M1 C2", previousSlot: "C2", courseDeckCount: 4 },
    "Starts from a copy of your M1 C2 deck.",
  ],
  [
    { mode: "course", previousAvailable: true, previousLabel: "M1 C2", previousSlot: "C2", courseDeckCount: 4 },
    "Starts from a copy of the deck you pick.",
  ],
  [
    { mode: "blank", previousAvailable: true, previousLabel: "M1 C2", previousSlot: "C2", courseDeckCount: 4 },
    "Starts from a blank 7-page deck.",
  ],
  [
    { mode: "blank", previousAvailable: false, previousSlot: "", courseDeckCount: 4 },
    "No earlier challenge in this module. Starting blank.",
  ],
  [
    { mode: "blank", previousAvailable: false, previousLabel: "M1 C2", previousSlot: "C2", courseDeckCount: 4 },
    "M1 C2 has no saved deck yet. Starting blank.",
  ],
  [
    { mode: "blank", previousAvailable: false, previousLabel: "M1 C2", previousSlot: "C2", courseDeckCount: 0 },
    "No other decks in this course yet.",
  ],
  [
    { phase: "loading", mode: "previous", previousAvailable: true, previousLabel: "M1 C2", courseDeckCount: 4 },
    "Loading decks…",
  ],
  [
    { phase: "error", mode: "blank", courseDeckCount: 0 },
    "Couldn't load decks. Blank still works.",
  ],
];

let failed = 0;
for (const [input, expected] of helpCases) {
  const got = deckSeedHelpText(input);
  if (got !== expected) {
    console.error("help " + JSON.stringify({ input, expected, got }));
    failed += 1;
  }
}

const here = { classId: 7, module: "M1", slot: "C3" };
if (!deckSeedResponseIsCurrent({ classId: 7, module: "M1", slot: "C3" }, here)) {
  console.error("same class and challenge should match");
  failed += 1;
}
if (!deckSeedResponseIsCurrent({ classId: 7, module: "m1", slot: "c3" }, here)) {
  console.error("module and slot compare should ignore case");
  failed += 1;
}
if (deckSeedResponseIsCurrent({ classId: 8, module: "M1", slot: "C3" }, here)) {
  console.error("a different class must be ignored");
  failed += 1;
}
if (deckSeedResponseIsCurrent({ classId: 7, module: "M1", slot: "C2" }, here)) {
  console.error("a different challenge must be ignored");
  failed += 1;
}
if (deckSeedResponseIsCurrent({ classId: 7, module: "M2", slot: "C3" }, here)) {
  console.error("a different module must be ignored");
  failed += 1;
}
if (deckSeedResponseIsCurrent({ classId: 7, module: "M1" }, here)) {
  console.error("a reply with no challenge must be ignored");
  failed += 1;
}
if (deckSeedResponseIsCurrent(null, here)) {
  console.error("a missing reply must be ignored");
  failed += 1;
}

if (!courseDeckChoiceDisabled(0)) {
  console.error("an empty course list disables Course deck");
  failed += 1;
}
if (!courseDeckChoiceDisabled(undefined)) {
  console.error("a missing course list disables Course deck");
  failed += 1;
}
if (courseDeckChoiceDisabled(1) || courseDeckChoiceDisabled(6)) {
  console.error("a course with other decks keeps Course deck enabled");
  failed += 1;
}

if (failed) process.exit(1);
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=LMS_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr or result.stdout,
        )
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        guard = js[
            js.index("function deckSeedFetchStillCurrent") : js.index(
                "function paintDeckSeedOptions"
            )
        ]
        self.assertIn("deckSeedResponseIsCurrent", guard)
        refresh = js[
            js.index("async function refreshDeckSeedOptions") : js.index(
                "async function applyDeckSeedChoice"
            )
        ]
        self.assertLess(
            refresh.index("deckSeedFetchStillCurrent"),
            refresh.index("paintDeckSeedOptions"),
        )
        paint = js[
            js.index("function paintDeckSeedOptions") : js.index(
                "async function refreshDeckSeedOptions"
            )
        ]
        self.assertIn("courseDeckChoiceDisabled(decks.length)", paint)
        self.assertLess(
            paint.index("courseDeckChoiceDisabled(decks.length)"),
            paint.index('deckSeedMode = "blank"'),
        )
