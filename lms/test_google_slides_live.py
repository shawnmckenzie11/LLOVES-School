#!/usr/bin/env python3
"""Google Slides in Run Live Class: split, push, and per-page unpublish."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

import requests

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from google_slides_import import (  # noqa: E402
    IMPORT_TIME_LIMIT_SECONDS,
    MAX_SLIDE_EDGE,
    MAX_SLIDE_PIXELS,
    SlidesShareNotPublic,
    _MAX_PDF_BYTES,
    _MAX_PNG_BYTES,
    fetch_public_pdf,
    import_shared_deck,
    limited_google_get,
    parse_presentation_id,
    public_redirect_allowed,
    render_pdf_pages,
    slides_api_url_allowed,
)
from live_class_constants import (  # noqa: E402
    SLIDES_READONLY_SCOPE,
    slides_read_scopes_ok,
)
from live_class_slides import (  # noqa: E402
    GoogleSlidesClient,
    SlidesConnectRequired,
    slides_read_oauth_url,
)

_FILE_ID = "DeckSlide01"


def _two_page_pdf() -> bytes:
    """A two-page PDF that pymupdf can render to PNG."""
    import fitz

    document = fitz.open()
    try:
        for label in ("One", "Two"):
            page = document.new_page(width=320, height=180)
            page.insert_text((24, 80), label)
        return document.tobytes()
    finally:
        document.close()


class _Http:
    """One-shot ``requests`` stand-in for the public PDF export."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)

    def get(self, url: str, **_kwargs: object) -> object:
        """Return the next canned response."""
        del url
        return self._responses.pop(0)


class _Resp:
    """Minimal response for ``fetch_public_pdf``."""

    def __init__(
        self, status: int, content: bytes = b"", location: str = ""
    ) -> None:
        self.status_code = status
        self.content = content
        self.headers = {"Location": location}


class GoogleSlidesImportUnitTests(unittest.TestCase):
    """Share-link parsing and PDF page splitting, with no Flask app."""

    def test_parse_share_link_and_refuse_other_hosts(self) -> None:
        """Docs and Drive links yield the file id. Other hosts do not."""
        link = f"https://docs.google.com/presentation/d/{_FILE_ID}/edit?usp=sharing"
        self.assertEqual(parse_presentation_id(link), _FILE_ID)
        drive = f"https://drive.google.com/file/d/{_FILE_ID}/view"
        self.assertEqual(parse_presentation_id(drive), _FILE_ID)
        self.assertEqual(parse_presentation_id(_FILE_ID), _FILE_ID)
        with self.assertRaises(ValueError):
            parse_presentation_id("http://docs.google.com/presentation/d/abcdefghij/edit")
        with self.assertRaises(ValueError):
            parse_presentation_id("https://evil.example/presentation/d/abcdefghij/edit")

    def test_public_redirect_stays_on_google_download_hosts(self) -> None:
        """Login redirects are refused. Docs and usercontent are allowed."""
        self.assertTrue(
            public_redirect_allowed("https://docs.google.com/presentation/d/x/export/pdf")
        )
        self.assertTrue(
            public_redirect_allowed("https://lh3.googleusercontent.com/slide.png")
        )
        self.assertFalse(
            public_redirect_allowed("https://accounts.google.com/ServiceLogin")
        )

    def test_render_pdf_splits_into_png_pages(self) -> None:
        """A two-page export becomes two PNG pages and is not truncated."""
        pages, truncated = render_pdf_pages(_two_page_pdf())
        self.assertEqual(len(pages), 2)
        self.assertFalse(truncated)
        for page in pages:
            self.assertTrue(page.startswith(b"\x89PNG"))

    def test_fetch_refuses_a_login_redirect(self) -> None:
        """A hop to the Google login host is not a public deck."""
        http = _Http(
            [
                _Resp(
                    302,
                    location="https://accounts.google.com/ServiceLogin?continue=1",
                )
            ]
        )
        with self.assertRaises(SlidesShareNotPublic):
            fetch_public_pdf(_FILE_ID, http=http)


class GoogleSlidesLiveTests(unittest.TestCase):
    """Import, current-slide push, and per-page unpublish on a live session."""

    def setUp(self) -> None:
        """Isolated app with one live class and a signed-in student."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(live)
        assert live is not None
        self.live_session_id = int(live["id"])
        joined = self.student.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "Maple"},
            follow_redirects=False,
        )
        self.assertIn(joined.status_code, (302, 303))
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})
        self.pdf = _two_page_pdf()
        self.school.set_live_session_slides(
            self.live_session_id,
            meeting_date="2026-10-20",
            presentation_id="lesson-snapshot",
            presentation_url="/preview/lesson",
            slides_json={"problem_ids": ["p1"], "process_keys": ["k1"]},
        )

    def tearDown(self) -> None:
        """Close the database and remove the temp directory."""
        self.school.close()
        self.tmp.cleanup()

    def _student_card(self) -> dict:
        """Return the student ``google_slide`` card from /api/student/state."""
        body = self.student.get("/api/student/state").get_json()
        self.assertIsInstance(body, dict)
        card = body.get("google_slide")
        self.assertIsInstance(card, dict)
        return card

    def _student_blob(self) -> str:
        """JSON text of the student poll, used to check unpublished URLs."""
        raw = self.student.get("/api/student/state").get_data(as_text=True)
        self.assertNotIn("google_slides", raw)
        return raw

    def _attach(self) -> dict:
        """POST a share link whose public PDF is the two-page fixture."""
        with mock.patch(
            "google_slides_import.fetch_public_pdf", return_value=self.pdf
        ):
            response = self.staff.post(
                f"/api/live-sessions/{self.live_session_id}/google-slides",
                json={
                    "url": (
                        "https://docs.google.com/presentation/d/"
                        f"{_FILE_ID}/edit?usp=sharing"
                    )
                },
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        return body

    def test_import_splits_pages_and_hides_them_until_shared(self) -> None:
        """Attach stores one unpublished page per slide and keeps lesson slides."""
        body = self._attach()
        deck = body["google_slides"]
        self.assertEqual(deck["kind"], "google_slides")
        self.assertEqual(deck["presentation_id"], _FILE_ID)
        self.assertEqual(deck["source"], "public_export")
        self.assertEqual(len(deck["pages"]), 2)
        self.assertTrue(all(not page["published"] for page in deck["pages"]))
        urls = [page["image_url"] for page in deck["pages"]]
        self.assertEqual(len(set(urls)), 2)
        for url in urls:
            self.assertTrue(url.startswith(f"/api/classes/{self.class_id}/"))
        card = body["google_slide"]
        self.assertTrue(card["attached"])
        self.assertFalse(card["shared"])
        self.assertEqual(card["image_url"], "")
        self.assertEqual(card["page_count"], 2)
        student = self._student_card()
        self.assertEqual(student["image_url"], "")
        blob = self._student_blob()
        for url in urls:
            self.assertNotIn(url, blob)
        session = self.school.get_live_session(self.live_session_id)
        assert session is not None
        self.assertEqual(
            session["slides_json"],
            {"problem_ids": ["p1"], "process_keys": ["k1"]},
        )
        page = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('data-tab="slides"', html)
        self.assertIn('id="live-google-slides-attach"', html)
        self.assertIn('id="live-google-slides-prev"', html)
        self.assertIn('id="live-google-slides-next"', html)
        home = self.student.get("/student/home")
        self.assertIn('id="slides-card"', home.get_data(as_text=True))

    def test_current_slide_push_and_unpublish_one_page(self) -> None:
        """Publishing and moving the pointer updates only the shared page."""
        deck = self._attach()["google_slides"]
        first = deck["pages"][0]["image_url"]
        second = deck["pages"][1]["image_url"]
        before = self.school.live_student_poll_stamp(
            self.live_session_id, self.class_id
        )
        published = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/pages/1/publish"
        )
        self.assertEqual(published.status_code, 200)
        pushed = published.get_json()
        self.assertEqual(pushed["google_slides"]["current_index"], 1)
        self.assertTrue(pushed["google_slides"]["pages"][1]["published"])
        self.assertFalse(pushed["google_slides"]["pages"][0]["published"])
        self.assertEqual(pushed["google_slide"]["image_url"], second)
        student = self._student_card()
        self.assertEqual(student["index"], 1)
        self.assertEqual(student["image_url"], second)
        self.assertNotIn(first, self._student_blob())
        image = self.student.get(second)
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.get_data().startswith(b"\x89PNG"))
        image.close()
        after_publish = self.school.live_student_poll_stamp(
            self.live_session_id, self.class_id
        )
        self.assertNotEqual(before, after_publish)

        self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/pages/0/publish"
        )
        moved = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/current",
            json={"index": 0},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.get_json()["google_slide"]["image_url"], first)
        self.assertEqual(self._student_card()["image_url"], first)
        self.assertNotIn(second, self._student_blob())

        hidden = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/pages/0/unpublish"
        )
        self.assertEqual(hidden.status_code, 200)
        body = hidden.get_json()
        self.assertEqual(body["google_slides"]["current_index"], 0)
        self.assertFalse(body["google_slides"]["pages"][0]["published"])
        self.assertTrue(body["google_slides"]["pages"][1]["published"])
        self.assertEqual(body["google_slide"]["image_url"], "")
        self.assertEqual(self._student_card()["image_url"], "")
        blob = self._student_blob()
        self.assertNotIn(first, blob)
        self.assertNotIn(second, blob)

        restored = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/current",
            json={"index": 1},
        )
        self.assertEqual(restored.get_json()["google_slide"]["image_url"], second)
        self.assertEqual(self._student_card()["image_url"], second)
        self.assertNotIn(first, self._student_blob())

    def test_bad_link_is_rejected(self) -> None:
        """A non-Google link does not attach a deck."""
        response = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides",
            json={"url": "https://evil.example/slides"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.school.google_slides_deck(self.live_session_id))
        self.assertIsNone(json.loads(response.get_data(as_text=True)).get("google_slides"))

    def test_slide_images_follow_publish_state(self) -> None:
        """Unpublished, closed, and catch-up requests do not receive the page."""
        deck = self._attach()["google_slides"]
        image_url = deck["pages"][0]["image_url"]
        hidden = self.student.get(image_url)
        self.assertEqual(hidden.status_code, 404)
        preview = self.staff.get(image_url)
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.get_data().startswith(b"\x89PNG"))

        published = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/pages/0/publish"
        )
        self.assertEqual(published.status_code, 200)
        shown = self.student.get(image_url)
        self.assertEqual(shown.status_code, 200)
        self.assertTrue(shown.get_data().startswith(b"\x89PNG"))

        self.school.conn.execute(
            "UPDATE live_class_sessions SET status = ? WHERE id = ?",
            ("ended", self.live_session_id),
        )
        self.school.conn.commit()
        closed = self.student.get(image_url)
        self.assertEqual(closed.status_code, 404)
        self.assertEqual(self.staff.get(image_url).status_code, 200)
        self.school.conn.execute(
            "UPDATE live_class_sessions SET status = ? WHERE id = ?",
            ("active", self.live_session_id),
        )
        self.school.conn.commit()

        rows = self.school.game.conn.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id",
            (self.class_id,),
        ).fetchall()
        student_id = int(rows[0]["id"])
        self.school.set_student_pin(
            self.class_id, student_id, "5678", pin_set_by="default"
        )
        stamp = self.school.catchup_pin_stamp(self.class_id, student_id)
        catchup = self.app.test_client()
        from catchup_session import establish_catchup_identity

        with catchup.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, student_id, stamp
            )
        self.assertEqual(catchup.get(image_url).status_code, 404)
        self.assertEqual(self.student.get(image_url).status_code, 200)

        import fitz

        drawing = fitz.open()
        try:
            page = drawing.new_page(width=40, height=40)
            page.insert_text((4, 24), "Q")
            question_png = page.get_pixmap(alpha=False).tobytes("png")
        finally:
            drawing.close()
        stored = self.school.store_live_question_image(
            self.class_id, question_png, filename="question.png"
        )
        question = self.student.get(stored["image_url"])
        self.assertEqual(question.status_code, 200)
        self.assertTrue(question.get_data().startswith(b"\x89PNG"))
        self.assertNotEqual(stored["image_url"], image_url)

    def test_private_deck_prompt_saves_the_readonly_token(self) -> None:
        """A refused deck links the read-only consent route and stores that token."""
        with mock.patch(
            "google_slides_import.import_shared_deck",
            side_effect=SlidesShareNotPublic(),
        ):
            response = self.staff.post(
                f"/api/live-sessions/{self.live_session_id}/google-slides",
                json={
                    "url": (
                        "https://docs.google.com/presentation/d/"
                        f"{_FILE_ID}/edit"
                    )
                },
            )
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        connect = str(body["connect_url"])
        parsed = urlparse(connect)
        self.assertEqual(parsed.path, "/auth/google/slides-read")
        self.assertNotEqual(parsed.path, "/auth/google/slides")
        self.assertNotIn("drive", connect)
        self.assertTrue(body["needs_slides_read"])
        nxt = unquote(parse_qs(parsed.query)["next"][0])
        self.assertIn(f"/staff/class/{self.class_id}", nxt)
        self.assertIn("tab=live", nxt)
        page = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertNotIn("Connect Google Slides", page.get_data(as_text=True))
        script = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/auth/google/slides-read", script)
        self.assertIn("paintSlidesAttachError", script)
        self.assertNotIn('"/auth/google/slides"', script)
        common = (
            REPO_ROOT / "tools" / "math-game-show" / "static" / "common.js"
        ).read_text(encoding="utf-8")
        self.assertIn("needsSlidesRead", common)
        self.assertIn("/auth/google/slides-read", common)

        operator = self.school.register_staff("solutions@mckenzian.com")
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=solutions@mckenzian.com&name=Op")
        code = self.school.get_user_by_email("solutions@mckenzian.com")[
            "verification_code"
        ]
        client.post("/verify-email", data={"code": code})
        started = client.get(connect)
        self.assertIn(started.status_code, (302, 303))
        read = self.school.get_google_slides_read_token(int(operator["id"]))
        self.assertIsNotNone(read)
        assert read is not None
        self.assertEqual(read["scopes"], SLIDES_READONLY_SCOPE)
        self.assertNotIn("drive", read["scopes"])
        self.assertIsNone(self.school.get_google_api_token(int(operator["id"])))

    def test_callback_hides_the_token_exchange_error(self) -> None:
        """A failed read-only callback shows a generic message and logs no secret."""
        operator = self.school.register_staff("solutions@mckenzian.com")
        del operator
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=solutions@mckenzian.com&name=Op")
        code = self.school.get_user_by_email("solutions@mckenzian.com")[
            "verification_code"
        ]
        client.post("/verify-email", data={"code": code})
        marker = "oauth-secret-marker"
        with client.session_transaction() as sess:
            sess["slides_read_oauth_state"] = "state-read"
        with mock.patch("auth.slides_connect_uses_mock", return_value=False), mock.patch(
            "auth.google_client_id", return_value="cid"
        ), mock.patch("auth.google_client_secret", return_value="secret"), mock.patch(
            "auth.requests.post",
            side_effect=requests.RequestException(f"code={marker} refresh=1//token"),
        ):
            with self.assertLogs(self.app.logger, level="ERROR") as logs:
                page = client.get(
                    "/auth/google/slides-read/callback",
                    query_string={"code": marker, "state": "state-read"},
                )
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 401)
        self.assertIn("Try again", html)
        self.assertNotIn(marker, html)
        self.assertNotIn("1//token", html)
        logged = "\n".join(logs.output)
        self.assertNotIn(marker, logged)
        self.assertNotIn("1//token", logged)
        self.assertIn("RequestException", logged)
        self.assertIn("status=None", logged)
        with client.session_transaction() as sess:
            sess["slides_oauth_state"] = "state-deck"
        with mock.patch("auth.slides_connect_uses_mock", return_value=False), mock.patch(
            "auth.google_client_id", return_value="cid"
        ), mock.patch("auth.google_client_secret", return_value="secret"), mock.patch(
            "auth.requests.post",
            side_effect=requests.RequestException(f"code={marker}"),
        ):
            with self.assertLogs(self.app.logger, level="ERROR") as deck_logs:
                deck = client.get(
                    "/auth/google/slides/callback",
                    query_string={"code": marker, "state": "state-deck"},
                )
        deck_html = deck.get_data(as_text=True)
        self.assertEqual(deck.status_code, 401)
        self.assertIn("Try again", deck_html)
        self.assertNotIn(marker, deck_html)
        self.assertNotIn(marker, "\n".join(deck_logs.output))

    def test_same_browser_catchup_uses_the_joined_live_session(self) -> None:
        """A published slide follows the live join, and the code clears catch-up."""
        deck = self._attach()["google_slides"]
        image_url = deck["pages"][0]["image_url"]
        published = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/google-slides/pages/0/publish"
        )
        self.assertEqual(published.status_code, 200)
        rows = self.school.game.conn.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id",
            (self.class_id,),
        ).fetchall()
        student_id = int(rows[0]["id"])
        self.school.set_student_pin(
            self.class_id, student_id, "5678", pin_set_by="default"
        )
        stamp = self.school.catchup_pin_stamp(self.class_id, student_id)
        from catchup_session import establish_catchup_identity, peek_catchup_identity

        with self.student.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, student_id, stamp
            )
        shown = self.student.get(image_url)
        self.assertEqual(shown.status_code, 200)
        self.assertTrue(shown.get_data().startswith(b"\x89PNG"))
        shown.close()
        with self.student.session_transaction() as sess:
            self.assertIsNotNone(peek_catchup_identity(sess, self.school))

        self.student.post("/api/student/leave")
        with self.student.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, student_id, stamp
            )
        self.assertEqual(self.student.get(image_url).status_code, 404)
        live = self.school.get_live_session(self.live_session_id)
        assert live is not None
        joined = self.student.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "Maple"},
            follow_redirects=False,
        )
        self.assertIn(joined.status_code, (302, 303))
        fixed = self.student.get(image_url)
        self.assertEqual(fixed.status_code, 200)
        self.assertTrue(fixed.get_data().startswith(b"\x89PNG"))
        fixed.close()
        with self.student.session_transaction() as sess:
            self.assertIsNone(peek_catchup_identity(sess, self.school))

    def test_reimport_deletes_replaced_page_files(self) -> None:
        """A new deck drops the previous page files and keeps the new ones."""
        first = self._attach()["google_slides"]
        old_names = [page["image_url"].rsplit("/", 1)[-1] for page in first["pages"]]
        for name in old_names:
            self.assertIsNotNone(
                self.school.live_question_image_path(self.class_id, name)
            )
        import fitz

        drawing = fitz.open()
        try:
            page = drawing.new_page(width=200, height=120)
            page.insert_text((12, 60), "Replacement")
            other = drawing.tobytes()
        finally:
            drawing.close()
        with mock.patch("google_slides_import.fetch_public_pdf", return_value=other):
            response = self.staff.post(
                f"/api/live-sessions/{self.live_session_id}/google-slides",
                json={
                    "url": (
                        "https://docs.google.com/presentation/d/"
                        f"{_FILE_ID}/edit?usp=sharing"
                    )
                },
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        second = response.get_json()["google_slides"]
        new_names = [page["image_url"].rsplit("/", 1)[-1] for page in second["pages"]]
        self.assertTrue(new_names)
        self.assertTrue(set(old_names).isdisjoint(new_names))
        for name in old_names:
            self.assertIsNone(self.school.live_question_image_path(self.class_id, name))
        for name in new_names:
            self.assertIsNotNone(
                self.school.live_question_image_path(self.class_id, name)
            )
        with mock.patch("google_slides_import.fetch_public_pdf", return_value=other):
            again = self.staff.post(
                f"/api/live-sessions/{self.live_session_id}/google-slides",
                json={
                    "url": (
                        "https://docs.google.com/presentation/d/"
                        f"{_FILE_ID}/edit?usp=sharing"
                    )
                },
            )
        self.assertEqual(again.status_code, 200)
        for name in new_names:
            self.assertIsNotNone(
                self.school.live_question_image_path(self.class_id, name)
            )

    def test_quit_deletes_slide_pages_and_keeps_question_images(self) -> None:
        """Quit removes this session's slide files and leaves other images."""
        deck = self._attach()["google_slides"]
        image_url = deck["pages"][0]["image_url"]
        filename = image_url.rsplit("/", 1)[-1]
        self.assertIsNotNone(
            self.school.live_question_image_path(self.class_id, filename)
        )
        import fitz

        drawing = fitz.open()
        try:
            page = drawing.new_page(width=48, height=48)
            page.insert_text((4, 28), "Keep")
            question_png = page.get_pixmap(alpha=False).tobytes("png")
        finally:
            drawing.close()
        stored = self.school.store_live_question_image(
            self.class_id, question_png, filename="keep.png"
        )
        quit_response = self.staff.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertIn(quit_response.status_code, (200, 302, 303))
        self.assertIsNone(
            self.school.live_question_image_path(self.class_id, filename)
        )
        self.assertEqual(self.staff.get(image_url).status_code, 404)
        self.assertIsNotNone(
            self.school.live_question_image_path(self.class_id, stored["filename"])
        )
        kept = self.staff.get(stored["image_url"])
        self.assertEqual(kept.status_code, 200)

    def test_read_connect_stores_a_separate_token(self) -> None:
        """Mock read-only connect does not replace the Drive token."""
        operator = self.school.register_staff("solutions@mckenzian.com")
        self.school.store_google_api_token(
            int(operator["id"]),
            refresh_token="drive-refresh",
            scopes=(
                "https://www.googleapis.com/auth/presentations "
                "https://www.googleapis.com/auth/drive"
            ),
            access_token="drive-access",
            expires_in=3600,
        )
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Op"
        )
        code = self.school.get_user_by_email("solutions@mckenzian.com")[
            "verification_code"
        ]
        client.post("/verify-email", data={"code": code})
        started = client.get("/auth/google/slides-read?next=/staff")
        self.assertIn(started.status_code, (302, 303))
        read = self.school.get_google_slides_read_token(int(operator["id"]))
        self.assertIsNotNone(read)
        assert read is not None
        self.assertEqual(read["scopes"], SLIDES_READONLY_SCOPE)
        self.assertNotIn("drive", read["scopes"])
        deck = self.school.get_google_api_token(int(operator["id"]))
        assert deck is not None
        self.assertEqual(deck["refresh_token"], "drive-refresh")
        self.assertIn("https://www.googleapis.com/auth/drive", deck["scopes"])

        with mock.patch("auth.slides_connect_uses_mock", return_value=False), mock.patch(
            "auth.google_credentials_present", return_value=True
        ), mock.patch("auth.google_client_id", return_value="cid"), mock.patch(
            "auth.google_client_secret", return_value="secret"
        ):
            begin = client.get("/auth/google/slides-read?next=/staff")
            self.assertIn(begin.status_code, (302, 303))
            location = begin.headers["Location"]
            self.assertIn("presentations.readonly", location)
            self.assertIn("include_granted_scopes=true", location)
            self.assertNotIn("auth%2Fdrive", location)
            with client.session_transaction() as sess:
                state = sess["slides_read_oauth_state"]
            payload = {
                "refresh_token": "should-not-store",
                "access_token": "acc",
                "expires_in": 100,
                "scope": SLIDES_READONLY_SCOPE
                + " https://www.googleapis.com/auth/drive",
            }
            response = mock.Mock()
            response.json.return_value = payload
            response.raise_for_status.return_value = None
            with mock.patch("auth.requests.post", return_value=response):
                denied = client.get(
                    "/auth/google/slides-read/callback",
                    query_string={"code": "abc", "state": state},
                )
            self.assertEqual(denied.status_code, 401)
        kept = self.school.get_google_slides_read_token(int(operator["id"]))
        assert kept is not None
        self.assertEqual(kept["refresh_token"], read["refresh_token"])
        self.assertNotIn("should-not-store", kept["refresh_token"])



class _StreamBody:
    """Response whose body arrives in chunks."""

    def __init__(self, chunks: list[bytes], status: int = 200, location: str = "") -> None:
        self._chunks = list(chunks)
        self.status_code = status
        self.headers = {"Location": location}
        self.reads = 0
        self.closed = False

    def iter_content(self, _size: int = 65536):
        """Yield each chunk and count the reads."""
        for chunk in self._chunks:
            self.reads += 1
            yield chunk

    def close(self) -> None:
        """Record that the client stopped reading."""
        self.closed = True


class _OneHttp:
    """HTTP double that returns one prepared response."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.gets = 0

    def get(self, url: str, **kwargs: object) -> object:
        """Return the canned response and require a streamed GET."""
        del url
        self.gets += 1
        if kwargs.get("stream") is not True or kwargs.get("allow_redirects") is not False:
            raise AssertionError("download was not streamed without redirects")
        return self.response


class GoogleSlidesLimitTests(unittest.TestCase):
    """Size, pixel, redirect, and time limits for a deck import."""

    def test_streamed_pdf_stops_at_twenty_megabytes(self) -> None:
        """A long stream is refused around 20 MB and is not fully read."""
        chunk = b"x" * (1024 * 1024)
        body = _StreamBody([chunk] * 100)
        http = _OneHttp(body)
        with self.assertRaises(ValueError) as caught:
            fetch_public_pdf(_FILE_ID, http=http)
        self.assertIn("too large", str(caught.exception))
        self.assertLessEqual(body.reads, 21)
        self.assertGreater(body.reads, 10)
        self.assertTrue(body.closed)
        self.assertEqual(http.gets, 1)

    def test_small_streamed_pdf_is_returned(self) -> None:
        """A streamed PDF under the cap comes back intact."""
        payload = b"%PDF-1.4 tiny"
        body = _StreamBody([payload])
        got = fetch_public_pdf(_FILE_ID, http=_OneHttp(body))
        self.assertEqual(got, payload)

    def test_large_page_stays_under_the_pixel_cap(self) -> None:
        """A huge PDF page is rasterized under the pixel and edge caps."""
        import fitz

        document = fitz.open()
        try:
            document.new_page(width=5000, height=5000)
            pdf = document.tobytes()
        finally:
            document.close()
        pages, truncated = render_pdf_pages(pdf)
        self.assertEqual(len(pages), 1)
        self.assertFalse(truncated)
        opened = fitz.open(stream=pages[0], filetype="png")
        try:
            pixmap = opened[0].get_pixmap()
            self.assertLessEqual(max(pixmap.width, pixmap.height), MAX_SLIDE_EDGE)
            self.assertLessEqual(pixmap.width * pixmap.height, MAX_SLIDE_PIXELS)
            self.assertLess(max(pixmap.width, pixmap.height), 2000)
        finally:
            opened.close()

    def test_import_time_limit_zero_fails_before_download(self) -> None:
        """A non-positive limit fails without fetching the deck."""
        self.assertGreater(IMPORT_TIME_LIMIT_SECONDS, 0)
        self.assertLess(IMPORT_TIME_LIMIT_SECONDS, 120)
        with mock.patch("google_slides_import.fetch_public_pdf") as fetch:
            with self.assertRaises(ValueError) as caught:
                import_shared_deck(
                    mock.Mock(),
                    1,
                    f"https://docs.google.com/presentation/d/{_FILE_ID}/edit",
                    user=None,
                    time_limit=0,
                )
        fetch.assert_not_called()
        self.assertIn("too long", str(caught.exception))

    def test_thumbnail_path_caps_pages_bytes_and_redirects(self) -> None:
        """Private-deck downloads follow only Google hosts and keep the caps."""
        png = b"\x89PNG" + b"ok"
        slides = [{"objectId": f"s{index}"} for index in range(41)]
        listing = json.dumps({"slides": slides}).encode()
        thumb = json.dumps(
            {"contentUrl": "https://lh3.googleusercontent.com/slide.png"}
        ).encode()
        responses: list[object] = [_Resp(200, listing)]
        for _index in range(40):
            responses.append(_Resp(200, thumb))
            responses.append(_Resp(200, png))
        client = GoogleSlidesClient(mock.Mock(), user={"id": 1}, http=_Http(responses))
        client._read_access_token = lambda: "read-token"  # type: ignore[method-assign]
        pages, truncated = client.download_slide_pngs(_FILE_ID)
        self.assertEqual(len(pages), 40)
        self.assertTrue(truncated)

        evil = _Http(
            [
                _Resp(200, json.dumps({"slides": [{"objectId": "s0"}]}).encode()),
                _Resp(200, thumb),
                _Resp(302, location="https://evil.example/pixel.png"),
            ]
        )
        blocked = GoogleSlidesClient(mock.Mock(), user={"id": 1}, http=evil)
        blocked._read_access_token = lambda: "read-token"  # type: ignore[method-assign]
        with self.assertRaises(SlidesShareNotPublic):
            blocked.download_slide_pngs(_FILE_ID)

        login = _Http(
            [_Resp(302, location="https://accounts.google.com/ServiceLogin")]
        )
        refused = GoogleSlidesClient(mock.Mock(), user={"id": 1}, http=login)
        refused._read_access_token = lambda: "read-token"  # type: ignore[method-assign]
        with self.assertRaises(SlidesShareNotPublic):
            refused.download_slide_pngs(_FILE_ID)

        followed = _Http(
            [
                _Resp(
                    302,
                    location=f"https://slides.googleapis.com/v1/presentations/{_FILE_ID}",
                ),
                _Resp(200, json.dumps({"slides": [{"objectId": "s0"}]}).encode()),
                _Resp(
                    302,
                    location=(
                        "https://slides.googleapis.com/v1/presentations/"
                        f"{_FILE_ID}/pages/s0/thumbnail"
                    ),
                ),
                _Resp(200, thumb),
                _Resp(302, location="https://lh3.googleusercontent.com/b.png"),
                _Resp(200, png),
            ]
        )
        ok = GoogleSlidesClient(mock.Mock(), user={"id": 1}, http=followed)
        ok._read_access_token = lambda: "read-token"  # type: ignore[method-assign]
        got, short = ok.download_slide_pngs(_FILE_ID)
        self.assertEqual(got, [png])
        self.assertFalse(short)

        fat = _StreamBody([b"\x89PNG" + (b"x" * 200)])
        with mock.patch("google_slides_import._MAX_PNG_BYTES", 32):
            with self.assertRaises(ValueError):
                limited_google_get(
                    _OneHttp(fat),
                    "https://lh3.googleusercontent.com/slide.png",
                    allow=lambda url: url.startswith("https://lh3.googleusercontent.com/"),
                    limit=32,
                )
        self.assertLess(fat.reads, 5)
        self.assertTrue(slides_api_url_allowed("https://slides.googleapis.com/v1/presentations/x"))
        self.assertFalse(slides_api_url_allowed("https://evil.example/x"))
        self.assertEqual(_MAX_PDF_BYTES, 20 * 1024 * 1024)
        self.assertGreater(_MAX_PNG_BYTES, 32)

    def test_slow_drip_stops_at_the_deadline(self) -> None:
        """A socket read timeout and a between-chunk check bound the download."""
        clock = {"now": 100.0}

        def monotonic() -> float:
            """Return the test clock."""
            return clock["now"]

        class _Drip:
            """Body that would keep yielding after the deadline."""

            def __init__(self) -> None:
                self.reads = 0
                self.closed = False
                self.status_code = 200
                self.headers: dict[str, str] = {}

            def iter_content(self, _size: int = 65536):
                """Yield one chunk, then move the clock past the deadline."""
                self.reads += 1
                clock["now"] = 200.0
                yield b"%PDF-1.4 drip"
                self.reads += 1
                yield b"late-bytes"

            def close(self) -> None:
                """Record that the reader stopped."""
                self.closed = True

        body = _Drip()
        captured: dict[str, object] = {}

        class _Http:
            """Captures the timeout passed to the streamed GET."""

            def get(self, url: str, **kwargs: object) -> _Drip:
                """Return the drip body and record the timeout tuple."""
                del url
                captured["timeout"] = kwargs.get("timeout")
                if kwargs.get("stream") is not True:
                    raise AssertionError("download was not streamed")
                return body

        with mock.patch("google_slides_import.time.monotonic", monotonic):
            with self.assertRaises(ValueError) as caught:
                fetch_public_pdf(
                    _FILE_ID, http=_Http(), timeout=30, deadline=130.0
                )
        self.assertIn("too long", str(caught.exception))
        self.assertEqual(body.reads, 1)
        self.assertTrue(body.closed)
        self.assertEqual(captured["timeout"], (10.0, 30.0))

        class _Fast:
            """One short PDF chunk."""

            def __init__(self) -> None:
                self.status_code = 200
                self.headers: dict[str, str] = {}

            def iter_content(self, _size: int = 65536):
                """Yield a tiny PDF."""
                yield b"%PDF-1.4 tiny"

            def close(self) -> None:
                """No-op close."""

        seen: dict[str, object] = {}

        class _ClockHttp:
            """Records the timeout for a download inside a short deadline."""

            def get(self, url: str, **kwargs: object) -> _Fast:
                """Return the short PDF."""
                del url
                seen["timeout"] = kwargs.get("timeout")
                return _Fast()

        with mock.patch("google_slides_import.time.monotonic", return_value=100.0):
            got = fetch_public_pdf(
                _FILE_ID, http=_ClockHttp(), timeout=30, deadline=112.0
            )
        self.assertEqual(got, b"%PDF-1.4 tiny")
        self.assertEqual(seen["timeout"], (10.0, 12.0))


class GoogleSlidesScopeTests(unittest.TestCase):
    """The thumbnail path uses a Slides read-only grant, not the Drive token."""

    def test_scope_helper_and_consent_url(self) -> None:
        """Consent asks only for presentations.readonly."""
        self.assertTrue(slides_read_scopes_ok(SLIDES_READONLY_SCOPE))
        self.assertTrue(
            slides_read_scopes_ok(
                SLIDES_READONLY_SCOPE + " openid email"
            )
        )
        self.assertFalse(
            slides_read_scopes_ok(
                SLIDES_READONLY_SCOPE + " https://www.googleapis.com/auth/drive"
            )
        )
        self.assertFalse(
            slides_read_scopes_ok("https://www.googleapis.com/auth/presentations")
        )
        url = slides_read_oauth_url(
            client_id="cid",
            redirect_uri="https://alc.mckenzian.com/auth/google/slides-read/callback",
            state="st",
        )
        self.assertIn("presentations.readonly", url)
        self.assertIn("include_granted_scopes=true", url)
        self.assertNotIn("auth%2Fdrive", url)
        self.assertNotIn("/auth/drive", url)
        self.assertNotIn("auth/drive", url)

    def test_download_does_not_use_the_drive_token(self) -> None:
        """A stored Drive token is not a substitute for the read-only grant."""
        school = mock.Mock()
        school.get_google_slides_read_token.return_value = None
        school.get_google_api_token.return_value = {
            "refresh_token": "drive-refresh",
            "scopes": "https://www.googleapis.com/auth/drive",
            "access_token": "drive-access",
            "access_token_expires_at": "2999-01-01T00:00:00",
        }
        client = GoogleSlidesClient(school, user={"id": 4}, http=_Http([]))
        with self.assertRaises(SlidesConnectRequired):
            client.download_slide_pngs(_FILE_ID)
        school.get_google_api_token.assert_not_called()

        school.get_google_slides_read_token.return_value = {
            "refresh_token": "read-refresh",
            "scopes": SLIDES_READONLY_SCOPE + " https://www.googleapis.com/auth/drive",
            "access_token": "read-access",
            "access_token_expires_at": "2999-01-01T00:00:00",
        }
        with self.assertRaises(SlidesConnectRequired):
            client.download_slide_pngs(_FILE_ID)



if __name__ == "__main__":
    unittest.main()
