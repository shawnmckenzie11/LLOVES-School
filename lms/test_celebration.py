#!/usr/bin/env python3
"""Public ALC ``/#celebrations`` Coming soon panel and staff award helpers."""

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
from celebration import build_celebration_board  # noqa: E402


class CelebrationTests(unittest.TestCase):
    """Landing hash route and staff featured-Codename picker."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher."""
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
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
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

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _populate(self, names: list[str]) -> int:
        """Create a rostered MCF3M class and return its id."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": names,
            },
        )
        self.assertEqual(rv.status_code, 200)
        return int(rv.get_json()["class"]["id"])

    def _log_day(
        self,
        class_id: int,
        meeting_date: str,
        present_ids: list[int],
        points: dict[int, float] | None = None,
    ) -> None:
        """Finalize one attendance day and optionally stamp participation points."""
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": meeting_date},
        )
        self.assertEqual(begin.status_code, 200)
        done = self.client.post(
            f"/api/classes/{class_id}/game/finalize-attendance",
            json={"present_ids": present_ids, "meeting_date": meeting_date},
        )
        self.assertEqual(done.status_code, 200)
        if not points:
            return
        game = self.school.game
        with game._lock:
            session = game.conn.execute(
                """
                SELECT id FROM sessions
                WHERE class_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (class_id,),
            ).fetchone()
            assert session is not None
            for student_id, amount in points.items():
                game.conn.execute(
                    """
                    UPDATE session_scores
                    SET points = ?
                    WHERE session_id = ? AND student_id = ?
                    """,
                    (float(amount), int(session["id"]), int(student_id)),
                )
            game.conn.commit()

    def test_landing_hash_route_coming_soon(self) -> None:
        """ALC ``/`` keeps #celebrations in nav and shows only Coming soon."""
        anon = self.app.test_client()
        rv = anon.get("/")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True)
        self.assertIn("Select your role:", body)
        self.assertIn('id="celebrations"', body)
        self.assertIn('href="#celebrations"', body)
        self.assertIn("location.hash === \"#celebrations\"", body)
        self.assertIn("Coming soon.", body)
        self.assertIn(
            "We’ll shout out strong work and engagement here when it’s ready.",
            body,
        )
        self.assertIn("class=\"calc-coming-soon\"", body)
        self.assertNotIn("data-card=", body)
        self.assertNotIn("Most Engaged", body)
        self.assertNotIn("Most Improved", body)
        self.assertNotIn("Quietly Cooking", body)
        self.assertNotIn("Celebrating a student", body)
        self.assertNotIn("A teacher will feature someone here.", body)
        self.assertNotIn("Waiting on the first attendance.", body)
        self.assertNotIn("calc.mckenzian.com", body)
        self.assertNotIn("LLOVES", body)

    def test_calc_path_redirects_to_hash(self) -> None:
        """``/calc`` is not the public URL; it sends people to ``/#celebrations``."""
        anon = self.app.test_client()
        rv = anon.get("/calc", follow_redirects=False)
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers.get("Location", "").endswith("/#celebrations"))

        alc = anon.get("/", headers={"Host": "calc.mckenzian.com"})
        self.assertEqual(alc.status_code, 200)
        self.assertIn("Select your role:", alc.get_data(as_text=True))

    def test_featured_award_and_most_engaged(self) -> None:
        """Staff can feature a Codename; attendance picks Most Engaged."""
        class_id = self._populate(["Maple", "Birch"])
        students = {
            row["codename"]: int(row["id"])
            for row in self.school.game.dashboard(class_id, sort="az")["students"]
        }
        self._log_day(
            class_id,
            "2026-09-09",
            [students["Maple"], students["Birch"]],
            {students["Maple"]: 10, students["Birch"]: 2},
        )
        self._log_day(
            class_id,
            "2026-09-11",
            [students["Maple"]],
            {students["Maple"]: 5},
        )

        posted = self.client.post(
            "/api/staff/celebration-award",
            json={
                "class_id": class_id,
                "student_id": students["Birch"],
                "blurb": "Kept the warm-up moving.",
            },
        )
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(posted.get_json()["award"]["name"], "Birch")

        home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Celebrate a student", home)
        self.assertIn("Now featuring", home)
        self.assertIn("Birch", home)
        self.assertIn("/#celebrations", home)

        page = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Coming soon.", page)
        self.assertNotIn("data-card=", page)
        self.assertNotIn("Kept the warm-up moving.", page)

        board = build_celebration_board(self.school)["cards"]
        by_key = {card["key"]: card for card in board}
        self.assertEqual(by_key["award"]["name"], "Birch")
        self.assertEqual(by_key["award"]["detail"], "Kept the warm-up moving.")
        self.assertEqual(by_key["engaged"]["name"], "Maple")

    def test_most_improved_and_quietly_cooking(self) -> None:
        """Four classes unlock Most Improved; steady attendance is Quietly Cooking."""
        class_id = self._populate(["Maple", "Aspen", "Cedar"])
        students = {
            row["codename"]: int(row["id"])
            for row in self.school.game.dashboard(class_id, sort="az")["students"]
        }
        days = ["2026-09-09", "2026-09-11", "2026-09-14", "2026-09-16"]
        everyone = [
            students["Maple"],
            students["Aspen"],
            students["Cedar"],
        ]
        point_plan = [
            {students["Cedar"]: 8, students["Aspen"]: 1, students["Maple"]: 0},
            {students["Cedar"]: 8, students["Aspen"]: 1, students["Maple"]: 0},
            {students["Cedar"]: 8, students["Aspen"]: 1, students["Maple"]: 12},
            {students["Cedar"]: 8, students["Aspen"]: 1, students["Maple"]: 12},
        ]
        for day, pts in zip(days, point_plan, strict=True):
            self._log_day(class_id, day, everyone, pts)

        page = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Coming soon.", page)
        self.assertNotIn("data-card=", page)

        board = build_celebration_board(self.school)["cards"]
        by_key = {card["key"]: card for card in board}
        self.assertEqual(by_key["engaged"]["name"], "Cedar")
        self.assertEqual(by_key["improved"]["name"], "Maple")
        self.assertEqual(by_key["cooking"]["name"], "Aspen")

    def test_foreign_class_rejected(self) -> None:
        """A teacher cannot feature a Codename from someone else's class."""
        class_id = self._populate(["Maple"])
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        other = self.school.register_staff("other@gmail.com")
        other_offering = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="MCR3U"
        )
        other_client = self.app.test_client()
        other_client.get("/auth/google?portal=staff")
        other_client.get("/auth/google/callback?email=other@gmail.com&name=O")
        other_client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("other@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = other_client.post(
            "/api/staff/classes",
            json={
                "offering_id": other_offering["id"],
                "days": "T/Th/F",
                "time": "2:00pm",
                "codenames": ["Oak"],
            },
        )
        self.assertEqual(created.status_code, 200)
        steal = other_client.post(
            "/api/staff/celebration-award",
            json={
                "class_id": class_id,
                "student_id": int(maple["id"]),
                "blurb": "nope",
            },
        )
        self.assertEqual(steal.status_code, 400)
        self.assertIn("not yours", steal.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
