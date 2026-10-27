#!/usr/bin/env python3
"""MCK-322: staff can delete one course slide deck without touching another."""

from __future__ import annotations

import os
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

from app import create_app  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\n" + b"shared-slide-page"
_PPTX = b"PK\x03\x04" + b"slide-source-bytes"


class CourseSlideDeleteTests(unittest.TestCase):
    """DELETE /api/classes/<id>/slide-deck removes one placement."""

    def setUp(self) -> None:
        """Two teachers, two classes, and one live run on the first class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.owner = self.school.register_staff("owner-staff@example.com")
        self.other = self.school.register_staff("other-staff@example.com")
        owner_offering = self.school.assign_course(
            teacher_user_id=int(self.owner["id"]), ontario_code="MCF3M"
        )
        other_offering = self.school.assign_course(
            teacher_user_id=int(self.other["id"]), ontario_code="MCR3U"
        )
        self.staff = self.app.test_client()
        self._login(self.staff, "owner-staff@example.com")
        self.other_client = self.app.test_client()
        self._login(self.other_client, "other-staff@example.com")
        self.class_id = self._class(self.staff, int(owner_offering["id"]), "Maple")
        self.other_class_id = self._class(
            self.other_client, int(other_offering["id"]), "Birch"
        )
        live = self.school.start_live_class_session(
            self.class_id, int(self.owner["id"])
        )
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C1", stage="round"
        )

    def tearDown(self) -> None:
        """Close sqlite and remove the temp directory."""
        self.school.close()
        self.tmp.cleanup()

    def _login(self, client, email: str) -> None:
        """Sign one staff client in through the local verification form.

        Args:
            client: Flask test client.
            email: Staff account already registered in this database.
        """
        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=T")
        client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email(email)["verification_code"]},
        )

    def _class(self, client, offering_id: int, codename: str) -> int:
        """Create one class as the signed-in teacher and return its id.

        Args:
            client: Staff test client that owns the offering.
            offering_id: Course offering id.
            codename: Roster codename. Not a student name.
        """
        created = client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering_id,
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": [codename],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        return int(created.get_json()["class"]["id"])

    def _page_path(self, class_id: int, module: str, slot: str) -> Path:
        """Return the on-disk page file for one course deck.

        Args:
            class_id: Game-show class id.
            module: Live module.
            slot: Live slot.
        """
        row = self.school.conn.execute(
            """
            SELECT pages.image_relpath
            FROM course_slide_pages AS pages
            JOIN course_slide_decks AS decks ON decks.id = pages.deck_id
            WHERE decks.class_id = ? AND decks.module = ? AND decks.slot = ?
            """,
            (int(class_id), module, slot),
        ).fetchone()
        self.assertIsNotNone(row)
        return Path(self.school.data_dir) / str(row["image_relpath"])

    def test_owner_can_delete_one_deck_and_keep_shared_files(self) -> None:
        """The owner removes one placement. A shared page and another class stay."""
        self.school.save_course_slide_deck(
            self.class_id,
            "M1",
            "C1",
            title="Graphs",
            source="pptx",
            page_pngs=[_PNG],
            source_bytes=_PPTX,
            source_suffix=".pptx",
        )
        self.school.save_course_slide_deck(
            self.class_id,
            "M1",
            "C2",
            title="Tables",
            source="public_export",
            page_pngs=[_PNG],
        )
        self.school.save_course_slide_deck(
            self.other_class_id,
            "M1",
            "C1",
            title="Other class",
            source="public_export",
            page_pngs=[_PNG],
        )
        shared = self._page_path(self.class_id, "M1", "C1")
        other_page = self._page_path(self.other_class_id, "M1", "C1")
        source = self.school.conn.execute(
            """
            SELECT source_relpath FROM course_slide_decks
            WHERE class_id = ? AND module = 'M1' AND slot = 'C1'
            """,
            (self.class_id,),
        ).fetchone()
        source_path = Path(self.school.data_dir) / str(source["source_relpath"])
        self.assertTrue(shared.is_file())
        self.assertTrue(source_path.is_file())
        bound = self.school.conn.execute(
            "SELECT google_slides_json FROM live_class_sessions WHERE id = ?",
            (self.session_id,),
        ).fetchone()
        self.assertIn("course_deck_id", str(bound["google_slides_json"]))
        page = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertIn('id="set-class-slides-remove"', page.get_data(as_text=True))
        forbidden = self.other_client.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertIsNotNone(
            self.school.course_slide_deck_summary(self.class_id, "M1", "C1")
        )
        anon = self.app.test_client()
        self.assertEqual(
            anon.delete(
                f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
            ).status_code,
            401,
        )
        removed = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        body = removed.get_json() or {}
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("deleted"))
        self.assertEqual(body.get("page_count"), 1)
        self.assertIsNone(body.get("deck"))
        self.assertNotIn("source_relpath", body)
        self.assertIsNone(
            self.school.course_slide_deck_summary(self.class_id, "M1", "C1")
        )
        kept = self.school.course_slide_deck_summary(self.class_id, "M1", "C2")
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept["page_count"], 1)
        self.assertEqual(kept["title"], "Tables")
        other = self.school.course_slide_deck_summary(self.other_class_id, "M1", "C1")
        self.assertIsNotNone(other)
        assert other is not None
        self.assertEqual(other["title"], "Other class")
        self.assertTrue(shared.is_file())
        self.assertTrue(other_page.is_file())
        self.assertFalse(source_path.exists())
        cleared = self.school.conn.execute(
            "SELECT google_slides_json FROM live_class_sessions WHERE id = ?",
            (self.session_id,),
        ).fetchone()
        self.assertEqual(cleared["google_slides_json"], "")
        again = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
        )
        self.assertEqual(again.status_code, 200)
        self.assertFalse((again.get_json() or {}).get("deleted"))
        self.assertTrue(shared.is_file())
        second = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C2"
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue((second.get_json() or {}).get("deleted"))
        self.assertFalse(shared.exists())
        self.assertTrue(other_page.is_file())
        refused = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=NOPE&slot=C1"
        )
        self.assertEqual(refused.status_code, 400)

    def test_cross_origin_delete_is_refused(self) -> None:
        """A DELETE from another site does not remove the deck."""
        self.school.save_course_slide_deck(
            self.class_id,
            "M1",
            "C1",
            title="Graphs",
            source="public_export",
            page_pngs=[_PNG],
        )
        blocked = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1",
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertIsNotNone(
            self.school.course_slide_deck_summary(self.class_id, "M1", "C1")
        )
        allowed = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1",
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(allowed.status_code, 200, allowed.get_json())
        self.assertTrue((allowed.get_json() or {}).get("deleted"))

    def test_page_count_counts_deleted_rows(self) -> None:
        """Two page rows that share one file count as two."""
        self.school.save_course_slide_deck(
            self.class_id,
            "M1",
            "C1",
            title="Graphs",
            source="public_export",
            page_pngs=[_PNG],
        )
        deck = self.school.conn.execute(
            """
            SELECT id FROM course_slide_decks
            WHERE class_id = ? AND module = 'M1' AND slot = 'C1'
            """,
            (self.class_id,),
        ).fetchone()
        page = self.school.conn.execute(
            "SELECT image_relpath FROM course_slide_pages WHERE deck_id = ?",
            (int(deck["id"]),),
        ).fetchone()
        self.school.conn.execute(
            """
            INSERT INTO course_slide_pages (deck_id, page_index, image_relpath)
            VALUES (?, 1, ?)
            """,
            (int(deck["id"]), str(page["image_relpath"])),
        )
        self.school.conn.commit()
        removed = self.staff.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        self.assertEqual((removed.get_json() or {}).get("page_count"), 2)

    def test_remove_slides_asks_before_a_live_class(self) -> None:
        """The Set Class button confirms only while this class is live."""
        text = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        start = text.index("async function removeSetClassSlides")
        chunk = text[start:start + 1200]
        self.assertIn("dataset.liveCode", chunk)
        self.assertIn("window.confirm", chunk)
        self.assertIn(
            "This class is live. Remove the slide deck for this placement?",
            chunk,
        )


if __name__ == "__main__":
    unittest.main()
