#!/usr/bin/env python3
"""Shared library attach: IT upload, thin instances, no staff upload UI."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402


def _minimal_imscc_bytes() -> bytes:
    """Return a tiny Common Cartridge with one module and one wiki page."""
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1" identifier="man1">
  <resources>
    <resource identifier="wiki1" type="webcontent" href="wiki_content/lesson-1.html"/>
  </resources>
</manifest>
"""
    modules = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="m1">
    <title>Module 1: Start</title>
    <workflow_state>active</workflow_state>
    <position>1</position>
    <items>
      <item identifier="i1">
        <title>Lesson 1</title>
        <content_type>WikiPage</content_type>
        <workflow_state>active</workflow_state>
        <position>1</position>
        <identifierref>wiki1</identifierref>
      </item>
    </items>
  </module>
</modules>
"""
    page = """<!DOCTYPE html>
<html><head><title>Lesson 1</title></head>
<body><h1>Lesson 1</h1><p>Uploaded pack body.</p></body></html>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("imsmanifest.xml", manifest)
        archive.writestr("course_settings/module_meta.xml", modules)
        archive.writestr("wiki_content/lesson-1.html", page)
    return buf.getvalue()


class ModulePackTests(unittest.TestCase):
    """IT-owned library upload vs preloaded template; staff has no upload card."""

    def setUp(self) -> None:
        """Isolated sqlite, one empty-course class, one MCF3M class."""
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
        if self.school.get_ontario_course("SBI3U") is None:
            self.school.upsert_ontario_course(
                "SBI3U",
                "Biology, Grade 11, University Preparation",
                grade=11,
                pathway="U",
                expectations_status="unverified",
            )
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.eng_offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI3U"
        )
        self.mcf_offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.eng_class = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="SBI3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.eng_offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.mcf_class = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="T/Th/F",
            time_label="2:00pm",
            codenames=["Aspen"],
            offering_id=int(self.mcf_offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post(
            "/verify-email", data={"code": user["verification_code"]}
        )

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_it(self) -> None:
        """Switch the test client to the bootstrap IT user."""
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def test_staff_course_has_no_upload_card(self) -> None:
        """Staff course UI never shows Upload Module Pack (any course code)."""
        for class_id in (self.mcf_class["id"], self.eng_class["id"]):
            rv = self.client.get(f"/staff/class/{class_id}")
            self.assertEqual(rv.status_code, 200)
            html = rv.get_data(as_text=True)
            self.assertNotIn("Upload Module Pack", html)
            self.assertNotIn("module-pack-form", html)

    def test_staff_cannot_upload_module_pack(self) -> None:
        """Staff POST to the leftover upload route is rejected."""
        payload = _minimal_imscc_bytes()
        rv = self.client.post(
            f"/staff/class/{self.eng_class['id']}/module-pack",
            data={"module_pack": (io.BytesIO(payload), "english.imscc")},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 403)
        self.assertIn("Ask Admin to attach a module pack", rv.get_data(as_text=True))
        offering = self.school.get_offering(int(self.eng_offering["id"]))
        self.assertFalse(offering.get("imscc_path"))

    def test_it_upload_installs_shared_library(self) -> None:
        """IT upload on SBI3U creates one library; Modules + Syllabus load."""
        empty_nav = self.client.get(
            f"/api/staff/class/{self.eng_class['id']}/modules"
        )
        body = empty_nav.get_json()
        self.assertTrue(body.get("empty"))
        self.assertIn("Ask Admin to attach a module pack", body.get("message") or "")

        self._login_it()
        payload = _minimal_imscc_bytes()
        rv = self.client.post(
            f"/it/offerings/{self.eng_offering['id']}/module-pack",
            data={"module_pack": (io.BytesIO(payload), "english.imscc")},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        offering = self.school.get_offering(int(self.eng_offering["id"]))
        self.assertTrue(offering.get("library_id"))
        self.assertTrue(offering.get("imscc_path"))
        stored = Path(offering["imscc_path"])
        self.assertTrue(stored.is_file())
        self.assertIn("libraries", stored.parts)
        self.assertEqual(stored.name, "course.imscc")
        inst = Path(self.tmp.name) / offering["instance_relpath"]
        self.assertFalse((inst / "pack" / "course.imscc").exists())

        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

        dash = self.client.get(f"/staff/class/{self.eng_class['id']}")
        self.assertNotIn("Upload Module Pack", dash.get_data(as_text=True))

        nav = self.client.get(f"/api/staff/class/{self.eng_class['id']}/modules")
        body = nav.get_json()
        self.assertTrue(body.get("ok"))
        self.assertFalse(body.get("empty"))
        self.assertEqual(body["modules"][0]["title"], "Module 1: Start")

        first = body["modules"][0]["items"][0]
        self.assertEqual(first["component_type"], "page")
        item = self.client.get(
            f"/staff/class/{self.eng_class['id']}/module-item?item={first['id']}"
        )
        self.assertEqual(item.status_code, 200)
        self.assertIn("Uploaded pack body", item.get_data(as_text=True))

        editor = self.client.get(
            f"/staff/class/{self.eng_class['id']}/syllabus/editor"
        )
        self.assertEqual(editor.status_code, 200)
        html_out = editor.get_data(as_text=True)
        self.assertNotIn("Ask Admin to attach a module pack", html_out)
        self.assertIn("Lesson 1", html_out)

    def test_two_teachers_share_uploaded_library(self) -> None:
        """Second teacher of a no-template code shares the IT-uploaded library."""
        self._login_it()
        payload = _minimal_imscc_bytes()
        self.client.post(
            f"/it/offerings/{self.eng_offering['id']}/module-pack",
            data={"module_pack": (io.BytesIO(payload), "english.imscc")},
        )
        first = self.school.get_offering(int(self.eng_offering["id"]))
        other = self.school.register_staff("other@gmail.com")
        second = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="SBI3U"
        )
        self.assertEqual(first["library_id"], second["library_id"])
        first_root = Path(self.tmp.name) / first["instance_relpath"]
        second_root = Path(self.tmp.name) / second["instance_relpath"]
        self.assertNotEqual(first_root, second_root)
        self.assertFalse((first_root / "pack" / "course.imscc").exists())
        self.assertFalse((second_root / "pack" / "course.imscc").exists())
        imsccs = list(Path(self.tmp.name).rglob("*.imscc"))
        self.assertEqual(len(imsccs), 1)
        self.assertIn("libraries", imsccs[0].parts)

    def test_rejects_non_imscc_upload(self) -> None:
        """A random text file is not stored as a library."""
        self._login_it()
        rv = self.client.post(
            f"/it/offerings/{self.eng_offering['id']}/module-pack",
            data={"module_pack": (io.BytesIO(b"not a zip"), "notes.txt")},
            follow_redirects=True,
        )
        self.assertEqual(rv.status_code, 400)
        self.assertIn("Module pack must be a .imscc", rv.get_data(as_text=True))
        offering = self.school.get_offering(int(self.eng_offering["id"]))
        self.assertFalse(offering.get("imscc_path"))
        self.assertFalse(offering.get("library_id"))

    def test_assert_imscc_within_size_skips_when_unlimited(self) -> None:
        """Optional byte ceiling is skipped when ``max_bytes`` is None."""
        from modules import assert_imscc_within_size

        path = Path(self.tmp.name) / "big.imscc"
        path.write_bytes(b"PK" + (b"x" * 2048))
        assert_imscc_within_size(path, None)
        with self.assertRaises(ValueError) as raised:
            assert_imscc_within_size(path, 100)
        self.assertIn("too large", str(raised.exception))

    def test_it_module_pack_status_idle_then_unpacking(self) -> None:
        """IT status starts idle and reflects a written install_status.json."""
        from instances import library_root
        from modules import read_pack_status, write_pack_status

        self._login_it()
        rv = self.client.get(
            f"/it/offerings/{self.eng_offering['id']}/module-pack/status"
        )
        self.assertEqual(rv.status_code, 200)
        idle = rv.get_json()
        self.assertEqual(idle["stage"], "idle")
        self.assertFalse(idle["busy"])

        created = self.school.create_library("SBI3U", origin="upload")
        self.school.attach_library(int(self.eng_offering["id"]), int(created["id"]))
        dest = library_root(Path(self.tmp.name), int(created["id"]))
        write_pack_status(
            dest,
            stage="unpacking",
            detail="Unpacking Common Cartridge… this can take a few minutes",
        )
        busy = self.client.get(
            f"/it/offerings/{self.eng_offering['id']}/module-pack/status"
        ).get_json()
        self.assertEqual(busy["stage"], "unpacking")
        self.assertTrue(busy["busy"])
        self.assertIn("Unpacking Common Cartridge", busy["detail"])
        disk = read_pack_status(dest)
        self.assertEqual(disk["stage"], "unpacking")

    def test_json_upload_installs_and_reports_redirect(self) -> None:
        """XHR IT upload (tests run install synchronously) returns JSON."""
        self._login_it()
        payload = _minimal_imscc_bytes()
        rv = self.client.post(
            f"/it/offerings/{self.eng_offering['id']}/module-pack",
            data={"module_pack": (io.BytesIO(payload), "english.imscc")},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertTrue(body.get("ok"))
        self.assertFalse(body.get("installing"))
        self.assertIn("pack=ok", body.get("redirect") or "")
        self.assertIn(
            f"/it/offerings/{self.eng_offering['id']}/module-pack/status",
            body.get("status_url") or "",
        )
        offering = self.school.get_offering(int(self.eng_offering["id"]))
        self.assertTrue(offering.get("library_id"))
        status = self.client.get(
            f"/it/offerings/{self.eng_offering['id']}/module-pack/status"
        ).get_json()
        self.assertEqual(status["stage"], "done")

    def test_mcf3m_assign_does_not_write_git_or_instance_pack(self) -> None:
        """Assign without a library is thin: instance only, no pack fork."""
        offering = self.school.get_offering(int(self.mcf_offering["id"]))
        rel = offering.get("instance_relpath")
        self.assertTrue(rel)
        inst = Path(self.tmp.name) / rel
        self.assertTrue(inst.is_dir())
        self.assertTrue((inst / "manifest.json").is_file())
        self.assertFalse((inst / "pack" / "course.imscc").exists())
        # School repo has no git template pack — Admin upload creates libraries.
        self.assertIsNone(offering.get("library_id"))
        self.assertFalse(offering.get("imscc_path"))
        self.assertEqual(list(Path(self.tmp.name).rglob("*.imscc")), [])

    def test_legacy_module_packs_still_resolves_without_delete(self) -> None:
        """Leftover ``module_packs/<id>`` still resolves; ensure does not delete it."""
        from instances import legacy_module_pack_root
        from modules import resolve_module_pack

        oid = int(self.eng_offering["id"])
        rel = self.eng_offering.get("instance_relpath")
        legacy = legacy_module_pack_root(Path(self.tmp.name), oid)
        legacy.mkdir(parents=True, exist_ok=True)
        payload = _minimal_imscc_bytes()
        (legacy / "course.imscc").write_bytes(payload)
        pack = resolve_module_pack(
            "SBI3U",
            str(legacy / "course.imscc"),
            data_dir=Path(self.tmp.name),
            offering_id=oid,
            instance_relpath=None,
        )
        self.assertIsNotNone(pack.imscc)
        self.assertEqual(pack.imscc, legacy / "course.imscc")

        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE course_offerings
                SET instance_relpath = NULL, imscc_path = ?, library_id = NULL
                WHERE id = ?
                """,
                (str(legacy / "course.imscc"), oid),
            )
            self.school.conn.commit()
        updated = self.school.ensure_offering_instance(self.school.get_offering(oid))
        self.assertTrue(updated.get("instance_relpath"))
        self.assertTrue(updated.get("library_id"))
        self.assertTrue(legacy.exists())
        dest = Path(self.tmp.name) / updated["instance_relpath"] / "pack" / "course.imscc"
        self.assertFalse(dest.exists())
        if rel:
            leftover_inst = Path(self.tmp.name) / rel
            self.assertTrue(leftover_inst.exists() or updated["instance_relpath"] == rel)


def is_template_or_library(path: Path) -> bool:
    """True when a stored IMSCC is a git template or a shared library file."""
    parts = path.parts
    return "courses" in parts or "libraries" in parts


if __name__ == "__main__":
    unittest.main(verbosity=2)
