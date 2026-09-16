#!/usr/bin/env python3
"""Tests for file-backed Run Live Class metadata."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from lms.live_class_metadata import (
    PUBLISH_MODES,
    RESPONSE_MODES,
    STAGE_ORDER,
    load_live_class_metadata,
    load_live_item_catalogue,
    metadata_path,
    metadata_root,
    normalize_live_class_metadata,
    questions_for_stage,
)
from scripts.generate_live_class_metadata import COURSES, LIVE_CLASSES, MODULES

SEEDS_ROOT = Path(__file__).resolve().parent / "seeds"
LEGACY_QUESTION_KEYS = {
    "id",
    "stage",
    "type",
    "text",
    "options",
    "correct_answer",
    "page_number",
    "order",
    "default_visibility",
}


class LiveClassMetadataTests(unittest.TestCase):
    """The LMS resolves v1 documents and thin v2 playlists safely."""

    def test_every_course_module_has_four_metadata_files(self) -> None:
        """All ten courses expose M1–M8 and C1–C4 on disk."""

        paths = [
            metadata_path(course, module, live_class)
            for course in COURSES
            for module in MODULES
            for live_class in LIVE_CLASSES
        ]
        self.assertEqual(len(paths), 320)
        self.assertTrue(all(path.is_file() for path in paths))

    def test_all_generated_files_are_thin_v2_playlists(self) -> None:
        """All 320 files contain refs and placement policy, not copied stems."""

        catalogue = load_live_item_catalogue()
        definitions = catalogue["items"]
        paths = [
            metadata_path(course, module, live_class)
            for course in COURSES
            for module in MODULES
            for live_class in LIVE_CLASSES
        ]
        forbidden_inline = {
            "text",
            "prompt",
            "options",
            "choices",
            "correct_answer",
            "file",
            "url",
            "feedback_id",
            "capabilities",
        }
        for path in paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 2, path)
            self.assertTrue(
                {"schema_version", "course", "module", "live_class", "items"}
                <= set(raw),
                path,
            )
            self.assertTrue(
                set(raw)
                <= {
                    "schema_version",
                    "course",
                    "module",
                    "live_class",
                    "items",
                    "pages",
                    "team_challenge",
                },
                path,
            )
            if path.parent.name == "M1" and path.stem == "C1" and path.parents[1].name in {
                "MCF3M",
                "MCR3U",
            }:
                self.assertEqual(len(raw.get("pages") or []), 7, path)
                self.assertTrue(str((raw.get("team_challenge") or {}).get("question") or ""), path)
            self.assertGreaterEqual(len(raw["items"]), 2, path)
            refs: list[str] = []
            for placement in raw["items"]:
                self.assertFalse(forbidden_inline.intersection(placement), path)
                self.assertEqual(placement["default_status"], "inactive", path)
                self.assertGreaterEqual(placement["page_number"], 1, path)
                self.assertGreaterEqual(placement["order"], 1, path)
                self.assertTrue(set(placement["publish_modes"]) <= PUBLISH_MODES, path)
                self.assertIn(placement["response_mode"], RESPONSE_MODES, path)
                ref = placement["ref"]
                refs.append(ref)
                self.assertIn(ref, definitions, path)
                supported = definitions[ref]["capabilities"]["publish_modes"]
                self.assertTrue(set(placement["publish_modes"]) <= set(supported), path)
            self.assertEqual(len(refs), len(set(refs)), path)
            spark = next(
                row for row in raw["items"] if row["ref"].endswith("/teams-spark")
            )
            self.assertEqual(spark["stage"], "teams", path)

    def test_mcf3m_m1c1_resolves_media_and_compatible_questions(self) -> None:
        """Known MCF3M refs project to legacy fields plus v2 placement policy."""

        metadata = load_live_class_metadata("MCF3M", "M1", "C1")
        self.assertEqual(metadata["schema_version"], 2)
        self.assertEqual(
            metadata["media"]["file"],
            "/static/live-media/m1c1-c1-real-slice.html",
        )
        self.assertEqual(
            metadata["media"]["ref"],
            "course/MCF3M/media/real-slice",
        )
        join = questions_for_stage(metadata, "join")
        self.assertEqual([row["type"] for row in join], ["mc"])
        self.assertEqual(join[0]["correct_answer"], "A")
        self.assertEqual(join[0]["feedback_id"], "minds_on")
        self.assertTrue(LEGACY_QUESTION_KEYS <= set(join[0]))
        self.assertFalse(join[0]["default_visibility"])
        self.assertEqual(join[0]["default_status"], "inactive")
        self.assertEqual(
            join[0]["ref"],
            "live-class/MCF3M/M1/C1/question/minds-on",
        )
        self.assertEqual(
            join[0]["publish_modes"],
            ["individual", "group_consensus"],
        )
        resolved_minds_on = next(
            item for item in metadata["items"] if item["id"] == "minds_on"
        )
        self.assertNotIn("correct_answer", resolved_minds_on)
        self.assertNotIn("feedback_id", resolved_minds_on)
        teams = questions_for_stage(metadata, "teams")
        self.assertEqual([row["id"] for row in teams], ["teams-spark"])
        self.assertEqual(teams[0]["page_number"], 2)

    def test_numeric_questions_drop_options(self) -> None:
        """Numeric metadata normalizes to integer-entry without choices."""

        metadata = load_live_class_metadata("MHF4U", "M8", "C4")
        teams = questions_for_stage(metadata, "teams")
        self.assertEqual(teams[0]["type"], "numeric")
        self.assertEqual(teams[0]["options"], [])
        self.assertIsNone(teams[0]["correct_answer"])

    def test_catalogue_has_all_scopes_and_explicit_content_capabilities(self) -> None:
        """Canonical items cover all scopes without inferring group behavior."""

        catalogue = load_live_item_catalogue()
        items = catalogue["items"]
        self.assertEqual(
            {item["scope"] for item in items.values()},
            {"universal", "course", "live_class"},
        )
        whiteboard = items["universal/whiteboard/live-workspace"]
        self.assertEqual(
            whiteboard["capabilities"]["publish_modes"],
            ["individual", "group_shared"],
        )
        slides = items["universal/slides/lesson-deck"]
        self.assertEqual(slides["capabilities"]["publish_modes"], ["individual"])
        for ref, item in items.items():
            if item["item_type"] in {"media", "slides"}:
                self.assertEqual(
                    item["capabilities"]["publish_modes"],
                    ["individual"],
                    ref,
                )

    def test_minds_on_feedback_ids_resolve_from_catalogue(self) -> None:
        """Each authored Minds-On carries its canonical server feedback id."""

        expected = {"C1": "minds_on", "C3": "C3-minds_on"}
        for live_class, feedback_id in expected.items():
            metadata = load_live_class_metadata("MCF3M", "M1", live_class)
            question = questions_for_stage(metadata, "join")[0]
            self.assertEqual(question["feedback_id"], feedback_id)

    def test_response_mode_and_capability_mismatch_are_normalized(self) -> None:
        """Only explicitly supported group behavior survives v2 resolution."""

        raw = {
            "schema_version": 2,
            "items": [
                {
                    "ref": "universal/question/teams-spark",
                    "stage": "teams",
                    "page_number": 1,
                    "order": 1,
                    "default_status": "active",
                    "publish_modes": ["individual", "group_consensus"],
                    "response_mode": "group_consensus",
                },
                {
                    "ref": "universal/question/meet-team",
                    "stage": "meet",
                    "page_number": 1,
                    "order": 1,
                    "default_status": "active",
                    "publish_modes": ["individual", "group_consensus"],
                    "response_mode": "group_consensus",
                },
                {
                    "ref": "universal/question/unknown",
                    "stage": "join",
                    "page_number": 1,
                    "order": 1,
                    "default_status": "inactive",
                    "publish_modes": ["individual"],
                    "response_mode": "individual",
                },
            ],
        }
        metadata = normalize_live_class_metadata(
            raw,
            course="MHF4U",
            module="M1",
            slot="C1",
        )
        self.assertEqual(len(metadata["items"]), 2)
        spark, meet = metadata["items"]
        self.assertEqual(spark["response_mode"], "group_consensus")
        self.assertEqual(spark["default_status"], "inactive")
        self.assertEqual(meet["publish_modes"], ["individual"])
        self.assertEqual(meet["response_mode"], "individual")

    def test_questions_for_stage_orders_by_page_then_order(self) -> None:
        """Resolved cards are deterministic within each stage."""

        raw = {
            "schema_version": 2,
            "items": [
                {
                    "ref": "universal/question/meet-team",
                    "stage": "join",
                    "page_number": 2,
                    "order": 1,
                    "default_status": "inactive",
                    "publish_modes": ["individual"],
                    "response_mode": "individual",
                },
                {
                    "ref": "universal/question/teams-spark",
                    "stage": "join",
                    "page_number": 1,
                    "order": 2,
                    "default_status": "inactive",
                    "publish_modes": ["individual"],
                    "response_mode": "individual",
                },
            ],
        }
        metadata = normalize_live_class_metadata(
            raw,
            course="MHF4U",
            module="M1",
            slot="C1",
        )
        self.assertEqual(
            [row["id"] for row in questions_for_stage(metadata, "join")],
            ["teams-spark", "meet-team"],
        )

    def test_v1_inline_metadata_remains_compatible(self) -> None:
        """Legacy documents retain inline media, questions, and visibility."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = metadata_path("MCF3M", "M1", "C1", root=root)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "media": {
                            "url": "/legacy/media.html",
                            "title": "Legacy media",
                        },
                        "questions": [
                            {
                                "id": "legacy-question",
                                "stage": "join",
                                "type": "mc",
                                "prompt": "Legacy prompt?",
                                "choices": ["Yes", "No"],
                                "correct": "A",
                                "page_number": 3,
                                "order": 2,
                                "default_visibility": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            metadata = load_live_class_metadata("MCF3M", "M1", "C1", root=root)
        self.assertEqual(metadata["schema_version"], 1)
        self.assertNotIn("items", metadata)
        self.assertEqual(metadata["media"]["file"], "/legacy/media.html")
        question = questions_for_stage(metadata, "join")[0]
        self.assertEqual(question["id"], "legacy-question")
        self.assertTrue(question["default_visibility"])
        self.assertEqual(question["options"], ["Yes", "No"])

    def test_missing_and_malformed_files_keep_v1_fallback(self) -> None:
        """Missing or invalid JSON returns the complete legacy empty shape."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = load_live_class_metadata("MCR3U", "M2", "C3", root=root)
            path = metadata_path("MCR3U", "M2", "C4", root=root)
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            malformed = load_live_class_metadata("MCR3U", "M2", "C4", root=root)
        for metadata in (missing, malformed):
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["questions"], [])
            self.assertIsNone(metadata["media"])
            self.assertEqual(
                metadata["slides"],
                {"deck_ref": None, "page_numbers": []},
            )

    def test_missing_v2_catalogue_skips_refs_without_crashing(self) -> None:
        """A v2 playlist remains safe when its separate catalogue is absent."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "live_classes"
            catalogue_root = Path(directory) / "empty-seeds"
            path = metadata_path("MCF3M", "M1", "C1", root=root)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "items": [
                            {
                                "ref": "universal/question/teams-spark",
                                "stage": "teams",
                                "page_number": 1,
                                "order": 1,
                                "default_status": "inactive",
                                "publish_modes": ["individual"],
                                "response_mode": "individual",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            metadata = load_live_class_metadata(
                "MCF3M",
                "M1",
                "C1",
                root=root,
                catalogue_root=catalogue_root,
            )
        self.assertEqual(metadata["schema_version"], 2)
        self.assertEqual(metadata["items"], [])
        self.assertEqual(metadata["questions"], [])

    def test_committed_json_schemas_describe_v2_contracts(self) -> None:
        """Catalogue and playlist schemas are committed, parseable JSON."""

        catalogue_schema = json.loads(
            (SEEDS_ROOT / "live_items.schema.json").read_text(encoding="utf-8")
        )
        playlist_schema = json.loads(
            (SEEDS_ROOT / "live_class_playlist.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(catalogue_schema["properties"]["schema_version"]["const"], 2)
        required = playlist_schema["$defs"]["placement"]["required"]
        self.assertEqual(
            set(required),
            {
                "ref",
                "stage",
                "page_number",
                "order",
                "default_status",
                "publish_modes",
                "response_mode",
            },
        )

    def test_resolved_items_follow_placement_order_in_all_playlists(self) -> None:
        """Every generated playlist resolves in stage/page/order order."""

        stage_order = {name: index for index, name in enumerate(STAGE_ORDER)}
        for path in metadata_root().glob("*/*/C*.json"):
            course = path.parents[1].name
            module = path.parent.name
            live_class = path.stem
            metadata = load_live_class_metadata(course, module, live_class)
            keys = [
                (
                    stage_order[item["stage"]],
                    item["page_number"],
                    item["order"],
                    item["ref"],
                )
                for item in metadata["items"]
            ]
            self.assertEqual(keys, sorted(keys), path)


    def test_mcf3m_m1c1_has_named_pages_and_team_challenge(self) -> None:
        """MCF3M M1C1 is the authored seven-page live-lesson preset."""

        metadata = load_live_class_metadata("MCF3M", "M1", "C1")
        pages = metadata["pages"]
        self.assertEqual([row["name"] for row in pages], [
            "Join",
            "Welcome",
            "Meet",
            "Round 1",
            "Round 2",
            "Round 3",
            "Summary",
        ])
        self.assertTrue(metadata["team_challenge"]["question"])
        mcr = load_live_class_metadata("MCR3U", "M1", "C1")
        self.assertEqual(len(mcr["pages"]), 7)
        self.assertTrue(mcr["team_challenge"]["question"])
        self.assertNotEqual(
            metadata["team_challenge"]["question"],
            mcr["team_challenge"]["question"],
        )

    def test_mcr3u_m1c2_uses_function_notation_and_parent_range(self) -> None:
        """MCR3U M1C2 Join, Round 1 domain MCs, and Round 3 parent questions."""

        metadata = load_live_class_metadata("MCR3U", "M1", "C2")
        self.assertEqual([row["name"] for row in metadata["pages"]], [
            "Join",
            "Welcome",
            "Meet",
            "Round 1",
            "Round 2",
            "Round 3",
            "Summary",
        ])
        questions = metadata["questions"]
        join_ids = [row["id"] for row in questions if row["stage"] == "join"]
        round1_ids = [row["id"] for row in questions if row["stage"] == "round"]
        round_ids = [row["id"] for row in questions if row["stage"] == "round_3"]
        self.assertEqual(join_ids, ["function-notation", "evaluate-f2"])
        self.assertEqual(round1_ids, ["not-in-domain-sqrt", "not-in-domain-reciprocal"])
        self.assertEqual(
            round_ids,
            [
                "parent-range-quadratic",
                "parent-domain-sqrt",
                "parent-domain-reciprocal",
            ],
        )
        sqrt_q = next(row for row in questions if row["id"] == "not-in-domain-sqrt")
        recip_q = next(row for row in questions if row["id"] == "not-in-domain-reciprocal")
        self.assertEqual(sqrt_q["page_number"], 4)
        self.assertEqual(recip_q["page_number"], 4)
        self.assertEqual(sqrt_q["correct_answer"], "B")
        self.assertEqual(recip_q["correct_answer"], "B")
        self.assertIn("√(x − 4)", sqrt_q["text"])
        self.assertIn("1/x", recip_q["text"])
        self.assertEqual(sqrt_q["options"], ["4", "3", "7", "9.3"])
        self.assertEqual(recip_q["options"], ["4", "0", "7", "9.3"])
        notation = next(row for row in questions if row["id"] == "function-notation")
        numeric = next(row for row in questions if row["id"] == "evaluate-f2")
        self.assertEqual(notation["type"], "mc")
        self.assertEqual(notation["correct_answer"], "B")
        self.assertEqual(numeric["type"], "numeric")
        self.assertEqual(numeric["correct_answer"], "-2")
        for row in questions:
            if row["stage"] in {"join", "round", "round_3"}:
                self.assertEqual(row["default_status"], "inactive")
                self.assertEqual(row["response_mode"], "individual")
                self.assertNotIn("feedback_id", row)

    def test_mcf3m_m1_c2_join_and_round_1(self) -> None:
        """MCF3M M1C2 Join matches MCR3U function notation; Round 1 asks a and c."""

        metadata = load_live_class_metadata("MCF3M", "M1", "C2")
        self.assertEqual(
            [row["name"] for row in metadata["pages"]],
            [
                "Join",
                "Welcome",
                "Meet",
                "Round 1",
                "Round 2",
                "Round 3",
                "Summary",
            ],
        )
        questions = metadata["questions"]
        join_ids = [row["id"] for row in questions if row["stage"] == "join"]
        round_ids = [row["id"] for row in questions if row["stage"] == "round"]
        self.assertEqual(join_ids, ["function-notation", "evaluate-f2"])
        self.assertEqual(round_ids, ["parabola-a", "parabola-c"])
        notation = next(row for row in questions if row["id"] == "function-notation")
        numeric = next(row for row in questions if row["id"] == "evaluate-f2")
        about_a = next(row for row in questions if row["id"] == "parabola-a")
        about_c = next(row for row in questions if row["id"] == "parabola-c")
        self.assertEqual(notation["type"], "mc")
        self.assertEqual(notation["correct_answer"], "B")
        self.assertEqual(numeric["type"], "numeric")
        self.assertEqual(numeric["correct_answer"], "-2")
        self.assertTrue(numeric.get("integer_only"))
        self.assertEqual(about_a["correct_answer"], "B")
        self.assertEqual(about_c["correct_answer"], "D")
        self.assertEqual(about_a["page_number"], 4)
        self.assertEqual(about_c["page_number"], 4)
        for row in questions:
            if row["stage"] in {"join", "round"}:
                self.assertEqual(row["default_status"], "inactive")
                self.assertEqual(row["response_mode"], "individual")
                self.assertNotIn("feedback_id", row)


if __name__ == "__main__":
    unittest.main()
