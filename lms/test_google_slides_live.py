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

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from google_slides_import import (  # noqa: E402
    SlidesShareNotPublic,
    fetch_public_pdf,
    parse_presentation_id,
    public_redirect_allowed,
    render_pdf_pages,
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


if __name__ == "__main__":
    unittest.main()
