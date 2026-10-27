#!/usr/bin/env python3
"""MCK-242: Team Draw group whiteboard icebreaker."""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from participation_real_students import exclude_participation_answer_student_ids  # noqa: E402
from team_draw import (  # noqa: E402
    BLANK_DRAWING_ERROR,
    BUILTIN_PROMPTS,
    DRAWING_TOO_LARGE_ERROR,
    MAX_PNG_PIXELS,
    MAX_PNG_SIDE,
    decode_png_payload,
    is_team_draw_item,
    load_inked_png,
    png_has_ink,
    resolved_prompt_text,
)

# 1x1 semi-transparent PNG. One colour, so it has no ink.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """One PNG chunk, including length and CRC.

    Args:
        tag: Four-byte chunk type.
        data: Chunk payload.
    """
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _rgba_png(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Build a non-interlaced 8-bit RGBA PNG using filter None.

    Args:
        width: Pixel width.
        height: Pixel height.
        pixels: Row-major RGBA tuples, ``width * height`` long.
    """
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixels[y * width + x])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
        + _png_chunk(b"IEND", b"")
    )


def _png_b64(blob: bytes) -> str:
    """Raw base64 for a PNG payload.

    Args:
        blob: PNG bytes.
    """
    return base64.b64encode(blob).decode("ascii")



def _zero_idat(width: int, height: int) -> bytes:
    """Zlib stream of a blank RGBA image, compressed a row at a time.

    Args:
        width: Pixel width.
        height: Pixel height.
    """
    comp = zlib.compressobj(level=9)
    parts: list[bytes] = []
    row = bytes(1 + width * 4)
    for _ in range(height):
        piece = comp.compress(row)
        if piece:
            parts.append(piece)
    parts.append(comp.flush())
    return b"".join(parts)


def _png_shell(width: int, height: int, idat: bytes) -> bytes:
    """PNG with the given header wrapped around an existing IDAT.

    Args:
        width: IHDR width.
        height: IHDR height.
        idat: Zlib-wrapped IDAT payload.
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


# Clear pixel plus one pen-coloured pixel: a stroke on an empty canvas.
_INKED_PNG = _rgba_png(2, 1, [(0, 0, 0, 0), (18, 32, 46, 255)])
_BLANK_PNG = _rgba_png(2, 1, [(0, 0, 0, 0), (0, 0, 0, 0)])
_FLAT_PNG = _rgba_png(2, 1, [(255, 255, 255, 255), (255, 255, 255, 255)])
_CLEAR_NOISE_PNG = _rgba_png(2, 1, [(1, 2, 3, 0), (9, 8, 7, 0)])


class TeamDrawTests(unittest.TestCase):
    """Publish, submit, gallery, clear, and snapshot rules."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
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
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch", "Cedar"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.school.game.begin_game(self.class_id)
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        self.student_ids = [int(s["id"]) for s in students]
        for student in students:
            self.school.join_live_class_session(
                self.session_id, int(student["id"]), codename=str(student["codename"])
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.student_ids,
            assignments=[
                {"student_id": self.student_ids[0], "team_index": 0},
                {"student_id": self.student_ids[1], "team_index": 0},
                {"student_id": self.student_ids[2], "team_index": 1},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id, run_as_group=True, groups_configured=True
        )

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def _add_publish_team_draw(
        self, *, prompt_id: str | None = None, publish_mode: str = "group_submit"
    ) -> dict:
        row = BUILTIN_PROMPTS[0]
        if prompt_id:
            row = next(p for p in BUILTIN_PROMPTS if p["id"] == prompt_id)
        body = {
            "type": "team_draw",
            "text": row["text"],
            "team_draw_prompt_id": row["id"],
        }
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/add-question",
            json=body,
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item_id = rv.get_json()["placement"]["item"]["id"]
        row = next(
            r
            for r in self.school.list_live_session_items(self.session_id)
            if r["item_id"] == item_id
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode=publish_mode
        )
        expected = "individual" if publish_mode == "individual" else "team_draw"
        self.assertEqual(published["response_mode"], expected)
        return published

    def _seed_draw_catchup(self) -> tuple[str, int]:
        """Insert one open individual-draw catch-up question.

        Returns:
            Token and roster id for the student who has not answered.
        """
        now = "2026-10-20T12:00:00"
        run_key = "run-draw-catchup"
        token = "draw-catchup-token"
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (class_id, run_key, is_real, auto_is_real, discarded)
            VALUES (?, ?, 1, 1, 0)
            """,
            (self.class_id, run_key),
        )
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, is_open, created_at, updated_at
            ) VALUES (?, ?, ?, 'M1', 'C1', 1, ?, ?)
            """,
            (token, self.class_id, run_key, now, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key, item_json,
                prompt_kind, offered_at
            ) VALUES (?, ?, 'M1', 'C1', 'item:draw1', ?, 'team_draw', ?)
            """,
            (
                self.class_id,
                run_key,
                json.dumps({"type": "team_draw", "text": "Draw the figure."}),
                now,
            ),
        )
        self.school.conn.commit()
        return token, int(self.student_ids[1])

    def _student_client(self, index: int = 0):
        """Flask client for a roster student already joined to the live session.

        Args:
            index: Position in ``self.student_ids``.
        """
        client = self.app.test_client()
        offering_id = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?",
            (self.class_id,),
        ).fetchone()[0]
        with client.session_transaction() as sess:
            sess["student_offering_id"] = int(offering_id)
            sess["student_class_id"] = self.class_id
            sess["student_id"] = self.student_ids[index]
            sess["student_live_session_id"] = self.session_id
        return client

    def test_builtin_prompts_and_exclusion_helper(self) -> None:
        self.assertEqual(len(BUILTIN_PROMPTS), 4)
        mystery = next(row for row in BUILTIN_PROMPTS if row["id"] == "mystery_rule")
        self.assertEqual(
            mystery["text"],
            "Mystery rule: write the rule on the board with the Text tool after you draw step 4.",
        )
        self.assertNotIn("title box", mystery["text"])
        exclude_participation_answer_student_ids(self.class_id, self.school)

    def test_mystery_prompt_is_looked_up_live(self) -> None:
        """A published mystery-rule item shows the catalog sentence, not a stored stem."""
        published = self._add_publish_team_draw(prompt_id="mystery_rule")
        question = dict(published["item"])
        question["text"] = (
            "Mystery rule: name the rule in the title box after you draw step 4."
        )
        self.school.conn.execute(
            "UPDATE live_session_items SET item_json = ? WHERE id = ?",
            (json.dumps(question), int(published["id"])),
        )
        self.school.conn.commit()
        fresh = self.school.get_live_session_item(
            self.session_id, int(published["id"])
        )
        prompt = resolved_prompt_text(fresh)
        self.assertIn("Text tool", prompt)
        self.assertNotIn("title box", prompt)
        view = self.school.team_draw_teacher_view(self.session_id, fresh)
        self.assertEqual(view["prompt"], prompt)

    def test_team_draw_submit_updates_teacher_card_and_history(self) -> None:
        """A drawing-only team submit fills the card, gallery, and results history."""
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        card = self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            image_b64=_png_b64(_INKED_PNG),
        )
        self.assertTrue(card.get("submitted"))
        self.assertEqual(card.get("title"), "")
        view = self.school.live_session_item_results(self.session_id, live_id)
        submitted = [row for row in view["status_board"] if row["submitted"]]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["status_label"], "✓ submitted")
        self.assertEqual(view["response_count"], 1)
        self.assertEqual(
            self.school.lifecycle_response_counts(self.session_id).get(live_id),
            1,
        )
        gallery = self.school.team_draw_gallery(self.session_id, live_id)
        sent = [row for row in gallery if row["submitted"] and row["image_url"]]
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["image_url"].startswith("data:image/png"))
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        snaps = self.school.conn.execute(
            "SELECT answer_json FROM live_result_snapshots WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchall()
        found = False
        for row in snaps:
            payload = json.loads(row["answer_json"] or "{}")
            final = payload.get("final") if isinstance(payload, dict) else None
            if isinstance(final, dict) and final.get("kind") == "team_draw":
                found = True
                self.assertNotIn("image", final)
                self.assertEqual(final.get("title"), "")
        self.assertTrue(found)

    def test_individual_draw_submit_updates_teacher_card_and_history(self) -> None:
        """An individual drawing submit fills that card, gallery, and history."""
        published = self._add_publish_team_draw(publish_mode="individual")
        self.assertEqual(published["response_mode"], "individual")
        live_id = int(published["id"])
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        card = self.school.submit_individual_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            image_b64=_png_b64(_INKED_PNG),
        )
        self.assertTrue(card.get("submitted"))
        self.assertFalse(card.get("can_submit"))
        view = self.school.live_session_item_results(self.session_id, live_id)
        self.assertTrue(view.get("draw"))
        submitted = [row for row in view["status_board"] if row["submitted"]]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["status_label"], "✓ submitted")
        self.assertEqual(view["response_count"], 1)
        self.assertEqual(
            self.school.lifecycle_response_counts(self.session_id).get(live_id),
            1,
        )
        self.assertGreater(view["eligible_count"], 1)
        gallery = self.school.team_draw_gallery(self.session_id, live_id)
        sent = [row for row in gallery if row["submitted"] and row["image_url"]]
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["image_url"].startswith("data:image/png"))
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        snaps = self.school.conn.execute(
            "SELECT answer_json FROM live_result_snapshots WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchall()
        found = False
        for row in snaps:
            payload = json.loads(row["answer_json"] or "{}")
            if isinstance(payload, dict) and payload.get("kind") == "team_draw":
                found = True
                self.assertNotIn("image", payload)
                self.assertEqual(payload.get("title"), "")
        self.assertTrue(found)

    def test_plain_whiteboard_has_no_submit(self) -> None:
        """The plain board has no Submit control. Draw questions do, with no title box."""
        root = Path(__file__).resolve().parents[1]
        staff = (root / "lms" / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        home = (root / "lms" / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("team-draw-submit", staff)
        self.assertNotIn("team-draw-title", staff)
        self.assertNotIn("team-draw-title", home)
        self.assertIn('id="team-draw-submit" class="team-draw-submit" hidden', home)
        script = """
import { drawSubmitVisible } from "./lms/static/team_draw.js";
if (drawSubmitVisible(null, true)) process.exit(1);
if (drawSubmitVisible({ kind: "whiteboard", response_mode: "individual", status: "active" }, true)) process.exit(2);
const team = {
  status: "active",
  response_mode: "team_draw",
  content: { type: "team_draw" },
  group_submit: { can_submit: true, submitted: false },
};
if (!drawSubmitVisible(team, true)) process.exit(3);
if (drawSubmitVisible(team, false)) process.exit(4);
team.group_submit.submitted = true;
if (drawSubmitVisible(team, true)) process.exit(5);
const solo = {
  status: "active",
  response_mode: "individual",
  content: { type: "team_draw" },
  can_submit: true,
  my_response: null,
};
if (!drawSubmitVisible(solo, true)) process.exit(6);
solo.my_response = { response: { kind: "team_draw" } };
if (drawSubmitVisible(solo, true)) process.exit(7);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


    def test_crafted_team_title_is_ignored(self) -> None:
        """A title on the team submit request is not stored."""
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        card = self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="crafted-title",
            image_b64=_png_b64(_INKED_PNG),
        )
        self.assertTrue(card.get("submitted"))
        self.assertEqual(card.get("title"), "")
        view = self.school.team_draw_teacher_view(self.session_id, published)
        self.assertEqual(view["status_board"][0]["title"], "")

    def test_catchup_draw_card_uses_the_board(self) -> None:
        """Catch-up draw questions render the board, and Submit starts disabled."""
        js = (LMS_DIR / "static" / "catchup.js").read_text(encoding="utf-8")
        self.assertIn('liveKind === "team_draw"', js)
        self.assertIn("catchupDrawControlsHtml()", js)
        self.assertIn("onInkChange", js)
        self.assertIn("dataset.hasStroke", js)
        script = """
import { catchupDrawControlsHtml } from "./lms/static/team_draw.js";
import { boardHasStroke } from "./lms/static/live_whiteboard.js";
const html = catchupDrawControlsHtml();
if (!html.includes("catchup-draw-canvas")) process.exit(1);
if (!html.includes(">Submit<")) process.exit(2);
if (html.includes("Your answer") || html.includes("<textarea")) process.exit(3);
const button = html.slice(html.indexOf("<button"));
if (!button.includes("disabled")) process.exit(4);
if (boardHasStroke(0, 0)) process.exit(5);
if (!boardHasStroke(1, 0) || !boardHasStroke(0, 2)) process.exit(6);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=LMS_DIR.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_individual_draw_catchup_requires_a_drawing(self) -> None:
        """An individual draw counts in participation only when catch-up sends ink."""
        token, student_id = self._seed_draw_catchup()
        questions = self.school.catchup_questions(token, student_id)
        self.assertEqual(questions[0]["prompt_kind"], "team_draw")
        self.assertFalse(questions[0]["answered"])
        self.assertFalse(
            self.school.submit_catchup_answer(token, student_id, "item:draw1", "typed words")
        )
        stored = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM participation_answers
            WHERE class_id = ? AND question_key = 'item:draw1' AND student_id = ?
            """,
            (self.class_id, student_id),
        ).fetchone()[0]
        self.assertEqual(int(stored), 0)
        image = "data:image/png;base64," + _png_b64(_INKED_PNG)
        self.assertTrue(
            self.school.submit_catchup_answer(token, student_id, "item:draw1", image)
        )
        row = self.school.conn.execute(
            """
            SELECT source, answer_json FROM participation_answers
            WHERE class_id = ? AND question_key = 'item:draw1' AND student_id = ?
            """,
            (self.class_id, student_id),
        ).fetchone()
        self.assertEqual(row["source"], "catchup")
        payload = json.loads(row["answer_json"])
        self.assertEqual(payload.get("kind"), "team_draw")
        self.assertEqual(payload.get("title"), "")
        self.assertNotIn("image", payload)
        offered, answered = self.school._participation_counts(self.class_id, "M1")
        self.assertEqual(offered.get("M1"), 1)
        self.assertEqual(answered.get((student_id, "M1"), {}).get("n"), 1)
        self.assertEqual(answered.get((student_id, "M1"), {}).get("catchup"), 1)
        again = self.school.catchup_questions(token, student_id)
        self.assertTrue(again[0]["answered"])

    def test_blank_catchup_drawing_is_rejected(self) -> None:
        """A blank catch-up canvas is a 400 and does not earn participation."""
        from catchup_session import establish_catchup_identity
        from participation_weight import participation_fraction

        token, student_id = self._seed_draw_catchup()
        self.school.set_student_pin(self.class_id, student_id, "2468")
        client = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, student_id)
        with client.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, student_id, stamp
            )
        for blob in (_BLANK_PNG, _FLAT_PNG, _TINY_PNG, _CLEAR_NOISE_PNG):
            rv = client.post(
                f"/catchup/{token}",
                data={
                    "question_key": "item:draw1",
                    "answer": "data:image/png;base64," + _png_b64(blob),
                },
            )
            self.assertEqual(rv.status_code, 400)
            body = rv.get_json() or {}
            self.assertEqual(body.get("ok"), False)
            self.assertEqual(body.get("error"), BLANK_DRAWING_ERROR)
        with self.assertRaises(ValueError) as raised:
            self.school.submit_catchup_answer(
                token, student_id, "item:draw1", _png_b64(_BLANK_PNG)
            )
        self.assertEqual(str(raised.exception), BLANK_DRAWING_ERROR)
        stored = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM participation_answers
            WHERE class_id = ? AND question_key = 'item:draw1' AND student_id = ?
            """,
            (self.class_id, student_id),
        ).fetchone()[0]
        self.assertEqual(int(stored), 0)
        offered, answered = self.school._participation_counts(self.class_id, "M1")
        self.assertEqual(offered.get("M1"), 1)
        score = answered.get((student_id, "M1"), {}).get("n", 0)
        self.assertEqual(participation_fraction(score, offered.get("M1") or 0), "0/1")
        image = "data:image/png;base64," + _png_b64(_INKED_PNG)
        self.assertTrue(
            self.school.submit_catchup_answer(token, student_id, "item:draw1", image)
        )
        offered, answered = self.school._participation_counts(self.class_id, "M1")
        score = answered.get((student_id, "M1"), {}).get("n", 0)
        self.assertEqual(participation_fraction(score, offered.get("M1") or 0), "1/1")

    def test_blank_live_draw_submit_is_rejected(self) -> None:
        """Live individual and team draw submits refuse a canvas with no ink."""
        client = self._student_client(0)
        cases = (
            ("individual", _BLANK_PNG),
            ("individual", _TINY_PNG),
            ("group_submit", _FLAT_PNG),
        )
        for publish_mode, blob in cases:
            published = self._add_publish_team_draw(publish_mode=publish_mode)
            rv = client.post(
                f"/api/student/live-items/{int(published['id'])}/team-draw",
                json={"image": _png_b64(blob)},
            )
            self.assertEqual(rv.status_code, 400)
            body = rv.get_json() or {}
            self.assertEqual(body.get("ok"), False)
            self.assertEqual(body.get("error"), BLANK_DRAWING_ERROR)
            if publish_mode == "individual":
                view = self.school.individual_draw_teacher_view(
                    self.session_id, published
                )
            else:
                view = self.school.team_draw_teacher_view(self.session_id, published)
            self.assertEqual(view["response_count"], 0)


    def test_oversized_blank_png_is_rejected_quickly(self) -> None:
        """A compressed blank canvas over the side cap is a fast 400.

        A 13000 by 13000 zero PNG is a few hundred kilobytes compressed and
        about two gigabytes once inflated. The header cap refuses it before
        that inflate. The same bytes are a 400 on catch-up and on both live
        draw submits, and they are not stored.
        """
        import time
        import tracemalloc

        from catchup_session import establish_catchup_identity

        idat = _zero_idat(13000, 13000)
        bomb = _png_shell(13000, 13000, idat)
        self.assertLess(len(bomb), 750_000)
        tracemalloc.start()
        started = time.perf_counter()
        with self.assertRaises(ValueError) as raised:
            png_has_ink(bomb)
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(str(raised.exception), DRAWING_TOO_LARGE_ERROR)
        self.assertLess(elapsed, 0.5)
        self.assertLess(peak, 8 * 1024 * 1024)
        # Declared 1x1, but the stream would expand to the bomb. Stop inflate.
        tracemalloc.start()
        started = time.perf_counter()
        with self.assertRaises(ValueError) as raised:
            png_has_ink(_png_shell(1, 1, idat))
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(str(raised.exception), DRAWING_TOO_LARGE_ERROR)
        self.assertLess(elapsed, 0.5)
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertFalse(png_has_ink(_png_shell(4096, 1, _zero_idat(4096, 1))))
        with self.assertRaises(ValueError) as raised:
            png_has_ink(_png_shell(4097, 1, idat[:32]))
        self.assertEqual(str(raised.exception), DRAWING_TOO_LARGE_ERROR)

        image = "data:image/png;base64," + _png_b64(bomb)
        token, student_id = self._seed_draw_catchup()
        self.school.set_student_pin(self.class_id, student_id, "2468")
        client = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, student_id)
        with client.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, student_id, stamp
            )
        started = time.perf_counter()
        rv = client.post(
            f"/catchup/{token}",
            data={"question_key": "item:draw1", "answer": image},
        )
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(rv.status_code, 400)
        body = rv.get_json() or {}
        self.assertEqual(body.get("ok"), False)
        self.assertEqual(body.get("error"), DRAWING_TOO_LARGE_ERROR)
        stored = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM participation_answers
            WHERE class_id = ? AND question_key = 'item:draw1' AND student_id = ?
            """,
            (self.class_id, student_id),
        ).fetchone()[0]
        self.assertEqual(int(stored), 0)

        live = self._student_client(0)
        for publish_mode in ("individual", "group_submit"):
            published = self._add_publish_team_draw(publish_mode=publish_mode)
            started = time.perf_counter()
            rv = live.post(
                f"/api/student/live-items/{int(published['id'])}/team-draw",
                json={"image": image},
            )
            self.assertLess(time.perf_counter() - started, 2.0)
            self.assertEqual(rv.status_code, 400, rv.get_data(as_text=True)[:300])
            body = rv.get_json() or {}
            self.assertEqual(body.get("ok"), False)
            self.assertEqual(body.get("error"), DRAWING_TOO_LARGE_ERROR)
            if publish_mode == "individual":
                view = self.school.individual_draw_teacher_view(
                    self.session_id, published
                )
            else:
                view = self.school.team_draw_teacher_view(self.session_id, published)
            self.assertEqual(view["response_count"], 0)

    def test_submitted_drawing_scores_one_of_one_live_and_catchup(self) -> None:
        """A submitted drawing is 1/1 participation, live and catch-up.

        The item carries a key the drawing does not match. Scoring does not
        read that key. Typed catch-up text stays 0. A team drawing stays out
        of the individual fraction.
        """
        from participation_weight import (
            item_participation_weight,
            participation_fraction,
        )

        keyed = {"type": "team_draw", "correct_answer": "B", "key": "B"}
        self.assertEqual(
            item_participation_weight(
                "team_draw", publish_mode="individual", item_json=keyed
            ),
            1,
        )
        self.assertEqual(
            item_participation_weight(
                "team_draw",
                publish_mode="group_submit",
                item_json={"type": "team_draw"},
            ),
            0,
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C1", stage="round"
        )
        published = self._add_publish_team_draw(publish_mode="individual")
        live_id = int(published["id"])
        row = self.school.conn.execute(
            "SELECT prompt_id, item_json FROM live_session_items WHERE id = ?",
            (live_id,),
        ).fetchone()
        item = json.loads(row["item_json"] or "{}")
        item["correct_answer"] = "B"
        item["key"] = "B"
        payload = json.dumps(item)
        self.school.conn.execute(
            "UPDATE live_session_items SET item_json = ? WHERE id = ?",
            (payload, live_id),
        )
        if row["prompt_id"]:
            self.school.conn.execute(
                "UPDATE live_session_prompts SET payload = ? WHERE id = ?",
                (payload, int(row["prompt_id"])),
            )
        self.school.conn.commit()
        image = _png_b64(_INKED_PNG)
        card = self.school.submit_individual_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            image_b64=image,
        )
        self.assertTrue(card.get("submitted"))
        # The catch-up window is a few days after this timestamp. A fixed
        # date in October 2026 is already closed, so the submit raises.
        ended = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        self.school.conn.execute(
            "UPDATE live_class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (ended, self.session_id),
        )
        self.school.conn.commit()
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        stored_item = self.school.conn.execute(
            """
            SELECT prompt_kind, item_json FROM participation_items
            WHERE class_id = ? AND prompt_kind = 'team_draw'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNotNone(stored_item)
        self.assertEqual(
            json.loads(stored_item["item_json"]).get("correct_answer"), "B"
        )
        live_answer = self.school.conn.execute(
            """
            SELECT answer_json FROM participation_answers
            WHERE class_id = ? AND student_id = ? AND source = 'live'
            """,
            (self.class_id, self.student_ids[0]),
        ).fetchone()
        self.assertIsNotNone(live_answer)
        self.assertNotIn("correct", live_answer["answer_json"])

        def cell_for(student_id: int) -> dict:
            data = self.school.class_participation(self.class_id)
            match = next(
                row for row in data["students"] if row["student_id"] == student_id
            )
            return match["modules"]["M1"]

        live_cell = cell_for(self.student_ids[0])
        self.assertEqual(
            participation_fraction(live_cell["answered"], live_cell["offered"]),
            "1/1",
        )
        absent_cell = cell_for(self.student_ids[1])
        self.assertEqual(absent_cell["answered"], 0)
        self.assertEqual(absent_cell["offered"], 1)
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        token = links[0]["token"]
        questions = self.school.catchup_questions(token, self.student_ids[1])
        draws = [q for q in questions if q.get("prompt_kind") == "team_draw"]
        self.assertEqual(len(draws), 1)
        question_key = draws[0]["question_key"]
        content = draws[0].get("content") or {}
        self.assertNotIn("correct_answer", content)
        self.assertNotIn("key", content)
        self.assertFalse(
            self.school.submit_catchup_answer(
                token, self.student_ids[1], question_key, "typed words"
            )
        )
        still_absent = cell_for(self.student_ids[1])
        self.assertEqual(still_absent["answered"], 0)
        data_url = "data:image/png;base64," + image
        self.assertTrue(
            self.school.submit_catchup_answer(
                token, self.student_ids[1], question_key, data_url
            )
        )
        catch_cell = cell_for(self.student_ids[1])
        self.assertEqual(
            participation_fraction(catch_cell["answered"], catch_cell["offered"]),
            "1/1",
        )
        self.assertEqual(
            participation_fraction(cell_for(self.student_ids[0])["answered"], 1),
            "1/1",
        )
        catch_row = self.school.conn.execute(
            """
            SELECT answer_json FROM participation_answers
            WHERE class_id = ? AND student_id = ? AND source = 'catchup'
            """,
            (self.class_id, self.student_ids[1]),
        ).fetchone()
        catch_payload = json.loads(catch_row["answer_json"])
        self.assertEqual(catch_payload.get("kind"), "team_draw")
        self.assertNotIn("correct_answer", catch_payload)
        self.assertNotIn("correct", catch_payload)
        self.assertNotEqual(catch_payload.get("value"), "B")

    def test_submit_stores_title_not_in_snapshot_on_test_run(self) -> None:
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        card = self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Maple Mascot",
            image_b64=_png_b64(_INKED_PNG),
        )
        self.assertTrue(card.get("submitted"))
        view = self.school.team_draw_teacher_view(self.session_id, published)
        self.assertEqual(view["response_count"], 1)
        self.assertEqual(view["status_board"][0]["title"], "")
        gallery = self.school.team_draw_gallery(self.session_id, live_id)
        self.assertTrue(gallery[0]["image_url"].startswith("data:image/png"))
        self.school.team_draw_end_and_clear(self.session_id, live_id)
        gallery_after = self.school.team_draw_gallery(self.session_id, live_id)
        self.assertEqual(gallery_after[0]["image_url"], "")
        self.school.end_live_class_session(self.session_id)
        snaps = self.school.conn.execute(
            "SELECT answer_json FROM live_result_snapshots WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchall()
        for row in snaps:
            payload = json.loads(row["answer_json"] or "{}")
            final = payload.get("final") or {}
            self.assertNotIn("image", final)
        self.assertTrue(is_team_draw_item(published))

    def test_decode_png_rejects_bad_data(self) -> None:
        with self.assertRaises(ValueError):
            decode_png_payload("not-valid")

    def test_mystery_rule_state_includes_steps_and_pass_turn(self) -> None:
        published = self._add_publish_team_draw(prompt_id="mystery_rule")
        card_a = self.school.student_team_draw_state(
            published, self.student_ids[0]
        )
        card_b = self.school.student_team_draw_state(
            published, self.student_ids[1]
        )
        self.assertTrue(card_a.get("mystery_rule"))
        self.assertEqual(len(card_a.get("mystery_steps") or []), 3)
        turn_a = card_a.get("pass_turn")
        turn_b = card_b.get("pass_turn")
        self.assertIsNotNone(turn_a)
        self.assertIsNotNone(turn_b)
        self.assertEqual(int(turn_a["turn_total"]), 2)
        self.assertEqual(turn_a["holder_name"], turn_b["holder_name"])
        self.assertEqual(int(turn_a["turn_index"]), 1)
        view = self.school.team_draw_teacher_view(self.session_id, published)
        self.assertTrue(view.get("mystery_rule"))
        self.assertEqual(len(view.get("mystery_steps") or []), 3)

    def test_test_run_end_skips_team_draw_snapshot(self) -> None:
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Practice sketch",
            image_b64=_png_b64(_INKED_PNG),
        )
        session = self.school.get_live_session(self.session_id)
        assert session is not None
        run_key = str(session["run_key"] or "")
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=False
        )
        count = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM live_result_snapshots
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchone()[0]
        self.assertEqual(int(count), 0)

    def test_real_run_end_keeps_team_draw_snapshot(self) -> None:
        """Real End snapshots Team Draw before ``participation_runs`` is written."""
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Class sketch",
            image_b64=_png_b64(_INKED_PNG),
        )
        session = self.school.get_live_session(self.session_id)
        assert session is not None
        run_key = str(session["run_key"] or "")
        self.school.cleanup_live_session_response_data(
            self.session_id, live_run=True
        )
        count = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM live_result_snapshots
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchone()[0]
        self.assertEqual(int(count), 1)

    def test_quit_wipe_purges_team_draw_images(self) -> None:
        published = self._add_publish_team_draw()
        live_id = int(published["id"])
        self.school.submit_team_draw(
            self.session_id,
            live_id,
            self.student_ids[0],
            title="Ephemeral",
            image_b64=_png_b64(_INKED_PNG),
        )
        before = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_team_draw_images WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        self.assertGreater(int(before), 0)
        self.school.finish_live_class(
            self.class_id, persist=False, celebrate=False
        )
        after = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_team_draw_images WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        self.assertEqual(int(after), 0)


class DrawingInkTests(unittest.TestCase):
    """Blank-canvas detection, without a live class."""

    def test_blank_pngs_have_no_ink_and_a_stroke_does(self) -> None:
        """Flat, clear, and one-colour images are blank. A second colour is ink."""
        self.assertFalse(png_has_ink(_TINY_PNG))
        self.assertFalse(png_has_ink(_BLANK_PNG))
        self.assertFalse(png_has_ink(_FLAT_PNG))
        self.assertFalse(png_has_ink(_CLEAR_NOISE_PNG))
        self.assertTrue(png_has_ink(_INKED_PNG))
        row = bytes([255, 255, 255, 255])
        raw = bytes([0]) + row + bytes([2, 0, 0, 0, 0])
        ihdr = struct.pack(">IIBBBBB", 1, 2, 8, 6, 0, 0, 0)
        filtered = (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b"")
        )
        self.assertFalse(png_has_ink(filtered))
        with self.assertRaises(ValueError) as raised:
            load_inked_png(_png_b64(_FLAT_PNG))
        self.assertEqual(str(raised.exception), BLANK_DRAWING_ERROR)
        self.assertTrue(load_inked_png(_png_b64(_INKED_PNG)).startswith(b"\x89PNG"))


    def test_blank_png_at_pixel_cap_is_fast(self) -> None:
        """A blank image at the pixel cap is refused or scanned in under a second.

        A square at the per-side limit is over 8 million pixels, so the
        header refuses it before inflate. The widest blank that still fits
        is all zeros and must not walk every pixel.
        """
        import time

        started = time.perf_counter()
        with self.assertRaises(ValueError) as raised:
            png_has_ink(_png_shell(MAX_PNG_SIDE, MAX_PNG_SIDE, b"\x00\x00"))
        self.assertEqual(str(raised.exception), DRAWING_TOO_LARGE_ERROR)
        self.assertLess(time.perf_counter() - started, 1.0)

        width = MAX_PNG_SIDE
        height = MAX_PNG_PIXELS // width
        self.assertGreater(height, 1)
        self.assertLessEqual(width * height, MAX_PNG_PIXELS)
        self.assertGreater(width * (height + 1), MAX_PNG_PIXELS)
        blob = _png_shell(width, height, _zero_idat(width, height))
        started = time.perf_counter()
        self.assertFalse(png_has_ink(blob))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)

    def test_mck242_shoot_fixture_has_ink(self) -> None:
        """The mck242 screenshot seeder submits a drawing that has a stroke."""
        source = (Path(__file__).resolve().parents[1] / "scripts" / "shoot.py").read_text()
        label = source.find("_MCK242_INKED_PNG_B64")
        self.assertGreater(label, 0)
        quote = source.find('"', label)
        end = source.find('"', quote + 1)
        blob = base64.b64decode(source[quote + 1 : end])
        self.assertTrue(png_has_ink(blob))


if __name__ == "__main__":
    unittest.main()
