"""MCK-341: Run Live Class setup reads the section's current module.

Setup does not write ``classes.current_live_module``. A module picked for
one session stays on that session.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from live_class_packs import live_class_registry  # noqa: E402
from set_class import (  # noqa: E402
    chapters_for_module,
    default_setup_class,
    registry_with_setup_module,
)
from test_mck339_begin_module import (  # noqa: E402
    _seed_held_class,
    _sign_in,
)
from test_participation_mck198 import Base  # noqa: E402

CATALOGUE = [("M1", "C1"), ("M1", "C2"), ("M1", "C3"), ("M1", "C4"), ("M2", "C1")]


def _record_run(school, class_id: int, module: str, slot: str, ended_at: str) -> None:
    """Insert one real Lesson Slides row for Set Class.

    Args:
        school: Open ``SchoolDB``.
        class_id: Section id.
        module: Module token.
        slot: Class slot token.
        ended_at: Stored end time. Later values win.
    """
    school.conn.execute(
        """
        INSERT INTO lesson_slides_latest (
            class_id, live_module, live_slot, run_key, ended_at,
            questions_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, '[]', ?)
        """,
        (
            int(class_id),
            module,
            slot,
            f"run-{int(class_id)}-{module}{slot}",
            ended_at,
            ended_at,
        ),
    )
    school.conn.commit()


def _stored_module(school, class_id: int):
    """Raw ``classes.current_live_module`` for one section.

    Args:
        school: Open ``SchoolDB``.
        class_id: Section id.

    Returns:
        The stored value, including None when the section has no pointer.
    """
    row = school.conn.execute(
        "SELECT current_live_module FROM classes WHERE id = ?",
        (int(class_id),),
    ).fetchone()
    if row is None:
        return None
    return row["current_live_module"]


class SetupDefaultRulesTests(unittest.TestCase):
    """Pure rules: stored module, first chapter, and a one-session pick."""

    def test_empty_pointer_still_follows_the_last_class(self) -> None:
        self.assertEqual(
            default_setup_class(CATALOGUE, ("M1", "C2")),
            ("M1", "C3"),
        )

    def test_stored_module_starts_at_its_first_chapter(self) -> None:
        self.assertEqual(
            default_setup_class(
                CATALOGUE,
                ("M1", "C2"),
                current_module="M2",
                current_chapters=["C1", "C2", "C3", "C4"],
            ),
            ("M2", "C1"),
        )

    def test_a_later_class_in_the_stored_module_advances_one_chapter(self) -> None:
        self.assertEqual(
            default_setup_class(
                CATALOGUE,
                ("M6", "C1"),
                current_module="M2",
                run_in_current=("M2", "C1"),
                current_chapters=["C1", "C2", "C3", "C4"],
            ),
            ("M2", "C2"),
        )

    def test_unpublished_module_uses_the_section_plan(self) -> None:
        chapters = chapters_for_module(
            "M12",
            CATALOGUE,
            [("M12", ["C1", "C2", "C3", "C4"])],
        )
        self.assertEqual(chapters, ["C1", "C2", "C3", "C4"])
        registry = {"modules": ["M1"], "slots_by_module": {"M1": ["C1", "C2"]}}
        copied = registry_with_setup_module(registry, "M12", chapters)
        self.assertEqual(registry["modules"], ["M1"])
        self.assertEqual(copied["modules"], ["M1", "M12"])
        self.assertEqual(copied["slots_by_module"]["M12"], chapters)


class SetupModuleTests(Base):
    """One section's wrap-up moves only that section's Set Class default."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        _sign_in(self.client, self.school, "teacher@gmail.com")

    def _other_section(self) -> int:
        """A second section of the same course, with its own pointer.

        Returns:
            The new section id.
        """
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCR3U",
            new_section=True,
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["B1"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        return int(created["id"])

    def test_wrap_up_defaults_setup_to_the_new_module(self) -> None:
        other_id = self._other_section()
        _record_run(self.school, self.class_id, "M1", "C2", "2026-10-12T20:00:00")
        _record_run(self.school, other_id, "M1", "C2", "2026-10-12T20:00:00")
        _seed_held_class(self.school, self.class_id, self.students)
        before = self.school.set_class_picker(self.class_id)
        other_before = self.school.set_class_picker(other_id)
        other_stored = _stored_module(self.school, other_id)
        self.assertEqual((before["module"], before["slot"]), ("M1", "C3"))
        self.assertEqual((other_before["module"], other_before["slot"]), ("M1", "C3"))

        started = self.school.begin_new_module(
            self.class_id,
            "M1",
            user_id=int(self.teacher["id"]),
        )
        self.assertTrue(started["changed"])
        after = self.school.set_class_picker(self.class_id)
        other_after = self.school.set_class_picker(other_id)
        self.assertEqual((after["module"], after["slot"]), ("M2", "C1"))
        self.assertEqual(after["chapters"][0], "C1")
        self.assertEqual(after["label"], "M2 · C1")
        self.assertEqual(_stored_module(self.school, self.class_id), "M2")
        self.assertEqual((other_after["module"], other_after["slot"]), ("M1", "C3"))
        self.assertEqual(_stored_module(self.school, other_id), other_stored)

        page = self.client.get(f"/staff/class/{self.class_id}?tab=live&run=1")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-next-module="M2"', html)
        self.assertIn('data-next-slot="C1"', html)
        self.assertIn('value="M2" selected', html)
        self.assertIn('value="C1" selected>M2 C1', html)
        self.assertIn("Starting live class: Module 2, Class 1", html)

        other_page = self.client.get(f"/staff/class/{other_id}?tab=live&run=1")
        other_html = other_page.get_data(as_text=True)
        self.assertEqual(other_page.status_code, 200)
        self.assertIn('data-next-module="M1"', other_html)
        self.assertIn('data-next-slot="C3"', other_html)
        self.assertNotIn('data-next-module="M2"', other_html)

    def test_next_chapter_stays_inside_the_stored_module(self) -> None:
        _record_run(self.school, self.class_id, "M1", "C2", "2026-10-12T20:00:00")
        _seed_held_class(self.school, self.class_id, self.students)
        self.school.begin_new_module(
            self.class_id,
            "M1",
            user_id=int(self.teacher["id"]),
        )
        _record_run(self.school, self.class_id, "M2", "C1", "2026-10-14T20:00:00")
        nxt = self.school.set_class_picker(self.class_id)
        self.assertEqual((nxt["module"], nxt["slot"]), ("M2", "C2"))
        _record_run(self.school, self.class_id, "M6", "C1", "2026-10-16T20:00:00")
        stayed = self.school.set_class_picker(self.class_id)
        self.assertEqual((stayed["module"], stayed["slot"]), ("M2", "C2"))
        self.assertEqual(_stored_module(self.school, self.class_id), "M2")

    def test_setup_pick_does_not_change_the_section_module(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        self.school.begin_new_module(
            self.class_id,
            "M1",
            user_id=int(self.teacher["id"]),
        )
        before = _stored_module(self.school, self.class_id)
        self.assertEqual(before, "M2")
        res = self.client.post(
            f"/api/classes/{self.class_id}/live-session/start",
            json={"live_module": "M5", "live_slot": "C3"},
        )
        self.assertEqual(res.status_code, 200)
        payload = res.get_json(silent=True) or {}
        session_id = int(payload.get("live_session_id") or 0)
        self.assertGreater(session_id, 0)
        state = self.school.live_session_teacher_state_payload(session_id)
        self.assertEqual(state.get("live_module"), "M5")
        self.assertEqual(state.get("live_slot"), "C3")
        self.assertEqual(_stored_module(self.school, self.class_id), before)
        still = self.school.set_class_picker(self.class_id)
        self.assertEqual((still["module"], still["slot"]), ("M2", "C1"))
        _record_run(self.school, self.class_id, "M5", "C3", "2026-10-20T20:00:00")
        after_run = self.school.set_class_picker(self.class_id)
        self.assertEqual((after_run["module"], after_run["slot"]), ("M2", "C1"))
        self.assertEqual(_stored_module(self.school, self.class_id), before)

    def test_setup_selects_a_module_the_pack_has_not_published(self) -> None:
        published = live_class_registry("MCR3U")["modules"]
        self.assertNotIn("M12", published)
        _record_run(self.school, self.class_id, "M1", "C3", "2026-10-12T20:00:00")
        self.school.conn.execute(
            "UPDATE classes SET current_live_module = 'M12' WHERE id = ?",
            (self.class_id,),
        )
        self.school.conn.commit()
        picker = self.school.set_class_picker(self.class_id)
        self.assertEqual((picker["module"], picker["slot"]), ("M12", "C1"))
        self.assertEqual(picker["chapters"][0], "C1")
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live&run=1")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-next-module="M12"', html)
        self.assertIn('data-next-slot="C1"', html)
        self.assertIn('value="M12" selected', html)
        self.assertIn('"M12"', html)


if __name__ == "__main__":
    unittest.main()
