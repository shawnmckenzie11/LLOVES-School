#!/usr/bin/env python3
"""MCK-316: course slide decks, setup attach, and volume-only files."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from course_slide_files import (  # noqa: E402
    COURSE_SLIDE_UPLOAD_MAX_BYTES,
    office_bytes_ok,
    office_suffix,
    read_capped_upload,
    soffice_bin,
)
from live_news_wire import reset_logs  # noqa: E402

_FILE_ID = "DeckSlide01"
_MARKER = b"MCK316-PPTX-NOT-IN-SQLITE"


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


def _png_pages() -> list[bytes]:
    """PNG pages for a mocked PowerPoint conversion."""
    from google_slides_import import render_pdf_pages

    pages, truncated = render_pdf_pages(_two_page_pdf())
    assert not truncated
    return pages


class _Reader:
    """File-like body for the upload cap."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw
        self._pos = 0

    def read(self, size: int) -> bytes:
        """Return the next chunk."""
        block = self._raw[self._pos : self._pos + size]
        self._pos += size
        return block


def _minimal_pptx() -> bytes:
    """A one-slide pptx package LibreOffice can open.

    Returns:
        Zip bytes with a PK header.
    """
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>
"""
    presentation = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
  <p:sldSz cx="9144000" cy="5143500" type="screen16x9"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>
"""
    presentation_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>
"""
    slide = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
    <p:grpSpPr/>
    <p:sp>
      <p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:spPr/>
      <p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Hello</a:t></a:r></a:p></p:txBody>
    </p:sp>
  </p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""
    slide_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>
"""
    layout = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank">
  <p:cSld name="Blank"><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
    <p:grpSpPr/>
  </p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>
"""
    layout_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>
"""
    master = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
    <p:grpSpPr/>
  </p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
</p:sldMaster>
"""
    master_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>
"""
    theme = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office">
  <a:themeElements>
    <a:clrScheme name="Office">
      <a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
      <a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="1F497D"/></a:dk2>
      <a:lt2><a:srgbClr val="EEECE1"/></a:lt2>
      <a:accent1><a:srgbClr val="4F81BD"/></a:accent1>
      <a:accent2><a:srgbClr val="C0504D"/></a:accent2>
      <a:accent3><a:srgbClr val="9BBB59"/></a:accent3>
      <a:accent4><a:srgbClr val="8064A2"/></a:accent4>
      <a:accent5><a:srgbClr val="4BACC6"/></a:accent5>
      <a:accent6><a:srgbClr val="F79646"/></a:accent6>
      <a:hlink><a:srgbClr val="0000FF"/></a:hlink>
      <a:folHlink><a:srgbClr val="800080"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="Office">
      <a:majorFont><a:latin typeface="Calibri"/></a:majorFont>
      <a:minorFont><a:latin typeface="Calibri"/></a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name="Office">
      <a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
      <a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
      <a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
      <a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
</a:theme>
"""
    parts = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": rels,
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": presentation_rels,
        "ppt/slides/slide1.xml": slide,
        "ppt/slides/_rels/slide1.xml.rels": slide_rels,
        "ppt/slideLayouts/slideLayout1.xml": layout,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": layout_rels,
        "ppt/slideMasters/slideMaster1.xml": master,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": master_rels,
        "ppt/theme/theme1.xml": theme,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name, text in parts.items():
            package.writestr(name, text)
    return buffer.getvalue()


class CourseSlideFileTests(unittest.TestCase):
    """Upload cap and package checks, with no Flask app."""

    def test_upload_cap_is_at_least_fifty_megabytes(self) -> None:
        """The server cap covers the 21.7 MB sample deck."""
        self.assertGreaterEqual(COURSE_SLIDE_UPLOAD_MAX_BYTES, 50 * 1024 * 1024)

    def test_reader_stops_past_the_cap(self) -> None:
        """A body one byte over the cap is refused before it is stored."""
        with self.assertRaises(ValueError):
            read_capped_upload(_Reader(b"PK" + b"x" * 64), 32)

    def test_suffix_and_package_magic(self) -> None:
        """Only ppt and pptx names, and only matching packages, are accepted."""
        self.assertEqual(office_suffix("deck.PPTX"), ".pptx")
        with self.assertRaises(ValueError):
            office_suffix("notes.pdf")
        office_bytes_ok(b"PK\x03\x04", ".pptx")
        with self.assertRaises(ValueError):
            office_bytes_ok(b"not-a-zip", ".pptx")


class CourseSlideSetupTests(unittest.TestCase):
    """Add Slides from Set Class, by link and by upload."""

    def setUp(self) -> None:
        """One signed-in teacher, one class, and a minted live run."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
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
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(live)
        assert live is not None
        self.live_session_id = int(live["id"])
        self.pdf = _two_page_pdf()

    def tearDown(self) -> None:
        """Close the news tape and the temp database."""
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def test_setup_link_saves_one_course_copy(self) -> None:
        """A share link from Which deck? is stored once and reused next run."""
        with mock.patch("google_slides_import.fetch_public_pdf", return_value=self.pdf):
            added = self.staff.post(
                f"/api/classes/{self.class_id}/slide-deck",
                json={
                    "url": (
                        "https://docs.google.com/presentation/d/"
                        f"{_FILE_ID}/edit?usp=sharing"
                    ),
                    "module": "M1",
                    "slot": "C1",
                    "default_published": False,
                },
            )
        self.assertEqual(added.status_code, 200, added.get_data(as_text=True))
        body = added.get_json()
        self.assertEqual(body["deck"]["title"], "Google Slides")
        self.assertEqual(body["deck"]["page_count"], 2)
        self.assertFalse(body["deck"]["default_published"])
        deck = body["google_slides"]
        self.assertFalse(deck["pages"][0]["published"])
        filename = deck["pages"][0]["image_url"].rsplit("/", 1)[-1]
        card = self.school.student_google_slide(self.live_session_id)
        assert card is not None
        self.assertEqual(card["image_url"], "")
        self.staff.post(
            f"/staff/class/{self.class_id}/quit-live", follow_redirects=False
        )
        again = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), live_module="M1", live_slot="C1"
        )
        rebound = self.school.google_slides_deck(int(again["id"]))
        assert rebound is not None
        self.assertEqual(rebound["pages"][0]["image_url"].rsplit("/", 1)[-1], filename)
        folder = Path(self.school.data_dir) / "live-question-images" / str(self.class_id)
        copies = list(folder.glob(filename))
        self.assertEqual(len(copies), 1)

    def test_setup_upload_respects_the_publish_toggle_and_stays_off_sqlite(self) -> None:
        """A pptx is a volume file. The default-publish checkbox is what students see."""
        pngs = _png_pages()
        payload = b"PK\x03\x04" + _MARKER
        with mock.patch(
            "course_slide_files.pages_from_office", return_value=(pngs, False)
        ):
            added = self.staff.post(
                f"/api/classes/{self.class_id}/slide-deck",
                data={
                    "module": "M1",
                    "slot": "C1",
                    "default_published": "1",
                    "file": (io.BytesIO(payload), "Quadratics.pptx"),
                },
            )
        self.assertEqual(added.status_code, 200, added.get_data(as_text=True))
        body = added.get_json()
        self.assertEqual(body["deck"]["title"], "Quadratics")
        self.assertTrue(body["deck"]["default_published"])
        self.assertTrue(body["google_slides"]["pages"][0]["published"])
        card = self.school.student_google_slide(self.live_session_id)
        assert card is not None
        self.assertTrue(card["image_url"].endswith(".png"))
        source = next(
            (Path(self.school.data_dir) / "live-question-images" / str(self.class_id) / "sources").glob(
                "*.pptx"
            )
        )
        self.assertIn(_MARKER, source.read_bytes())
        self.school.conn.execute("PRAGMA wal_checkpoint(FULL)")
        blob = Path(self.school.db_path).read_bytes()
        wal = Path(str(self.school.db_path) + "-wal")
        if wal.is_file():
            blob += wal.read_bytes()
        self.assertNotIn(_MARKER, blob)
        self.assertNotIn(pngs[0], blob)
        with mock.patch(
            "course_slide_files.pages_from_office", return_value=(pngs, False)
        ):
            second = self.staff.post(
                f"/api/classes/{self.class_id}/slide-deck",
                data={
                    "module": "M1",
                    "slot": "C2",
                    "file": (io.BytesIO(payload), "Quadratics.pptx"),
                },
            )
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        sources = list(
            (Path(self.school.data_dir) / "live-question-images" / str(self.class_id) / "sources").glob(
                "*.pptx"
            )
        )
        self.assertEqual(len(sources), 1)

    def test_over_cap_upload_is_refused(self) -> None:
        """The handler enforces its own cap, below Flask's IMSCC limit."""
        with mock.patch("course_slide_files.COURSE_SLIDE_UPLOAD_MAX_BYTES", 32):
            refused = self.staff.post(
                f"/api/classes/{self.class_id}/slide-deck",
                data={
                    "module": "M1",
                    "slot": "C1",
                    "file": (io.BytesIO(b"PK" + b"x" * 64), "big.pptx"),
                },
            )
        self.assertEqual(refused.status_code, 400)
        self.assertIn("too large", refused.get_json()["error"])

    def test_layout_keeps_16_by_9_and_drops_prev_next(self) -> None:
        """Slides fit the card. The old Prev and Next controls are gone."""
        page = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        deck = html[html.index('id="set-class-deck-step"') : html.index('id="set-class-day-step"')]
        self.assertIn('id="set-class-slides-add"', deck)
        self.assertIn(">Add Slides<", deck)
        self.assertNotIn('id="live-google-slides-prev"', html)
        self.assertNotIn('id="live-google-slides-next"', html)
        self.assertIn('id="live-google-slides-stage"', html)
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="slides-fit-stage"', home)
        for name in ("student-portal.css", "staff-shell.css"):
            css = (LMS_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertIn("aspect-ratio: 16 / 9", css)
            self.assertIn("object-fit: contain", css)
            self.assertIn("#e4e1dc", css)
            self.assertIn("min(100cqw, calc(100cqh * 16 / 9))", css)

    @unittest.skipUnless(soffice_bin(), "LibreOffice is not installed")
    def test_pptx_pages_render(self) -> None:
        """LibreOffice renders an uploaded pptx into PNG pages."""
        added = self.staff.post(
            f"/api/classes/{self.class_id}/slide-deck",
            data={
                "module": "M1",
                "slot": "C1",
                "default_published": "1",
                "file": (io.BytesIO(_minimal_pptx()), "Hello.pptx"),
            },
        )
        self.assertEqual(added.status_code, 200, added.get_data(as_text=True))
        deck = added.get_json()["google_slides"]
        self.assertGreaterEqual(len(deck["pages"]), 1)
        self.assertTrue(deck["pages"][0]["published"])
        image = self.staff.get(deck["pages"][0]["image_url"])
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.get_data().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
