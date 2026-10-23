#!/usr/bin/env python3
"""Per-class MC import overlay and metadata merge tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from content_store import ContentBlobStore  # noqa: E402
from school_db import _now  # noqa: E402

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_LOGIN_HTML = b"<!doctype html><html><body>login</body></html>"


class _UrlOpenBody:
    """One canned ``urlopen`` body, so image-mirror tests do not touch the network."""

    def __init__(self, body: bytes, content_type: str) -> None:
        """Store the bytes and content type a download would return.

        Args:
            body: Response body.
            content_type: ``Content-Type`` header value.
        """
        self.headers = {"Content-Type": content_type}
        self._body = body

    def read(self, size: int = -1) -> bytes:
        """Return the canned body, honouring a streamed size cap.

        Args:
            size: Maximum bytes to return. Negative reads the rest.
        """
        if size is None or size < 0 or size >= len(self._body):
            data = self._body
            self._body = b""
            return data
        data = self._body[:size]
        self._body = self._body[size:]
        return data

    def __enter__(self) -> "_UrlOpenBody":
        """Enter the ``with urlopen(...)`` block."""
        return self

    def __exit__(self, *_exc: object) -> bool:
        """Leave the ``with urlopen(...)`` block without swallowing errors."""
        return False


def _login_page_urlopen(*_args: object, **_kwargs: object) -> _UrlOpenBody:
    """Return the HTML login page a dead Canvas verifier downloads."""
    return _UrlOpenBody(_LOGIN_HTML, "text/html; charset=utf-8")


def _insert_bank(
    school: Any,
    library_id: int,
    *,
    title: str,
    import_key: str,
) -> int:
    """Insert one question bank row for tests."""
    cur = school.conn.execute(
        """
        INSERT INTO question_banks (
            library_id, import_key, title, settings_json, created_at
        ) VALUES (?, ?, ?, '{}', ?)
        """,
        (int(library_id), import_key, title, _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


def _insert_mc_question(
    school: Any,
    bank_id: int,
    *,
    import_key: str,
    title: str,
    stem: str,
    correct_index: int = 0,
    extra_payload: dict[str, Any] | None = None,
) -> int:
    """Insert one multiple-choice question row for tests."""
    choices = [
        {"id": "a1", "html": "First option", "correct": correct_index == 0},
        {"id": "a2", "html": "Second option", "correct": correct_index == 1},
        {"id": "a3", "html": "Third option", "correct": correct_index == 2},
    ]
    payload = {
        "stem_html": stem,
        "points_possible": 1.0,
        "choices": choices,
        "correct_ids": [choices[correct_index]["id"]],
    }
    if extra_payload:
        payload.update(extra_payload)
    cur = school.conn.execute(
        """
        INSERT INTO questions (
            bank_id, import_key, item_type, title, payload_json, created_at
        ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
        """,
        (int(bank_id), import_key, title, json.dumps(payload), _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)



class BankImageHostTests(unittest.TestCase):
    """Allowlist checks that do not need a school database."""

    def test_fragment_userinfo_and_http_do_not_match(self) -> None:
        """Hostname matching ignores fragments, queries, and userinfo tricks."""
        try:
            from bank_image_mirror import is_remote_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import is_remote_bank_image_url

        self.assertFalse(
            is_remote_bank_image_url("https://169.254.169.254#.instructure.com/")
        )
        self.assertFalse(
            is_remote_bank_image_url(
                "http://127.0.0.1:8789/?u=https://x.instructure.com/"
            )
        )
        self.assertFalse(
            is_remote_bank_image_url("https://evil.example?.instructure.com/")
        )
        self.assertFalse(
            is_remote_bank_image_url(
                "https://virtuallearning.instructure.com@169.254.169.254/latest/"
            )
        )
        self.assertFalse(is_remote_bank_image_url("https://evilinstructure.com/a.png"))
        self.assertFalse(
            is_remote_bank_image_url(
                "https://169.254.169.254@virtuallearning.instructure.com/a.png"
            )
        )
        self.assertFalse(
            is_remote_bank_image_url(
                "https://user:pass@virtuallearning.instructure.com/a.png"
            )
        )
        self.assertFalse(
            is_remote_bank_image_url(
                "https://169.254.169.254\\@virtuallearning.instructure.com/a.png"
            )
        )
        self.assertTrue(
            is_remote_bank_image_url("https://x.instructure.com/a%5Cb.png")
        )
        self.assertTrue(
            is_remote_bank_image_url(
                "https://virtuallearning.instructure.com/a\\b.png"
            )
        )
        self.assertTrue(
            is_remote_bank_image_url(
                "https://equatio-api.texthelp.com/svg/latex/%5Cfrac%7B1%7D%7B2%7D"
            )
        )
        self.assertTrue(
            is_remote_bank_image_url(
                "https://virtuallearning.instructure.com/a.png?q=%5C"
            )
        )
        self.assertFalse(
            is_remote_bank_image_url(
                "https://virtuallearning.instructure.com%5C.evil.com/a.png"
            )
        )
        self.assertFalse(is_remote_bank_image_url("https://[::1/a"))
        self.assertFalse(is_remote_bank_image_url("https://[::1]@host/"))
        self.assertTrue(
            is_remote_bank_image_url("https://virtuallearning.instructure.com/a.png")
        )
        self.assertTrue(is_remote_bank_image_url("https://www.wiris.net/demo/x"))

    def test_redirect_handler_reports_the_next_url(self) -> None:
        """The redirect handler does not open the Location target itself."""
        import urllib.request

        try:
            from bank_image_mirror import _CaptureRedirect, _Redirect
        except ImportError:
            from lms.bank_image_mirror import _CaptureRedirect, _Redirect

        handler = _CaptureRedirect()
        req = urllib.request.Request("https://virtuallearning.instructure.com/a")
        with self.assertRaises(_Redirect) as caught:
            handler.redirect_request(
                req, None, 302, "Found", {}, "https://169.254.169.254/latest/"
            )
        self.assertEqual(
            caught.exception.location, "https://169.254.169.254/latest/"
        )

    def test_svg_sanitiser_neutralises_qa_bypasses(self) -> None:
        """Namespace scripts, encoded javascript URLs, and external use are stripped.

        The cleaned document is what the mirror stores. A DTD and malformed
        markup are refused instead of stored.
        """
        try:
            from bank_image_mirror import sanitize_svg
        except ImportError:
            from lms.bank_image_mirror import sanitize_svg

        kept = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<s:script xmlns:s="http://www.w3.org/2000/svg">alert(1)</s:script>'
            b'<h:script xmlns:h="http://www.w3.org/1999/xhtml">alert(2)</h:script>'
            b'<a href="javascript&#58;alert(1)">t</a>'
            b'<a href="&#106;avascript:alert(1)">t</a>'
            b'<a href="java&#x09;script:alert(1)">t</a>'
            b'<a xmlns:xlink="http://www.w3.org/1999/xlink" '
            b'xlink:href="java&#x09;script:alert(9)">t</a>'
            b'<use href="https://external/evil.svg"/>'
            b'<use href="#glyph"/>'
            b'<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#mark"/>'
            b'<foreignObject><script>alert(3)</script></foreignObject>'
            b'<path d="M9 9"/>'
            b"</svg>"
        )
        cleaned = sanitize_svg(kept)
        self.assertIsNotNone(cleaned)
        assert cleaned is not None
        lower = cleaned.lower()
        self.assertNotIn(b"<script", lower)
        self.assertNotIn(b"javascript", lower)
        self.assertNotIn(b"foreignobject", lower)
        self.assertNotIn(b"onload", lower)
        self.assertNotIn(b"external", lower)
        self.assertNotIn(b"alert", lower)
        self.assertIn(b"#glyph", cleaned)
        self.assertIn(b"#mark", cleaned)
        self.assertIn(b"M9 9", cleaned)
        onload = sanitize_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
        )
        self.assertIsNotNone(onload)
        assert onload is not None
        self.assertNotIn(b"onload", onload.lower())
        self.assertIsNone(
            sanitize_svg(b'<svg x="1"onload="alert(1)"></svg>')
        )
        self.assertIsNone(
            sanitize_svg(
                b'<?xml version="1.0"?>'
                b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                b'<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
            )
        )
        self.assertIsNone(
            sanitize_svg(b"<!doctype html><html><body><svg></svg></body></html>")
        )
        styled = sanitize_svg(
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b"<style>.ok{fill:red}"
            b"@font-face{font-family:w;src:url(data:font/woff;base64,QQ)}"
            b".keep{fill:url(#grad)}</style>"
            b'<path style="fill:\\75 rl(https://evil.example/a);stroke:url(#g)"/>'
            b'<path style="fill:image-set(&quot;https://evil.example/b&quot;)"/>'
            b'<path style="fill:u\\rl(https://evil.example/c)"/>'
            b'<path style="@\\69mport url(https://evil.example/d);fill:blue"/>'
            b'<path d="M9 9"/>'
            b"</svg>"
        )
        self.assertIsNotNone(styled)
        assert styled is not None
        folded = styled.lower()
        self.assertNotIn(b"data:", folded)
        self.assertNotIn(b"evil", folded)
        self.assertNotIn(b"image-set", folded)
        self.assertNotIn(b"@import", folded)
        self.assertIn(b"#grad", styled)
        self.assertIn(b"#g", styled)
        self.assertIn(b"M9 9", styled)
        self.assertIn(b"fill:red", folded)
        doctype = sanitize_svg(
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
            b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
            b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M2 2"/></svg>'
        )
        self.assertIsNotNone(doctype)
        assert doctype is not None
        self.assertIn(b"M2 2", doctype)

    def test_svg_writer_does_not_touch_the_process_namespace_map(self) -> None:
        """Concurrent SVG, manifest, and rels writers leave the global map alone."""
        import sys
        import threading
        import xml.etree.ElementTree as ET

        try:
            from bank_image_mirror import sanitize_svg
            from course_slide_files import _strip_rels_xml
            from slim_pack import _rewrite_manifest
        except ImportError:
            from lms.bank_image_mirror import sanitize_svg
            from lms.course_slide_files import _strip_rels_xml
            from lms.slim_pack import _rewrite_manifest

        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        manifest = (
            '<manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">'
            '<resources><resource identifier="r" href="a.png">'
            '<file href="a.png"/></resource></resources></manifest>'
        )
        rels = (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            b'Target="https://evil.example/a.png" TargetMode="External"/>'
            b"</Relationships>"
        )
        saved = dict(ET._namespace_map)
        errors: list[str] = []

        def sanitise() -> None:
            """Sanitise one SVG many times and record a bad result."""
            try:
                for _ in range(40):
                    out = sanitize_svg(svg)
                    if out is None or b"ns0:" in out or b"<svg" not in out:
                        errors.append(repr(out))
            except Exception as exc:  # noqa: BLE001 - the test must see worker failures
                errors.append(f"sanitise: {exc!r}")

        def rewrite() -> None:
            """Rewrite a tiny manifest while SVG sanitising is in flight."""
            try:
                for _ in range(20):
                    out = _rewrite_manifest(manifest, {"a.png"})
                    if b"ns0:" in out or b"a.png" not in out:
                        errors.append(out.decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001 - the test must see worker failures
                errors.append(f"rewrite: {exc!r}")

        def strip() -> None:
            """Strip an external relationship while SVG sanitising is in flight."""
            try:
                for _ in range(20):
                    out = _strip_rels_xml(rels)
                    if b"ns0:" in out or b"https://evil" in out:
                        errors.append(out.decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001 - the test must see worker failures
                errors.append(f"strip: {exc!r}")

        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            threads = [
                threading.Thread(target=sanitise),
                threading.Thread(target=sanitise),
                threading.Thread(target=sanitise),
                threading.Thread(target=sanitise),
                threading.Thread(target=rewrite),
                threading.Thread(target=strip),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(previous)
        self.assertEqual(errors, [])
        self.assertEqual(dict(ET._namespace_map), saved)

    def test_refusal_cache_stays_within_cap(self) -> None:
        """Distinct refusals do not grow past the cap, and a hit stays warm."""
        try:
            from bank_image_mirror import (
                _REFUSAL_MAX,
                _refusal_active,
                _refusal_lock,
                _refusals,
                _remember_refusal,
                clear_bank_image_refusals,
            )
        except ImportError:
            from lms.bank_image_mirror import (
                _REFUSAL_MAX,
                _refusal_active,
                _refusal_lock,
                _refusals,
                _remember_refusal,
                clear_bank_image_refusals,
            )

        clear_bank_image_refusals()
        try:
            total = _REFUSAL_MAX + 25
            for index in range(total):
                _remember_refusal(
                    1, f"https://virtuallearning.instructure.com/{index}.png"
                )
            newest = (
                "https://virtuallearning.instructure.com/"
                f"{total - 1}.png"
            )
            oldest = "https://virtuallearning.instructure.com/0.png"
            with _refusal_lock:
                self.assertEqual(len(_refusals), _REFUSAL_MAX)
                self.assertNotIn((1, oldest), _refusals)
                self.assertIn((1, newest), _refusals)
            with mock.patch("bank_image_mirror._REFUSAL_MAX", 2):
                clear_bank_image_refusals()
                _remember_refusal(1, "https://virtuallearning.instructure.com/a.png")
                _remember_refusal(1, "https://virtuallearning.instructure.com/b.png")
                self.assertTrue(
                    _refusal_active(1, "https://virtuallearning.instructure.com/a.png")
                )
                _remember_refusal(1, "https://virtuallearning.instructure.com/c.png")
                with _refusal_lock:
                    self.assertEqual(
                        set(_refusals),
                        {
                            (1, "https://virtuallearning.instructure.com/a.png"),
                            (1, "https://virtuallearning.instructure.com/c.png"),
                        },
                    )
        finally:
            clear_bank_image_refusals()


class ModuleBankImportMergeTests(unittest.TestCase):
    """Metadata merge and import persistence without HTTP."""

    def setUp(self) -> None:
        """Teacher-owned MCR3U class with attached library and confirmed M1 bank."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        try:
            from bank_image_mirror import clear_bank_image_refusals
        except ImportError:
            from lms.bank_image_mirror import clear_bank_image_refusals
        clear_bank_image_refusals()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        self.m1_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Chapter 1 MC",
            import_key="bank:ch1",
        )
        self.m1_q = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q1",
            title="Factoring",
            stem="Factor x squared minus one",
        )
        self.school.confirm_module_bank_links(
            self.library_id, 1, [self.m1_bank]
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
        )

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        try:
            from bank_image_mirror import clear_bank_image_refusals
        except ImportError:
            from lms.bank_image_mirror import clear_bank_image_refusals
        clear_bank_image_refusals()
        self.school.close()
        self.tmp.cleanup()

    def test_import_merges_into_live_metadata(self) -> None:
        """Imported MC appears in session metadata questions for the class."""
        before = self.school.live_class_metadata_for_session(self.session_id)
        seed_count = len(before.get("questions") or [])
        placement = self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        self.assertTrue(str(placement.get("placement_key", "")).startswith("class:"))
        after = self.school.live_class_metadata_for_session(self.session_id)
        self.assertGreater(len(after.get("questions") or []), seed_count)
        imported = [
            row
            for row in after.get("questions") or []
            if str(row.get("id", "")).startswith("bank-import-")
        ]
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["text"], "Factor x squared minus one")
        self.assertEqual(imported[0]["correct_answer"], "A")

    def test_student_payload_rewrites_staff_bank_image_urls(self) -> None:
        """Published bank graphs leave the staff-only module-files path."""
        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-graph",
            title="Graph",
            stem=(
                '<p>Refer to the graph.</p>'
                '<img src="$IMS-CC-FILEBASE$/diagram.png">'
            ),
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_id,
            library_id=self.library_id,
            page_number=1,
            stage="round",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        with self.school.game._lock:
            student = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchone()
        student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, student_id, codename="Maple"
        )
        payload = self.school.student_live_items_payload(
            self.session_id, student_id
        )
        content = (payload.get("active_questions") or [{}])[0].get("content") or {}
        blob = json.dumps(content)
        staff_root = f"/staff/class/{self.class_id}/module-files/"
        student_root = f"/api/classes/{self.class_id}/module-files/"
        self.assertIn("<img", str(content.get("text_html") or ""))
        self.assertIn(student_root, blob)
        self.assertNotIn(staff_root, blob)

    def test_student_payload_remirrors_leftover_canvas_images(self) -> None:
        """Stored Instructure imgs remirror onto the student module-files URL."""
        remote = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "70463/files/188452/download?verifier=test"
        )
        # The dead verifier downloads a login page. That must not occupy the
        # cache before this fixture plants the real mirrored PNG.
        with mock.patch("bank_image_mirror._open_remote", side_effect=_login_page_urlopen):
            self._remirror_leftover_canvas_image(remote)

    def _remirror_leftover_canvas_image(self, remote: str) -> None:
        """Import a leftover Canvas img and assert the student card uses the PNG.

        Args:
            remote: Canvas file URL left in the stored stem.
        """
        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-canvas-graph",
            title="Canvas graph",
            stem=f'<p>Refer to the graph.</p><img src="{remote}">',
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_id,
            library_id=self.library_id,
            page_number=1,
            stage="round",
        )
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(
            _TINY_PNG, filename="web_resources/diagram.png", mime="image/png"
        )
        try:
            from bank_image_mirror import ensure_bank_image_cache_schema
        except ImportError:
            from lms.bank_image_mirror import ensure_bank_image_cache_schema
        ensure_bank_image_cache_schema(self.school)
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                self.library_id,
                "web_resources/diagram.png",
                stored.sha256,
                _now(),
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO bank_image_cache (
                library_id, source_url, relpath, blob_sha, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(library_id, source_url) DO UPDATE SET
                relpath = excluded.relpath,
                blob_sha = excluded.blob_sha
            """,
            (
                self.library_id,
                remote,
                "web_resources/diagram.png",
                stored.sha256,
                _now(),
            ),
        )
        self.school.conn.commit()
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        with self.school.game._lock:
            student = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchone()
        student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, student_id, codename="Maple"
        )
        payload = self.school.student_live_items_payload(
            self.session_id, student_id
        )
        content = (payload.get("active_questions") or [{}])[0].get("content") or {}
        blob = json.dumps(content)
        self.assertIn(
            f"/api/classes/{self.class_id}/module-files/web_resources/diagram.png",
            blob,
        )
        self.assertNotIn("instructure.com", blob)
        self.assertNotIn(f"/staff/class/{self.class_id}/module-files/", blob)

    def test_login_html_is_not_cached_as_a_bank_image(self) -> None:
        """A Canvas login page does not take the image-cache key; a PNG does."""
        try:
            from bank_image_mirror import mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import mirror_bank_image_url

        html_url = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "1/files/1/download?verifier=html"
        )
        png_url = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "1/files/2/download?verifier=png"
        )
        octet_url = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "1/files/3/download?verifier=octet"
        )
        responses = [
            _UrlOpenBody(_LOGIN_HTML, "text/html; charset=utf-8"),
            _UrlOpenBody(_TINY_PNG, "image/png"),
            _UrlOpenBody(_TINY_PNG, "application/octet-stream"),
        ]
        with mock.patch("bank_image_mirror._open_remote", side_effect=responses):
            html_rel = mirror_bank_image_url(self.school, self.library_id, html_url)
            png_rel = mirror_bank_image_url(self.school, self.library_id, png_url)
            octet_rel = mirror_bank_image_url(self.school, self.library_id, octet_url)
        self.assertIsNone(html_rel)
        self.assertTrue(str(png_rel).endswith(".png"))
        self.assertTrue(str(octet_rel or "").endswith(".png"))
        rows = self.school.conn.execute(
            "SELECT source_url FROM bank_image_cache"
        ).fetchall()
        self.assertEqual(
            {str(row["source_url"]) for row in rows},
            {png_url, octet_url},
        )

    def test_sniff_uses_magic_bytes_not_content_type(self) -> None:
        """A PNG labeled text/plain is kept; HTML labeled as an image is not."""
        try:
            from bank_image_mirror import mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import mirror_bank_image_url

        svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        svg_prolog = b'<?xml version="1.0"?>\n  <svg xmlns="http://www.w3.org/2000/svg"></svg>'
        cases = [
            (_TINY_PNG, "text/plain", ".png"),
            (b"\xff\xd8\xff\xe0", "text/plain", ".jpg"),
            (b"GIF89a", "text/html", ".gif"),
            (b"RIFF\x00\x00\x00\x00WEBP", "application/octet-stream", ".webp"),
            (_LOGIN_HTML, "image/png", None),
            (_LOGIN_HTML, "image/svg+xml", None),
            (svg, "application/octet-stream", ".svg"),
            (svg_prolog, "text/plain", ".svg"),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad</script></svg>', "image/svg+xml", ".svg"),
            (b'<svg onload="alert(1)"></svg>', "image/svg+xml", ".svg"),
            (b'<svg><a href="javascript:alert(1)"></svg>', "image/svg+xml", None),
            (b'<svg><foreignObject></foreignObject></svg>', "image/svg+xml", ".svg"),
            (b'<!doctype html><html><body><svg></svg></body></html>', "image/svg+xml", None),
        ]
        for index, (body, content_type, expected) in enumerate(cases):
            url = (
                "https://virtuallearning.instructure.com/assessment_questions/"
                f"1/files/{20 + index}/download?verifier=sniff"
            )
            with mock.patch(
                "bank_image_mirror._open_remote",
                return_value=_UrlOpenBody(body, content_type),
            ):
                relpath = mirror_bank_image_url(self.school, self.library_id, url)
            if expected is None:
                self.assertIsNone(relpath, content_type)
            else:
                self.assertTrue(str(relpath).endswith(expected), relpath)

    def test_second_student_refresh_does_not_fetch(self) -> None:
        """A refused image is downloaded once, then student refreshes stay local."""
        try:
            from bank_image_mirror import clear_bank_image_refusals
        except ImportError:
            from lms.bank_image_mirror import clear_bank_image_refusals

        remote = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "1/files/40/download?verifier=refresh"
        )
        calls = {"n": 0}

        def opener(*_args: object, **_kwargs: object) -> _UrlOpenBody:
            calls["n"] += 1
            return _UrlOpenBody(_LOGIN_HTML, "text/html")

        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-refresh",
            title="Dead graph",
            stem=f'<p>Refer to the graph.</p><img src="{remote}">',
        )
        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            self.school.import_mc_to_class_playlist(
                self.class_id,
                "M1",
                "C2",
                question_id,
                library_id=self.library_id,
                page_number=1,
                stage="round",
            )
            items = self.school.ensure_live_session_items(self.session_id)
            imported = next(
                row
                for row in items
                if str(row.get("item_id") or "") == f"bank-import-{question_id}"
            )
            self.school.publish_live_session_item(
                self.session_id, int(imported["id"])
            )
            with self.school.game._lock:
                student = self.school.game.conn.execute(
                    "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id ASC",
                    (self.class_id,),
                ).fetchone()
            student_id = int(student["id"])
            self.school.join_live_class_session(
                self.session_id, student_id, codename=str(student["codename"])
            )
            clear_bank_image_refusals()
            calls["n"] = 0
            self.school.student_live_items_payload(self.session_id, student_id)
            first = calls["n"]
            calls["n"] = 0
            self.school.student_live_items_payload(self.session_id, student_id)
            second = calls["n"]
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)

    def test_redirect_to_metadata_is_not_opened(self) -> None:
        """A redirect off the allowlist is refused before that hop is opened."""
        try:
            from bank_image_mirror import _Redirect, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import _Redirect, mirror_bank_image_url

        opened: list[str] = []

        def opener(request: object, timeout: float) -> _UrlOpenBody:
            target = str(getattr(request, "full_url", "") or "")
            opened.append(target)
            if "169.254.169.254" in target:
                return _UrlOpenBody(b"secret", "text/plain")
            raise _Redirect("https://169.254.169.254/latest/meta-data")

        url = (
            "https://virtuallearning.instructure.com/files/41/download?verifier=meta"
        )
        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            relpath = mirror_bank_image_url(self.school, self.library_id, url)
        self.assertIsNone(relpath)
        self.assertEqual(opened, [url])

    def test_redirect_cap_stops_before_a_fourth_hop(self) -> None:
        """Three redirects may be followed; the next one is not opened."""
        try:
            from bank_image_mirror import _Redirect, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import _Redirect, mirror_bank_image_url

        opened: list[str] = []

        def opener(request: object, timeout: float) -> _UrlOpenBody:
            opened.append(str(getattr(request, "full_url", "") or ""))
            raise _Redirect("https://virtuallearning.instructure.com/next")

        url = (
            "https://virtuallearning.instructure.com/files/42/download?verifier=hops"
        )
        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            relpath = mirror_bank_image_url(self.school, self.library_id, url)
        self.assertIsNone(relpath)
        self.assertEqual(len(opened), 4)

    def test_oversized_body_is_refused_once(self) -> None:
        """A body over the size cap is not stored and is not fetched again."""
        try:
            from bank_image_mirror import _MAX_IMAGE_BYTES, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import _MAX_IMAGE_BYTES, mirror_bank_image_url

        huge = b"\x89PNG\r\n\x1a\n" + (b"x" * _MAX_IMAGE_BYTES)
        calls = {"n": 0}

        def opener(*_args: object, **_kwargs: object) -> _UrlOpenBody:
            calls["n"] += 1
            return _UrlOpenBody(huge, "image/png")

        url = (
            "https://virtuallearning.instructure.com/files/43/download?verifier=huge"
        )
        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            self.assertIsNone(mirror_bank_image_url(self.school, self.library_id, url))
            self.assertEqual(calls["n"], 1)
            self.assertIsNone(mirror_bank_image_url(self.school, self.library_id, url))
            self.assertEqual(calls["n"], 1)
        rows = self.school.conn.execute(
            "SELECT source_url FROM bank_image_cache WHERE source_url = ?",
            (url,),
        ).fetchall()
        self.assertEqual(rows, [])

    def test_expired_refusal_fetches_again(self) -> None:
        """A refusal older than the TTL does not keep blocking the download."""
        import time

        try:
            from bank_image_mirror import _refusal_lock, _refusals, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import _refusal_lock, _refusals, mirror_bank_image_url

        url = (
            "https://virtuallearning.instructure.com/files/44/download?verifier=ttl"
        )
        calls = {"n": 0}

        def opener(*_args: object, **_kwargs: object) -> _UrlOpenBody:
            calls["n"] += 1
            return _UrlOpenBody(_LOGIN_HTML, "text/html")

        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            self.assertIsNone(mirror_bank_image_url(self.school, self.library_id, url))
            with _refusal_lock:
                _refusals[(self.library_id, url)] = time.monotonic() - 5
            self.assertIsNone(mirror_bank_image_url(self.school, self.library_id, url))
        self.assertEqual(calls["n"], 2)

    def test_mirrored_svg_stores_sanitised_bytes(self) -> None:
        """A bypass SVG is stored as the cleaned document, not the download."""
        try:
            from bank_image_mirror import mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import mirror_bank_image_url

        raw = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<h:script xmlns:h="http://www.w3.org/1999/xhtml">alert(1)</h:script>'
            b'<a href="javascript&#58;alert(1)">t</a>'
            b'<use href="https://external/evil.svg"/>'
            b'<path d="M4 4"/>'
            b"</svg>"
        )
        url = (
            "https://virtuallearning.instructure.com/files/70/download?verifier=svg"
        )
        with mock.patch(
            "bank_image_mirror._open_remote",
            return_value=_UrlOpenBody(raw, "image/svg+xml"),
        ):
            relpath = mirror_bank_image_url(self.school, self.library_id, url)
        self.assertTrue(str(relpath).endswith(".svg"))
        row = self.school.conn.execute(
            "SELECT blob_sha FROM bank_image_cache WHERE source_url = ?",
            (url,),
        ).fetchone()
        self.assertIsNotNone(row)
        blob = (self.school.data_dir / "blobs" / row["blob_sha"][:2] / row["blob_sha"]).read_bytes()
        self.assertNotEqual(blob, raw)
        lower = blob.lower()
        self.assertNotIn(b"<script", lower)
        self.assertNotIn(b"javascript", lower)
        self.assertNotIn(b"&#58;", lower)
        self.assertNotIn(b"external", lower)
        self.assertIn(b"M4 4", blob)

    def test_cached_svg_is_not_rewritten(self) -> None:
        """A cache hit leaves an already stored SVG blob untouched."""
        try:
            from bank_image_mirror import (
                ensure_bank_image_cache_schema,
                mirror_bank_image_url,
            )
        except ImportError:
            from lms.bank_image_mirror import (
                ensure_bank_image_cache_schema,
                mirror_bank_image_url,
            )

        hostile = (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )
        url = (
            "https://virtuallearning.instructure.com/files/71/download?verifier=oldsvg"
        )
        relpath = "web_resources/bank-mirror/already-stored.svg"
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(hostile, filename=relpath, mime="image/svg+xml")
        ensure_bank_image_cache_schema(self.school)
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.library_id, relpath, stored.sha256, _now()),
        )
        self.school.conn.execute(
            """
            INSERT INTO bank_image_cache (
                library_id, source_url, relpath, blob_sha, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (self.library_id, url, relpath, stored.sha256, _now()),
        )
        self.school.conn.commit()

        def opener(*_args: object, **_kwargs: object) -> _UrlOpenBody:
            raise AssertionError("cached svg must not be fetched")

        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            found = mirror_bank_image_url(self.school, self.library_id, url)
        self.assertEqual(found, relpath)
        row = self.school.conn.execute(
            "SELECT blob_sha FROM bank_image_cache WHERE source_url = ?",
            (url,),
        ).fetchone()
        self.assertEqual(str(row["blob_sha"]), stored.sha256)
        self.assertEqual(stored.path.read_bytes(), hostile)

    def test_network_refusal_expires_before_a_non_image(self) -> None:
        """A transport failure is remembered for about five minutes, not one hour."""
        import urllib.error

        try:
            from bank_image_mirror import mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import mirror_bank_image_url

        net_url = (
            "https://virtuallearning.instructure.com/files/72/download?verifier=net"
        )
        html_url = (
            "https://virtuallearning.instructure.com/files/73/download?verifier=html"
        )
        clock = {"now": 1_000_000.0}
        calls = {"net": 0, "html": 0}

        def opener(request: object, timeout: float) -> _UrlOpenBody:
            target = str(getattr(request, "full_url", "") or "")
            if "verifier=net" in target:
                calls["net"] += 1
                raise urllib.error.URLError("timed out")
            calls["html"] += 1
            return _UrlOpenBody(_LOGIN_HTML, "text/html")

        with mock.patch("bank_image_mirror.time.monotonic", lambda: clock["now"]):
            with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
                self.assertIsNone(
                    mirror_bank_image_url(self.school, self.library_id, net_url)
                )
                self.assertIsNone(
                    mirror_bank_image_url(self.school, self.library_id, html_url)
                )
                self.assertEqual(calls, {"net": 1, "html": 1})
                clock["now"] += (5 * 60) + 1
                self.assertIsNone(
                    mirror_bank_image_url(self.school, self.library_id, net_url)
                )
                self.assertIsNone(
                    mirror_bank_image_url(self.school, self.library_id, html_url)
                )
                self.assertEqual(calls, {"net": 2, "html": 1})
                clock["now"] += 3600
                self.assertIsNone(
                    mirror_bank_image_url(self.school, self.library_id, html_url)
                )
                self.assertEqual(calls["html"], 2)

    def test_transient_svg_failure_uses_the_short_refusal(self) -> None:
        """A writer failure is remembered for five minutes, not one hour.

        A confirmed non-image, including an SVG the parser rejects, still
        uses the hour-long refusal.
        """
        try:
            from bank_image_mirror import _serialize_svg, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import _serialize_svg, mirror_bank_image_url

        real_serialize = _serialize_svg
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        broken = b'<svg xmlns="http://www.w3.org/2000/svg"><path'
        transient_url = (
            "https://virtuallearning.instructure.com/files/90/download?verifier=transient"
        )
        broken_url = (
            "https://virtuallearning.instructure.com/files/91/download?verifier=broken"
        )
        clock = {"now": 2_000_000.0}
        calls = {"transient": 0, "broken": 0}
        state = {"fail": True}

        def serialize(elem):
            """Fail the first write, then use the real writer.

            Args:
                elem: Sanitised SVG element.
            """
            if state["fail"]:
                state["fail"] = False
                raise ValueError("transient writer failure")
            return real_serialize(elem)

        def opener(request: object, timeout: float) -> _UrlOpenBody:
            """Return one SVG body, or a truncated SVG, for this URL.

            Args:
                request: Outbound request.
                timeout: Unused socket timeout.
            """
            target = str(getattr(request, "full_url", "") or "")
            if "verifier=transient" in target:
                calls["transient"] += 1
                return _UrlOpenBody(svg, "image/svg+xml")
            calls["broken"] += 1
            return _UrlOpenBody(broken, "image/svg+xml")

        with mock.patch("bank_image_mirror.time.monotonic", lambda: clock["now"]):
            with mock.patch("bank_image_mirror._serialize_svg", side_effect=serialize):
                with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
                    self.assertIsNone(
                        mirror_bank_image_url(
                            self.school, self.library_id, transient_url
                        )
                    )
                    self.assertIsNone(
                        mirror_bank_image_url(self.school, self.library_id, broken_url)
                    )
                    self.assertEqual(calls, {"transient": 1, "broken": 1})
                    clock["now"] += (5 * 60) + 1
                    stored = mirror_bank_image_url(
                        self.school, self.library_id, transient_url
                    )
                    self.assertIsNotNone(stored)
                    self.assertIsNone(
                        mirror_bank_image_url(self.school, self.library_id, broken_url)
                    )
                    self.assertEqual(calls, {"transient": 2, "broken": 1})
                    clock["now"] += 3600
                    self.assertIsNone(
                        mirror_bank_image_url(self.school, self.library_id, broken_url)
                    )
                    self.assertEqual(calls["broken"], 2)

    def test_userinfo_and_backslash_redirects_are_not_opened(self) -> None:
        """A redirect that adds userinfo or a backslash is refused before the hop."""
        try:
            from bank_image_mirror import _Redirect, mirror_bank_image_url
        except ImportError:
            from lms.bank_image_mirror import _Redirect, mirror_bank_image_url

        opened: list[str] = []

        def opener(request: object, timeout: float) -> _UrlOpenBody:
            target = str(getattr(request, "full_url", "") or "")
            opened.append(target)
            if "verifier=user" in target:
                raise _Redirect(
                    "https://169.254.169.254@virtuallearning.instructure.com/a.png"
                )
            raise _Redirect(
                "https://169.254.169.254\\@virtuallearning.instructure.com/a.png"
            )

        user_url = (
            "https://virtuallearning.instructure.com/files/74/download?verifier=user"
        )
        slash_url = (
            "https://virtuallearning.instructure.com/files/75/download?verifier=slash"
        )
        with mock.patch("bank_image_mirror._open_remote", side_effect=opener):
            self.assertIsNone(
                mirror_bank_image_url(self.school, self.library_id, user_url)
            )
            self.assertIsNone(
                mirror_bank_image_url(self.school, self.library_id, slash_url)
            )
        self.assertEqual(opened, [user_url, slash_url])

    def test_string_choices_are_mirrored(self) -> None:
        """Answer choices stored as strings do not crash the library prefetch."""
        try:
            from bank_image_mirror import mirror_library_bank_images
        except ImportError:
            from lms.bank_image_mirror import mirror_library_bank_images

        url = (
            "https://virtuallearning.instructure.com/files/45/download?verifier=choice"
        )
        _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-string-choice",
            title="Choice image",
            stem="Plain stem",
            extra_payload={"choices": [f'<img src="{url}">', "plain"]},
        )
        with mock.patch(
            "bank_image_mirror._open_remote",
            side_effect=lambda *_args, **_kwargs: _UrlOpenBody(_TINY_PNG, "text/plain"),
        ):
            summary = mirror_library_bank_images(self.school, self.library_id)
        self.assertGreaterEqual(int(summary["mirrored"]), 1)
        self.assertEqual(int(summary["failed"]), 0)

    def test_import_creates_lifecycle_row_on_active_session(self) -> None:
        """Import during an active session seeds a new lifecycle placement."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        items = self.school.ensure_live_session_items_if_stale(self.session_id)
        imported = [
            row
            for row in items
            if str(row.get("item_id") or "").startswith("bank-import-")
        ]
        self.assertEqual(len(imported), 1)
        published = self.school.publish_live_session_item(
            self.session_id, int(imported[0]["id"])
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        payload = prompt.get("payload") if isinstance(prompt, dict) else {}
        self.assertEqual(str(payload.get("correct_answer") or payload.get("key")), "A")

    def test_overlay_edit_reflected_in_search(self) -> None:
        """Staff overlay edits change module search output without touching ingest."""
        self.school.upsert_library_question_overlay(
            self.library_id,
            self.m1_q,
            stem_text="Edited stem for import test",
            options=["Alpha", "Beta", "Gamma"],
            correct_answer="B",
            points=2.0,
        )
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "Edited")["items"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["text"], "Edited stem for import test")
        self.assertEqual(hits[0]["correct_answer"], "B")

    def test_move_import_changes_stage(self) -> None:
        """Imported MC can move to another page in the same lesson deck."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        result = self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=2,
        )
        self.assertEqual(result["action"], "moved")
        self.assertEqual(result["stage"], "teams")
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        moved = [
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == item_id
        ]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["stage"], "teams")

    def test_move_published_item_resets_unpublished_and_keeps_points(self) -> None:
        """A move lands unpublished, clears answers, and leaves session points."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == item_id
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        with self.school.game._lock:
            student = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchone()
        student_id = int(student["id"])
        self.school.game.begin_game(self.class_id)
        self.school.join_live_class_session(
            self.session_id, student_id, codename="Maple"
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), student_id, {"choice": "A"}
        )
        self.school.award_live_prompt_points(
            self.session_id,
            int(prompt["id"]),
            mode="manual",
            student_ids=[student_id],
            amount=3,
        )
        before_points = self.school.live_awarded_session_points(self.class_id)
        self.assertEqual(before_points.get(student_id), 3)
        self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=2,
        )
        after_points = self.school.live_awarded_session_points(self.class_id)
        self.assertEqual(after_points.get(student_id), 3)
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        moved = next(
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == item_id
        )
        self.assertEqual(moved["stage"], "teams")
        lifecycle = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == item_id
        )
        self.assertEqual(lifecycle["status"], "inactive")
        self.assertIsNone(lifecycle.get("published_at"))
        linked = self.school._prompt_for_live_item(lifecycle)
        if linked is not None:
            self.assertFalse(bool(linked.get("active")))
            self.assertEqual(
                self.school.list_live_prompt_responses(int(linked["id"])),
                [],
            )

    def test_remove_import_deletes_placement(self) -> None:
        """Removing an imported MC deletes its sqlite placement row."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", item_id
        )
        rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(rows, [])
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        imported = [
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "").startswith("bank-import-")
        ]
        self.assertEqual(imported, [])

    def test_remove_seed_question_hides_from_metadata(self) -> None:
        """Seed questions persist a hide override instead of editing git JSON."""
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        join_questions = [
            row
            for row in metadata.get("questions") or []
            if row.get("stage") == "join"
        ]
        self.assertTrue(join_questions)
        item_id = str(join_questions[0].get("id") or "")
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", item_id
        )
        after = self.school.live_class_metadata_for_session(self.session_id)
        remaining = [
            row
            for row in after.get("questions") or []
            if str(row.get("id") or "") == item_id
        ]
        self.assertEqual(remaining, [])

    def test_move_seed_question_changes_stage(self) -> None:
        """Seed questions can move to another page via sqlite override."""
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        join_questions = [
            row
            for row in metadata.get("questions") or []
            if row.get("stage") == "join"
        ]
        item_id = str(join_questions[0].get("id") or "")
        self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=3,
        )
        after = self.school.live_class_metadata_for_session(self.session_id)
        moved = [
            row
            for row in after.get("questions") or []
            if str(row.get("id") or "") == item_id
        ]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["stage"], "meet")

    def test_move_then_dest_page_cards_include_item(self) -> None:
        """After a move, dest metadata and dest-page cards keep the item."""

        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        item_id = f"bank-import-{self.m1_q}"
        self.school.ensure_live_session_items(self.session_id)
        dest_meta = self.school.live_class_metadata_for_class_lesson(
            self.class_id, "M1", "C2"
        )
        dest_pages = [
            row
            for row in dest_meta.get("pages") or []
            if isinstance(row, dict) and row.get("stage")
        ]
        dest = dest_pages[1]
        dest_stage = str(dest.get("stage") or "").strip().lower()
        dest_page = int(dest.get("page_number") or 2)
        self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            item_id,
            target_page_index=2,
        )
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        moved = next(
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == item_id
        )
        self.assertEqual(str(moved.get("stage") or ""), dest_stage)
        self.assertEqual(int(moved.get("page_number") or 0), dest_page)
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage=dest_stage,
            page_id=str(dest.get("id") or ""),
        )
        cards = self.school.live_session_question_cards(self.session_id)
        dest_card = next(
            row for row in cards if str(row.get("id") or "") == item_id
        )
        self.assertEqual(int(dest_card.get("page_number") or 0), dest_page)
        self.assertEqual(str(dest_card.get("stage") or ""), dest_stage)

    def test_add_staff_mc_on_current_page_unpublished(self) -> None:
        """Add New MC lands unpublished on the current overlay page."""

        placement = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Which parent is this?",
            page_number=4,
            stage="round",
            options=["Linear", "Quadratic", "Cubic", "Absolute"],
            correct_index=1,
            equation="y=x^2",
        )
        item = placement.get("item") or {}
        self.assertEqual(item.get("type"), "mc")
        self.assertEqual(item.get("options"), ["Linear", "Quadratic", "Cubic", "Absolute"])
        self.assertEqual(item.get("key"), "B")
        self.assertEqual(item.get("equation_latex"), "y=x^2")
        self.assertEqual(item.get("page_number"), 4)
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        added = next(
            row
            for row in metadata.get("questions") or []
            if str(row.get("id") or "") == str(item.get("id") or "")
        )
        self.assertEqual(added["page_number"], 4)
        lifecycle = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == str(item.get("id") or "")
        )
        self.assertEqual(lifecycle["status"], "inactive")

    def test_add_staff_numeric_and_poll_shapes(self) -> None:
        """Numeric stores tolerance; poll has no key."""

        numeric = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="numeric",
            text="Estimate the vertex y-value",
            page_number=2,
            stage="teams",
            correct_answer="4",
            tolerance=10,
            tolerance_kind="percent",
        )["item"]
        self.assertEqual(numeric.get("type"), "numeric")
        self.assertEqual(str(numeric.get("correct_answer")), "4")
        self.assertEqual(numeric.get("tolerance"), 10.0)
        self.assertEqual(numeric.get("tolerance_kind"), "percent")
        poll = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="poll",
            text="What do you notice?",
            page_number=2,
            stage="teams",
        )["item"]
        self.assertEqual(poll.get("type"), "poll")
        self.assertFalse(poll.get("key"))
        self.assertFalse(poll.get("correct_answer"))

    def test_add_staff_question_saves_course_wide_bank(self) -> None:
        """Course-wide bank save is importable from another module picker."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Course-wide staff MC",
            page_number=1,
            stage="join",
            options=["W", "X", "Y", "Z"],
            correct_index=2,
            save_to_bank=True,
            bank_scope="course",
            library_id=self.library_id,
        )
        source_id = int(placed.get("source_question_id") or 0)
        self.assertGreater(source_id, 0)
        m2_hits = self.school.search_module_bank_mcs(
            self.library_id, 2, "Course-wide staff"
        )["items"]
        self.assertTrue(
            any(int(row.get("question_id") or 0) == source_id for row in m2_hits)
        )
        m1_hits = self.school.search_module_bank_mcs(
            self.library_id, 1, "Course-wide staff"
        )["items"]
        self.assertTrue(
            any(int(row.get("question_id") or 0) == source_id for row in m1_hits)
        )

    def test_add_staff_question_saves_module_two_bank(self) -> None:
        """``M2`` / ``2`` bank scope links only that module."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Module two staff MC",
            page_number=1,
            stage="join",
            options=["W", "X", "Y", "Z"],
            correct_index=0,
            save_to_bank=True,
            bank_scope="M2",
            library_id=self.library_id,
        )
        source_id = int(placed.get("source_question_id") or 0)
        self.assertGreater(source_id, 0)
        m2_hits = self.school.search_module_bank_mcs(
            self.library_id, 2, "Module two staff"
        )["items"]
        self.assertTrue(
            any(int(row.get("question_id") or 0) == source_id for row in m2_hits)
        )
        m1_hits = self.school.search_module_bank_mcs(
            self.library_id, 1, "Module two staff"
        )["items"]
        self.assertFalse(
            any(int(row.get("question_id") or 0) == source_id for row in m1_hits)
        )
        self.assertEqual(
            self.school._bank_scope_module_numbers("2", 1),
            [2],
        )
        self.assertEqual(
            self.school._bank_scope_module_numbers("module", 3),
            [3],
        )

    def test_add_staff_question_strips_wrapping_equation_dollars(self) -> None:
        """Stored equation_latex is raw TeX without wrapping dollar signs."""

        self.assertEqual(self.school.strip_equation_latex("  $y=x^2$  "), "y=x^2")
        self.assertEqual(
            self.school.strip_equation_latex(r"$$\frac{a}{b}$$"), r"\frac{a}{b}"
        )
        self.assertEqual(self.school.strip_equation_latex("y=x^2"), "y=x^2")
        self.assertEqual(self.school.strip_equation_latex("$$"), "")
        item = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="poll",
            text="What does the graph show?",
            page_number=1,
            stage="round",
            equation="$y = x^{2}$",
        )["item"]
        self.assertEqual(item.get("equation_latex"), "y = x^{2}")
        self.assertNotIn("$", str(item.get("equation_latex") or ""))

    def test_import_search_excludes_warmup_unless_scoped(self) -> None:
        """Core Math hides warmup and Custom until Kind is scoped."""

        warmup_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-warmup",
            title="Icebreaker",
            stem="Notice and wonder about equal groups",
            extra_payload={"kind": "warmup"},
        )
        tagged_warmup_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-warmup-tag",
            title="Tagged opener",
            stem="Rotating opener about attendance",
            extra_payload={"tags": ["warmup"]},
        )
        contest_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-contest",
            title="Contest",
            stem="Ferris wheel first height",
            extra_payload={"kind": "contest"},
        )
        standard_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-standard",
            title="Standard",
            stem="Equal-group factor pairs",
            extra_payload={"kind": "standard"},
        )
        default_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(self.library_id, 1, "")[
                "items"
            ]
        }
        self.assertNotIn(warmup_id, default_ids)
        self.assertNotIn(tagged_warmup_id, default_ids)
        self.assertIn(contest_id, default_ids)
        self.assertNotIn(standard_id, default_ids)
        self.assertIn(self.m1_q, default_ids)
        standard_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="standard"
            )["items"]
        }
        self.assertEqual(standard_ids, {standard_id})
        custom_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="custom"
            )["items"]
        }
        self.assertEqual(custom_ids, {standard_id})
        warmup_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="warmup"
            )["items"]
        }
        self.assertEqual(warmup_ids, {warmup_id, tagged_warmup_id})
        contest_ids = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="contest"
            )["items"]
        }
        self.assertEqual(contest_ids, {contest_id})

    def test_course_bank_scope_search_is_unique_across_modules(self) -> None:
        """Course Wide search returns each linked question once."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="mc",
            text="Course scope dedupe MC",
            page_number=1,
            stage="join",
            options=["W", "X", "Y", "Z"],
            correct_index=1,
            save_to_bank=True,
            bank_scope="course",
            library_id=self.library_id,
        )
        source_id = int(placed.get("source_question_id") or 0)
        hits = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "Course scope dedupe"
        )
        ids = [int(row.get("question_id") or 0) for row in hits["items"]]
        self.assertEqual(ids.count(source_id), 1)
        self.assertEqual(hits["filtered"], 1)
        module_only = self.school.search_bank_scope_mcs(
            self.library_id, "M2", 1, "Course scope dedupe"
        )
        module_ids = [
            int(row.get("question_id") or 0) for row in module_only["items"]
        ]
        self.assertIn(source_id, module_ids)

    def test_course_wide_warmups_stay_out_of_module_import(self) -> None:
        """Kind=Warmup lists the locked stems; Process Kind hides them."""

        seeded = self.school.seed_course_wide_warmups(self.library_id)
        self.assertEqual(seeded["count"], 11)
        again = self.school.seed_course_wide_warmups(self.library_id)
        self.assertEqual(again["question_ids"], seeded["question_ids"])
        for module_number in range(1, 9):
            linked = {
                int(row["bank_id"])
                for row in self.school.list_module_bank_links(
                    self.library_id, module_number
                )
            }
            self.assertIn(int(seeded["bank_id"]), linked)
        hidden = self.school.search_bank_scope_mcs(self.library_id, "course", 1, "")
        hidden_ids = {int(row.get("question_id") or 0) for row in hidden["items"]}
        for question_id in seeded["question_ids"]:
            self.assertNotIn(question_id, hidden_ids)
        course = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="warmup"
        )
        stems = " ".join(str(row.get("text") or "") for row in course["items"])
        for needle in (
            "Aisle seat or window seat — pick one and defend it in one sentence.",
            "Texting or calling forever — pick one, no going back.",
            "Beach vacation or mountain cabin?",
            "What's the most overrated food that everyone else seems to love?",
            "that should honestly just be different.",
            "something completely unimportant?",
            "weirdly good at that has almost no real-world use?",
            "If your group had a mascot right now, what would it be?",
            "laugh way harder than it should have?",
            "never ridden a roller coaster? (Teacher swaps X.)",
            "slow wifi · wet socks · someone chewing loudly.",
        ):
            self.assertIn(needle, stems)
        self.assertEqual(str(course["items"][0].get("kind")), "warmup")
        self.assertEqual(str(course["items"][0].get("bank_scope")), "course")
        from course_warmup_seed import locked_course_warmup_titles

        warmup_titles = [
            str(row.get("question_title") or "")
            for row in course["items"]
            if str(row.get("bank_scope") or "") == "course"
        ]
        self.assertEqual(warmup_titles, list(locked_course_warmup_titles()))
        kind_warmup = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="warmup"
        )
        self.assertEqual(
            [str(row.get("question_title") or "") for row in kind_warmup["items"]],
            list(locked_course_warmup_titles()),
        )
        module_default = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(self.library_id, 1, "")[
                "items"
            ]
        }
        module_warmup = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "", kind="warmup"
            )["items"]
        }
        for question_id in seeded["question_ids"]:
            self.assertNotIn(question_id, module_default)
            self.assertIn(question_id, module_warmup)
        standard = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="standard"
        )
        standard_ids = {
            int(row.get("question_id") or 0) for row in standard["items"]
        }
        for question_id in seeded["question_ids"]:
            self.assertNotIn(question_id, standard_ids)
        aisle = next(
            row
            for row in course["items"]
            if "aisle seat or window seat" in str(row.get("text") or "").lower()
        )
        placed = self.school.import_mc_to_class_playlist(
            self.class_id,
            "M2",
            "C1",
            int(aisle["question_id"]),
            library_id=self.library_id,
            page_number=1,
            stage="join",
        )
        item = placed["item"]
        self.assertEqual(item.get("type"), "mc")
        self.assertEqual(item.get("options"), ["Aisle seat", "Window seat"])
        self.assertFalse(item.get("key"))
        self.assertFalse(item.get("correct_answer"))
        self.assertEqual(item.get("kind"), "warmup")

    def test_kind_warmup_seeds_locked_titles_from_live_problems(self) -> None:
        """Course Wide Kind=Warmup lists the 11 titles when only live_problems exist."""
        from course_warmup_seed import locked_course_warmup_titles
        from live_problem_seed import course_wide_warmups

        locked = list(locked_course_warmup_titles())
        for code in ("MCF3M", "MCR3U"):
            live_titles = [
                str(row["title"])
                for row in course_wide_warmups()
                if row["ontario_code"] == code
            ]
            self.assertEqual(live_titles, locked)
        self.assertEqual(self.school._course_wide_warmup_items(self.library_id), [])
        course = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="warmup"
        )
        self.assertEqual(
            [str(row.get("question_title") or "") for row in course["items"]],
            locked,
        )
        self.assertEqual(course["filtered"], 11)
        module = self.school.search_bank_scope_mcs(
            self.library_id, "M1", 1, "", kind="warmup"
        )
        module_titles = {
            str(row.get("question_title") or "") for row in module["items"]
        }
        for title in locked:
            self.assertIn(title, module_titles)
        mcf = self.school.create_library("MCF3M", origin="upload")
        mcf_hits = self.school.search_bank_scope_mcs(
            int(mcf["id"]), "course", 1, "", kind="warmup"
        )
        self.assertEqual(
            [str(row.get("question_title") or "") for row in mcf_hits["items"]],
            locked,
        )

    def test_kind_warmup_rewrites_near_miss_titles(self) -> None:
        """Course Wide search replaces the three 610899b short titles with the locked ones."""
        from course_warmup_seed import COURSE_WIDE_WARMUP_BANK_KEY, locked_course_warmup_titles

        near_miss = {
            "warmup-overrated-food": "Overrated food",
            "warmup-useless-skill": "Useless skill",
            "warmup-fraction-never": "Fraction who never did it",
        }
        locked = list(locked_course_warmup_titles())
        self.school.seed_course_wide_warmups(self.library_id)
        for import_key, short_title in near_miss.items():
            self.school.conn.execute(
                """
                UPDATE questions
                SET title = ?
                WHERE import_key = ?
                  AND bank_id IN (
                    SELECT id FROM question_banks
                    WHERE library_id = ? AND import_key = ?
                  )
                """,
                (short_title, import_key, self.library_id, COURSE_WIDE_WARMUP_BANK_KEY),
            )
        self.school.conn.commit()
        stale = self.school._course_wide_warmup_items(self.library_id)
        stale_titles = {str(row.get("question_title") or "") for row in stale}
        for short_title in near_miss.values():
            self.assertIn(short_title, stale_titles)
        course = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="warmup"
        )
        titles = [str(row.get("question_title") or "") for row in course["items"]]
        self.assertEqual(titles, locked)
        self.assertEqual(course["filtered"], 11)
        for short_title in near_miss.values():
            self.assertNotIn(short_title, titles)
        self.assertIn("Most overrated food", titles)
        self.assertIn("Weirdly useless skill", titles)
        self.assertIn("Fraction who never did X", titles)

    def _present_pair(self) -> tuple[int, int]:
        """Join Maple and Birch and put them on two manual teams.

        Group submission requires groups that already exist, and team
        setup rejects a single team.

        Returns:
            Roster ids ``(maple, birch)``.
        """
        self.school.game.add_student(self.class_id, codename="Birch")
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id ASC",
                (self.class_id,),
            ).fetchall()
        ids = [int(row["id"]) for row in rows]
        self.assertGreaterEqual(len(ids), 2)
        for student_id, name in zip(ids[:2], ("Maple", "Birch")):
            self.school.join_live_class_session(
                self.session_id, student_id, codename=name
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=ids[:2],
            assignments=[
                {"student_id": ids[0], "team_index": 0},
                {"student_id": ids[1], "team_index": 1},
            ],
        )
        return ids[0], ids[1]

    def _open_mcr3u_m1_c4_page(self, page_number: int) -> dict[str, Any]:
        """Point the live session at one MCR3U M1 C4 deck page.

        Page numbers follow the staff rail: stored ``page_number``, or the
        1-based index when the seed page omits it. C4 page 4 is Round 1.

        Args:
            page_number: Question-binding page, such as ``4``.

        Returns:
            The merged page dict the teacher is now standing on.
        """
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C4",
        )
        metadata = self.school.live_class_metadata_for_session(self.session_id)
        self.assertEqual(str(metadata.get("course") or ""), "MCR3U")
        self.assertEqual(str(metadata.get("live_class") or ""), "C4")
        pages = [
            row
            for row in metadata.get("pages") or []
            if isinstance(row, dict)
        ]
        page = next(
            (
                row
                for row in pages
                if int(row.get("page_number") or 0) == int(page_number)
            ),
            None,
        )
        self.assertIsNotNone(page, pages)
        assert page is not None
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C4",
            stage=str(page.get("stage") or ""),
            page_id=str(page.get("id") or ""),
        )
        return page

    def _publish_imported(
        self, question_id: int, *, publish_mode: str, page_number: int = 4
    ) -> dict[str, Any]:
        """Import one M1 bank MC onto an MCR3U M1 C4 page and publish it.

        Args:
            question_id: ``questions.id`` already in the M1 bank.
            publish_mode: Token passed to ``publish_live_session_item``.
            page_number: Staff rail page. Defaults to Shawn's page 4.

        Returns:
            The published lifecycle row.
        """
        page = self._open_mcr3u_m1_c4_page(page_number)
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C4",
            question_id,
            library_id=self.library_id,
            page_number=int(page_number),
            stage=str(page.get("stage") or ""),
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        return self.school.publish_live_session_item(
            self.session_id,
            int(imported["id"]),
            publish_mode=publish_mode,
        )

    def test_mcr3u_m1_c4_page4_math_mc_group_submit_reaches_student(self) -> None:
        """A math MC added to MCR3U M1 C4 page 4 is the student Group prompt.

        C4 page 4 is Round 1, so Questions view stays none. That used to drop
        the facing prompt and leave the student on the wait line. The same
        Group button still works for an icebreaker. The spaced ``group submit``
        alias must still store ``group_submit``.
        """
        maple, _birch = self._present_pair()
        published = self._publish_imported(self.m1_q, publish_mode="group submit")
        self.assertEqual(published["publish_mode"], "group_submit")
        self.assertEqual(published["response_mode"], "group_submit")
        self.assertEqual(int(published.get("page_number") or 0), 4)
        view = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(view["live_module"], "M1")
        self.assertEqual(view["live_slot"], "C4")
        self.assertEqual(view["page_id"], "round_1")
        self.assertEqual(view["student_view"]["questions"], "none")
        self.assertEqual(
            self.school._current_student_page_number(self.session_id), 4
        )
        payload = self.school.student_live_prompt_payload(self.session_id, maple)
        prompt = payload.get("prompt") or {}
        body = prompt.get("payload") or {}
        self.assertEqual(payload.get("question_view"), "none")
        self.assertIsNotNone(payload.get("prompt"))
        self.assertIn(
            "Factor x squared minus one",
            str(body.get("prompt") or body.get("text") or ""),
        )
        self.assertTrue(body.get("choices") or body.get("options"))
        card = next(
            row
            for row in payload.get("active_questions") or []
            if row.get("item_id") == f"bank-import-{self.m1_q}"
        )
        self.assertEqual(card["response_mode"], "group_submit")
        self.assertEqual(int(card.get("page_number") or 0), 4)
        self.assertEqual((card.get("group_submit") or {}).get("phase"), "drafting")
        state = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, maple
        )
        self.assertIsNotNone(state.get("prompt"))
        self.assertTrue(
            any(
                row.get("item_id") == f"bank-import-{self.m1_q}"
                and row.get("response_mode") == "group_submit"
                and int(row.get("page_number") or 0) == 4
                for row in state.get("active_questions") or []
            )
        )

    def test_individual_bank_mc_stays_on_student_round_stack(self) -> None:
        """Individual math MC on MCR3U M1 C4 page 4 still faces the student."""
        maple, _birch = self._present_pair()
        published = self._publish_imported(self.m1_q, publish_mode="individual")
        self.assertEqual(published["response_mode"], "individual")
        payload = self.school.student_live_prompt_payload(self.session_id, maple)
        self.assertIsNone(payload.get("prompt"))
        card = next(
            row
            for row in payload.get("active_questions") or []
            if row.get("item_id") == f"bank-import-{self.m1_q}"
        )
        self.assertEqual(card["response_mode"], "individual")
        self.assertIn(
            "Factor x squared minus one",
            str((card.get("content") or {}).get("text") or ""),
        )

    def test_mcr3u_m1_c4_page3_open_and_numeric_individual_in_group_is_live(
        self,
    ) -> None:
        """Page 3 open text and numeric keep Individual in Group, with a live feed.

        Rank already stores ``group_consensus``. The overrated-food poll and a
        staff numeric on the same Meet page only store ``individual``, but
        both must publish ``individual_in_group`` and show the member answer
        on the teacher light poll.
        """
        self.assertIn(
            "group_consensus",
            self.school._question_publish_modes(
                {
                    "type": "poll",
                    "publish_modes": ["individual"],
                    "options": [],
                }
            ),
        )
        self.assertIn(
            "group_consensus",
            self.school._question_publish_modes(
                {
                    "type": "numeric",
                    "publish_modes": ["individual"],
                    "integer_only": True,
                }
            ),
        )
        self.assertNotIn(
            "group_consensus",
            self.school._question_publish_modes(
                {
                    "id": "meet-team",
                    "type": "poll",
                    "publish_modes": ["individual"],
                }
            ),
        )
        self.assertNotIn(
            "group_consensus",
            self.school._question_publish_modes(
                {
                    "type": "mc",
                    "publish_modes": ["individual"],
                    "options": ["Aisle seat", "Window seat"],
                }
            ),
        )
        maple, _birch = self._present_pair()
        self.school.seed_course_wide_warmups(self.library_id)
        course = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="warmup"
        )
        rank = next(
            row
            for row in course["items"]
            if str(row.get("question_title") or "") == "Rank three annoyances"
        )
        food = next(
            row
            for row in course["items"]
            if str(row.get("question_title") or "") == "Most overrated food"
        )
        self.assertIn("Rank most", str(rank.get("text") or ""))
        page = self._open_mcr3u_m1_c4_page(3)
        self.assertEqual(page.get("id"), "meet")
        self.assertEqual(str(page.get("stage") or ""), "meet")
        for question_id in (int(rank["question_id"]), int(food["question_id"])):
            self.school.import_mc_to_class_playlist(
                self.class_id,
                "M1",
                "C4",
                question_id,
                library_id=self.library_id,
                page_number=3,
                stage="meet",
            )
        numeric = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C4",
            question_type="numeric",
            text="Enter a whole number.",
            page_number=3,
            stage="meet",
            correct_answer="4",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        by_item_id = {str(row.get("item_id") or ""): row for row in items}
        food_row = by_item_id[f"bank-import-{int(food['question_id'])}"]
        rank_row = by_item_id[f"bank-import-{int(rank['question_id'])}"]
        numeric_row = by_item_id[str(numeric.get("item_id") or "")]
        self.assertEqual(
            (food_row.get("item") or {}).get("publish_modes"),
            ["individual"],
        )
        self.assertEqual(food_row.get("response_mode"), "individual")
        self.assertIn(
            "group_consensus",
            self.school._question_publish_modes(food_row.get("item") or {}),
        )
        self.assertEqual(rank_row.get("response_mode"), "group_consensus")
        published: dict[str, dict[str, Any]] = {}
        for label, row in (
            ("food", food_row),
            ("rank", rank_row),
            ("numeric", numeric_row),
        ):
            published[label] = self.school.publish_live_session_item(
                self.session_id,
                int(row["id"]),
                publish_mode="individual_in_group",
            )
            self.assertEqual(published[label]["publish_mode"], "group_consensus")
            self.assertEqual(published[label]["response_mode"], "group_consensus")
            self.assertEqual(int(published[label].get("page_number") or 0), 3)
        self.school.ensure_live_session_items(self.session_id)
        stuck = self.school.get_live_session_item(
            self.session_id, int(published["food"]["id"])
        )
        self.assertEqual(stuck["response_mode"], "group_consensus")
        before = self.school.get_live_session_state(self.session_id, light=True)
        counts = before.get("lifecycle_response_counts") or {}
        self.assertEqual(counts.get(int(published["food"]["id"])), 0)
        self.assertEqual(counts.get(int(published["numeric"]["id"])), 0)
        quiet = self.school.live_session_item_results(
            self.session_id, int(published["food"]["id"])
        )
        self.assertEqual(quiet["response_count"], 0)
        self.assertGreaterEqual(int(quiet["eligible_count"]), 1)
        stamp_before = self.school.live_student_poll_stamp(
            self.session_id, self.class_id
        )
        self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["food"]["id"]),
            maple,
            {"text": "kale chips"},
        )
        self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["numeric"]["id"]),
            maple,
            {"value": 9},
        )
        self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["rank"]["id"]),
            maple,
            {"text": "wifi then socks"},
        )
        after = self.school.get_live_session_state(self.session_id, light=True)
        live_counts = after.get("lifecycle_response_counts") or {}
        self.assertEqual(live_counts.get(int(published["food"]["id"])), 1)
        self.assertEqual(live_counts.get(int(published["numeric"]["id"])), 1)
        self.assertEqual(live_counts.get(int(published["rank"]["id"])), 1)
        food_results = self.school.live_session_item_results(
            self.session_id, int(published["food"]["id"])
        )
        self.assertEqual(food_results["response_count"], 1)
        self.assertGreaterEqual(int(food_results["eligible_count"]), 1)
        food_answers = [
            answer
            for team in food_results["teams"]
            for answer in (team.get("member_answers") or [])
        ]
        self.assertIn("kale chips", food_answers)
        numeric_results = self.school.live_session_item_results(
            self.session_id, int(published["numeric"]["id"])
        )
        numeric_answers = [
            answer
            for team in numeric_results["teams"]
            for answer in (team.get("member_answers") or [])
        ]
        self.assertIn(9, numeric_answers)
        self.assertEqual(numeric_results["response_count"], 1)
        stamp_after = self.school.live_student_poll_stamp(
            self.session_id, self.class_id
        )
        self.assertNotEqual(stamp_before, stamp_after)

    def test_icebreaker_group_submit_still_reaches_student(self) -> None:
        """Course Wide warmup Group on MCR3U M1 C4 join still has a student prompt."""
        maple, _birch = self._present_pair()
        seeded = self.school.seed_course_wide_warmups(self.library_id)
        course = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "", kind="warmup"
        )
        aisle = next(
            row
            for row in course["items"]
            if "aisle seat or window seat" in str(row.get("text") or "").lower()
        )
        self._open_mcr3u_m1_c4_page(1)
        self.school.set_live_session_teacher_state(
            self.session_id,
            student_view={"questions": "student"},
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C4",
            int(aisle["question_id"]),
            library_id=self.library_id,
            page_number=1,
            stage="join",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{int(aisle['question_id'])}"
        )
        published = self.school.publish_live_session_item(
            self.session_id,
            int(imported["id"]),
            publish_mode="group_submit",
        )
        self.assertEqual(published["response_mode"], "group_submit")
        self.assertIn(int(aisle["question_id"]), seeded["question_ids"])
        payload = self.school.student_live_prompt_payload(self.session_id, maple)
        prompt = payload.get("prompt") or {}
        body = prompt.get("payload") or {}
        self.assertIsNotNone(payload.get("prompt"))
        self.assertIn("Aisle seat", str(body.get("prompt") or body.get("text") or ""))
        card = next(
            row
            for row in payload.get("active_questions") or []
            if row.get("response_mode") == "group_submit"
        )
        self.assertEqual(card["item_id"], f"bank-import-{int(aisle['question_id'])}")

    def test_question_kind_without_item_type_still_links_prompt(self) -> None:
        """Lifecycle kind question still links a prompt if item_type was dropped."""
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            self.m1_q,
            library_id=self.library_id,
            page_number=4,
            stage="round",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{self.m1_q}"
        )
        question = dict(imported.get("item") or {})
        question.pop("item_type", None)
        self.school.conn.execute(
            """
            UPDATE live_session_items
            SET item_json = ?, kind = 'question', prompt_id = NULL
            WHERE id = ?
            """,
            (json.dumps(question), int(imported["id"])),
        )
        self.school.conn.commit()
        refreshed = self.school.get_live_session_item(
            self.session_id, int(imported["id"])
        )
        prompt = self.school._ensure_prompt_for_live_item(refreshed)
        self.assertIsNotNone(prompt)
        assert prompt is not None
        self.assertEqual(prompt["kind"], "mc")

    def test_numeric_tolerance_scores_nearby_answers(self) -> None:
        """Absolute tolerance marks nearby numeric responses correct."""

        self.assertTrue(
            self.school._numeric_within_tolerance(10.4, 10, 0.5, "absolute")
        )
        self.assertFalse(
            self.school._numeric_within_tolerance(11, 10, 0.5, "absolute")
        )
        self.assertTrue(
            self.school._numeric_within_tolerance(105, 100, 10, "percent")
        )
        self.assertFalse(
            self.school._numeric_within_tolerance(120, 100, 10, "percent")
        )


class ModuleBankImportApiTests(unittest.TestCase):
    """HTTP endpoints for import and overlay edit."""

    def setUp(self) -> None:
        """Teacher-owned class with library, banks, and live session."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        self.m1_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Unit 1 Quiz",
            import_key="bank:unit1",
        )
        self.m2_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Module 2 Test",
            import_key="bank:m2",
        )
        self.m1_q = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-m1",
            title="Roots",
            stem="What is a square root?",
        )
        self.m2_q = _insert_mc_question(
            self.school,
            self.m2_bank,
            import_key="q-m2",
            title="Vertex",
            stem="Find the vertex of a parabola",
        )
        self.school.confirm_module_bank_links(
            self.library_id, 1, [self.m1_bank]
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self._login("teacher@gmail.com")

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        try:
            from bank_image_mirror import clear_bank_image_refusals
        except ImportError:
            from lms.bank_image_mirror import clear_bank_image_refusals
        clear_bank_image_refusals()
        self.school.close()
        self.tmp.cleanup()

    def _login(self, email: str) -> None:
        """Complete local staff login for API calls."""
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email(email)["verification_code"]
            },
        )

    def test_course_wide_warmup_kind_api_lists_locked_titles(self) -> None:
        """GET Course Wide Kind=Warmup returns the 11 titles for an MCR3U class."""
        from course_warmup_seed import locked_course_warmup_titles

        locked = list(locked_course_warmup_titles())
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/course/mc-search?kind=warmup"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        self.assertEqual(body.get("bank_scope"), "course")
        self.assertEqual(body.get("kind"), "warmup")
        self.assertEqual(body.get("count"), 11)
        self.assertEqual(
            [str(row.get("question_title") or "") for row in body.get("items") or []],
            locked,
        )
        module = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search?kind=warmup"
        )
        self.assertEqual(module.status_code, 200, module.get_json())
        module_body = module.get_json() or {}
        module_titles = {
            str(row.get("question_title") or "")
            for row in module_body.get("items") or []
        }
        for title in locked:
            self.assertIn(title, module_titles)

    def test_import_mc_api(self) -> None:
        """POST import-mc persists placement and returns item payload."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={
                "question_id": self.m1_q,
                "page_number": 4,
                "stage": "round",
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json()
        self.assertTrue(body.get("ok"))
        placement = body.get("placement") or {}
        self.assertEqual(placement.get("item_id"), f"bank-import-{self.m1_q}")
        rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(len(rows), 1)

    def test_import_rejects_unconfirmed_module_bank(self) -> None:
        """M2 question cannot import while only M1 banks are confirmed."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m2_q, "page_number": 1, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 404, rv.get_json())

    def test_overlay_patch_api(self) -> None:
        """PATCH overlay saves staff edits for one question."""
        rv = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-overlays/{self.m1_q}",
            json={
                "stem_text": "Patched stem",
                "options": ["One", "Two", "Three"],
                "correct_answer": "C",
                "points": 3,
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "Patched")["items"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["correct_answer"], "C")


    def test_import_mc_api_returns_live_metadata(self) -> None:
        """POST import-mc returns merged deck metadata with the new placement."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 2, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        meta = body.get("live_metadata") or {}
        ids = [row.get("id") for row in meta.get("questions") or []]
        self.assertIn(f"bank-import-{self.m1_q}", ids)

    def test_live_lesson_deck_api(self) -> None:
        """GET deck returns merged metadata for staff lesson editing."""
        self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 2, "stage": "round"},
        )
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/deck"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        ids = [
            row.get("id")
            for row in (rv.get_json() or {}).get("live_metadata", {}).get("questions") or []
        ]
        self.assertIn(f"bank-import-{self.m1_q}", ids)

    def test_relocate_playlist_item_post_api(self) -> None:
        """POST playlist-item removes an imported MC from the class deck."""
        item_id = f"bank-import-{self.m1_q}"
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 2, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/playlist-item",
            json={"item_id": item_id, "action": "remove"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertTrue((rv.get_json() or {}).get("ok"))
        rows = self.school.list_class_playlist_placements(
            self.class_id, "M1", "C2"
        )
        self.assertEqual(rows, [])

    def test_relocate_move_api_returns_live_metadata(self) -> None:
        """POST playlist-item move returns merged metadata with the dest page."""

        item_id = f"bank-import-{self.m1_q}"
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="round"
        )
        self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": self.m1_q, "page_number": 4, "stage": "round"},
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/playlist-item",
            json={"item_id": item_id, "target_page_index": 2},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("action"), "moved")
        questions = (body.get("live_metadata") or {}).get("questions") or []
        moved = next(
            row for row in questions if str(row.get("id") or "") == item_id
        )
        self.assertEqual(moved.get("stage"), "teams")
        self.assertIn("question_cards", body)

    def test_add_question_api_creates_unpublished_mc(self) -> None:
        """POST add-question creates an unpublished current-page MC."""

        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="round"
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={
                "type": "mc",
                "text": "API added MC",
                "options": ["A1", "A2", "A3", "A4"],
                "correct_index": 0,
                "page_number": 4,
                "stage": "round",
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        item = (body.get("placement") or {}).get("item") or {}
        self.assertEqual(item.get("type"), "mc")
        self.assertEqual(item.get("key"), "A")
        ids = [
            str(row.get("id") or "")
            for row in (body.get("live_metadata") or {}).get("questions") or []
        ]
        self.assertIn(str(item.get("id") or ""), ids)

    def _put_library_png(self, relpath: str = "web_resources/diagram.png") -> None:
        """Store one PNG in the class library blob store for image-route tests."""
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(_TINY_PNG, filename=relpath, mime="image/png")
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.library_id, relpath, stored.sha256, _now()),
        )
        self.school.conn.execute(
            """
            INSERT INTO module_outlines (
                library_id, import_key, title, position, created_at
            ) VALUES (?, 'mod1', 'Module 1', 1, ?)
            """,
            (self.library_id, _now()),
        )
        self.school.conn.commit()

    def _join_maple_student(self) -> Any:
        """Return a student test client after Maple joins the live session."""
        live = self.school.get_live_session(self.session_id)
        assert live is not None
        student = self.app.test_client()
        join = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302, join.headers.get("Location"))
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        return student

    def test_student_can_get_rewritten_bank_module_file(self) -> None:
        """Student GET of the rewritten module-files URL returns the graph."""
        self._put_library_png()
        question_id = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-graph-http",
            title="Graph HTTP",
            stem=(
                '<p>Refer to the graph.</p>'
                '<img src="$IMS-CC-FILEBASE$/diagram.png">'
            ),
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="round"
        )
        self.school.import_mc_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_id,
            library_id=self.library_id,
            page_number=1,
            stage="round",
        )
        items = self.school.ensure_live_session_items(self.session_id)
        imported = next(
            row
            for row in items
            if str(row.get("item_id") or "") == f"bank-import-{question_id}"
        )
        self.school.publish_live_session_item(
            self.session_id, int(imported["id"])
        )
        student = self._join_maple_student()
        state = student.get("/api/student/state").get_json() or {}
        cards = state.get("active_questions") or []
        self.assertTrue(cards, state)
        blob = json.dumps(cards[0].get("content") or {})
        public_url = (
            f"/api/classes/{self.class_id}/module-files/web_resources/diagram.png"
        )
        self.assertIn(public_url, blob)
        self.assertNotIn(f"/staff/class/{self.class_id}/module-files/", blob)
        rv = student.get(public_url)
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_data(), _TINY_PNG)
        blocked = student.get(
            f"/staff/class/{self.class_id}/module-files/web_resources/diagram.png"
        )
        self.assertIn(blocked.status_code, {302, 403})
        anon = self.app.test_client()
        self.assertEqual(anon.get(public_url).status_code, 403)

    def test_served_svg_keeps_stored_bytes_and_sends_sandbox_csp(self) -> None:
        """An SVG already on disk is served unchanged, with a sandbox policy."""
        hostile = (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )
        rel = "web_resources/bank-mirror/already-stored.svg"
        store = ContentBlobStore(self.school.data_dir, self.school)
        stored = store.put_bytes(hostile, filename=rel, mime="image/svg+xml")
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.library_id, rel, stored.sha256, _now()),
        )
        self.school.conn.commit()
        self._put_library_png()
        student = self._join_maple_student()
        public_url = f"/api/classes/{self.class_id}/module-files/{rel}"
        served = student.get(public_url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.get_data(), hostile)
        self.assertEqual(stored.path.read_bytes(), hostile)
        self.assertEqual(
            served.headers.get("Content-Security-Policy"),
            "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        )
        self.assertEqual(served.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(served.mimetype, "image/svg+xml")
        png_url = (
            f"/api/classes/{self.class_id}/module-files/web_resources/diagram.png"
        )
        png = student.get(png_url)
        self.assertEqual(png.status_code, 200)
        png_policy = png.headers.get("Content-Security-Policy") or ""
        self.assertIn("default-src 'self'", png_policy)
        self.assertNotIn("sandbox", png_policy)
        staff = self.client.get(
            f"/staff/class/{self.class_id}/module-files/{rel}"
        )
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff.get_data(), hostile)
        self.assertEqual(
            staff.headers.get("Content-Security-Policy"),
            "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        )
        self.assertEqual(staff.headers.get("X-Content-Type-Options"), "nosniff")

    def test_active_markup_extensions_get_the_sandbox_policy(self) -> None:
        """SVG bytes stored as xml, xhtml, html, or htm are sandboxed on both routes."""
        hostile = (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )
        student = self._join_maple_student()
        store = ContentBlobStore(self.school.data_dir, self.school)
        for suffix in (".xml", ".XML", ".xhtml", ".html", ".htm"):
            rel = f"web_resources/bank-mirror/active{suffix}"
            stored = store.put_bytes(hostile, filename=rel, mime="text/plain")
            self.school.conn.execute(
                """
                INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (self.library_id, rel, stored.sha256, _now()),
            )
            self.school.conn.commit()
            for root in (
                f"/api/classes/{self.class_id}/module-files/{rel}",
                f"/staff/class/{self.class_id}/module-files/{rel}",
            ):
                client = student if root.startswith("/api/") else self.client
                served = client.get(root)
                self.assertEqual(served.status_code, 200, root)
                self.assertEqual(served.get_data(), hostile, root)
                self.assertEqual(
                    served.headers.get("Content-Security-Policy"),
                    "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                    root,
                )
                self.assertEqual(
                    served.headers.get("X-Content-Type-Options"), "nosniff", root
                )
                self.assertNotEqual(served.mimetype, "image/svg+xml", root)
            self.assertEqual(stored.path.read_bytes(), hostile)

    def test_raw_svgz_is_not_labelled_gzip(self) -> None:
        """A raw SVG named .svgz is sandboxed and is not sent as gzip."""
        import gzip

        raw = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        student = self._join_maple_student()
        store = ContentBlobStore(self.school.data_dir, self.school)
        rel = "web_resources/bank-mirror/plain.svgz"
        stored = store.put_bytes(raw, filename=rel, mime="image/svg+xml")
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.library_id, rel, stored.sha256, _now()),
        )
        packed = gzip.compress(raw)
        gzip_rel = "web_resources/bank-mirror/packed.svgz"
        gzip_stored = store.put_bytes(packed, filename=gzip_rel, mime="image/svg+xml")
        self.school.conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.library_id, gzip_rel, gzip_stored.sha256, _now()),
        )
        self.school.conn.commit()
        plain = student.get(f"/api/classes/{self.class_id}/module-files/{rel}")
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(plain.get_data(), raw)
        self.assertEqual(plain.mimetype, "image/svg+xml")
        self.assertNotEqual(plain.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(
            plain.headers.get("Content-Security-Policy"),
            "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        )
        packed_response = student.get(
            f"/api/classes/{self.class_id}/module-files/{gzip_rel}"
        )
        self.assertEqual(packed_response.status_code, 200)
        self.assertEqual(packed_response.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(packed_response.mimetype, "image/svg+xml")

    def test_student_can_get_live_question_image(self) -> None:
        """Staff-authored live-question-images are GET-able by the student."""
        stored = self.school.store_live_question_image(
            self.class_id, _TINY_PNG, filename="graph.png"
        )
        student = self._join_maple_student()
        rv = student.get(stored["image_url"])
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_data(), _TINY_PNG)
        anon = self.app.test_client()
        self.assertEqual(anon.get(stored["image_url"]).status_code, 403)


if __name__ == "__main__":
    unittest.main()
