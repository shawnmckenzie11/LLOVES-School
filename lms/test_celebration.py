#!/usr/bin/env python3
"""Public ALC ``/#celebrations`` board and staff award helpers."""

from __future__ import annotations

import html
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from celebration import (  # noqa: E402
    SETTING_FEATURED_AWARD,
    WONDER_COPY,
    build_celebration_board,
    clear_public_celebration_memo,
    public_celebration_board,
)


class CelebrationTests(unittest.TestCase):
    """Landing hash route and staff featured-Codename picker."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher."""
        clear_public_celebration_memo()
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

    def _populate(self, names: list[str], offering_id: int | None = None) -> int:
        """Create a rostered class (MCF3M unless ``offering_id``) and return its id."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering_id or self.offering["id"],
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

    def _rename_student(
        self,
        class_id: int,
        student_id: int,
        *,
        codename: str | None,
        first_name: str,
        last_display: str,
    ) -> None:
        """Set Codename, first name, and last name on one roster row.

        Args:
            class_id: Class that owns the student.
            student_id: Students primary key.
            codename: Public Codename, or None when unset.
            first_name: Given name. Empty string means blank.
            last_display: Legal last name. Must never reach the public API.
        """
        game = self.school.game
        with game._lock:
            game.conn.execute(
                """
                UPDATE students
                SET codename = ?, first_name = ?, last_display = ?
                WHERE id = ? AND class_id = ?
                """,
                (codename, first_name, last_display, int(student_id), int(class_id)),
            )
            game.conn.commit()

    def _ids(self, class_id: int) -> dict[str, int]:
        """Codename to student id for one class."""
        return {
            row["codename"]: int(row["id"])
            for row in self.school.game.dashboard(class_id, sort="az")["students"]
        }

    def _feature(self, class_id: int, student_id: int, blurb: str) -> None:
        """Post one Shoutout pick through the staff API.

        Args:
            class_id: Class of the featured student.
            student_id: Featured student.
            blurb: One-line reason.
        """
        posted = self.client.post(
            "/api/staff/celebration-award",
            json={
                "class_id": class_id,
                "student_id": student_id,
                "blurb": blurb,
            },
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())

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
        self.assertIn(WONDER_COPY["coming_soon_line"], body)
        self.assertIn(WONDER_COPY["coming_soon_sub"], html.unescape(body))
        self.assertNotIn("Coming soon.", body)
        self.assertNotIn(
            "We’ll shout out strong work and engagement here when it’s ready.",
            body,
        )
        self.assertIn("class=\"calc-coming-soon\"", body)
        self.assertIn('class="calc-sparkle-dot"', body)
        self.assertIn("pulseSparkle", body)
        self.assertIn("is-pulse", body)
        css = anon.get("/static/lloves.css")
        self.assertEqual(css.status_code, 200)
        css_text = css.get_data(as_text=True)
        self.assertIn(".calc-sparkle-dot", css_text)
        self.assertIn("@keyframes calc-sparkle-pulse", css_text)
        self.assertIn("prefers-reduced-motion", css_text)
        self.assertIn("animation: none", css_text)
        self.assertIn('class="calc-board"', body)
        self.assertIn('"/api/celebrations"', body)
        script = body.split("function syncLandingHash()", 1)[1]
        gate = script.split("if (celebrations)", 1)[1]
        self.assertIn("loadCelebrations()", gate)
        self.assertNotIn("Most Engaged", body)
        self.assertNotIn("Most Improved", body)
        self.assertNotIn("Quietly Cooking", body)
        self.assertNotIn("Celebrating a student", body)
        self.assertNotIn("Codenames from live class", body)
        self.assertNotIn("Codenames only", body)
        self.assertNotIn("A teacher will feature someone here.", body)
        self.assertNotIn("Waiting on the first attendance.", body)
        self.assertNotIn("calc.mckenzian.com", body)
        self.assertNotIn("LLOVES", body)

    def test_api_empty_board_keeps_coming_soon(self) -> None:
        """An empty board returns no cards and the landing still shows Coming soon."""
        anon = self.app.test_client()
        rv = anon.get("/api/celebrations")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.headers.get("Cache-Control"), "max-age=60")
        payload = rv.get_json()
        self.assertEqual(payload["cards"], [])
        self.assertEqual(payload["copy"]["page_title"], WONDER_COPY["page_title"])
        self.assertEqual(payload["copy"]["empty_board"], WONDER_COPY["empty_board"])
        self.assertNotIn("sub_line", payload["copy"])
        self.assertNotIn("footer", payload["copy"])
        page = anon.get("/").get_data(as_text=True)
        self.assertIn(WONDER_COPY["coming_soon_line"], page)
        self.assertIn('class="calc-coming-soon"', page)
        self.assertIn('class="calc-board"', page)
        self.assertNotIn("data-card=", page)

    def test_api_award_and_engaged_strip_ids(self) -> None:
        """Shoutout plus MCF3M Most Engaged returns two cards and no roster ids."""
        class_id = self._populate(["Maple", "Birch"])
        students = {
            row["codename"]: int(row["id"])
            for row in self.school.game.dashboard(class_id, sort="az")["students"]
        }
        self._log_day(
            class_id,
            "2026-09-09",
            [students["Maple"]],
            {students["Maple"]: 4},
        )
        self._feature(class_id, students["Birch"], "Kept the warm-up moving.")
        rv = self.app.test_client().get("/api/celebrations")
        self.assertEqual(rv.status_code, 200)
        cards = rv.get_json()["cards"]
        self.assertEqual([card["key"] for card in cards], ["award", "engaged"])
        by_key = {card["key"]: card for card in cards}
        self.assertEqual(by_key["award"]["name"], "Birch")
        self.assertEqual(by_key["award"]["title"], "Shoutout")
        self.assertEqual(by_key["award"]["kicker"], "")
        self.assertEqual(by_key["award"]["detail"], "Kept the warm-up moving.")
        self.assertEqual(by_key["engaged"]["name"], "Maple")
        self.assertEqual(by_key["engaged"]["title"], "Most Engaged")
        self.assertEqual(by_key["engaged"]["kicker"], "")
        self.assertEqual(by_key["engaged"]["course"], "MCF3M")
        dumped = json.dumps(rv.get_json())
        self.assertNotIn("class_id", dumped)
        self.assertNotIn("student_id", dumped)
        for gone in (
            "Awards",
            "Celebrating a student",
            "Codenames only",
            "Codenames from live class",
            "Showed up and jumped in",
            "Biggest climb lately",
            "Steady work",
        ):
            self.assertNotIn(gone, dumped)
        for card in cards:
            self.assertEqual(
                set(card),
                {"key", "name", "course", "detail", "title", "kicker"},
            )

    def test_api_codename_wins_over_first_name(self) -> None:
        """A set Codename is the public name, not the first or last name."""
        class_id = self._populate(["Maple"])
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self._rename_student(
            class_id,
            int(maple["id"]),
            codename="Maple",
            first_name="Amina",
            last_display="Pereira",
        )
        self._feature(class_id, int(maple["id"]), "Warm-up lead.")
        rv = self.app.test_client().get("/api/celebrations")
        payload = rv.get_json()
        self.assertEqual(payload["cards"][0]["name"], "Maple")
        dumped = json.dumps(payload)
        self.assertNotIn("Amina", dumped)
        self.assertNotIn("Pereira", dumped)

    def test_api_blank_codename_uses_first_name_only(self) -> None:
        """A blank Codename shows the first name and never the last name."""
        class_id = self._populate(["Maple"])
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self._rename_student(
            class_id,
            int(maple["id"]),
            codename=None,
            first_name="Amina",
            last_display="Pereira",
        )
        self._feature(class_id, int(maple["id"]), "Warm-up lead.")
        rv = self.app.test_client().get("/api/celebrations")
        payload = rv.get_json()
        self.assertEqual(payload["cards"][0]["name"], "Amina")
        dumped = json.dumps(payload)
        self.assertNotIn("Pereira", dumped)
        self.assertNotIn("Amina Pereira", dumped)
        self.assertNotIn("last_display", dumped)

    def test_api_blank_names_use_course_placeholder(self) -> None:
        """No Codename and no first name becomes 'A student in MCF3M'."""
        class_id = self._populate(["Maple"])
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self._rename_student(
            class_id,
            int(maple["id"]),
            codename=None,
            first_name="",
            last_display="Pereira",
        )
        self._feature(class_id, int(maple["id"]), "Warm-up lead.")
        rv = self.app.test_client().get("/api/celebrations")
        payload = rv.get_json()
        self.assertEqual(payload["cards"][0]["name"], "A student in MCF3M")
        self.assertEqual(payload["cards"][0]["course"], "MCF3M")
        dumped = json.dumps(payload)
        self.assertNotIn("Pereira", dumped)

    def test_public_board_memo(self) -> None:
        """A second read inside 60 seconds does not see a newer Awards pick."""
        class_id = self._populate(["Maple", "Birch"])
        students = {
            row["codename"]: int(row["id"])
            for row in self.school.game.dashboard(class_id, sort="az")["students"]
        }
        self._log_day(class_id, "2026-09-09", [students["Maple"]])
        self._feature(class_id, students["Birch"], "First pick.")
        started = time.monotonic()
        first = public_celebration_board(self.school, now=started)
        self.assertEqual(first["cards"][0]["name"], "Birch")
        self.school.set_school_setting(
            SETTING_FEATURED_AWARD,
            json.dumps(
                {
                    "class_id": class_id,
                    "student_id": students["Maple"],
                    "blurb": "Later pick.",
                }
            ),
        )
        second = public_celebration_board(self.school, now=started + 20)
        self.assertEqual(second["cards"][0]["name"], "Birch")
        self.assertEqual(second["cards"][0]["detail"], "First pick.")
        third = public_celebration_board(self.school, now=started + 61)
        award = next(card for card in third["cards"] if card["key"] == "award")
        self.assertEqual(award["name"], "Maple")
        self.assertEqual(award["detail"], "Later pick.")

    def test_api_celebrations_error_returns_empty_cards(self) -> None:
        """A stats failure still returns HTTP 200 and an empty card list."""
        with patch("app.public_celebration_board", side_effect=RuntimeError("down")):
            rv = self.app.test_client().get("/api/celebrations")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json()["cards"], [])
        self.assertIn("page_title", rv.get_json()["copy"])
        self.assertEqual(rv.headers.get("Cache-Control"), "max-age=60")

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
        self.assertIn("Post to Shoutout", home)
        self.assertNotIn("Awards", home)
        self.assertIn("Now featuring", home)
        self.assertIn("Birch", home)
        self.assertIn("/#celebrations", home)

        page = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn(WONDER_COPY["coming_soon_line"], page)
        self.assertIn('class="calc-sparkle-dot"', page)
        self.assertNotIn("data-card=", page)
        self.assertNotIn("Kept the warm-up moving.", page)

        board = build_celebration_board(self.school)["cards"]
        self.assertEqual(board[0]["key"], "award")
        self.assertEqual(board[0]["name"], "Birch")
        self.assertEqual(board[0]["detail"], "Kept the warm-up moving.")
        mcf3m = next(
            card
            for card in board
            if card["key"] == "engaged" and card["course"] == "MCF3M"
        )
        self.assertEqual(mcf3m["name"], "Maple")

    def test_most_engaged_per_class_lists_ties(self) -> None:
        """One Most Engaged card per class; presents beat points; ties all show."""
        mcr3u = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        mcr3u_2 = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCR3U",
            new_section=True,
        )
        self.assertEqual(mcr3u_2["section_code"], "MCR3U-2")

        mcf3m_id = self._populate(["Maple", "Birch", "Cedar"])
        mcf = self._ids(mcf3m_id)
        self._log_day(
            mcf3m_id,
            "2026-09-09",
            [mcf["Maple"], mcf["Birch"], mcf["Cedar"]],
            {mcf["Maple"]: 5, mcf["Birch"]: 5, mcf["Cedar"]: 1},
        )

        mcr_id = self._populate(["Oak", "Pine"], int(mcr3u["id"]))
        mcr = self._ids(mcr_id)
        self._log_day(mcr_id, "2026-09-09", [mcr["Oak"]], {mcr["Oak"]: 3})

        mcr2_id = self._populate(["Elm", "Ash"], int(mcr3u_2["id"]))
        mcr2 = self._ids(mcr2_id)
        self._log_day(
            mcr2_id,
            "2026-09-09",
            [mcr2["Elm"], mcr2["Ash"]],
            {mcr2["Elm"]: 9},
        )
        self._log_day(mcr2_id, "2026-09-11", [mcr2["Ash"]])

        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        self.assertEqual(
            [(card["key"], card["course"], card["name"]) for card in cards],
            [
                ("engaged", "MCR3U", "Oak"),
                ("engaged", "MCR3U-2", "Ash"),
                ("engaged", "MCF3M", "Birch, Maple"),
            ],
        )
        for card in cards:
            self.assertEqual(card["title"], "Most Engaged")
            self.assertEqual(card["kicker"], "")
        self.assertEqual(cards[2]["detail"], "1 class present · 5 pts")
        self.assertEqual(cards[1]["detail"], "2 classes present")

    def _session_days(self, class_id: int) -> list[str]:
        """Meeting day of every non-template session in one class, oldest first."""
        game = self.school.game
        with game._lock:
            rows = game.conn.execute(
                """
                SELECT substr(starts_at, 1, 10) AS day FROM sessions
                WHERE class_id = ? AND status != 'template'
                ORDER BY starts_at ASC, id ASC
                """,
                (class_id,),
            ).fetchall()
        return [str(row["day"]) for row in rows]

    def test_most_engaged_counts_distinct_days_not_rows(self) -> None:
        """Several sessions on one day count once; points still sum; no merging.

        Re-logging a day (a second attendance pass or an extra game) adds a
        new session row on that date. Most Engaged used to count every
        ``session_scores`` row with ``present`` set, so Norah showed more
        classes present than the class had met.
        """
        mcr3u = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        mcf3m_id = self._populate(["Norah", "Birch"])
        mcf = self._ids(mcf3m_id)
        self._log_day(
            mcf3m_id,
            "2026-09-09",
            [mcf["Norah"], mcf["Birch"]],
            {mcf["Norah"]: 1, mcf["Birch"]: 1},
        )
        self._log_day(mcf3m_id, "2026-09-09", [mcf["Norah"]], {mcf["Norah"]: 2})
        self._log_day(mcf3m_id, "2026-09-09", [mcf["Norah"]])
        self._log_day(
            mcf3m_id,
            "2026-09-11",
            [mcf["Norah"], mcf["Birch"]],
            {mcf["Birch"]: 2},
        )
        days = self._session_days(mcf3m_id)
        self.assertEqual(
            days, ["2026-09-09", "2026-09-09", "2026-09-09", "2026-09-11"]
        )

        # Same Codename in another section is a separate roster row.
        mcr_id = self._populate(["Norah", "Oak"], int(mcr3u["id"]))
        mcr = self._ids(mcr_id)
        self.assertNotEqual(mcr["Norah"], mcf["Norah"])
        self._log_day(mcr_id, "2026-09-09", [mcr["Norah"]], {mcr["Norah"]: 7})

        board = build_celebration_board(self.school)["cards"]
        engaged = {card["course"]: card for card in board if card["key"] == "engaged"}
        mcf_card = engaged["MCF3M"]
        self.assertEqual(mcf_card["name"], "Birch, Norah")
        self.assertEqual(mcf_card["detail"], "2 classes present · 3 pts")
        mcr_card = engaged["MCR3U"]
        self.assertEqual(mcr_card["name"], "Norah")
        self.assertEqual(mcr_card["detail"], "1 class present · 7 pts")
        self.assertEqual(
            mcr_card["students"],
            [{"class_id": mcr_id, "student_id": mcr["Norah"]}],
        )

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
