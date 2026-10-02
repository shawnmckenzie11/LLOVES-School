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
import celebration  # noqa: E402
from celebration import (  # noqa: E402
    CELEBRATIONS_FROZEN_ENV,
    SETTING_FEATURED_AWARD,
    SETTING_FREEZE_EPOCH,
    SETTING_PUBLIC_SNAPSHOT,
    WONDER_COPY,
    build_celebration_board,
    celebrated_students,
    celebrations_frozen,
    clear_public_celebration_memo,
    name_sort_key,
    note_celebrations_unfrozen,
    public_celebration_board,
    refresh_public_celebration_snapshot,
)


class CelebrationTests(unittest.TestCase):
    """Landing hash route and staff featured-Codename picker."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher."""
        clear_public_celebration_memo()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(CELEBRATIONS_FROZEN_ENV, None)
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
        self.assertIn("calc-winner-list", body)
        self.assertIn("Array.isArray(card.names)", body)
        self.assertIn("is-pulse", body)
        css = anon.get("/static/lloves.css")
        self.assertEqual(css.status_code, 200)
        css_text = css.get_data(as_text=True)
        self.assertIn(".calc-sparkle-dot", css_text)
        self.assertIn("@keyframes calc-sparkle-pulse", css_text)
        self.assertIn("prefers-reduced-motion", css_text)
        self.assertIn("animation: none", css_text)
        self.assertIn(".calc-winner-list", css_text)
        self.assertIn("flex-wrap: wrap", css_text.split(".calc-winner-list", 1)[1])
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
                {"key", "name", "names", "course", "detail", "title", "kicker"},
            )
            self.assertEqual(card["names"], [card["name"]])

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

    def _three_sections(self) -> dict[str, int]:
        """Offering id per section code: MCR3U, MCR3U-2, and MCF3M."""
        mcr3u = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        mcr3u_2 = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCR3U",
            new_section=True,
        )
        return {
            "MCR3U": int(mcr3u["id"]),
            "MCR3U-2": int(mcr3u_2["id"]),
            "MCF3M": int(self.offering["id"]),
        }

    def _tie_board(self, tied: int) -> dict[str, dict[str, int]]:
        """Give every course ``tied`` students sharing the top score.

        Each class also has one present student with fewer points and one
        who never attended, so neither may join the tie.

        Args:
            tied: How many students share first place in each course.

        Returns:
            Section code to ``{codename: student_id}``.
        """
        rosters: dict[str, dict[str, int]] = {}
        for course, offering_id in self._three_sections().items():
            prefix = course.replace("-", "")
            leaders = [f"{prefix}Lead{n}" for n in range(1, tied + 1)]
            names = leaders + [f"{prefix}Close", f"{prefix}Absent"]
            class_id = self._populate(names, offering_id)
            ids = self._ids(class_id)
            points = {ids[name]: 6 for name in leaders}
            points[ids[f"{prefix}Close"]] = 5
            self._log_day(
                class_id,
                "2026-09-09",
                [ids[name] for name in leaders] + [ids[f"{prefix}Close"]],
                points,
            )
            ids["_class_id"] = class_id
            rosters[course] = ids
        return rosters

    def _assert_ties(self, rosters: dict[str, dict[str, int]], tied: int) -> None:
        """Every course card lists exactly its ``tied`` leaders, in order."""
        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        engaged = {c["course"]: c for c in cards if c["key"] == "engaged"}
        self.assertEqual(list(engaged), ["MCR3U", "MCR3U-2", "MCF3M"])
        for course, card in engaged.items():
            prefix = course.replace("-", "")
            want = [f"{prefix}Lead{n}" for n in range(1, tied + 1)]
            self.assertEqual(card["names"], want)
            self.assertEqual(card["name"], ", ".join(want))
            self.assertEqual(card["detail"], "1 class present · 6 pts")
            self.assertNotIn(f"{prefix}Close", card["name"])
            self.assertNotIn(f"{prefix}Absent", card["name"])
        raw = self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT)
        snapshot = json.loads(raw)
        stored = {c["course"]: c for c in snapshot["cards"]}
        rows = celebrated_students(self.school)
        for course, ids in rosters.items():
            prefix = course.replace("-", "")
            want = [f"{prefix}Lead{n}" for n in range(1, tied + 1)]
            # Ids and a fingerprint only: no names in the stored snapshot.
            self.assertEqual(
                [(r["class_id"], r["student_id"]) for r in stored[course]["students"]],
                [(ids["_class_id"], ids[name]) for name in want],
            )
            for ref in stored[course]["students"]:
                self.assertEqual(set(ref), {"class_id", "student_id", "fp"})
            for name in want:
                self.assertNotIn(name, raw)
            self.assertEqual(
                [
                    (r["class_id"], r["student_id"], r["name"])
                    for r in rows
                    if r["card"] == "engaged" and r["course"] == course
                ],
                [(ids["_class_id"], ids[name], name) for name in want],
            )

    def test_two_way_tie_each_course(self) -> None:
        """MCK-118: two students tied on top all show, in every course."""
        self._assert_ties(self._tie_board(2), 2)

    def test_three_way_tie_each_course(self) -> None:
        """MCK-118: three students tied on top all show, in every course."""
        self._assert_ties(self._tie_board(3), 3)

    def test_frozen_by_default(self) -> None:
        """MCK-118: frozen unless env CELEBRATIONS_FROZEN says 0/false/no/off."""
        self.assertIs(celebrations_frozen(), True)
        for raw, want in (("", True), ("1", True), ("yes", True), ("0", False),
                          ("false", False), ("OFF", False), ("no", False)):
            os.environ[CELEBRATIONS_FROZEN_ENV] = raw
            self.assertIs(celebrations_frozen(), want, raw)

    def _three_tie(self) -> tuple[int, dict[str, int]]:
        """MCF3M class with Maple, Birch, and Cedar tied on one day."""
        class_id = self._populate(["Maple", "Birch", "Cedar"])
        ids = self._ids(class_id)
        self._log_day(
            class_id,
            "2026-09-09",
            [ids["Maple"], ids["Birch"], ids["Cedar"]],
            {ids["Maple"]: 3, ids["Birch"]: 3, ids["Cedar"]: 3},
        )
        return class_id, ids

    def _engaged_names(self) -> dict[str, list[str]]:
        """Public Most Engaged names per course, memo cleared first."""
        clear_public_celebration_memo()
        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        return {c["course"]: c["names"] for c in cards if c["key"] == "engaged"}

    def test_frozen_board_ignores_new_classes(self) -> None:
        """MCK-118: after the snapshot, new attendance leaves the winners alone.

        The ranking still runs, unfreezing goes back to live results, and
        refresh re-takes the snapshot.
        """
        class_id, ids = self._three_tie()
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Cedar", "Maple"]})
        snapshot = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))
        self.assertTrue(snapshot["taken_at"].endswith("+00:00"))
        self.assertEqual(snapshot["semester_id"], int(self.school.get_active_semester()["id"]))

        self._log_day(class_id, "2026-09-11", [ids["Cedar"]], {ids["Cedar"]: 4})
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Cedar", "Maple"]})
        live = build_celebration_board(self.school)["cards"]
        mcf3m = next(c for c in live if c["key"] == "engaged" and c["course"] == "MCF3M")
        self.assertEqual(mcf3m["name"], "Cedar")
        self.assertEqual(
            json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT)), snapshot
        )

        refreshed = refresh_public_celebration_snapshot(self.school)
        self.assertEqual(len(refreshed["cards"][0]["students"]), 1)
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Cedar"]})

    def test_frozen_names_follow_roster_within_memo(self) -> None:
        """MCK-118 HIGH-1: a roster delete and a Codename change reach the
        frozen public page once the 60 s memo expires.
        """
        class_id, ids = self._three_tie()
        started = time.monotonic()
        first = public_celebration_board(self.school, now=started)
        self.assertEqual(first["cards"][0]["names"], ["Birch", "Cedar", "Maple"])

        deleted = self.client.post(
            f"/api/classes/{class_id}/students/delete",
            json={"student_id": ids["Birch"]},
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        self._rename_student(
            class_id, ids["Maple"], codename="Moonbeam", first_name="Maple",
            last_display="Pereira",
        )
        # Inside the memo window the old payload may still be served.
        cached = public_celebration_board(self.school, now=started + 20)
        self.assertEqual(cached["cards"][0]["names"], ["Birch", "Cedar", "Maple"])
        after = public_celebration_board(self.school, now=started + 61)
        self.assertEqual(after["cards"][0]["names"], ["Cedar", "Moonbeam"])
        self.assertEqual(after["cards"][0]["name"], "Cedar, Moonbeam")
        dumped = json.dumps(after)
        for gone in ("Birch", "Maple", "Pereira"):
            self.assertNotIn(gone, dumped)
        raw = self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT)
        for name in ("Birch", "Maple", "Cedar", "Moonbeam"):
            self.assertNotIn(name, raw)
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Cedar", "Moonbeam"]})
        rows = celebrated_students(self.school)
        self.assertEqual(
            [(r["student_id"], r["name"]) for r in rows],
            [(ids["Cedar"], "Cedar"), (ids["Maple"], "Moonbeam")],
        )

        # Every frozen winner gone: the card is dropped, not refilled.
        for name in ("Cedar", "Maple"):
            self.client.post(
                f"/api/classes/{class_id}/students/delete",
                json={"student_id": ids[name]},
            )
        self.assertEqual(self._engaged_names(), {})

    def test_reused_student_id_is_not_celebrated(self) -> None:
        """MCK-118 LOW-5: a new student who gets a deleted winner's id is skipped."""
        class_id = self._populate(["Birch", "Winner"])
        ids = self._ids(class_id)
        self.assertGreater(ids["Winner"], ids["Birch"])
        self._log_day(class_id, "2026-09-09", [ids["Winner"]], {ids["Winner"]: 5})
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Winner"]})
        self.client.post(
            f"/api/classes/{class_id}/students/delete",
            json={"student_id": ids["Winner"]},
        )
        added = self.client.post(
            f"/api/classes/{class_id}/students", json={"codename": "Newkid"}
        )
        self.assertEqual(added.status_code, 200, added.get_json())
        self.assertEqual(self._ids(class_id)["Newkid"], ids["Winner"])
        self.assertEqual(self._engaged_names(), {})
        self.assertEqual(
            [r for r in celebrated_students(self.school) if r["card"] == "engaged"], []
        )

    def test_no_snapshot_until_someone_attends(self) -> None:
        """MCK-118 MED-1: an empty board is never frozen; the next read retries."""
        class_id = self._populate(["Maple", "Birch"])
        ids = self._ids(class_id)
        self.assertEqual(self._engaged_names(), {})
        self.assertIsNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))
        self._log_day(class_id, "2026-09-09", [ids["Maple"]], {ids["Maple"]: 1})
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Maple"]})
        self.assertIsNotNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))

    def test_no_snapshot_without_active_semester(self) -> None:
        """MCK-118 MED-1: no active semester stores nothing."""
        class_id, _ids = self._three_tie()
        with patch.object(self.school, "get_active_semester", return_value=None):
            self.assertEqual(self._engaged_names(), {})
        self.assertIsNone(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT, None))
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Cedar", "Maple"]})

    def test_snapshot_from_another_semester_is_retaken(self) -> None:
        """MCK-118 MED-1: the snapshot is keyed to the active semester."""
        class_id, ids = self._three_tie()
        self._engaged_names()
        stale = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))
        stale["semester_id"] = stale["semester_id"] + 1000
        self.school.set_school_setting(SETTING_PUBLIC_SNAPSHOT, json.dumps(stale))
        self._log_day(class_id, "2026-09-11", [ids["Cedar"]], {ids["Cedar"]: 4})
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Cedar"]})
        fresh = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))
        self.assertEqual(fresh["semester_id"], int(self.school.get_active_semester()["id"]))

    def test_course_without_winner_is_added_later(self) -> None:
        """MCK-118 MED-1: a course with no attendance at freeze time gets its card
        when it first has a winner; courses already frozen stay put.
        """
        mcr3u = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        mcf_id, mcf = self._three_tie()
        mcr_id = self._populate(["Oak", "Pine"], int(mcr3u["id"]))
        mcr = self._ids(mcr_id)
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Cedar", "Maple"]})
        self._log_day(mcf_id, "2026-09-11", [mcf["Cedar"]], {mcf["Cedar"]: 4})
        self._log_day(mcr_id, "2026-09-11", [mcr["Pine"]], {mcr["Pine"]: 2})
        names = self._engaged_names()
        self.assertEqual(list(names), ["MCR3U", "MCF3M"])
        self.assertEqual(names["MCR3U"], ["Pine"])
        self.assertEqual(names["MCF3M"], ["Birch", "Cedar", "Maple"])

    def test_blank_or_corrupt_snapshot_is_retaken(self) -> None:
        """MCK-118 LOW-2: a blank, corrupt, or old-format value is replaced,
        and the board stays frozen.
        """
        class_id, ids = self._three_tie()
        legacy = json.dumps({"taken_at": "x", "cards": [{"course": "MCF3M", "names": ["Old"]}]})
        for bad in ("", "{not json", legacy):
            self.school.set_school_setting(SETTING_PUBLIC_SNAPSHOT, bad)
            self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Cedar", "Maple"]}, bad)
            stored = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))
            self.assertEqual(stored["version"], 2)
        self._log_day(class_id, "2026-09-11", [ids["Cedar"]], {ids["Cedar"]: 4})
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Cedar", "Maple"]})

    def test_unfreeze_then_refreeze_takes_fresh_snapshot(self) -> None:
        """MCK-118 LOW-4: off goes live; back on does not revive the old snapshot."""
        class_id, ids = self._three_tie()
        self._engaged_names()
        old_raw = self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT)
        self._log_day(class_id, "2026-09-11", [ids["Cedar"]], {ids["Cedar"]: 4})

        os.environ[CELEBRATIONS_FROZEN_ENV] = "0"
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Cedar"]})
        epoch = self.school.get_school_setting(SETTING_FREEZE_EPOCH, "")
        self.assertTrue(epoch)
        self._engaged_names()
        self.assertEqual(self.school.get_school_setting(SETTING_FREEZE_EPOCH, ""), epoch)

        os.environ[CELEBRATIONS_FROZEN_ENV] = "1"
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Cedar"]})
        fresh = json.loads(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT))
        self.assertEqual(fresh["epoch"], epoch)
        self.assertNotEqual(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT), old_raw)

    def test_boot_with_flag_off_retires_snapshot(self) -> None:
        """MCK-118 LOW-4: a restart with CELEBRATIONS_FROZEN=0 bumps the epoch
        even if nobody opens the page while unfrozen.
        """
        self._three_tie()
        self._engaged_names()
        before = self.school.get_school_setting(SETTING_FREEZE_EPOCH, "")
        note_celebrations_unfrozen(self.school)
        self.assertEqual(self.school.get_school_setting(SETTING_FREEZE_EPOCH, ""), before)
        os.environ[CELEBRATIONS_FROZEN_ENV] = "off"
        with patch("app.note_celebrations_unfrozen") as hook:
            create_app(
                db_path=Path(self.tmp.name) / "boot.sqlite",
                data_dir=Path(self.tmp.name) / "boot",
                testing=True,
            ).config["SCHOOL_DB"].close()
        hook.assert_called_once()
        note_celebrations_unfrozen(self.school)
        self.assertNotEqual(self.school.get_school_setting(SETTING_FREEZE_EPOCH, ""), before)

    def test_tie_order_folds_accents(self) -> None:
        """MCK-118 LOW-1: accented names sort with their base letter."""
        names = ["Zoë", "Ïsabeau", "Đorđe", "Łukasz", "Élodie", "Ōtake", "adam", "Birch"]
        self.assertEqual(
            sorted(names, key=name_sort_key),
            ["adam", "Birch", "Đorđe", "Élodie", "Ïsabeau", "Łukasz", "Ōtake", "Zoë"],
        )
        class_id = self._populate(["Zoë", "Élodie", "Birch"])
        ids = self._ids(class_id)
        self._log_day(
            class_id,
            "2026-09-09",
            list(ids.values()),
            {sid: 2 for sid in ids.values()},
        )
        self.assertEqual(self._engaged_names(), {"MCF3M": ["Birch", "Élodie", "Zoë"]})
        board = build_celebration_board(self.school)["cards"]
        mcf3m = next(c for c in board if c["key"] == "engaged" and c["course"] == "MCF3M")
        self.assertEqual(mcf3m["names"], ["Birch", "Élodie", "Zoë"])

    def test_frozen_shoutout_still_follows_teacher(self) -> None:
        """MCK-118: a new teacher Shoutout shows while Most Engaged stays frozen."""
        class_id = self._populate(["Maple", "Birch"])
        ids = self._ids(class_id)
        self._log_day(class_id, "2026-09-09", [ids["Maple"]], {ids["Maple"]: 2})
        first = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        self.assertEqual([c["key"] for c in first], ["engaged"])
        self._log_day(class_id, "2026-09-11", [ids["Birch"]], {ids["Birch"]: 9})
        self._feature(class_id, ids["Birch"], "Great question.")
        clear_public_celebration_memo()
        cards = self.app.test_client().get("/api/celebrations").get_json()["cards"]
        self.assertEqual(
            [(c["key"], c["name"]) for c in cards],
            [("award", "Birch"), ("engaged", "Maple")],
        )
        rows = celebrated_students(self.school)
        self.assertEqual(
            [(r["card"], r["student_id"], r["name"]) for r in rows],
            [("award", ids["Birch"], "Birch"), ("engaged", ids["Maple"], "Maple")],
        )

    def test_snapshot_first_insert_wins(self) -> None:
        """MCK-118: a second worker's snapshot never overwrites the first."""
        self.assertTrue(
            self.school.add_school_setting_if_missing(SETTING_PUBLIC_SNAPSHOT, '{"cards": []}')
        )
        self.assertFalse(
            self.school.add_school_setting_if_missing(SETTING_PUBLIC_SNAPSHOT, "other")
        )
        self.assertEqual(
            self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT), '{"cards": []}'
        )
        self.assertFalse(
            self.school.compare_and_set_school_setting(SETTING_PUBLIC_SNAPSHOT, "stale", "x")
        )
        self.assertTrue(
            self.school.compare_and_set_school_setting(
                SETTING_PUBLIC_SNAPSHOT, '{"cards": []}', "y"
            )
        )
        self.assertEqual(self.school.get_school_setting(SETTING_PUBLIC_SNAPSHOT), "y")

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
