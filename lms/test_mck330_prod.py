#!/usr/bin/env python3
"""MCK-330, MCK-331, and MCK-332: capped slide import, /state logs, quit retry.

Fixtures stay synthetic. Logs and assertion text do not include join
codes, catch-up tokens, or mailbox addresses.
"""

from __future__ import annotations

import base64
import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from google_slides_import import render_pdf_to_dir  # noqa: E402
from test_mck275_end_quit import EndAlwaysSavesTests  # noqa: E402
from test_mck316_course_slides import _minimal_pptx  # noqa: E402


def _pdf_bytes(pages: int) -> bytes:
    """Build a small PDF with ``pages`` blank-ish pages.

    Args:
        pages: Page count. Each page is a short label, not a scan.
    """
    import fitz

    document = fitz.open()
    try:
        for index in range(int(pages)):
            page = document.new_page(width=320, height=180)
            page.insert_text((24, 80), f"p{index}")
        return document.tobytes()
    finally:
        document.close()


def _png_magic(path: Path) -> bytes:
    """First bytes of a page file. The body is not loaded.

    Args:
        path: PNG written by the renderer.
    """
    with path.open("rb") as handle:
        return handle.read(8)


class SlideMemoryTests(unittest.TestCase):
    """A 40-page deck stays in a capped child. The parent keeps serving."""

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "school.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=None,
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.app.config["SCHOOL_DB"].close()
        self.tmp.cleanup()

    def test_forty_page_pdf_renders_under_the_cap(self) -> None:
        """Forty pages land on disk one file at a time, and /health stays up."""
        root = Path(self.tmp.name)
        pdf = root / "deck.pdf"
        pdf.write_bytes(_pdf_bytes(40))
        box: dict[str, object] = {}

        def render() -> None:
            """Render off the request thread, the way an import job does."""
            box["result"] = render_pdf_to_dir(pdf, root / "pages")

        worker = threading.Thread(target=render)
        worker.start()
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        worker.join(60)
        self.assertFalse(worker.is_alive())
        paths, truncated = box["result"]
        self.assertFalse(truncated)
        self.assertEqual(len(paths), 40)
        self.assertTrue(all(_png_magic(path).startswith(b"\x89PNG") for path in paths))
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_over_limit_render_fails_cleanly(self) -> None:
        """A child past the address cap dies. The parent still renders and serves."""
        root = Path(self.tmp.name)
        pdf = root / "deck.pdf"
        pdf.write_bytes(_pdf_bytes(2))
        with self.assertRaises(ValueError) as caught:
            render_pdf_to_dir(pdf, root / "too-big", address_limit=1024 * 1024)
        self.assertIn("too large", str(caught.exception).lower())
        self.assertNotIn(str(pdf), str(caught.exception))
        self.assertEqual(self.client.get("/health").status_code, 200)
        paths, truncated = render_pdf_to_dir(pdf, root / "ok")
        self.assertFalse(truncated)
        self.assertGreaterEqual(len(paths), 1)
        self.assertTrue(_png_magic(paths[0]).startswith(b"\x89PNG"))

    def test_forty_page_pptx_converts_inside_the_sandbox(self) -> None:
        """LibreOffice's stand-in stays inside pivot_root, then pages hit disk."""
        import course_slide_files as files

        root = Path(self.tmp.name)
        pdf = _pdf_bytes(40)
        script = root / "fake-soffice.sh"
        encoded = base64.b64encode(pdf).decode("ascii")
        script.write_text(
            "#!/bin/sh\nmkdir -p out\nbase64 -d > out/deck.pdf << 'END'\n"
            + encoded
            + "\nEND\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        source = root / "deck.pptx"
        source.write_bytes(_minimal_pptx())
        self.assertIn("pivot_root . oldroot", files._SANDBOX_INNER)
        self.assertNotIn("chroot", files._SANDBOX_INNER)
        self.assertIn('ulimit -v "$as_kb"', files._SANDBOX_INNER)
        with mock.patch.object(files, "soffice_bin", return_value=str(script)):
            paths, truncated = files.pages_from_office_to_dir(
                source, ".pptx", root / "pages", timeout=60
            )
        self.assertFalse(truncated)
        self.assertEqual(len(paths), 40)
        self.assertTrue(_png_magic(paths[0]).startswith(b"\x89PNG"))
        self.assertTrue(_png_magic(paths[-1]).startswith(b"\x89PNG"))
        self.assertEqual(self.client.get("/health").status_code, 200)


class VanishedSessionTests(EndAlwaysSavesTests):
    """A join that loses its session is a 409, not a server error."""

    def test_attendee_insert_survives_a_deleted_session(self) -> None:
        """The foreign-key failure becomes KeyError once the row is gone."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        sid = int(live["id"])
        import sqlite3

        other = sqlite3.connect(self.school.db_path)
        other.execute("PRAGMA foreign_keys = ON")
        real = self.school.get_live_session

        def wrapped(session_id: int):
            """Return the row once, after another connection has deleted it."""
            row = real(session_id)
            if (
                row is not None
                and int(session_id) == sid
                and not getattr(wrapped, "did", False)
            ):
                wrapped.did = True
                other.execute(
                    "DELETE FROM live_class_sessions WHERE id = ?",
                    (sid,),
                )
                other.commit()
                return row
            return real(session_id)

        wrapped.did = False
        self.school.get_live_session = wrapped
        try:
            with self.assertRaises(KeyError) as caught:
                self.school.record_live_session_attendee(
                    sid, int(self.students[0]), codename="Ava"
                )
        finally:
            self.school.get_live_session = real
            other.close()
        self.assertIn("gone", str(caught.exception))

    def test_student_code_keyerror_is_409(self) -> None:
        """A vanished class does not count as a wrong code."""
        sid = self._arm(is_test=False, module="M1", slot="C3")
        session = self.school.get_live_session(sid) or {}
        code = str(session.get("session_code") or "")
        before = self._count("SELECT COUNT(*) AS n FROM student_code_attempts")
        with mock.patch.object(
            self.school,
            "join_live_class_session",
            side_effect=KeyError("live session is gone"),
        ):
            response = self.client.post(
                "/auth/student-code",
                json={"code": code, "name": "Ava"},
            )
        self.assertEqual(response.status_code, 409)
        body = response.get_json() or {}
        self.assertIn("No live class", str(body.get("error") or ""))
        self.assertNotIn("IntegrityError", response.get_data(as_text=True))
        self.assertEqual(
            self._count("SELECT COUNT(*) AS n FROM student_code_attempts"),
            before,
        )


class QuitRetryTests(EndAlwaysSavesTests):
    """Quit stays in the dialog and a retry finishes the wipe."""

    def _json_quit(self):
        """POST Quit the way the dialog's fetch does."""
        return self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            headers={"Accept": "application/json"},
        )

    def test_busy_quit_retries_and_keeps_course_slides(self) -> None:
        """A 409 leaves the run. The next Quit wipes it and keeps the deck."""
        import fitz

        sid = self._arm(is_test=False, module="M1", slot="C2")
        columns = self._gradebook_columns()
        run_key = str(self.school.get_live_session(sid)["run_key"] or "")
        document = fitz.open()
        try:
            document.new_page(width=32, height=18)
            png = document[0].get_pixmap(alpha=False).tobytes("png")
        finally:
            document.close()
        self.school.import_google_slides_pages(
            sid,
            presentation_id="QuitDeck",
            page_pngs=[png],
            default_published=True,
            title="Live deck",
        )
        self.school.mint_open_catchup_link(self.class_id, run_key, "M1", "C2")
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ),
            1,
        )
        calls = {"n": 0}
        original = self.school.finish_live_class

        def finish(*args, **kwargs):
            """First Quit is busy. The retry performs the real wipe."""
            calls["n"] += 1
            if calls["n"] == 1:
                return {"finish_busy": True}
            return original(*args, **kwargs)

        self.school.finish_live_class = finish
        first = self._json_quit()
        self.assertEqual(first.status_code, 409)
        self.assertEqual(
            (first.get_json() or {}).get("error"), "Still finishing — Retry"
        )
        self.assertIsNotNone(self.school.get_live_session(sid))
        second = self._json_quit()
        self.assertEqual(second.status_code, 200)
        self.assertTrue((second.get_json() or {}).get("ok"))
        self.assertIsNone(self.school.get_live_session(sid))
        self.assertEqual(self._count("SELECT COUNT(*) AS n FROM live_session_attendees"), 0)
        self.assertEqual(self._count("SELECT COUNT(*) AS n FROM live_session_responses"), 0)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ),
            0,
        )
        self.assertEqual(self._gradebook_columns(), columns)
        summary = self.school.course_slide_deck_summary(self.class_id, "M1", "C2")
        assert summary is not None
        self.assertEqual(summary["page_count"], 1)
        third = self._json_quit()
        self.assertEqual(third.status_code, 200)
        self.assertTrue((third.get_json() or {}).get("already_ended"))

    def test_quit_while_state_is_slow(self) -> None:
        """Quit returns while a staff /state call is still blocked."""
        sid = self._arm(is_test=False, module="M1", slot="C1")
        entered = threading.Event()
        release = threading.Event()
        original = self.school.get_live_session_state

        def slow(session_id: int, *, light: bool = False):
            """Hold the poll. Quit must not wait on this."""
            entered.set()
            release.wait(5)
            return original(session_id, light=light)

        self.school.get_live_session_state = slow
        other = self.app.test_client()
        with self.client.session_transaction() as sess:
            copied = dict(sess)
        with other.session_transaction() as sess:
            sess.update(copied)
        box: dict[str, object] = {}

        def poll() -> None:
            """One staff /state that sits inside the patched builder."""
            box["response"] = other.get(f"/api/live-sessions/{sid}/state")

        worker = threading.Thread(target=poll)
        worker.start()
        self.assertTrue(entered.wait(3))
        try:
            quit_response = self._json_quit()
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(quit_response.status_code, 200)
        self.assertTrue((quit_response.get_json() or {}).get("ok"))
        self.assertIsNone(self.school.get_live_session(sid))

    def test_state_log_has_duration_and_size_only(self) -> None:
        """A live /state line records duration and bytes, and nothing personal."""
        sid = self._arm(is_test=False, module="M1", slot="C4")
        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs("app", level="INFO") as captured:
                response = self.client.get(f"/api/live-sessions/{sid}/state")
        finally:
            logging.disable(logging.WARNING)
        self.assertEqual(response.status_code, 200)
        lines = [line for line in captured.output if "live /state" in line]
        self.assertTrue(lines)
        self.assertIn("duration_ms=", lines[0])
        self.assertIn("payload_bytes=", lines[0])
        self.assertNotIn("@", lines[0])
        self.assertNotIn("session", lines[0])


if __name__ == "__main__":
    unittest.main()
