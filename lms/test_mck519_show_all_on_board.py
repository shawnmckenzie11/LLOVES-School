#!/usr/bin/env python3
"""MCK-519: Show All on Board in the teacher whiteboard dashboard.

The header button sits beside Tiles/row. Activating it projects every
board, with the same image-under-marks rule as Show on Board. Names stay
on the teacher gallery and the projector. Student phones receive the
grid with those names removed. Ink rows are not rewritten, and the
wipe path is not entered. One-board Show on Board stays as it was.

Synthetic roster only. Assertions do not print names, emails, or join codes.
"""

from __future__ import annotations

import json
import logging
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

from live_news_wire import log_for, reset_logs  # noqa: E402
from question_board import public_board_image_url, question_run_key  # noqa: E402

IMAGE = "/static/bank-graphs/parabola-grid.svg"
UNSAFE_URLS = (
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "http://example.test/board.png",
    "//cdn.example/board.png",
)


def _staff_key() -> str:
    """Synthetic staff login. Not a real account."""
    return "fixture-staff" + "@" + "example.com"


def _stroke(stroke_id: str) -> list[dict]:
    """One short stroke inside the unit square.

    Args:
        stroke_id: Client stroke id.
    """
    return [
        {"type": "stroke_add", "id": stroke_id, "points": [[0.25, 0.5], [0.75, 0.5]]},
        {"type": "stroke_end", "id": stroke_id},
    ]


class ShowAllButtonTests(unittest.TestCase):
    """The control is in the teacher whiteboard header, beside Tiles/row."""

    def test_button_sits_beside_the_tiles_dropdown(self) -> None:
        """Dashboard and live whiteboard both put Show All next to Tiles/row."""
        sources = {
            "course": (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8"),
            "dashboard": (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8"),
        }
        for label, source in sources.items():
            start = source.index("Tiles/row")
            window = source[start : start + 700]
            self.assertIn("Show All on Board", window, label)
            self.assertLess(window.index("Tiles per row"), window.index("Show All on Board"), label)
            self.assertLess(window.index("</select>"), window.index("Show All on Board"), label)

    def test_grid_paints_image_under_marks_and_keeps_names(self) -> None:
        """Node check for the class grid: fit, names, and rejected image URLs."""
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "poll_board_show_all.test.mjs")],
            cwd=str(LMS_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class ShowAllLiveTests(unittest.TestCase):
    """One live class with two synthetic students and a board question."""

    def setUp(self) -> None:
        """Open a temp school and join two roster rows."""
        from app import create_app

        logging.disable(logging.WARNING)
        reset_logs()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        staff = _staff_key()
        self.teacher = self.school.register_staff(staff)
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={staff}&name=T")
        code = self.school.get_user_by_email(staff)["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["P1", "P2"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round"},
        )
        self.school.game.begin_game(self.class_id)
        self.ids: list[int] = []
        for codename in ("P1", "P2"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=codename
            )

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _publish(self, text: str, **extra: object) -> tuple[int, int]:
        """Add a board question and publish it.

        Args:
            text: Stem, used only to find the row.
            extra: More editor fields, such as ``image_url``.

        Returns:
            ``(prompt id, lifecycle id)``.
        """
        body: dict = {
            "type": "poll",
            "text": text,
            "page_number": 1,
            "stage": "round",
            "answer_with": "board",
            "save_to_bank": False,
            **extra,
        }
        posted = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertLess(posted.status_code, 400, posted.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str((row.get("item") or {}).get("text") or "") == text
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        return int(published["prompt_id"]), int(item["id"])

    def _show_all(self, prompt_id: int):
        """Post Show All for one question."""
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/show-all"
        )

    def _ink_rows(self, prompt_id: int) -> list[dict]:
        """Stored ops for both solo boards. No image bytes belong here."""
        base, _gen = self.school._live_board_base_run_key(self.session_id)
        run = question_run_key(base, prompt_id)
        keys = [f"solo:{sid}" for sid in self.ids]
        return list(self.school.boards.load_keys(run, keys))

    def test_show_all_projects_every_board_with_one_image_and_a_name(self) -> None:
        """The class receives every board and one image reference. Names stay off the phone."""
        prompt_id, _item_id = self._publish(
            "Show the row", image_url=IMAGE, image_as_board_background=True
        )
        for index, sid in enumerate(self.ids):
            self.school.append_question_board_ops(
                self.session_id, prompt_id, sid, _stroke(f"mark-{index}")
            )
        before = len(self._ink_rows(prompt_id))
        self.assertGreater(before, 0)

        def refuse_wipe(*_args: object, **_kwargs: object) -> None:
            """Show All must not enter wipe or unwipe."""
            raise AssertionError("show all entered the wipe path")

        self.school.apply_live_board_wipe = refuse_wipe  # type: ignore[method-assign]
        self.school.apply_live_board_unwipe = refuse_wipe  # type: ignore[method-assign]
        self.school.boards.append_wipe = refuse_wipe  # type: ignore[method-assign]
        seq_before = int(
            self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0
        )
        tape = log_for(self.school.data_dir)
        cursor = max((int(row.get("id") or 0) for row in tape.since(self.session_id, 0)), default=0)
        shown = self._show_all(prompt_id)
        self.assertEqual(shown.status_code, 200, shown.get_json())
        self.assertTrue(shown.get_json().get("projected_all"))
        self.assertEqual(
            int(self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0),
            seq_before,
        )
        dash = self.school.question_dashboard(self.session_id, prompt_id)
        self.assertTrue(dash.get("projected_all"))
        self.assertEqual(dash.get("projected_board") or "", "")
        names = {
            str(tile.get("board_key") or ""): str(tile.get("name") or "")
            for tile in dash.get("tiles") or []
        }
        self.assertEqual(set(names), {f"solo:{sid}" for sid in self.ids})
        for label in names.values():
            self.assertTrue(label.strip())
        payload = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertIsNone(payload.get("class_board"))
        boards = payload.get("class_boards") or []
        self.assertEqual(len(boards), len(self.ids))
        for board in boards:
            self.assertNotIn("name", board)
            self.assertNotIn("member_names", board)
            self.assertEqual(board.get("image_url"), IMAGE)
            image_keys = [field for field in board if "image" in str(field).lower()]
            self.assertEqual(image_keys, ["image_url"])
            self.assertNotIn("student_id", board)
            self.assertNotIn("email", board)
            self.assertTrue((board.get("canvas_view") or {}).get("strokes"))
            self.assertEqual(public_board_image_url(board.get("image_url")), IMAGE)
        stored = json.dumps(self._ink_rows(prompt_id), default=str)
        self.assertEqual(len(self._ink_rows(prompt_id)), before)
        self.assertNotIn(IMAGE, stored)
        self.assertNotIn("data:image", stored)
        news = [
            row
            for row in tape.since(self.session_id, cursor)
            if row.get("type") == "board_show"
        ]
        self.assertTrue(news)
        self.assertEqual(news[-1].get("board_key"), "all")
        self.assertNotIn("board", news[-1])
        self.assertNotIn('"name"', json.dumps(news[-1]))
        self.assertFalse(
            any(row.get("type") == "state_seq" for row in tape.since(self.session_id, cursor))
        )

    def _nested_keys(self, value: object) -> list[str]:
        """Every object key in a JSON tree, lowercased.

        Args:
            value: Decoded JSON object, list, or scalar.
        """
        found: list[str] = []

        def walk(node: object) -> None:
            """Collect keys from one node."""
            if isinstance(node, dict):
                for key, item in node.items():
                    found.append(str(key).lower())
                    walk(item)
                return
            if isinstance(node, list):
                for item in node:
                    walk(item)

        walk(value)
        return found

    def _nested_strings(self, value: object) -> list[str]:
        """Every string in a JSON tree, keys and values included.

        Args:
            value: Decoded JSON object, list, or scalar.
        """
        found: list[str] = []

        def walk(node: object) -> None:
            """Collect strings from one node."""
            if isinstance(node, str):
                found.append(node)
                return
            if isinstance(node, dict):
                for key, item in node.items():
                    walk(str(key))
                    walk(item)
                return
            if isinstance(node, list):
                for item in node:
                    walk(item)

        walk(value)
        return found

    def test_show_all_strips_names_from_student_phones(self) -> None:
        """Phones get no name or member_names. The teacher and projector still do."""
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.ids,
            assignments=[
                {"student_id": self.ids[0], "team_index": 0},
                {"student_id": self.ids[1], "team_index": 1},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id, groups_configured=True, run_as_group=True
        )
        body = {
            "type": "poll",
            "text": "Team grid",
            "page_number": 1,
            "stage": "round",
            "answer_with": "board",
            "save_to_bank": False,
            "live_response_mode": "group",
        }
        posted = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertLess(posted.status_code, 400, posted.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str((row.get("item") or {}).get("text") or "") == "Team grid"
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="group_shared"
        )
        prompt_id = int(published["prompt_id"])
        shown = self._show_all(prompt_id)
        self.assertEqual(shown.status_code, 200, shown.get_json())
        self.assertTrue(shown.get_json().get("projected_all"))

        dash = self.school.question_dashboard(self.session_id, prompt_id)
        tiles = dash.get("tiles") or []
        self.assertTrue(tiles)
        labels = [
            str(tile.get("name") or "").strip()
            for tile in tiles
            if str(tile.get("name") or "").strip()
        ]
        members = [
            str(name).strip()
            for tile in tiles
            for name in (tile.get("member_names") or [])
            if str(name).strip()
        ]
        self.assertTrue(labels)
        self.assertTrue(members)
        panel = self.client.get(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/team:0"
        )
        self.assertEqual(panel.status_code, 200, panel.get_json())
        projector = panel.get_json()
        self.assertTrue(str(projector.get("name") or "").strip())
        self.assertTrue(
            any(str(row.get("name") or "").strip() for row in projector.get("legend") or [])
        )
        source = self.school.whiteboard_class_boards(self.session_id) or []
        self.assertTrue(any(str(card.get("name") or "").strip() for card in source))
        self.assertTrue(
            any(card.get("member_names") for card in source)
        )

        state = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        phone = self.school.read_board(
            self.session_id,
            "team:0",
            0,
            student_id=self.ids[0],
        )
        for payload in (state.get("class_boards"), phone.get("class_boards")):
            self.assertIsInstance(payload, list)
            self.assertTrue(payload)
            keys = self._nested_keys(payload)
            self.assertNotIn("name", keys)
            self.assertNotIn("codename", keys)
            self.assertNotIn("member_names", keys)
            blob = "\n".join(self._nested_strings(payload)).casefold()
            for label in labels + members:
                self.assertNotIn(label.casefold(), blob)

    def test_unsafe_image_urls_are_left_off_the_grid(self) -> None:
        """javascript, data, http, and protocol-relative URLs are not sent."""
        for index, url in enumerate(UNSAFE_URLS):
            prompt_id, _item_id = self._publish(
                f"Unsafe {index}", image_url=url, image_as_board_background=True
            )
            shown = self._show_all(prompt_id)
            self.assertEqual(shown.status_code, 200, shown.get_json())
            boards = self.school.whiteboard_class_boards(self.session_id) or []
            self.assertTrue(boards)
            for board in boards:
                self.assertNotIn("image_url", board)
                self.assertNotIn(url, json.dumps(board))

    def test_single_show_on_board_is_unchanged(self) -> None:
        """One board still projects alone, with the image reference and no grid."""
        prompt_id, _item_id = self._publish(
            "Just one", image_url=IMAGE, image_as_board_background=True
        )
        sid = self.ids[0]
        self.school.append_question_board_ops(
            self.session_id, prompt_id, sid, _stroke("one")
        )
        self._show_all(prompt_id)
        self.assertIsNotNone(self.school.whiteboard_class_boards(self.session_id))
        reset_logs()
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/solo:{sid}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        self.assertTrue(shown.get_json().get("projected"))
        board = self.school.whiteboard_class_board(self.session_id)
        self.assertEqual(board.get("board_key"), f"solo:{sid}")
        self.assertEqual(board.get("image_url"), IMAGE)
        self.assertNotIn("name", board)
        self.assertIsNone(self.school.whiteboard_class_boards(self.session_id))
        dash = self.school.question_dashboard(self.session_id, prompt_id)
        self.assertFalse(dash.get("projected_all"))
        self.assertEqual(dash.get("projected_board"), f"solo:{sid}")
        tape = log_for(self.school.data_dir).since(self.session_id, 0)
        cards = [row for row in tape if row.get("type") == "board_show" and row.get("board")]
        self.assertTrue(cards)
        self.assertEqual(cards[-1]["board"].get("image_url"), IMAGE)
        self.assertNotEqual(cards[-1].get("board_key"), "all")
        rows = self._ink_rows(prompt_id)
        self.assertNotIn(IMAGE, json.dumps(rows, default=str))

    def test_live_whiteboard_show_all_toggles(self) -> None:
        """The live whiteboard header uses the same grid toggle."""
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/show-all"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        self.assertTrue(shown.get_json().get("projected_all"))
        payload = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertIsNone(payload.get("class_board"))
        self.assertEqual(payload.get("class_boards"), [])
        again = self.client.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/show-all"
        )
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertFalse(again.get_json().get("projected_all"))
        cleared = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertIsNone(cleared.get("class_boards"))

    def test_pressing_show_all_again_clears_the_grid(self) -> None:
        """The second press stops the projection, the way Show on Board does."""
        prompt_id, _item_id = self._publish("Toggle", image_url=IMAGE)
        first = self._show_all(prompt_id)
        self.assertTrue(first.get_json().get("projected_all"))
        second = self._show_all(prompt_id)
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertFalse(second.get_json().get("projected_all"))
        self.assertIsNone(self.school.whiteboard_class_boards(self.session_id))
        payload = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertIsNone(payload.get("class_boards"))
        self.assertIsNone(payload.get("class_board"))


if __name__ == "__main__":
    unittest.main()
