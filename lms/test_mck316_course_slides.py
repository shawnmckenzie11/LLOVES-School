#!/usr/bin/env python3
"""MCK-316: course slide decks, setup attach, and volume-only files."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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
from slide_import_jobs import follow_slide_import  # noqa: E402

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
        """A pptx name is accepted. A legacy ppt asks for pptx."""
        self.assertEqual(office_suffix("deck.PPTX"), ".pptx")
        with self.assertRaises(ValueError) as legacy:
            office_suffix("old.ppt")
        self.assertIn("pptx", str(legacy.exception))
        with self.assertRaises(ValueError):
            office_suffix("notes.pdf")
        office_bytes_ok(b"PK\x03\x04", ".pptx")
        with self.assertRaises(ValueError):
            office_bytes_ok(b"not-a-zip", ".pptx")


    def test_external_relationships_are_stripped(self) -> None:
        """http and file relationships are removed before conversion."""
        import course_slide_files as files

        rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="http://127.0.0.1/secret.png" TargetMode="External"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="file:///tmp/lloves-secret.txt" TargetMode="External"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.test/a.png"/>
</Relationships>
"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            package.writestr("ppt/slides/slide1.xml", "<p:sld/>")
            package.writestr("ppt/slides/_rels/slide1.xml.rels", rels)
        cleaned = files.strip_external_relationships(buffer.getvalue())
        with zipfile.ZipFile(io.BytesIO(cleaned)) as package:
            text_rels = package.read("ppt/slides/_rels/slide1.xml.rels").decode()
            slide = package.read("ppt/slides/slide1.xml")
        self.assertIn("slideLayout1.xml", text_rels)
        self.assertNotIn("127.0.0.1", text_rels)
        self.assertNotIn("file:", text_rels.lower())
        self.assertNotIn("https:", text_rels.lower())
        self.assertNotIn("TargetMode", text_rels)
        self.assertEqual(slide, b"<p:sld/>")
        slipped = io.BytesIO()
        with zipfile.ZipFile(slipped, "w") as package:
            package.writestr("../secret.txt", "nope")
        with self.assertRaises(ValueError):
            files.strip_external_relationships(slipped.getvalue())
        huge = io.BytesIO()
        with zipfile.ZipFile(huge, "w") as package:
            package.writestr("ppt/_rels/huge.rels", b"x" * (1_000_001))
        with self.assertRaises(ValueError):
            files.strip_external_relationships(huge.getvalue())

    def test_timeout_kills_the_process_group_and_removes_the_temp_dir(self) -> None:
        """A timed-out conversion does not leave a child or its temp dir."""
        import subprocess

        import course_slide_files as files

        script = Path("/tmp/lloves-sleeper-317.sh")
        script.write_text("#!/bin/sh\nsleep 317 &\nwait\n", encoding="utf-8")
        script.chmod(0o755)
        before = set(Path("/tmp").glob("lloves-slides-*"))
        try:
            with self.assertRaises(ValueError) as caught:
                files._office_to_pdf(
                    b"PK\x03\x04", ".pptx", str(script), timeout=0.6
                )
            self.assertIn("too long", str(caught.exception))
        finally:
            script.unlink(missing_ok=True)
        after = set(Path("/tmp").glob("lloves-slides-*"))
        self.assertEqual(before, after)
        listed = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, check=False
        )
        self.assertNotIn("sleep 317", listed.stdout)

    @unittest.skipUnless(soffice_bin(), "LibreOffice is not installed")
    def test_linked_http_and_file_targets_are_not_fetched(self) -> None:
        """LibreOffice does not fetch an external picture or a file link."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        import course_slide_files as files

        hits: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            """Count GETs. The conversion must not make one."""

            def do_GET(self) -> None:  # noqa: N802
                """Record the path and answer with an empty body."""
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, fmt: str, *args) -> None:
                """Drop the default access log."""
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        marker = Path("/tmp/lloves-slide-file-marker.txt")
        marker.write_bytes(b"LLOVES-FILE-LINK-MARKER")
        try:
            package = _minimal_pptx()
            with zipfile.ZipFile(io.BytesIO(package)) as source:
                rels = source.read("ppt/slides/_rels/slide1.xml.rels").decode()
            extra = (
                f'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                f'Target="http://127.0.0.1:{port}/hit.png" TargetMode="External"/>'
                '<Relationship Id="rId10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="file:///tmp/lloves-slide-file-marker.txt" TargetMode="External"/>'
            )
            rels = rels.replace("</Relationships>", extra + "</Relationships>")
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(package)) as source, zipfile.ZipFile(out, "w") as dest:
                for info in source.infolist():
                    data = source.read(info)
                    if info.filename == "ppt/slides/_rels/slide1.xml.rels":
                        data = rels.encode()
                    dest.writestr(info.filename, data)
            pages, _truncated = files.pages_from_office(out.getvalue(), ".pptx", timeout=60)
        finally:
            server.shutdown()
            server.server_close()
            marker.unlink(missing_ok=True)
        self.assertEqual(hits, [])
        self.assertTrue(pages)
        self.assertNotIn(b"LLOVES-FILE-LINK-MARKER", pages[0])


    def test_legacy_ppt_is_refused_before_conversion(self) -> None:
        """A .ppt is not handed to LibreOffice."""
        import course_slide_files as files

        with mock.patch.object(files, "run_sandboxed") as run:
            with self.assertRaises(ValueError) as caught:
                files.pages_from_office(b"\xd0\xcf\x11\xe0" + b"x" * 32, ".ppt")
        run.assert_not_called()
        self.assertIn("pptx", str(caught.exception))

    def test_conversion_is_refused_when_the_sandbox_is_missing(self) -> None:
        """A failed unshare probe does not run LibreOffice."""
        import course_slide_files as files

        files._sandbox_ready = None
        files._sandbox_failed_at = None
        try:
            with mock.patch.object(files, "_probe_sandbox", return_value=None):
                with mock.patch.object(files, "run_sandboxed") as run:
                    with self.assertLogs("course_slide_files", level="ERROR") as logs:
                        with self.assertRaises(ValueError) as caught:
                            files.pages_from_office(_minimal_pptx(), ".pptx", timeout=5)
            run.assert_not_called()
        finally:
            files._sandbox_ready = None
            files._sandbox_failed_at = None
        self.assertIn("not available", str(caught.exception))
        self.assertTrue(any("sandbox" in line for line in logs.output))

    def test_svg_file_href_is_removed(self) -> None:
        """An SVG file:// image is not left in the package."""
        import course_slide_files as files

        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<image xlink:href="file:///tmp/lloves-svg-secret.png" width="10" height="10"/>'
            '<image href="https://127.0.0.1/x.png"/>'
            "</svg>"
        )
        note = '<a:t>see file:///tmp/lloves-svg-secret.png</a:t>'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            package.writestr("ppt/slides/slide1.xml", "<p:sld/>")
            package.writestr("ppt/media/pic.svg", svg)
            package.writestr("ppt/media/note.xml", note)
            package.writestr(
                "ppt/media/clip.emf",
                b"EMF file:///tmp/lloves-svg-secret.png",
            )
        cleaned = files.strip_external_relationships(buffer.getvalue())
        with zipfile.ZipFile(io.BytesIO(cleaned)) as package:
            picture = package.read("ppt/media/pic.svg").decode()
            written = package.read("ppt/media/note.xml")
            clip = package.read("ppt/media/clip.emf")
            slide = package.read("ppt/slides/slide1.xml")
        self.assertNotIn("file:", picture.lower())
        self.assertNotIn("127.0.0.1", picture)
        self.assertNotIn(b"file://", written)
        self.assertNotIn(b"lloves-svg-secret", written)
        self.assertTrue(clip.startswith(b"\x89PNG"))
        self.assertEqual(slide, b"<p:sld/>")

    @unittest.skipUnless(soffice_bin(), "LibreOffice is not installed")
    def test_svg_file_target_is_not_rendered(self) -> None:
        """LibreOffice does not paint a file:// image stored in an SVG."""
        import course_slide_files as files

        secret = Path("/tmp/lloves-svg-secret.png")
        secret.write_bytes(b"LLOVES-SVG-FILE-MARKER")
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<image xlink:href="file:///tmp/lloves-svg-secret.png" width="200" height="200"/>'
            "</svg>"
        )
        package = _minimal_pptx()
        with zipfile.ZipFile(io.BytesIO(package)) as source:
            rels = source.read("ppt/slides/_rels/slide1.xml.rels").decode()
        rels = rels.replace(
            "</Relationships>",
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../media/pic.svg"/></Relationships>',
        )
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(package)) as source, zipfile.ZipFile(out, "w") as dest:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "ppt/slides/_rels/slide1.xml.rels":
                    data = rels.encode()
                dest.writestr(info.filename, data)
            dest.writestr("ppt/media/pic.svg", svg)
        try:
            pdf = files._office_to_pdf(
                out.getvalue(), ".pptx", files.soffice_bin(), timeout=60
            )
        finally:
            secret.unlink(missing_ok=True)
        self.assertNotIn(b"lloves-svg-secret", pdf)
        self.assertNotIn(b"LLOVES-SVG-FILE-MARKER", pdf)
        self.assertNotIn(b"file://", pdf)

    @unittest.skipUnless(shutil.which("unshare"), "unshare is not installed")
    def test_sandbox_hides_files_outside_the_job_dir(self) -> None:
        """The converter can read its temp dir and nothing else on disk."""
        import pwd

        import course_slide_files as files

        secret = Path("/tmp/lloves-slide-outside-secret.txt")
        secret.write_text("OUTSIDE-SECRET")
        work = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
        host_name = pwd.getpwuid(os.geteuid()).pw_name
        try:
            (work / "inside.txt").write_text("INSIDE-OK")
            completed = files.run_sandboxed(
                [
                    "sh",
                    "-c",
                    f'cat "{work}/inside.txt"; '
                    "if [ -f /tmp/lloves-slide-outside-secret.txt ]; then echo LEAK; "
                    "else echo HIDDEN; fi; "
                    "if [ -d /workspace ]; then echo WS; else echo NOWS; fi; "
                    "if [ -d /data ]; then echo DATA; else echo NODATA; fi; "
                    "if [ -f /etc/shadow ]; then echo SHADOW; else echo NOSHADOW; fi; "
                    "if [ -f /etc/hostname ]; then echo HOSTNAME; else echo NOHOSTNAME; fi; "
                    "if [ -d /oldroot ]; then echo OLD; else echo NOOLD; fi; "
                    "echo PASSWD:; cat /etc/passwd",
                ],
                work=work,
                timeout=15,
                env=files.office_env(work),
                cwd=str(work),
            )
        finally:
            secret.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(Path("/dev/shm") / f"lloves-root-{work.name}", ignore_errors=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = completed.stdout.decode()
        self.assertIn("INSIDE-OK", output)
        self.assertIn("HIDDEN", output)
        self.assertIn("NOWS", output)
        self.assertIn("NODATA", output)
        self.assertIn("NOSHADOW", output)
        self.assertIn("NOHOSTNAME", output)
        self.assertIn("NOOLD", output)
        self.assertNotIn("OUTSIDE-SECRET", output)
        self.assertNotIn("LEAK", output)
        if host_name not in {"root", "nobody"}:
            self.assertNotIn(host_name, output)

    def test_failed_sandbox_probe_is_retried_after_the_cooldown(self) -> None:
        """A failed probe blocks conversion briefly, then it is tried again."""
        import course_slide_files as files

        files._sandbox_ready = None
        files._sandbox_failed_at = time.monotonic()
        try:
            with mock.patch.object(files, "_probe_sandbox") as probe:
                with self.assertRaises(ValueError) as caught:
                    files.sandbox_tools()
            probe.assert_not_called()
            self.assertIn("not available", str(caught.exception))
            files._sandbox_failed_at = time.monotonic() - (
                files.SANDBOX_PROBE_RETRY_SECONDS + 5
            )
            with mock.patch.object(
                files,
                "_probe_sandbox",
                return_value=("/usr/bin/unshare", "/usr/bin/timeout"),
            ) as probe:
                found = files.sandbox_tools()
            probe.assert_called_once()
            self.assertEqual(found, ("/usr/bin/unshare", "/usr/bin/timeout"))
            self.assertIsNone(files._sandbox_failed_at)
        finally:
            files._sandbox_ready = None
            files._sandbox_failed_at = None

    @unittest.skipUnless(
        subprocess.run(["sudo", "-n", "true"], check=False).returncode == 0,
        "passwordless sudo is required to prove the root drop",
    )
    def test_root_sandbox_is_nobody_and_cannot_see_host_root(self) -> None:
        """Host root maps the converter to nobody and drops the host root."""
        lms_dir = str(Path(__file__).resolve().parent)
        driver = Path("/tmp/lloves-root-sandbox-driver.py")
        driver.write_text(
            "\n".join(
                [
                    "import shutil, sys, tempfile",
                    "from pathlib import Path",
                    f"sys.path.insert(0, {lms_dir!r})",
                    "import course_slide_files as files",
                    "marker = Path('/etc/lloves-slide-sandbox-secret')",
                    "marker.write_text('HOST-ETC-SECRET')",
                    "work = Path(tempfile.mkdtemp(prefix='lloves-slides-'))",
                    "check = work / 'check.sh'",
                    "try:",
                    "    (work / 'inside.txt').write_text('INSIDE-OK')",
                    "    check.write_text(",
                    "        '#!/bin/sh\\n'",
                    "        'id -u\\n'",
                    "        'cat inside.txt\\n'",
                    "        'if [ -d /data ]; then echo DATA; else echo NODATA; fi\\n'",
                    "        'if [ -f /etc/shadow ]; then echo SHADOW; else echo NOSHADOW; fi\\n'",
                    "        'if [ -f /etc/lloves-slide-sandbox-secret ]; then echo LEAK; else echo HIDDEN; fi\\n'",
                    "        'if [ -d /oldroot ]; then echo OLD; else echo NOOLD; fi\\n'",
                    "    )",
                    "    check.chmod(0o755)",
                    "    completed = files.run_sandboxed(",
                    "        ['sh', str(check)],",
                    "        work=work,",
                    "        timeout=20,",
                    "        env=files.office_env(work),",
                    "        cwd=str(work),",
                    "    )",
                    "    sys.stdout.buffer.write(completed.stdout)",
                    "    sys.stderr.buffer.write(completed.stderr)",
                    "    raise SystemExit(completed.returncode)",
                    "finally:",
                    "    marker.unlink(missing_ok=True)",
                    "    shutil.rmtree(work, ignore_errors=True)",
                    "    shutil.rmtree(Path('/dev/shm') / f'lloves-root-{work.name}', ignore_errors=True)",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            ran = subprocess.run(
                ["sudo", "-n", "python3", str(driver)],
                timeout=30,
                check=False,
                capture_output=True,
            )
        finally:
            driver.unlink(missing_ok=True)
        output = ran.stdout.decode()
        self.assertEqual(ran.returncode, 0, ran.stderr.decode() + output)
        self.assertTrue(output.splitlines())
        self.assertEqual(output.splitlines()[0].strip(), "65534")
        self.assertIn("INSIDE-OK", output)
        self.assertIn("NODATA", output)
        self.assertIn("NOSHADOW", output)
        self.assertIn("HIDDEN", output)
        self.assertIn("NOOLD", output)
        self.assertNotIn("HOST-ETC-SECRET", output)
        self.assertNotIn("LEAK", output)

    @unittest.skipUnless(
        subprocess.run(["sudo", "-n", "true"], check=False).returncode == 0
        and bool(soffice_bin()),
        "passwordless sudo and LibreOffice are required",
    )
    def test_root_sandbox_converts_pptx_as_nobody(self) -> None:
        """A root worker still renders a pptx after the uid drop."""
        lms_dir = str(Path(__file__).resolve().parent)
        # Same interpreter as this process. sudo's python3 is a different
        # build on CI, and mixing its stdlib with this one breaks ctypes.
        pythonpath = [lms_dir]
        for entry in sys.path:
            if entry and "site-packages" in entry and entry not in pythonpath:
                pythonpath.append(entry)
        deck = Path("/tmp/lloves-root-deck.pptx")
        driver = Path("/tmp/lloves-root-convert-driver.py")
        deck.write_bytes(_minimal_pptx())
        driver.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "import course_slide_files as files",
                    "raw = Path('/tmp/lloves-root-deck.pptx').read_bytes()",
                    "pages, _truncated = files.pages_from_office(raw, '.pptx', timeout=70)",
                    "if pages and pages[0].startswith(b'\\x89PNG'):",
                    "    print('PNG', len(pages))",
                    "else:",
                    "    print('BAD', len(pages))",
                    "    raise SystemExit(2)",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            ran = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "env",
                    "PYTHONPATH=" + os.pathsep.join(pythonpath),
                    sys.executable,
                    str(driver),
                ],
                timeout=80,
                check=False,
                capture_output=True,
            )
        finally:
            deck.unlink(missing_ok=True)
            driver.unlink(missing_ok=True)
        self.assertEqual(ran.returncode, 0, ran.stderr.decode() + ran.stdout.decode())
        self.assertIn("PNG", ran.stdout.decode())

    def test_libreoffice_env_omits_app_secrets(self) -> None:
        """The converter environment does not carry app secrets."""
        import course_slide_files as files

        seen: dict[str, dict[str, str]] = {}

        def capture(command, *, work, timeout, env, cwd):
            """Record the child environment and pretend LibreOffice wrote a PDF."""
            seen["env"] = dict(env)
            out = Path(cwd) / "out"
            out.mkdir(exist_ok=True)
            (out / "deck.pdf").write_bytes(b"%PDF-1.4\n")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        binary = shutil.which("true") or "/usr/bin/true"
        with mock.patch.dict(
            os.environ,
            {"FLASK_SECRET_KEY": "super-secret", "GOOGLE_CLIENT_SECRET": "oauth-secret"},
            clear=False,
        ):
            with mock.patch.object(files, "run_sandboxed", side_effect=capture):
                files._office_to_pdf(b"PK\x03\x04", ".pptx", binary, timeout=5)
        env = seen["env"]
        self.assertNotIn("FLASK_SECRET_KEY", env)
        self.assertNotIn("GOOGLE_CLIENT_SECRET", env)
        self.assertNotIn("super-secret", " ".join(env.values()))
        self.assertTrue(env.get("HOME"))

    def test_converter_dies_when_the_worker_dies(self) -> None:
        """Killing the worker kills the converter and its grandchild."""
        script = Path("/tmp/lloves-sleeper-323.sh")
        script.write_text("#!/bin/sh\nsleep 323 &\nwait\n", encoding="utf-8")
        script.chmod(0o755)
        driver = Path("/tmp/lloves-pdeath-driver.py")
        lms_dir = str(Path(__file__).resolve().parent)
        driver.write_text(
            "import os, sys, subprocess, threading, time\n"
            f"sys.path.insert(0, {lms_dir!r})\n"
            "import course_slide_files as files\n"
            "def go():\n"
            "    try:\n"
            "        files._office_to_pdf(b'PK\\x03\\x04', '.pptx', '/tmp/lloves-sleeper-323.sh', timeout=30)\n"
            "    except Exception:\n"
            "        pass\n"
            "threading.Thread(target=go, daemon=True).start()\n"
            "for _ in range(80):\n"
            "    listed = subprocess.run(['ps', '-eo', 'args'], capture_output=True, text=True)\n"
            "    if 'sleep 323' in listed.stdout:\n"
            "        os._exit(0)\n"
            "    time.sleep(0.1)\n"
            "os._exit(2)\n",
            encoding="utf-8",
        )
        try:
            ran = subprocess.run(["python3", str(driver)], timeout=20, check=False)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            time.sleep(0.4)
            listed = subprocess.run(
                ["ps", "-eo", "args"], capture_output=True, text=True, check=False
            )
            self.assertNotIn("sleep 323", listed.stdout)
        finally:
            script.unlink(missing_ok=True)
            driver.unlink(missing_ok=True)
            import course_slide_files as files

            files.sweep_stale_slide_dirs(max_age_seconds=0)

    def test_stale_conversion_dirs_are_swept(self) -> None:
        """A boot sweep removes an old job dir and leaves a fresh one."""
        import course_slide_files as files

        old = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
        fresh = Path(tempfile.mkdtemp(prefix="lloves-slides-"))
        os.utime(old, (1, 1))
        try:
            removed = files.sweep_stale_slide_dirs()
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertGreaterEqual(removed, 1)
        finally:
            shutil.rmtree(fresh, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)

    def test_missing_content_length_stops_at_the_cap(self) -> None:
        """A stream with no declared length cannot be read past the cap."""
        import course_slide_files as files
        from werkzeug.exceptions import RequestEntityTooLarge

        small = {"wsgi.input": io.BytesIO(b"abcd")}
        files.cap_wsgi_input(small, limit=8)
        self.assertEqual(small["wsgi.input"].read(20), b"abcd")
        huge = {"wsgi.input": io.BytesIO(b"x" * 40)}
        files.cap_wsgi_input(huge, limit=8)
        self.assertEqual(huge["wsgi.input"].read(100), b"x" * 8)
        with self.assertRaises(RequestEntityTooLarge):
            huge["wsgi.input"].read(1)

    def test_at_most_two_imports_run_at_once(self) -> None:
        """A third import waits until one of the two slots is free."""
        from slide_import_jobs import start_slide_import

        class Jobs:
            """Records how many imports overlapped."""

            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.current = 0
                self.max_seen = 0
                self.ready: list[str] = []

            def finish_slide_import_job(self, job_id: str, result: dict) -> None:
                """Count one finished import."""
                with self.lock:
                    self.ready.append(job_id)

            def fail_slide_import_job(self, job_id: str, error: str, **_kwargs) -> None:
                """Record a failed import as finished so the test can wait."""
                with self.lock:
                    self.ready.append(job_id)

        jobs = Jobs()

        def work() -> dict:
            """Hold a slot long enough for the others to overlap."""
            with jobs.lock:
                jobs.current += 1
                jobs.max_seen = max(jobs.max_seen, jobs.current)
            time.sleep(0.3)
            with jobs.lock:
                jobs.current -= 1
            return {"ok": True}

        for index in range(4):
            start_slide_import(jobs, f"job-{index}", work)
        deadline = time.time() + 5
        while time.time() < deadline and len(jobs.ready) < 4:
            time.sleep(0.02)
        self.assertEqual(len(jobs.ready), 4)
        self.assertLessEqual(jobs.max_seen, 2)
        self.assertGreaterEqual(jobs.max_seen, 2)

    def test_import_slots_are_shared_across_processes(self) -> None:
        """Two other processes fill the cap, and a dead holder frees a slot."""
        from slide_import_jobs import acquire_import_slot

        lms_dir = str(Path(__file__).resolve().parent)
        holder = Path("/tmp/lloves-slot-holder.py")
        holder.write_text(
            "import sys, time\n"
            f"sys.path.insert(0, {lms_dir!r})\n"
            "from slide_import_jobs import acquire_import_slot\n"
            "slot = acquire_import_slot(2)\n"
            "if slot is None:\n"
            "    raise SystemExit(3)\n"
            "sys.stdout.write('held\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
            "slot.release()\n",
            encoding="utf-8",
        )
        procs: list[subprocess.Popen[str]] = []
        try:
            for _index in range(2):
                proc = subprocess.Popen(
                    ["python3", str(holder)],
                    stdout=subprocess.PIPE,
                    text=True,
                )
                procs.append(proc)
                assert proc.stdout is not None
                line = proc.stdout.readline()
                self.assertIn("held", line)
            self.assertIsNone(acquire_import_slot(0.3))
            procs[0].kill()
            procs[0].wait(timeout=5)
            freed = acquire_import_slot(2)
            self.assertIsNotNone(freed)
            assert freed is not None
            freed.release()
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout is not None:
                    proc.stdout.close()
            holder.unlink(missing_ok=True)

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
            added = follow_slide_import(self.staff, added)
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
            added = follow_slide_import(self.staff, added)
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
            second = follow_slide_import(self.staff, second)
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

    def test_declared_body_over_the_cap_is_refused_before_read(self) -> None:
        """A Content-Length past 64 MiB never reaches the upload reader."""
        with mock.patch("course_slide_files.write_capped_upload") as writer:
            refused = self.staff.post(
                f"/api/classes/{self.class_id}/slide-deck",
                data=b"",
                content_type="multipart/form-data; boundary=----bound",
                environ_overrides={"CONTENT_LENGTH": str(80 * 1024 * 1024)},
            )
        self.assertEqual(refused.status_code, 400)
        self.assertIn("too large", refused.get_json()["error"])
        writer.assert_not_called()


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
        added = follow_slide_import(self.staff, added, timeout=90)
        self.assertEqual(added.status_code, 200, added.get_data(as_text=True))
        deck = added.get_json()["google_slides"]
        self.assertGreaterEqual(len(deck["pages"]), 1)
        self.assertTrue(deck["pages"][0]["published"])
        image = self.staff.get(deck["pages"][0]["image_url"])
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.get_data().startswith(b"\x89PNG"))



    def test_legacy_ppt_upload_asks_for_pptx(self) -> None:
        """Uploading a .ppt returns the save-as-pptx message and no job."""
        added = self.staff.post(
            f"/api/classes/{self.class_id}/slide-deck",
            data={
                "module": "M1",
                "slot": "C1",
                "file": (io.BytesIO(b"\xd0\xcf\x11\xe0" + b"x" * 32), "Legacy.ppt"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(added.status_code, 400, added.get_data(as_text=True))
        self.assertIn("pptx", (added.get_json() or {}).get("error", ""))
        self.assertNotIn("job_id", added.get_json() or {})

    def test_failed_db_write_removes_the_new_files(self) -> None:
        """A failed deck row does not leave the page or the source on disk."""
        import base64

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with mock.patch.object(
            self.school, "_replace_course_slide_rows", side_effect=RuntimeError("db")
        ):
            with self.assertRaises(RuntimeError):
                self.school.save_course_slide_deck(
                    self.class_id,
                    "M1",
                    "C2",
                    title="Broken",
                    source="pptx",
                    page_pngs=[png],
                    source_bytes=b"PK\x03\x04broken-source",
                    source_suffix=".pptx",
                )
        root = Path(self.school.data_dir) / "live-question-images" / str(self.class_id)
        files = [path for path in root.rglob("*")] if root.exists() else []
        self.assertFalse([path for path in files if path.is_file()])

    def test_restart_closes_a_running_import(self) -> None:
        """A job left running is failed on boot, and a finished job stays."""
        running = "ab" * 16
        ready = "cd" * 16
        self.school.insert_slide_import_job(
            running, int(self.teacher["id"]), self.class_id, None
        )
        self.school.insert_slide_import_job(
            ready, int(self.teacher["id"]), self.class_id, None
        )
        self.school.finish_slide_import_job(ready, {"deck": {"title": "Kept"}})
        closed = self.school.fail_orphaned_slide_import_jobs()
        self.assertEqual(closed, 1)
        failed = self.school.get_slide_import_job(running)
        assert failed is not None
        self.assertEqual(failed["status"], "error")
        self.assertIn("restarted", failed["error"])
        kept = self.school.get_slide_import_job(ready)
        assert kept is not None
        self.assertEqual(kept["status"], "ready")

    def test_expired_running_import_is_failed_without_a_restart(self) -> None:
        """A running job past the budget is failed while a fresh one stays."""
        from datetime import datetime, timedelta, timezone

        from slide_import_jobs import JOB_BUDGET_SECONDS

        stale = "ef" * 16
        fresh = "12" * 16
        ready = "34" * 16
        owner = int(self.teacher["id"])
        self.school.insert_slide_import_job(stale, owner, self.class_id, None)
        self.school.insert_slide_import_job(fresh, owner, self.class_id, None)
        self.school.insert_slide_import_job(ready, owner, self.class_id, None)
        self.school.finish_slide_import_job(ready, {"deck": {"title": "Kept"}})
        old = (
            datetime.now(timezone.utc) - timedelta(seconds=JOB_BUDGET_SECONDS + 30)
        ).isoformat()
        self.school.conn.execute(
            "UPDATE slide_import_jobs SET created_at = ? WHERE id = ?",
            (old, stale),
        )
        closed = self.school.fail_expired_slide_import_jobs(JOB_BUDGET_SECONDS)
        self.assertEqual(closed, 1)
        failed = self.school.get_slide_import_job(stale)
        assert failed is not None
        self.assertEqual(failed["status"], "error")
        self.assertIn("too long", failed["error"])
        still = self.school.get_slide_import_job(fresh)
        assert still is not None
        self.assertEqual(still["status"], "running")
        kept = self.school.get_slide_import_job(ready)
        assert kept is not None
        self.assertEqual(kept["status"], "ready")
        self.school.finish_slide_import_job(stale, {"deck": {"title": "Late"}})
        late = self.school.get_slide_import_job(stale)
        assert late is not None
        self.assertEqual(late["status"], "error")

if __name__ == "__main__":
    unittest.main()
