"""MCK-494: A&P tabs, Overview, and the Run Live Class Begin Module prompt."""

from __future__ import annotations

import uuid
from pathlib import Path

from begin_module import live_start_dialog_copy, live_start_should_prompt
from live_begin_prompt import confirm_begin_then_start
from test_mck339_begin_module import _seed_held_class
from test_participation_mck198 import Base


LMS_DIR = Path(__file__).resolve().parent


def _sign_in(client, school, user) -> None:
    """Open a staff session for a user that is already registered.

    The callback issues a fresh verification code. This reads that code
    after the callback. The address is not logged.

    Args:
        client: Flask test client.
        school: ``SchoolDB`` used to read the verification code.
        user: Staff user row.
    """
    client.get("/auth/google?portal=staff")
    client.get("/auth/google/callback?email=" + str(user["email"]) + "&name=T")
    fresh = school.get_user_by_email(str(user["email"]))
    client.post("/verify-email", data={"code": fresh["verification_code"]})


class CopyTests(Base):
    """Dialog sentences. No database writes."""

    def test_live_start_copy_names_the_module_and_section(self) -> None:
        copy = live_start_dialog_copy(5, "MCR3U-2")
        self.assertEqual(copy["title"], "Begin Module 5 for MCR3U-2?")
        self.assertEqual(
            copy["body"],
            "No Module 5 classes are saved for MCR3U-2 yet. "
            "Beginning a new module wraps up the current module in "
            "Attendance & Participation for this class only. "
            "This can't be undone.",
        )
        self.assertEqual(copy["confirm"], "Begin Module 5")
        self.assertEqual(copy["skip"], "Not yet, just run class")
        self.assertIn("This can't be undone.", copy["body"])

    def test_prompt_gates(self) -> None:
        """Next module with no saved runs prompts. The other cases do not."""
        yes = dict(
            can_begin=True,
            next_module="M5",
            target_module="M5",
            target_has_meetings=False,
            is_test=False,
            is_office=False,
        )
        self.assertTrue(live_start_should_prompt(**yes))
        self.assertFalse(live_start_should_prompt(**{**yes, "is_test": True}))
        self.assertFalse(live_start_should_prompt(**{**yes, "is_office": True}))
        self.assertFalse(live_start_should_prompt(**{**yes, "can_begin": False}))
        self.assertFalse(live_start_should_prompt(**{**yes, "target_module": "M7"}))
        self.assertFalse(live_start_should_prompt(**{**yes, "target_has_meetings": True}))
        self.assertTrue(
            live_start_should_prompt(
                **{**yes, "can_begin": False, "blocked_only_by_setup_shell": True}
            )
        )


class PageTests(Base):
    """Attendance & Participation renders every tab without a reload."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        _sign_in(self.client, self.school, self.teacher)

    def test_overview_is_the_landing_tab(self) -> None:
        page = self.client.get(f"/staff/class/{self.class_id}?tab=ap")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-ap-view="overview"', html)
        self.assertIn('data-ap-tab="overview"', html)
        self.assertIn(">Overview<", html)
        self.assertIn('data-ap-tab="past"', html)
        self.assertIn(">Past Modules<", html)
        self.assertLess(html.index('data-ap-tab="overview"'), html.index('data-ap-tab="past"'))
        self.assertLess(html.index('data-ap-tab="past"'), html.index('data-ap-tab="attendance"'))
        self.assertLess(html.index("ap-subtabs"), html.index("begin-mod-current-table"))
        self.assertIn('id="ap-panel-overview" data-ap-panel="overview">', html)
        self.assertIn('id="ap-panel-past" data-ap-panel="past" hidden', html)
        self.assertIn('id="ap-panel-attendance" data-ap-panel="attendance" hidden', html)
        overview = html.split('id="ap-panel-overview"', 1)[1].split('id="ap-panel-past"', 1)[0]
        self.assertNotIn("begin-mod-past", overview)
        self.assertIn('id="live-fix-history"', html)
        self.assertIn("End Class report", html)
        self.assertLess(html.index("begin-mod-current-table"), html.index("live-fix-history"))
        self.assertNotIn('class="ap-roster-pin"', html)
        self.assertNotIn("Set new PIN", html)
        self.assertNotIn("/roster-pins", html)
        self.assertIn("/static/staff_ap_tabs.js", html)
        tabs = (LMS_DIR / "static" / "staff_ap_tabs.js").read_text(encoding="utf-8")
        self.assertIn("preventDefault", tabs)
        self.assertIn("pushState", tabs)
        self.assertNotIn("location.assign", tabs)
        self.assertNotIn("location.reload", tabs)
        self.assertIn("/roster-pins", tabs)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("padding: 0.28rem 0.55rem;", css)

    def test_past_table_is_display_only(self) -> None:
        """The Past Modules tab shows the old table and does not delete rows."""
        _seed_held_class(self.school, self.class_id, self.students)
        self.school.begin_new_module(self.class_id, "M1", user_id=int(self.teacher["id"]))
        before_marks = int(
            self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM participation_module_marks WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()["n"]
        )
        before_scores = int(
            self.school.conn.execute("SELECT COUNT(*) AS n FROM session_scores").fetchone()["n"]
        )
        before_credits = int(
            self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_participation_credits"
            ).fetchone()["n"]
        )
        page = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=past")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="ap-panel-past" data-ap-panel="past">', html)
        self.assertIn('id="begin-mod-past"', html)
        self.assertIn("<h3>Past modules</h3>", html)
        self.assertIn("1 / 1", html)
        self.assertIn("<th>Name</th>", html)
        self.assertIn("<th>Attended</th>", html)
        self.assertIn("<th>Participation</th>", html)
        self.assertIn("<th>%</th>", html)
        past_html = html.split('id="ap-panel-past"', 1)[1].split(
            'id="ap-panel-attendance"', 1
        )[0]
        self.assertNotIn("begin-mod-confirm", past_html)
        anon = self.app.test_client()
        locked = anon.get(f"/staff/class/{self.class_id}?tab=ap&view=past")
        self.assertIn(locked.status_code, {401, 302, 403})
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        after_marks = int(
            self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM participation_module_marks WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()["n"]
        )
        after_scores = int(
            self.school.conn.execute("SELECT COUNT(*) AS n FROM session_scores").fetchone()["n"]
        )
        after_credits = int(
            self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_participation_credits"
            ).fetchone()["n"]
        )
        self.assertEqual(after_marks, before_marks)
        self.assertEqual(after_scores, before_scores)
        self.assertEqual(after_credits, before_credits)

    def test_roster_tab_loads_pins_only_when_opened(self) -> None:
        overview = self.client.get(f"/staff/class/{self.class_id}?tab=ap").get_data(as_text=True)
        self.assertNotIn('class="ap-roster-pin"', overview)
        direct = self.client.get(f"/staff/class/{self.class_id}?tab=ap&view=roster")
        direct_html = direct.get_data(as_text=True)
        self.assertEqual(direct.headers.get("Cache-Control"), "no-store")
        self.assertIn('class="ap-roster-pin"', direct_html)
        self.assertIn("Set new PIN", direct_html)
        anon = self.app.test_client()
        locked = anon.get(f"/api/staff/class/{self.class_id}/roster-pins")
        self.assertIn(locked.status_code, {401, 302, 403})
        pins = self.client.get(f"/api/staff/class/{self.class_id}/roster-pins")
        self.assertEqual(pins.status_code, 200)
        self.assertEqual(pins.headers.get("Cache-Control"), "no-store")
        body = pins.get_data(as_text=True)
        self.assertIn('class="ap-roster-pin"', body)
        self.assertIn("••••", body)
        self.assertNotIn("Your catch-up PIN:", body)


class BeginPromptTests(Base):
    """Run Live Class offers Begin Module only for the next real module."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        _sign_in(self.client, self.school, self.teacher)

    def _prompt(self, module: str, slot: str = "C1", *, is_test: bool = False):
        """GET the prompt for one Set Class choice."""
        query = f"module={module}&slot={slot}"
        if is_test:
            query += "&is_test=1"
        return self.client.get(
            f"/api/classes/{self.class_id}/live-session/begin-module-prompt?{query}"
        )

    def test_next_module_with_no_saved_runs_prompts(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        response = self._prompt("M2")
        self.assertEqual(response.status_code, 200)
        prompt = response.get_json()["prompt"]
        section = self.school._section_code_for_class(self.class_id)
        self.assertEqual(prompt["title"], f"Begin Module 2 for {section}?")
        self.assertIn("This can't be undone.", prompt["body"])
        self.assertEqual(prompt["confirm"], "Begin Module 2")
        self.assertEqual(prompt["skip"], "Not yet, just run class")
        self.assertNotIn("name", prompt)

    def test_held_zero_does_not_prompt(self) -> None:
        response = self._prompt("M2")
        self.assertIsNone(response.get_json()["prompt"])

    def test_jump_ahead_does_not_prompt(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        self.assertEqual(
            self.school.begin_module_board(self.class_id)["next_module"],
            "M2",
        )
        self.assertIsNone(self._prompt("M5").get_json()["prompt"])

    def test_test_office_discarded_and_quit_do_not_count_or_prompt(self) -> None:
        """A test class, Open Office, a discarded run, and a quit do not block M2."""
        _seed_held_class(self.school, self.class_id, self.students)
        now = "2026-10-14T15:00:00"
        run_key = f"discarded-{uuid.uuid4().hex}"
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 1, ?)
            """,
            (self.class_id, run_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key, item_json,
                prompt_kind, offered_at
            ) VALUES (?, ?, 'M2', 'C1', 'item:q', '{}', 'mc', ?)
            """,
            (self.class_id, run_key, now),
        )
        self.school.conn.commit()
        test = self.school.start_live_class_session(
            self.class_id,
            int(self.teacher["id"]),
            live_module="M2",
            live_slot="C1",
            is_test=True,
        )
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET status = 'ended', ended_at = ?, is_test = 1
            WHERE id = ?
            """,
            (now, int(test["id"])),
        )
        self.school.conn.commit()
        quit_row = self.school.start_live_class_session(
            self.class_id,
            int(self.teacher["id"]),
            live_module="M2",
            live_slot="C2",
        )
        self.school.finish_live_class(
            self.class_id,
            celebrate=False,
            persist=False,
            run_key=str(quit_row.get("run_key") or "") or None,
        )
        self.assertFalse(self.school._begin_module_meetings(self.class_id).get("M2"))
        self.assertIsNotNone(self._prompt("M2").get_json()["prompt"])
        self.assertIsNone(self._prompt("M2", is_test=True).get_json()["prompt"])
        self.assertIsNone(self._prompt("M2", slot="O1").get_json()["prompt"])

    def test_running_class_does_not_prompt(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        session = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.school.set_live_session_teacher_state(int(session["id"]), class_set=True)
        self.assertIsNone(self._prompt("M2").get_json()["prompt"])

    def test_confirm_wraps_before_the_session_and_skip_does_not(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        order: list[str] = []
        real_begin = self.school.begin_new_module
        real_start = self.school.start_live_class_session

        def begin(*args, **kwargs):
            """Record wrap-up and require no active live class."""
            order.append("begin")
            self.assertIsNone(self.school.get_active_live_session_for_class(self.class_id))
            return real_begin(*args, **kwargs)

        def start(*args, **kwargs):
            """Record the session insert after the wrap-up commit."""
            order.append("start")
            self.assertTrue(self.school.module_complete(self.class_id, "M1"))
            return real_start(*args, **kwargs)

        self.school.begin_new_module = begin
        self.school.start_live_class_session = start
        try:
            confirm_begin_then_start(
                self.school,
                self.class_id,
                int(self.teacher["id"]),
                live_module="M2",
                live_slot="C1",
                start=lambda school, class_id, teacher_id, **kwargs: school.start_live_class_session(
                    class_id,
                    teacher_id,
                    live_module=kwargs.get("live_module"),
                    live_slot=kwargs.get("live_slot"),
                    is_test=bool(kwargs.get("is_test")),
                ),
            )
        finally:
            self.school.begin_new_module = real_begin
            self.school.start_live_class_session = real_start
        self.assertEqual(order, ["begin", "start"])
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")
        active = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(active)
        self.school.finish_live_class(
            self.class_id,
            celebrate=False,
            persist=False,
            run_key=str(active.get("run_key") or "") or None,
        )

        fresh = self.school.start_live_class_session(
            self.class_id,
            int(self.teacher["id"]),
            live_module="M3",
            live_slot="C1",
        )
        self.assertEqual(int(fresh["class_id"]), self.class_id)
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")
        self.assertFalse(self.school.module_complete(self.class_id, "M2"))

    def test_http_confirm_commits_before_start_and_skip_does_not_wrap(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        order: list[str] = []
        real_begin = self.school.begin_new_module
        real_start = self.school.start_live_class_session

        def begin(*args, **kwargs):
            order.append("begin")
            self.assertIsNone(self.school.get_active_live_session_for_class(self.class_id))
            return real_begin(*args, **kwargs)

        def start(*args, **kwargs):
            order.append("start")
            return real_start(*args, **kwargs)

        self.school.begin_new_module = begin
        self.school.start_live_class_session = start
        try:
            skipped = self.client.post(
                f"/api/classes/{self.class_id}/live-session/start",
                json={"live_module": "M2", "live_slot": "C1"},
            )
            self.assertEqual(skipped.status_code, 200, skipped.get_data(as_text=True))
            self.assertEqual(order, ["start"])
            self.assertFalse(self.school.module_complete(self.class_id, "M1"))
            self.school.finish_live_class(
                self.class_id,
                celebrate=False,
                persist=False,
                run_key=str(skipped.get_json()["live_session"].get("run_key") or "") or None,
            )
            order.clear()
            confirmed = self.client.post(
                f"/api/classes/{self.class_id}/live-session/start",
                json={
                    "live_module": "M2",
                    "live_slot": "C1",
                    "begin_module": True,
                    "is_test": False,
                    "is_office": False,
                },
            )
        finally:
            self.school.begin_new_module = real_begin
            self.school.start_live_class_session = real_start
        self.assertEqual(confirmed.status_code, 200, confirmed.get_data(as_text=True))
        self.assertEqual(order, ["begin", "start"])
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")
        body = confirmed.get_json()
        self.assertNotIn("students", body)
        self.assertNotIn("roster", body)

    def test_setup_shell_is_released_before_wrap_up(self) -> None:
        _seed_held_class(self.school, self.class_id, self.students)
        shell = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.assertIsNotNone(self._prompt("M2").get_json()["prompt"])
        order: list[str] = []
        real_begin = self.school.begin_new_module

        def begin(*args, **kwargs):
            order.append("begin")
            self.assertIsNone(self.school.get_active_live_session_for_class(self.class_id))
            return real_begin(*args, **kwargs)

        self.school.begin_new_module = begin
        try:
            started = confirm_begin_then_start(
                self.school,
                self.class_id,
                int(self.teacher["id"]),
                live_module="M2",
                live_slot="C1",
                start=lambda school, class_id, teacher_id, **kwargs: school.start_live_class_session(
                    class_id,
                    teacher_id,
                    live_module=kwargs.get("live_module"),
                    live_slot=kwargs.get("live_slot"),
                ),
            )
        finally:
            self.school.begin_new_module = real_begin
        self.assertEqual(order, ["begin"])
        self.assertNotEqual(str(started.get("run_key") or ""), str(shell.get("run_key") or ""))
        self.assertEqual(self.school._begin_module_pointer(self.class_id), "M2")
        self.assertEqual(
            int(self.school.get_active_live_session_for_class(self.class_id)["id"]),
            int(started["id"]),
        )

    def test_live_page_has_the_in_page_dialog(self) -> None:
        html = self.client.get(f"/staff/class/{self.class_id}?tab=live").get_data(as_text=True)
        self.assertIn('id="begin-live-module-dialog"', html)
        self.assertIn('id="begin-live-module-confirm"', html)
        self.assertIn("Not yet, just run class", html)
        source = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        start = source.index("function openBeginLiveModuleDialog")
        dialog_fn = source[start:source.index("async function unfinishedModuleChoice")]
        self.assertIn("dialog.showModal()", dialog_fn)
        self.assertNotIn("confirm(", dialog_fn)
        self.assertIn("begin_module: true", source)
