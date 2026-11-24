"""MCK-511: student whiteboard equations, text styles, and board wipe."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))
sys.path.insert(0, str(ROOT))

from board_ops import BoardOpRejected, SqliteBoardOps, WIPE_UNWIPE_MAX_BYTES, fold_one_board  # noqa: E402


class Mck511TemplateTests(unittest.TestCase):
    """Student home toolbar exposes Equation and Clear."""

    def test_home_toolbar_has_equation_and_clear(self) -> None:
        """Floating whiteboard keeps Pen, Line, Text, √, Undo, Clear order."""
        home = (LMS / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="student-canvas-equation"', home)
        self.assertIn('id="student-canvas-clear"', home)
        self.assertIn('aria-label="Equation"', home)
        idx_text = home.index("student-canvas-text")
        idx_eq = home.index("student-canvas-equation")
        idx_undo = home.index("student-canvas-undo")
        idx_clear = home.index("student-canvas-clear")
        self.assertLess(idx_text, idx_eq)
        self.assertLess(idx_eq, idx_undo)
        self.assertLess(idx_undo, idx_clear)


class Mck511FoldTests(unittest.TestCase):
    """Board op folding keeps wipe, text style, and equation round-trips."""

    def setUp(self) -> None:
        """Private sqlite file with board ops schema."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "boards.sqlite"
        import sqlite3

        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self.store = SqliteBoardOps(conn)
        self.store.ensure_schema()
        self.conn = conn

    def tearDown(self) -> None:
        """Close connection and remove the temp directory."""
        self.conn.close()
        self.tmp.cleanup()

    def test_text_style_fold_round_trip(self) -> None:
        """Bold, underline, script, and size survive a reload."""
        self.store.append_text(
            7,
            "solo:5",
            owner="5",
            name="A",
            text_id="t1",
            text="Hi",
            x=0.2,
            y=0.3,
            color="#12202e",
            team_id=None,
            bold=True,
            underline=True,
            script="super",
            size=4,
        )
        rows = self.store.load_keys(7, ["solo:5"])
        view = fold_one_board(rows)
        label = view["texts"][0]
        self.assertTrue(label.get("bold"))
        self.assertTrue(label.get("underline"))
        self.assertEqual(label.get("script"), "super")
        self.assertEqual(label.get("size"), 4)

    def test_equation_fold_round_trip(self) -> None:
        """Equation latex and position fold from board ops."""
        self.store.append_equation(
            8,
            "solo:6",
            owner="6",
            name="B",
            equation_id="e1",
            latex=r"x^2",
            x=0.4,
            y=0.5,
            color="#12202e",
            team_id=None,
        )
        rows = self.store.load_keys(8, ["solo:6"])
        view = fold_one_board(rows)
        self.assertEqual(len(view.get("equations") or []), 1)
        eq = view["equations"][0]
        self.assertEqual(eq["id"], "e1")
        self.assertEqual(eq["latex"], "x^2")
        self.assertAlmostEqual(float(eq["x"]), 0.4)
        self.assertAlmostEqual(float(eq["y"]), 0.5)

    def test_board_wipe_unwipe_round_trip(self) -> None:
        """Wipe clears one owner; unwipe restores the capped snapshot."""
        self.store.append_ops(
            9,
            "solo:7",
            owner="7",
            ops=[
                {"type": "stroke_add", "id": "s1", "owner": "7", "points": [[0.1, 0.2]]},
                {"type": "stroke_end", "id": "s1"},
            ],
        )
        self.store.append_text(
            9,
            "solo:7",
            owner="7",
            name="C",
            text_id="tx",
            text="note",
            x=0.25,
            y=0.35,
            color="#12202e",
            team_id=None,
        )
        folded_before = fold_one_board(self.store.load_keys(9, ["solo:7"]))
        snapshot = {
            "strokes": folded_before["strokes"],
            "texts": folded_before["texts"],
            "equations": folded_before.get("equations") or [],
        }
        self.store.append_wipe(9, "solo:7", owner="7", wipe_id="w1")
        wiped = fold_one_board(self.store.load_keys(9, ["solo:7"]))
        self.assertEqual(wiped.get("strokes"), [])
        self.assertEqual(wiped.get("texts"), [])
        self.store.append_unwipe(9, "solo:7", owner="7", unwipe_id="u1", snapshot=snapshot)
        restored = fold_one_board(self.store.load_keys(9, ["solo:7"]))
        self.assertEqual(len(restored.get("strokes") or []), 1)
        self.assertEqual(len(restored.get("texts") or []), 1)

    def test_board_unwipe_rejects_oversize_snapshot(self) -> None:
        """Snapshots above WIPE_UNWIPE_MAX_BYTES are rejected."""
        fat_points = [[i / 10000.0, (i % 97) / 100.0] for i in range(2000)]
        snapshot = {
            "strokes": [
                {
                    "id": "heavy",
                    "owner": "8",
                    "color": "#12202e",
                    "points": fat_points,
                }
            ],
            "texts": [],
            "equations": [],
        }
        payload = json.dumps(
            {"type": "board_unwipe", "id": "u-big", "owner": "8", **snapshot},
            separators=(",", ":"),
        )
        self.assertGreater(len(payload.encode("utf-8")), WIPE_UNWIPE_MAX_BYTES)
        with self.assertRaises(BoardOpRejected):
            self.store.append_unwipe(
                10,
                "solo:8",
                owner="8",
                unwipe_id="u-big",
                snapshot=snapshot,
            )


if __name__ == "__main__":
    unittest.main()