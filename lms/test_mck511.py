"""MCK-511: student whiteboard equations, text styles, and board wipe."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))
sys.path.insert(0, str(ROOT))

from board_ops import BoardOpRejected, SqliteBoardOps, fold_one_board  # noqa: E402
from live_canvas import MAX_POINTS  # noqa: E402


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

    def test_board_wipe_unwipe_round_trip_by_wipe_id(self) -> None:
        """Wipe clears one owner; unwipe by wipe_id restores server-captured ink."""
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
        self.store.append_wipe(9, "solo:7", owner="7", wipe_id="w1")
        wiped = fold_one_board(self.store.load_keys(9, ["solo:7"]))
        self.assertEqual(wiped.get("strokes"), [])
        self.assertEqual(wiped.get("texts"), [])
        self.store.append_unwipe(9, "solo:7", owner="7", unwipe_id="u1", wipe_id="w1")
        restored = fold_one_board(self.store.load_keys(9, ["solo:7"]))
        self.assertEqual(len(restored.get("strokes") or []), 1)
        self.assertEqual(len(restored.get("texts") or []), 1)

    def test_team_unwipe_cannot_touch_other_owners(self) -> None:
        """Student A unwipe restores only A ink; B and teacher stay unchanged."""
        rk = 20
        board = "team:3"
        self.store.append_ops(
            rk,
            board,
            owner="11",
            ops=[
                {"type": "stroke_add", "id": "a-stroke", "owner": "11", "points": [[0.2, 0.2]]},
                {"type": "stroke_end", "id": "a-stroke"},
            ],
        )
        self.store.append_ops(
            rk,
            board,
            owner="12",
            ops=[
                {"type": "stroke_add", "id": "b-stroke", "owner": "12", "points": [[0.4, 0.4]]},
                {"type": "stroke_end", "id": "b-stroke"},
            ],
        )
        self.store.append_text(
            rk,
            board,
            owner="teacher",
            name="T",
            text_id="t-teacher",
            text="watch",
            x=0.5,
            y=0.5,
            color="#12202e",
            team_id=3,
        )
        self.store.append_wipe(rk, board, owner="11", wipe_id="w-a")
        mid = fold_one_board(self.store.load_keys(rk, [board]))
        self.assertEqual(len(mid["strokes"]), 1)
        self.assertEqual(mid["strokes"][0]["id"], "b-stroke")
        b_points = list(mid["strokes"][0]["points"])
        self.store.append_unwipe(rk, board, owner="11", unwipe_id="u-a", wipe_id="w-a")
        after = fold_one_board(self.store.load_keys(rk, [board]))
        self.assertEqual(len(after["strokes"]), 2)
        b_after = next(row for row in after["strokes"] if row["id"] == "b-stroke")
        self.assertEqual(b_after["owner"], "12")
        self.assertEqual(b_after["points"], b_points)
        self.assertEqual(after["texts"][0]["owner"], "teacher")
        with self.assertRaises(BoardOpRejected):
            self.store.append_unwipe(rk, board, owner="11", unwipe_id="u2", wipe_id="w-a")
        with self.assertRaises(BoardOpRejected):
            self.store.append_unwipe(rk, board, owner="12", unwipe_id="u3", wipe_id="w-a")

    def test_large_board_single_unwipe_restores_all(self) -> None:
        """Several max-point strokes plus text and equation undo in one unwipe op."""
        rk = 21
        board = "solo:99"
        owner = "99"
        fat = [[i / max(1, MAX_POINTS), (i % 97) / 100.0] for i in range(MAX_POINTS)]
        for n in range(4):
            sid = f"fat-{n}"
            self.store.append_ops(
                rk,
                board,
                owner=owner,
                ops=[
                    {"type": "stroke_add", "id": sid, "owner": owner, "points": fat},
                    {"type": "stroke_end", "id": sid},
                ],
            )
        self.store.append_text(
            rk,
            board,
            owner=owner,
            name="N",
            text_id="lbl",
            text="long",
            x=0.12,
            y=0.88,
            color="#12202e",
            team_id=None,
        )
        self.store.append_equation(
            rk,
            board,
            owner=owner,
            name="N",
            equation_id="eq",
            latex=r"\frac{a}{b}",
            x=0.55,
            y=0.44,
            color="#12202e",
            team_id=None,
        )
        self.store.append_wipe(rk, board, owner=owner, wipe_id="big")
        self.store.append_unwipe(rk, board, owner=owner, unwipe_id="undo-big", wipe_id="big")
        restored = fold_one_board(self.store.load_keys(rk, [board]))
        self.assertEqual(len(restored["strokes"]), 4)
        for row in restored["strokes"]:
            self.assertEqual(len(row["points"]), MAX_POINTS)
        self.assertEqual(len(restored["texts"]), 1)
        self.assertEqual(len(restored.get("equations") or []), 1)

    def test_unwipe_rejects_double_undo_stale_and_foreign_wipe(self) -> None:
        """Second undo, missing wipe id, and another owner's wipe are rejected."""
        rk = 22
        board = "solo:3"
        self.store.append_ops(
            rk,
            board,
            owner="3",
            ops=[{"type": "stroke_add", "id": "s", "owner": "3", "points": [[0.1, 0.2]]}],
        )
        self.store.append_wipe(rk, board, owner="3", wipe_id="wipe-3")
        self.store.append_unwipe(rk, board, owner="3", unwipe_id="u1", wipe_id="wipe-3")
        with self.assertRaises(BoardOpRejected):
            self.store.append_unwipe(rk, board, owner="3", unwipe_id="u2", wipe_id="wipe-3")
        with self.assertRaises(BoardOpRejected):
            self.store.append_unwipe(rk, board, owner="3", unwipe_id="u3", wipe_id="missing")
        self.store.append_wipe(rk, board, owner="4", wipe_id="wipe-4")
        with self.assertRaises(BoardOpRejected):
            self.store.append_unwipe(rk, board, owner="3", unwipe_id="u4", wipe_id="wipe-4")

    def test_legacy_op_log_folds_unchanged_without_wipe_ops(self) -> None:
        """Ink-only sequences fold the same before and after unwipe redesign."""
        rk = 23
        board = "solo:8"
        ops = [
            {"type": "stroke_add", "id": "a", "owner": "8", "points": [[0.1, 0.1], [0.2, 0.2]]},
            {"type": "stroke_end", "id": "a"},
            {"type": "text_upsert", "id": "t", "owner": "8", "text": "ok", "x": 0.3, "y": 0.3},
            {"type": "equation_upsert", "id": "e", "owner": "8", "latex": "y", "x": 0.4, "y": 0.4},
        ]
        self.store.append_ops(rk, board, owner="8", ops=ops)
        view = fold_one_board(self.store.load_keys(rk, [board]))
        self.assertEqual(len(view["strokes"]), 1)
        self.assertEqual(view["strokes"][0]["id"], "a")
        self.assertEqual(len(view["texts"]), 1)
        self.assertEqual(view["texts"][0]["text"], "ok")
        self.assertEqual(len(view.get("equations") or []), 1)


if __name__ == "__main__":
    unittest.main()
