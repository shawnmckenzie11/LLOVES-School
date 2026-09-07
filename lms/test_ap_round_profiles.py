"""Tests for Open Question action profiles normalize/seed/persist."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from ap_round_profiles import (  # noqa: E402
    BUILTIN_OPEN_ACTIONS,
    active_open_actions,
    builtin_open_document,
    dump_profiles_json,
    normalize_profiles_document,
    pack_profiles_path,
    parse_profiles_json,
    seed_document_for_offering,
)


class ApRoundProfilesUnitTests(unittest.TestCase):
    """Pure helpers for profile documents."""

    def test_builtin_has_six_open_actions(self) -> None:
        """Default profile matches historical chip set."""
        doc = builtin_open_document()
        self.assertEqual(doc["open"]["active_id"], "default")
        self.assertEqual(len(doc["open"]["profiles"]), 1)
        self.assertEqual(
            active_open_actions(doc),
            BUILTIN_OPEN_ACTIONS,
        )

    def test_normalize_rejects_too_many_profiles(self) -> None:
        """Hard cap protects offering JSON size."""
        profiles = [
            {"id": f"p{i}", "name": f"P{i}", "actions": [{"id": "a", "label": "A", "amount": 1}]}
            for i in range(21)
        ]
        with self.assertRaises(ValueError):
            normalize_profiles_document({"open": {"active_id": "p0", "profiles": profiles}})

    def test_empty_break_title_style_defaults_active(self) -> None:
        """Corrupt active_id falls back to first profile."""
        doc = normalize_profiles_document(
            {
                "open": {
                    "active_id": "missing",
                    "profiles": [
                        {
                            "id": "alpha",
                            "name": "Alpha",
                            "actions": [{"id": "a", "label": "Ask", "amount": 2}],
                        }
                    ],
                }
            }
        )
        self.assertEqual(doc["open"]["active_id"], "alpha")
        self.assertEqual(active_open_actions(doc)[0]["amount"], 2)

    def test_pack_seed_used_when_offering_empty(self) -> None:
        """Pack JSON seeds a new offering before builtin."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            lib_id = 7
            path = pack_profiles_path(data, lib_id)
            path.parent.mkdir(parents=True)
            custom = {
                "open": {
                    "active_id": "live",
                    "profiles": [
                        {
                            "id": "live",
                            "name": "Live",
                            "actions": [{"id": "q", "label": "Great Q", "amount": 4}],
                        }
                    ],
                }
            }
            path.write_text(json.dumps(custom), encoding="utf-8")
            seeded = seed_document_for_offering(data, lib_id, None)
            self.assertEqual(seeded["open"]["active_id"], "live")
            self.assertEqual(active_open_actions(seeded)[0]["label"], "Great Q")

    def test_existing_offering_json_wins_over_pack(self) -> None:
        """Teacher edits on the offering are not clobbered by pack seed."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            existing = dump_profiles_json(
                {
                    "open": {
                        "active_id": "mine",
                        "profiles": [
                            {
                                "id": "mine",
                                "name": "Mine",
                                "actions": [{"id": "a", "label": "Mine", "amount": 9}],
                            }
                        ],
                    }
                }
            )
            seeded = seed_document_for_offering(data, None, existing)
            self.assertEqual(active_open_actions(seeded)[0]["amount"], 9)

    def test_parse_corrupt_falls_back(self) -> None:
        """Corrupt offering JSON must not break scoring."""
        doc = parse_profiles_json("{not-json")
        self.assertEqual(doc["open"]["active_id"], "default")


class ApRoundProfilesDbTests(unittest.TestCase):
    """Offering persistence via SchoolDB / Flask app factory."""

    def setUp(self) -> None:
        """Create an isolated school DB with one offering."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # create_app seeds ontario courses; SchoolDB alone does not.
        from app import create_app

        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.db = self.app.config["SCHOOL_DB"]
        self.db.activate_from_semester_json()
        teacher = self.db.register_staff("oq-teacher@gmail.com")
        self.offering = self.db.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )

    def tearDown(self) -> None:
        """Close DB and temp dir."""
        self.db.close()
        self.tmp.cleanup()

    def test_ensure_seeds_builtin(self) -> None:
        """Empty offering gets builtin Default profiles."""
        doc = self.db.ensure_offering_ap_round_profiles(int(self.offering["id"]))
        self.assertEqual(doc["open"]["profiles"][0]["id"], "default")
        again = self.db.get_offering(int(self.offering["id"]))
        self.assertTrue((again.get("ap_round_profiles_json") or "").strip())

    def test_set_and_get_round_trip(self) -> None:
        """PUT-style save persists custom actions."""
        custom = {
            "open": {
                "active_id": "custom",
                "profiles": [
                    {
                        "id": "custom",
                        "name": "Custom",
                        "actions": [
                            {"id": "hwk", "label": "Hwk Q", "amount": 2},
                            {"id": "peer", "label": "Peer", "amount": 3},
                        ],
                    }
                ],
            }
        }
        saved = self.db.set_offering_ap_round_profiles(int(self.offering["id"]), custom)
        self.assertEqual(len(saved["open"]["profiles"][0]["actions"]), 2)
        loaded = self.db.get_offering_ap_round_profiles(int(self.offering["id"]))
        self.assertEqual(loaded["open"]["active_id"], "custom")
        self.assertEqual(active_open_actions(loaded)[1]["label"], "Peer")


if __name__ == "__main__":
    unittest.main()
