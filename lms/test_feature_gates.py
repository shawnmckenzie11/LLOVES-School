"""MCK-346 through MCK-349: basic plan gates on the live routes and screens.

Assertions use status codes and counts. They do not print join codes,
catch-up tokens, names, or emails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from feature_gates import (  # noqa: E402
    news_events_for_session,
    reset_whiteboard_refresh,
    scope_catchup_links,
)
from features import _caller_has_feature, features_for_page, set_feature_override  # noqa: E402
from live_news_wire import log_for, reset_logs  # noqa: E402


def _sign_in_basic(client, user_id: int) -> None:
    """Sign in without the mock Google callback, so the launch rows stay off.

    Args:
        client: Flask test client.
        user_id: ``users.id``.
    """
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = int(user_id)
        sess["portal"] = "staff"
        sess["role"] = "staff"


class _School(unittest.TestCase):
    """One fresh school. Subclasses add the people they need."""

    def setUp(self) -> None:
        self._prev_local = os.environ.get("LOCAL_DEV_LOGIN")
        os.environ.pop("LOCAL_DEV_LOGIN", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url="",
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        reset_whiteboard_refresh()
        reset_logs()

    def tearDown(self) -> None:
        reset_logs()
        reset_whiteboard_refresh()
        self.school.close()
        self.tmp.cleanup()
        if self._prev_local is None:
            os.environ.pop("LOCAL_DEV_LOGIN", None)
        else:
            os.environ["LOCAL_DEV_LOGIN"] = self._prev_local

    def _teacher(self, email: str) -> dict:
        """Register a staff user and return the row. No login grant."""
        return self.school.register_staff(email)

    def _class_for(self, teacher_id: int, names: list[str]) -> int:
        """Create one class owned by ``teacher_id`` and return its id."""
        offering = self.school.assign_course(
            teacher_user_id=int(teacher_id), ontario_code="MCF3M"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=names,
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher_id),
        )
        return int(created["id"])

    def _live(self, class_id: int, teacher_id: int) -> dict:
        """Start a live session and return the row."""
        return self.school.start_live_class_session(int(class_id), int(teacher_id))


class SlidesImportGateTests(_School):
    """MCK-346: import routes 404 without slides_import. Built-in deck reads stay."""

    def setUp(self) -> None:
        super().setUp()
        self.teacher = self._teacher("gate-slides@example.com")
        self.class_id = self._class_for(int(self.teacher["id"]), ["Ada", "Bea"])
        self.client = self.app.test_client()
        _sign_in_basic(self.client, int(self.teacher["id"]))

    def test_basic_teacher_gets_404_on_each_import_route(self) -> None:
        """Share-link import, job poll, and Slides connect are 404."""
        post = self.client.post(
            f"/api/classes/{self.class_id}/slide-deck",
            json={"module": "M1", "slot": "C1", "url": "https://docs.google.com/presentation/d/DeckSlide01/edit"},
        )
        self.assertEqual(post.status_code, 404)
        job = self.client.get("/api/slide-imports/" + ("ab" * 16))
        self.assertEqual(job.status_code, 404)
        status = self.client.get("/api/auth/slides-status")
        self.assertEqual(status.status_code, 404)
        for path in (
            "/auth/google/slides",
            "/auth/google/slides/callback",
            "/auth/google/slides-read",
            "/auth/google/slides-read/callback",
        ):
            got = self.client.get(path)
            self.assertEqual(got.status_code, 404, path)

    def test_builtin_deck_read_and_delete_stay_open(self) -> None:
        """GET and DELETE are not the import branch."""
        read = self.client.get(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
        )
        self.assertNotEqual(read.status_code, 404)
        remove = self.client.delete(
            f"/api/classes/{self.class_id}/slide-deck?module=M1&slot=C1"
        )
        self.assertNotEqual(remove.status_code, 404)

    def test_teacher_with_the_feature_is_not_404(self) -> None:
        """Turning slides_import on leaves the import routes reachable."""
        set_feature_override(self.school, int(self.teacher["id"]), "slides_import", True)
        status = self.client.get("/api/auth/slides-status")
        self.assertNotEqual(status.status_code, 404)
        self.assertEqual(status.status_code, 200)
        connect = self.client.get("/auth/google/slides")
        self.assertNotEqual(connect.status_code, 404)


class WhiteboardGateTests(_School):
    """MCK-347: real-time boards are pro. Submit-only overviews refresh slowly."""

    def setUp(self) -> None:
        super().setUp()
        self.teacher = self._teacher("gate-board@example.com")
        self.class_id = self._class_for(int(self.teacher["id"]), ["One", "Two"])
        self.live = self._live(self.class_id, int(self.teacher["id"]))
        self.session_id = int(self.live["id"])
        self.staff = self.app.test_client()
        _sign_in_basic(self.staff, int(self.teacher["id"]))

    def _join(self, name: str):
        """Join one seat and return that phone. The code is not asserted."""
        phone = self.app.test_client()
        joined = phone.post(
            "/auth/student-code",
            data={"code": str(self.live["session_code"]), "name": name},
        )
        self.assertEqual(joined.status_code, 302)
        return phone

    def test_submit_overview_holds_a_second_immediate_refresh(self) -> None:
        """The first gallery read works. The next one inside 30 seconds waits."""
        first = self.staff.get(f"/api/live-sessions/{self.session_id}/whiteboard-gallery")
        self.assertNotEqual(first.status_code, 404)
        self.assertNotEqual(first.status_code, 429)
        second = self.staff.get(f"/api/live-sessions/{self.session_id}/whiteboard-gallery")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers.get("Retry-After"), "30")
        tapped = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery?tap=1"
        )
        self.assertNotEqual(tapped.status_code, 429)
        self.assertNotEqual(tapped.status_code, 404)

    def test_both_whiteboard_flags_off_is_404_for_staff_and_student(self) -> None:
        """A teacher with neither whiteboard key, and their student, get 404."""
        set_feature_override(self.school, int(self.teacher["id"]), "whiteboard_submit", False)
        staff_paths = (
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery",
            f"/api/live-sessions/{self.session_id}/board/teacher",
        )
        for path in staff_paths:
            got = self.staff.get(path)
            self.assertEqual(got.status_code, 404, path)
        presence = self.staff.post(
            f"/api/live-sessions/{self.session_id}/canvas-presence",
            json={"x": 0.1, "y": 0.2},
        )
        self.assertEqual(presence.status_code, 404)
        phone = self._join("One")
        student_board = phone.get("/api/student/board/mine?since=0")
        self.assertEqual(student_board.status_code, 404)
        student_ink = phone.post(
            "/api/student/canvas-presence",
            json={"x": 0.2, "y": 0.3, "stroke_id": "pen"},
        )
        self.assertEqual(student_ink.status_code, 404)

    def test_realtime_teacher_can_refresh_the_gallery_twice(self) -> None:
        """whiteboard on removes the 30 second hold."""
        set_feature_override(self.school, int(self.teacher["id"]), "whiteboard", True)
        reset_whiteboard_refresh()
        first = self.staff.get(f"/api/live-sessions/{self.session_id}/whiteboard-gallery")
        second = self.staff.get(f"/api/live-sessions/{self.session_id}/whiteboard-gallery")
        self.assertNotEqual(first.status_code, 429)
        self.assertNotEqual(second.status_code, 429)
        self.assertNotEqual(first.status_code, 404)
        self.assertNotEqual(second.status_code, 404)

    def test_submit_stroke_does_not_reload_the_other_phone(self) -> None:
        """One student's ink must not move the other phone's /state (MCK-283)."""
        first = self._join("One")
        second = self._join("Two")
        before = second.get("/api/student/state")
        self.assertEqual(before.status_code, 200)
        prior = before.get_json() or {}
        ink = first.post(
            "/api/student/canvas-presence",
            json={"x": 0.4, "y": 0.4, "stroke_id": "solo-pen", "points": [[0.4, 0.4], [0.5, 0.5]]},
        )
        self.assertNotEqual(ink.status_code, 404)
        again = second.get(
            "/api/student/state",
            query_string={"seq": prior.get("state_seq"), "stamp": prior.get("stamp")},
        )
        self.assertEqual(again.status_code, 200)
        body = again.get_json() or {}
        self.assertTrue(body.get("unchanged"))
        tape = log_for(self.app.config["SCHOOL_DB"].data_dir)
        kinds = [str(row.get("type") or "") for row in tape.since(self.session_id, 0)]
        self.assertNotIn("board_ink", kinds)


class OtherFeatureGateTests(_School):
    """MCK-349: media, draws, portfolio Doc, news extras, catch-up, and caps."""

    def test_basic_media_team_draw_and_drive_are_404(self) -> None:
        """Off features 404. The same routes answer once the key is on."""
        teacher = self._teacher("gate-extra@example.com")
        class_id = self._class_for(int(teacher["id"]), ["Ada", "Bea"])
        live = self._live(class_id, int(teacher["id"]))
        session_id = int(live["id"])
        client = self.app.test_client()
        _sign_in_basic(client, int(teacher["id"]))
        media = client.get(f"/api/live-sessions/{session_id}/active-media")
        self.assertEqual(media.status_code, 404)
        draw = client.get(f"/api/live-sessions/{session_id}/items/1/team-draw/gallery")
        self.assertEqual(draw.status_code, 404)
        added = client.post(
            f"/api/staff/class/{class_id}/live-lessons/M1/C1/add-question",
            json={"type": "team_draw", "text": "Sketch", "page_number": 1},
        )
        self.assertEqual(added.status_code, 404)
        drive = client.post(
            f"/api/classes/{class_id}/portfolio/generate",
            json={"module": 1, "drive": True},
        )
        self.assertEqual(drive.status_code, 404)
        set_feature_override(self.school, int(teacher["id"]), "media", True)
        set_feature_override(self.school, int(teacher["id"]), "team_draw", True)
        set_feature_override(self.school, int(teacher["id"]), "portfolio_gdoc", True)
        self.assertNotEqual(
            client.get(f"/api/live-sessions/{session_id}/active-media").status_code, 404
        )
        self.assertNotEqual(
            client.post(
                f"/api/staff/class/{class_id}/live-lessons/M1/C1/add-question",
                json={"type": "team_draw", "text": "Sketch", "page_number": 1},
            ).status_code,
            404,
        )
        self.assertNotEqual(
            client.post(
                f"/api/classes/{class_id}/portfolio/generate",
                json={"module": 1, "drive": True},
            ).status_code,
            404,
        )

    def test_student_team_draw_follows_the_session_owner(self) -> None:
        """A student in a basic session cannot post a team drawing."""
        teacher = self._teacher("gate-student-draw@example.com")
        class_id = self._class_for(int(teacher["id"]), ["Ada", "Bea"])
        live = self._live(class_id, int(teacher["id"]))
        phone = self.app.test_client()
        joined = phone.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "Ada"},
        )
        self.assertEqual(joined.status_code, 302)
        set_feature_override(self.school, int(teacher["id"]), "team_draw", True)
        staff = self.app.test_client()
        _sign_in_basic(staff, int(teacher["id"]))
        added = staff.post(
            f"/api/staff/class/{class_id}/live-lessons/M1/C1/add-question",
            json={"type": "team_draw", "text": "Sketch", "page_number": 1, "stage": "round"},
        )
        self.assertEqual(added.status_code, 200)
        placed = str(added.get_json()["placement"]["item"]["id"])
        self.school.ensure_live_session_items(int(live["id"]))
        rows = [
            row
            for row in self.school.list_live_session_items(int(live["id"]))
            if str(row.get("item_id") or "") == placed
        ]
        self.assertTrue(rows)
        item_id = int(rows[-1]["id"])
        set_feature_override(self.school, int(teacher["id"]), "team_draw", False)
        denied = phone.post(
            f"/api/student/live-items/{item_id}/team-draw",
            json={"image": "abc"},
        )
        self.assertEqual(denied.status_code, 404)
        set_feature_override(self.school, int(teacher["id"]), "team_draw", True)
        allowed = phone.post(
            f"/api/student/live-items/{item_id}/team-draw",
            json={"image": "abc"},
        )
        self.assertNotEqual(allowed.status_code, 404)

    def test_news_extras_drop_for_a_basic_session(self) -> None:
        """Board ink and media postcards stay off the wire. Question postcards stay."""
        teacher = self._teacher("gate-news@example.com")
        class_id = self._class_for(int(teacher["id"]), ["Ada"])
        live = self._live(class_id, int(teacher["id"]))
        session_id = int(live["id"])
        with self.app.app_context():
            kept = news_events_for_session(
                session_id,
                [
                    {"type": "state_seq", "state_seq": 1},
                    {"type": "board_ink", "board_key": "solo:1"},
                    {"type": "active_media"},
                    {"type": "google_slide"},
                    {"type": "flag_work", "kind": "publish"},
                ],
            )
        kinds = [str(row.get("type") or "") for row in kept]
        self.assertEqual(kinds, ["state_seq", "flag_work"])
        set_feature_override(self.school, int(teacher["id"]), "whiteboard", True)
        set_feature_override(self.school, int(teacher["id"]), "media", True)
        with self.app.app_context():
            opened = news_events_for_session(
                session_id,
                [
                    {"type": "board_ink", "board_key": "solo:1"},
                    {"type": "active_media"},
                ],
            )
        opened_kinds = [str(row.get("type") or "") for row in opened]
        self.assertEqual(opened_kinds, ["board_ink", "active_media"])

    def test_catchup_scope_keeps_only_the_newest_for_basic(self) -> None:
        """Full catch-up keeps every row. Basic keeps the last in-window row."""
        teacher = self._teacher("gate-catchup@example.com")
        rows = [
            {"live_module": "M1", "live_slot": "C1", "run_key": "older", "created_at": "2026-10-01T00:00:00"},
            {"live_module": "M1", "live_slot": "C2", "run_key": "newer", "created_at": "2026-10-28T00:00:00"},
        ]
        with self.app.app_context():
            basic = scope_catchup_links(teacher, rows)
            self.assertEqual(len(basic), 1)
            self.assertEqual(basic[0]["run_key"], "newer")
            set_feature_override(self.school, int(teacher["id"]), "catchup_full", True)
            fresh = self.school.get_user(int(teacher["id"]))
            full = scope_catchup_links(fresh, rows)
        self.assertEqual(len(full), 2)

    def test_basic_caps_are_three_classes_and_thirty_five_seats(self) -> None:
        """The fourth class and a 36th name are refused. Three classes of two are not."""
        teacher = self._teacher("gate-caps@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        client = self.app.test_client()
        _sign_in_basic(client, int(teacher["id"]))
        too_many_names = client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": [f"N{i}" for i in range(36)],
            },
        )
        self.assertEqual(too_many_names.status_code, 400)
        made = []
        for _ in range(3):
            got = client.post(
                "/api/staff/classes",
                json={
                    "offering_id": int(offering["id"]),
                    "days": "M/W/F",
                    "time": "2:00pm",
                    "codenames": ["Ada", "Bea"],
                },
            )
            self.assertEqual(got.status_code, 200)
            made.append(int(got.get_json()["class"]["id"]))
        fourth = client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Ada", "Bea"],
            },
        )
        self.assertEqual(fourth.status_code, 400)
        self.assertEqual(len(made), 3)
        set_feature_override(self.school, int(teacher["id"]), "catchup_full", True)
        fifth = client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Ada", "Bea"],
            },
        )
        self.assertEqual(fifth.status_code, 200)


class StudentOwnerResolveTests(_School):
    """A student page follows the live room, not another signed-in teacher."""

    def test_page_flags_ignore_a_signed_in_teacher_and_a_body_session(self) -> None:
        """The join cookie wins over a staff login and a posted session id."""
        owner = self._teacher("resolve-owner@example.com")
        other = self._teacher("resolve-other@example.com")
        set_feature_override(self.school, int(other["id"]), "media", True)
        set_feature_override(self.school, int(other["id"]), "slides_import", True)
        class_a = self._class_for(int(owner["id"]), ["Ada"])
        class_b = self._class_for(int(other["id"]), ["Bea"])
        live_a = self._live(class_a, int(owner["id"]))
        live_b = self._live(class_b, int(other["id"]))
        with self.app.test_request_context(
            "/api/student/state",
            method="POST",
            json={"session_id": int(live_b["id"]), "teacher_user_id": int(other["id"])},
        ):
            from flask import session

            session["logged_in"] = True
            session["user_id"] = int(other["id"])
            session["portal"] = "staff"
            session["role"] = "staff"
            session["student_live_session_id"] = int(live_a["id"])
            self.assertFalse(_caller_has_feature("media", {}))
            self.assertFalse(_caller_has_feature("slides_import", {}))
            self.assertTrue(_caller_has_feature("live_slides", {}))
            self.assertTrue(_caller_has_feature("whiteboard_submit", {}))
            flags = features_for_page()
            self.assertFalse(flags["media"])
            self.assertTrue(flags["mc_questions"])
            self.assertFalse(flags["whiteboard"])
        with self.app.test_request_context("/api/live-sessions/1/active-media"):
            from flask import session

            session["logged_in"] = True
            session["user_id"] = int(other["id"])
            session["portal"] = "staff"
            session["role"] = "staff"
            self.assertTrue(_caller_has_feature("media", {}))
            self.assertTrue(features_for_page()["media"])


def _chrome() -> str:
    """First Chrome binary on this machine, or an empty string."""
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ):
        if path and Path(path).exists():
            return path
    return ""


def _playwright_ready() -> bool:
    """True when node, Chrome, and playwright-core can launch."""
    if not (shutil.which("node") and _chrome()):
        return False
    node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "/tmp/pw/node_modules")
    probe = subprocess.run(
        ["node", "-e", "require.resolve('playwright-core')"],
        env={**os.environ, "NODE_PATH": node_path},
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@unittest.skipUnless(_playwright_ready(), "node, Chrome, and playwright-core are required")
class FeatureScreenTests(_School):
    """MCK-348: a new teacher does not see off controls. A granted teacher does."""

    def _cookies(self, client) -> dict[str, str]:
        """Session cookies for the browser. Values are not logged."""
        return {cookie.key: cookie.value for cookie in client._cookies.values()}

    def _page_client(self, email: str, *, basic: bool, boards_off: bool = False):
        """Sign in through the mock callback, then drop the grant when ``basic``.

        ``boards_off`` also turns both whiteboard keys off, which is the
        registry state where the Whiteboard tab is gone.

        Args:
            email: Staff email. Not printed.
            basic: Delete the launch rows so plan defaults apply.
            boards_off: Override both whiteboard keys off after that.
        """
        teacher = self._teacher(email)
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=T")
        client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email(email)["verification_code"]},
        )
        created = client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Ada", "Bea"],
            },
        )
        self.assertEqual(created.status_code, 200)
        class_id = int(created.get_json()["class"]["id"])
        if basic or boards_off:
            self.school.conn.execute(
                "DELETE FROM user_features WHERE user_id = ?",
                (int(teacher["id"]),),
            )
        if boards_off:
            set_feature_override(
                self.school, int(teacher["id"]), "whiteboard", False
            )
            set_feature_override(
                self.school, int(teacher["id"]), "whiteboard_submit", False
            )
        return client, class_id, int(teacher["id"])

    def _live_for(self, client, class_id: int) -> dict:
        """Start the live class and return the session row. The code is not logged."""
        started = client.post(
            f"/staff/class/{class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(started.status_code, 302)
        live = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(live)
        assert live is not None
        return live

    def _phone(self, code: str, name: str, *, also_user_id: int | None = None):
        """Join through mood and character. Optionally keep a staff login too."""
        phone = self.app.test_client()
        joined = phone.post(
            "/auth/student-code",
            data={"code": code, "name": name},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302)
        self.assertEqual(phone.post("/student/mood", data={"mood": "good"}).status_code, 302)
        picked = phone.post("/student/character", data={"character": "fox"})
        self.assertIn(picked.status_code, (200, 302))
        if also_user_id is not None:
            with phone.session_transaction() as sess:
                sess["logged_in"] = True
                sess["user_id"] = int(also_user_id)
                sess["portal"] = "staff"
                sess["role"] = "staff"
        return phone

    def test_screens_follow_the_plan(self) -> None:
        """New teachers hide off surfaces. A granted teacher keeps them.

        ``whiteboard_submit`` stays on for a new teacher, so the Whiteboard
        tab stays in refresh mode. Turning both whiteboard keys off removes
        the tab, the canvas, and the gallery request. Student pages follow
        the session owner even when another teacher is signed in.
        """
        from werkzeug.serving import make_server

        basic_client, basic_class, basic_id = self._page_client(
            "screen-basic@example.com", basic=True
        )
        full_client, full_class, full_id = self._page_client(
            "screen-full@example.com", basic=False
        )
        off_client, off_class, _off_id = self._page_client(
            "screen-boards@example.com", basic=False, boards_off=True
        )
        basic_live = self._live_for(basic_client, basic_class)
        full_live = self._live_for(full_client, full_class)
        off_live = self._live_for(off_client, off_class)
        basic_phone = self._phone(
            str(basic_live["session_code"]), "Ada", also_user_id=full_id
        )
        full_phone = self._phone(
            str(full_live["session_code"]), "Bea", also_user_id=basic_id
        )
        off_phone = self._phone(str(off_live["session_code"]), "Ada")
        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        art = Path("/opt/cursor/artifacts")
        art.mkdir(parents=True, exist_ok=True)
        shots = {
            "basic": art / "mck348-basic-live.png",
            "full": art / "mck348-full-live.png",
            "boards_off": art / "mck348-boards-off-live.png",
            "basic_student": art / "mck348-basic-student.png",
            "full_student": art / "mck348-full-student.png",
            "boards_off_student": art / "mck348-boards-off-student.png",
        }
        spec = {
            "port": server.server_port,
            "chrome": _chrome(),
            "pages": [
                {
                    "name": "basic",
                    "cookies": self._cookies(basic_client),
                    "path": f"/staff/class/{basic_class}?tab=live",
                    "shot": str(shots["basic"]),
                    "ready": "#live-content-tabs",
                },
                {
                    "name": "full",
                    "cookies": self._cookies(full_client),
                    "path": f"/staff/class/{full_class}?tab=live",
                    "shot": str(shots["full"]),
                    "ready": "#live-content-tabs",
                },
                {
                    "name": "boards_off",
                    "cookies": self._cookies(off_client),
                    "path": f"/staff/class/{off_class}?tab=live",
                    "shot": str(shots["boards_off"]),
                    "ready": "#live-content-tabs",
                },
                {
                    "name": "basic_student",
                    "cookies": self._cookies(basic_phone),
                    "path": "/student/home",
                    "shot": str(shots["basic_student"]),
                    "ready": "#live-response",
                },
                {
                    "name": "full_student",
                    "cookies": self._cookies(full_phone),
                    "path": "/student/home",
                    "shot": str(shots["full_student"]),
                    "ready": "#live-response",
                },
                {
                    "name": "boards_off_student",
                    "cookies": self._cookies(off_phone),
                    "path": "/student/home",
                    "shot": str(shots["boards_off_student"]),
                    "ready": "#live-response",
                },
            ],
        }
        cfg = Path(self.tmp.name) / "feature-gates-e2e.json"
        cfg.write_text(json.dumps(spec), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["node", str(LMS_DIR / "e2e" / "feature_gates_mck348.mjs"), str(cfg)],
                env={
                    **os.environ,
                    "NODE_PATH": os.environ.get("LLOVES_E2E_NODE_PATH", "/tmp/pw/node_modules"),
                },
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, (proc.stderr or "")[-1500:])
        out = json.loads(lines[-1])
        for name in shots:
            self.assertEqual(out[name]["errors"], [], name)
            self.assertTrue(shots[name].is_file(), name)
        basic = out["basic"]["counts"]
        full = out["full"]["counts"]
        boards = out["boards_off"]["counts"]
        self.assertEqual(basic["media"], 0)
        self.assertEqual(basic["mediaZone"], 0)
        self.assertEqual(basic["importForm"], 0)
        self.assertEqual(basic["googleStage"], 0)
        self.assertEqual(basic["questions"], 1)
        self.assertEqual(basic["slides"], 1)
        self.assertEqual(basic["whiteboard"], 1)
        self.assertEqual(basic["canvasZone"], 1)
        self.assertEqual(basic["refresh"], 1)
        self.assertEqual(basic["livePill"], 0)
        self.assertFalse(basic["flags"]["media"])
        self.assertFalse(basic["flags"]["slides_import"])
        self.assertTrue(basic["flags"]["live_slides"])
        self.assertTrue(basic["flags"]["whiteboard_submit"])
        self.assertFalse(any("active-media" in row for row in out["basic"]["requests"]))
        self.assertEqual(full["media"], 1)
        self.assertEqual(full["mediaZone"], 1)
        self.assertEqual(full["importForm"], 1)
        self.assertEqual(full["googleStage"], 1)
        self.assertEqual(full["questions"], 1)
        self.assertEqual(full["slides"], 1)
        self.assertEqual(full["whiteboard"], 1)
        self.assertEqual(full["livePill"], 1)
        self.assertTrue(full["flags"]["media"])
        self.assertEqual(boards["questions"], 1)
        self.assertEqual(boards["slides"], 1)
        self.assertEqual(boards["media"], 0)
        self.assertEqual(boards["whiteboard"], 0)
        self.assertEqual(boards["canvasZone"], 0)
        self.assertEqual(boards["importForm"], 0)
        self.assertEqual(boards["googleStage"], 0)
        self.assertEqual(boards["refresh"], 0)
        self.assertFalse(any("whiteboard-gallery" in row for row in out["boards_off"]["requests"]))
        self.assertFalse(any("/board/" in row for row in out["boards_off"]["requests"]))
        self.assertFalse(any("active-media" in row for row in out["boards_off"]["requests"]))
        basic_student = out["basic_student"]["counts"]
        full_student = out["full_student"]["counts"]
        off_student = out["boards_off_student"]["counts"]
        self.assertEqual(basic_student["mediaPane"], 0)
        self.assertEqual(basic_student["slidesCard"], 0)
        self.assertEqual(basic_student["slidesFrame"], 1)
        self.assertEqual(basic_student["canvasPane"], 1)
        self.assertEqual(basic_student["cursors"], 0)
        self.assertEqual(basic_student["teamDrawMystery"], 0)
        self.assertEqual(basic_student["teamDrawPhone"], 1)
        self.assertFalse(basic_student["flags"]["media"])
        self.assertTrue(basic_student["flags"]["whiteboard_submit"])
        self.assertFalse(any("/board/" in row for row in out["basic_student"]["requests"]))
        self.assertFalse(any("active-media" in row for row in out["basic_student"]["requests"]))
        self.assertEqual(full_student["mediaPane"], 1)
        self.assertEqual(full_student["slidesCard"], 1)
        self.assertEqual(full_student["canvasPane"], 1)
        self.assertEqual(full_student["cursors"], 1)
        self.assertEqual(full_student["teamDrawMystery"], 1)
        self.assertTrue(full_student["flags"]["media"])
        self.assertTrue(full_student["flags"]["whiteboard"])
        self.assertEqual(off_student["canvasPane"], 0)
        self.assertEqual(off_student["mediaPane"], 0)
        self.assertEqual(off_student["slidesCard"], 0)
        self.assertEqual(off_student["slidesFrame"], 1)
        self.assertEqual(off_student["cursors"], 0)
        self.assertEqual(off_student["teamDrawPhone"], 0)
        self.assertFalse(any("/board/" in row for row in out["boards_off_student"]["requests"]))
        self.assertFalse(any("whiteboard-gallery" in row for row in out["boards_off_student"]["requests"]))


if __name__ == "__main__":
    unittest.main()
