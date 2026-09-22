#!/usr/bin/env python3
"""Teacher Run Live Class IA v2 shell markup and existing control IDs."""

from __future__ import annotations

import os
import subprocess
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
from live_class_metadata import empty_live_class_metadata  # noqa: E402
from meet_team import meet_team_prompt_payload  # noqa: E402
from minds_on import MINDS_ON_CHOICES  # noqa: E402
from teams_spark import teams_spark_prompt_payload  # noqa: E402


class LiveShellTests(unittest.TestCase):
    """Staff live tab is IA v2 single-line header + OptionsStrip + dual body."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher and rostered class."""
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
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = created.get_json()["class"]["id"]

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _use_legacy_live_metadata(self) -> None:
        """Route this test through the schema-v1 singleton compatibility path."""
        self.school.live_class_metadata_for_session = (
            lambda _sid: empty_live_class_metadata("MCF3M", "M1", "C1")
        )

    def test_live_tab_shell_has_header_and_dual_panes(self) -> None:
        """IA v2 shell IDs are present; v1 full-bleed leftovers are gone."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("live-shell-ia-v2", html)
        self.assertNotIn("live-shell-ia-v1", html)
        self.assertIn('id="live-header"', html)
        self.assertIn('id="live-stage-rail"', html)
        self.assertIn('id="live-stage-prev"', html)
        self.assertIn('id="live-stage-next"', html)
        self.assertIn('id="live-page-counter"', html)
        self.assertIn('id="live-page-name"', html)
        self.assertIn('id="live-add-page"', html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function isBlankOverlayLivePage()", js)
        artifact = js.split("function paintQuestionArtifact(")[1].split("function paintMeetChainChrome(")[0]
        self.assertIn("isBlankOverlayLivePage()", artifact)
        self.assertIn("paintLiveSlotPicks()", artifact)
        self.assertIn("paintLiveQuestionCards()", artifact)
        self.assertIn("hideLiveQuestionBody()", artifact)
        self.assertIn("hideTeamsSparkCard()", artifact)
        self.assertNotIn("paintLiveQuestionBody(", artifact)
        self.assertNotIn("Today I’m the teammate", artifact)
        self.assertNotIn("Waiting room · 1", artifact)
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Your answer:", student_js)
        self.assertIn('id="live-delete-page"', html)
        self.assertIn('id="live-add-page-dialog"', html)
        self.assertIn('id="live-delete-page-dialog"', html)
        self.assertNotIn('id="live-save-as"', html)
        self.assertNotIn("Save As", html)
        self.assertIn('value="welcome"', html)
        self.assertIn('value="winner"', html)
        self.assertIn("Blank page", html)
        self.assertIn("function submitAddLiveLessonPage(name, kind)", js)
        self.assertIn("kind: pageKind", js)
        self.assertNotIn('$("live-save-as")', js)
        self.assertIn("Set Class", html)
        self.assertNotIn("Attendance: 0", html)
        self.assertNotIn('id="live-stage-prev">Prev<', html)
        self.assertIn('id="live-stage-prev">Back<', html)
        self.assertIn('id="live-stage-next">Forward<', html)
        self.assertNotIn("live-stage-pill", html)
        self.assertNotIn("live-stage-btn", html)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('id="live-option-card"', html)
        self.assertIn("live-options-strip", html)
        self.assertNotIn('id="join-options-hint"', html)
        self.assertNotIn("Waiting for students to join.", html)
        self.assertIn('id="teams-option-card"', html)
        self.assertIn('id="meet-option-card"', html)
        self.assertIn('id="round-option-card"', html)
        self.assertIn('id="play-option-card"', html)
        self.assertIn('id="live-view-media"', html)
        self.assertIn('id="live-view-canvas"', html)
        self.assertIn('id="live-view-slides"', html)
        self.assertIn('id="live-question-list"', html)
        self.assertNotIn('id="live-round-type"', html)
        self.assertNotIn('id="live-round-set"', html)
        self.assertIn('id="class-list-pane"', html)
        self.assertIn("course-live", html)
        self.assertIn('id="team-assign-pane"', html)
        self.assertIn('id="live-active-content"', html)
        self.assertIn('id="live-content-tabs"', html)
        self.assertIn(">Content<", html)
        self.assertIn(">Media<", html)
        self.assertIn(">Questions<", html)
        self.assertIn(">Whiteboard<", html)
        self.assertIn(">Slides<", html)
        self.assertNotIn('id="live-edit-layout"', html)
        self.assertNotIn('id="live-layout-presets"', html)
        self.assertIn('id="live-frames"', html)
        self.assertIn('data-frame="A"', html)
        self.assertIn('data-frame="B"', html)
        self.assertIn('data-frame="C"', html)
        self.assertIn('id="live-canvas-stub"', html)
        self.assertIn('id="live-canvas-undo"', html)
        self.assertIn('id="live-canvas-redo"', html)
        self.assertIn('id="live-canvas-erase"', html)
        self.assertIn("canvas_ephemeral: true", html)
        self.assertIn('id="round-slide-settings"', html)
        self.assertIn('id="media-artifact-zone"', html)
        self.assertIn('id="question-artifact-zone"', html)
        self.assertIn('id="results-strip"', html)
        self.assertIn('id="results-strip" class="live-flag-panel" data-flag="score" hidden', html)
        q_index = html.index('id="question-artifact-zone"')
        r_index = html.index('id="results-strip"')
        self.assertLess(q_index, r_index)
        self.assertGreater(html.find("</section>", r_index), r_index)
        self.assertNotIn('id="track-accordion"', html)
        self.assertNotIn("data-accordion-toggle", html)
        self.assertEqual(html.count('id="ap-media-preview"'), 1)
        self.assertIn('id="ap-media-copy-editor"', html)
        self.assertIn('id="ap-media-stem"', html)
        self.assertIn('id="live-add-question-btn"', html)
        self.assertIn('id="live-add-question-dialog"', html)
        type_row = html[
            html.index('class="live-add-type-chip-row"') : html.index('id="live-add-q-text"')
        ]
        self.assertIn('name="live-add-q-type" value="mc" checked', type_row)
        self.assertIn('name="live-add-q-type" value="numeric"', type_row)
        self.assertIn('name="live-add-q-type" value="poll"', type_row)
        self.assertIn(">mc</span>", type_row)
        self.assertIn(">numeric</span>", type_row)
        self.assertIn(">poll</span>", type_row)
        self.assertNotIn('value="essay"', type_row)
        self.assertNotIn("Multiple choice", type_row)
        self.assertEqual(type_row.count('name="live-add-q-type"'), 3)
        self.assertIn('id="live-add-q-equation-preview"', html)
        self.assertIn('data-eq-insert="\\frac{a}{b}"', html)
        self.assertIn('id="live-add-q-bank-scope-select"', html)
        self.assertIn('<option value="M1">Module 1</option>', html)
        self.assertIn('<option value="M8">Module 8</option>', html)
        self.assertIn('<option value="course">Course Wide</option>', html)
        self.assertIn(">Bank scope<", html)
        self.assertIn("Using Module 1 — set live module if wrong.", html)
        self.assertNotIn("Save to bank in", html)
        self.assertIn("Course Wide", html)
        self.assertNotIn("This module", html)
        self.assertNotIn("Media + Q", html)
        self.assertNotIn("A / B / C", html)
        js_shell = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/static/live-media/m1c1-c1-real-slice.html", js_shell)
        self.assertIn("function teacherPaneContent(", js_shell)
        self.assertIn("Show one teacher pane", js_shell)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertNotIn('patchTeacherState({ stage })', js)
        self.assertIn("Stage pills are display-only", js)

    def test_live_tab_preserves_existing_control_ids(self) -> None:
        """Attendance, teams, meet, rounds, media, and scoring IDs stay wired."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        for control_id in (
            "ap-att-list",
            "ap-att-log",
            "ap-allow-guests",
            "ap-gamify-next",
            "ap-n-teams",
            "ap-assign-balanced",
            "ap-assign-random",
            "ap-assign-manual",
            "ap-scoreboard-toggle",
            "live-view-slides",
            "live-timer-toggle",
            "live-teams-start",
            "live-run-as-group",
            "live-hide-absent",
            "live-responses-dialog",
            "ap-teams-rename",
            "ap-teams-next",
            "ap-meet-start",
            "ap-meet-minutes",
            "session-timer",
            "meet-chain-chrome",
            "meet-chain-next",
            "ap-name-list",
            "ap-start-game",
            "ap-rounds-start",
            "ap-rounds-list",
            "ap-active-media",
            "ap-media-preview",
            "ap-join-billboard",
            "ap-join-billboard-code",
            "ap-join-strip",
            "ap-score-end",
            "ap-score-list",
        ):
            self.assertIn(f'id="{control_id}"', html)

    def test_global_round_options_and_lifecycle_actions_are_wired(self) -> None:
        """Global controls and item actions use the phase-one API contract."""

        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn("Round options", html)
        self.assertIn(">Hide Absent<", html)
        self.assertIn(">Run as Group<", html)
        self.assertIn(">Rename Teams<", html)
        self.assertIn(">Set Up<", html)
        self.assertIn('id="live-teams-start"', html)
        start_snip = html[html.index('id="live-teams-start"'): html.index('id="live-teams-start"') + 90]
        self.assertIn("disabled", start_snip)
        self.assertIn(">Publish<", html)
        self.assertIn(">Close<", html)
        self.assertIn(">Whiteboard<", html)
        self.assertNotIn(">Canvas<", html)
        self.assertIn('data-response-select="answered"', html)
        self.assertIn('data-response-select="correct"', html)
        self.assertIn("data-response-commit", html)
        self.assertIn(">Award Points<", html)
        self.assertNotIn("data-response-award", html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function applyQuestionResponseSelection(", js)
        self.assertIn("button[data-response-commit]", js)
        self.assertIn("replace: true", js)
        for field in (
            "groups_configured",
            "run_as_group",
            "scoreboard_visible",
            "hide_absent",
            "class_list",
            "live_items",
            "active_questions",
        ):
            self.assertIn(field, js)
        self.assertIn("/items/${liveItemId}/publish", js)
        self.assertIn("/items/${liveItemId}/close", js)
        self.assertIn("/items/${liveItemId}/settings", js)
        self.assertIn("/items/${liveItemId}/end-voting", js)
        self.assertIn("Show Live Results", js)
        self.assertIn("Save to card", js)
        self.assertIn("data-save-to-card", js)
        self.assertIn("function setLifecycleSaveToCard(", js)
        self.assertIn("function interimQuestionControlMenu(", js)
        self.assertIn("function interimQuestionControlPiece(", js)
        self.assertIn("const QCHROME_STRIP_GROUPS = [", js)
        self.assertIn('data-qchrome-strip="1"', js)
        self.assertIn('data-qchrome-group="${group.id}"', js)
        self.assertIn("Add / stage this question first", js)
        self.assertIn("teacher-live-q-chrome-ia-v0.md", js)
        groups = js.split("const QCHROME_STRIP_GROUPS = [", 1)[1].split("];", 1)[0]
        self.assertLess(groups.index('"persist"'), groups.index('"visibility"'))
        self.assertLess(groups.index('"visibility"'), groups.index('"lifecycle"'))
        self.assertLess(groups.index('"save_to_card"'), groups.index('"show_live_results"'))
        self.assertLess(groups.index('"show_live_results"'), groups.index('"publish"'))
        self.assertNotIn("relocate", groups)
        self.assertIn("function groupConsensusResultsHtml(", js)
        self.assertIn("Individual in Group", js)
        self.assertIn("Reveal answers", js)
        self.assertNotIn("lloves-scoreboard-", js)

    def test_keyed_numeric_enables_select_correct(self) -> None:
        """Staff JS enables Select correct when a numeric card has a singular key."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function liveQuestionHasSingularKey(", js)
        self.assertIn("data-has-answer-key", js)
        self.assertIn("hasAnswerKey", js)
        self.assertNotIn('correct.disabled = questionType !== "mc"', js)
        open_fn = js.split("async function openQuestionResponses(")[1].split(
            "function paintQuestionArtifact("
        )[0]
        self.assertIn("Boolean(hasAnswerKey)", open_fn)
        self.assertIn("questionType === \"mc\"", open_fn)
        self.assertIn("row.correct != null", open_fn)
        helper = js.split("function liveQuestionHasSingularKey(")[0].rsplit(
            "/**", 1
        )[-1] + js.split("function liveQuestionHasSingularKey(")[1].split(
            "async function openQuestionResponses("
        )[0]
        self.assertIn("correct_answer", helper)
        self.assertIn("keyless numeric", helper)

    def test_group_setup_does_not_start_timer_or_change_stage(self) -> None:
        """Set Up fixes memberships while leaving stage and timer untouched."""

        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        session_id = int(live["id"])
        self.school.game.begin_game(self.class_id)
        students = self.school.game.dashboard(self.class_id)["students"]
        ids = [int(row["id"]) for row in students]
        for row in students:
            self.school.join_live_class_session(
                session_id, int(row["id"]), codename=str(row["codename"])
            )
        response = self.client.post(
            f"/api/live-sessions/{session_id}/teacher-state",
            json={
                "assign": {
                    "n_teams": 2,
                    "mode": "balanced",
                    "present_ids": ids,
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        state = response.get_json()["teacher_state"]
        self.assertEqual(state["stage"], "join")
        self.assertTrue(state["groups_configured"])
        self.assertTrue(state["run_as_group"])
        self.assertTrue(state["scoreboard_visible"])
        game = response.get_json().get("game") or {}
        self.assertFalse((game.get("game") or {}).get("round_ends_at_ms"))
        self.assertFalse((game.get("game") or {}).get("timer_paused"))

    def test_live_tab_end_class_is_placement_only(self) -> None:
        """Header Save and End Class and Quit are distinct finish routes."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn("Choose what to save before ending this class.", html)
        self.assertIn("Save attendance", html)
        self.assertIn("Save participation", html)
        self.assertIn(f"/staff/class/{self.class_id}/end-live", html)
        self.assertIn(f"/staff/class/{self.class_id}/quit-live", html)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('id="live-quit-class"', html)
        self.assertIn('aria-label="End Live Class"', html)
        self.assertIn(">End Live Class<", html)
        self.assertIn('aria-label="Quit"', html)
        self.assertIn("live-end-class-form", html)
        self.assertIn("live-quit-class-form", html)
        self.assertNotIn("All session data will be lost", html)
        self.assertNotIn('class="live-header-end danger live-legacy-control"', html)
        self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        self.assertIn("End Live Class", home_html)
        self.assertIn("Choose what to save before ending this class.", home_html)
        self.assertIn(f"/staff/class/{self.class_id}/end-live", home_html)
        self.assertIn("course-action-live-row", home_html)

    def test_class_list_pane_has_readable_clamp(self) -> None:
        """ClassListPane uses the v2 readable clamp, not the v1 140px rail."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("--live-left-width: clamp(220px, 24%, 320px)", css)
        self.assertIn("grid-template-columns: var(--live-left-width) minmax(0, 1fr)", css)
        self.assertIn("flex-wrap: nowrap", css)
        self.assertNotIn("--live-left-min: 140px", css)
        self.assertNotIn("minmax(var(--live-left-min), 15%)", css)
        self.assertNotIn("body.staff-shell #team-assign-pane {\n  grid-column: 1 / -1;", css)
        self.assertIn(
            "grid-template-columns: 1.25rem minmax(4.5rem, 1fr) 2.4rem 2.2rem auto",
            css,
        )

    def test_beat1_geometry_locks_three_rows_and_stretch(self) -> None:
        """Beat 1: header / height-capped OptionsStrip / Left|Right stretch."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("--live-options-max-h: calc(var(--live-options-row-h) * 2 + 1.1rem)", css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("grid-template-rows: auto auto minmax(12rem, 1fr)", css)
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr)", css)
        self.assertIn("body.staff-shell.course-live #class-list-pane,", css)
        self.assertIn("body.staff-shell.course-live #live-frames,", css)
        self.assertIn("body.staff-shell.course-live .staff-top-menu,", css)
        self.assertIn("body.staff-shell.course-live .tabs.course-tabs {", css)
        self.assertIn(
            "body.staff-shell.course-live .staff-top-menu,\n"
            "body.staff-shell.course-live .topbar,\n"
            "body.staff-shell.course-live .tabs.course-tabs {\n"
            "  display: none !important;",
            css,
        )
        self.assertIn("body.staff-shell .live-shell-ia-v2 > .live-header {\n  grid-row: 1;", css)
        self.assertIn(
            "body.staff-shell .live-shell-ia-v2 > .live-options-strip,\nbody.staff-shell .live-shell-ia-v2 > .live-date-panel {\n  grid-row: 2;",
            css,
        )
        self.assertIn("body.staff-shell .live-shell-ia-v2 > .live-shell-body {\n  grid-row: 3;", css)
        self.assertIn("is-set-class", css)
        self.assertIn("body.staff-shell .live-shell-body {\n  display: grid;", css)
        self.assertIn("align-items: stretch", css)
        self.assertIn(
            "body.staff-shell .live-active-content {\n"
            "  flex: 1 1 auto;\n"
            "  display: flex;\n"
            "  flex-direction: column;\n"
            "  min-height: 100%;",
            css,
        )
        self.assertIn("body.staff-shell #class-list-pane {\n  flex: 1 1 auto;\n  min-height: 0;", css)
        self.assertIn("body.staff-shell #session-timer {", css)
        self.assertNotIn("align-items: start;", css.split("body.staff-shell .live-shell-body")[1][:240])
        self.assertNotIn("position: absolute;\n  inset: 0;", css)
        self.assertNotIn("body.staff-shell .live-options-strip {\n  position: absolute;", css)

    def test_beat1_markup_keeps_class_list_outside_option_swap(self) -> None:
        """ClassList stays in Left; TEAMS/ROUND/Results never full-bleed outside Right."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        header_i = html.index('id="live-header"')
        strip_i = html.index('id="live-option-card"')
        body_i = html.index('class="live-shell-body"')
        left_i = html.index('id="live-shell-left"')
        timer_i = html.index('id="session-timer"')
        list_i = html.index('id="class-list-pane"')
        right_i = html.index('id="live-shell-right"')
        active_i = html.index('id="live-active-content"')
        teams_i = html.index('id="teams-option-card"')
        meet_i = html.index('id="meet-option-card"')
        round_i = html.index('id="round-option-card"')
        play_i = html.index('id="play-option-card"')
        team_pane_i = html.index('id="team-assign-pane"')
        results_i = html.index('id="results-strip"')
        self.assertLess(header_i, strip_i)
        self.assertLess(strip_i, timer_i)
        self.assertLess(timer_i, body_i)
        self.assertLess(strip_i, body_i)
        self.assertLess(body_i, left_i)
        self.assertLess(left_i, list_i)
        self.assertLess(list_i, right_i)
        self.assertLess(right_i, active_i)
        self.assertLess(strip_i, teams_i)
        self.assertLess(teams_i, meet_i)
        self.assertLess(meet_i, round_i)
        self.assertLess(round_i, play_i)
        self.assertLess(play_i, body_i)
        self.assertLess(teams_i, team_pane_i)
        self.assertLess(team_pane_i, body_i)
        self.assertLess(active_i, results_i)
        self.assertGreater(html.find("</section>", results_i), results_i)
        self.assertEqual(html.count('id="class-list-pane"'), 1)
        self.assertEqual(html.count('id="session-timer"'), 1)
        self.assertEqual(html.count('id="live-shell-left"'), 1)

    def test_beat1_stage_swaps_keep_class_list_mounted(self) -> None:
        """Prev/Next and every stage only swap OptionsStrip; Left chrome stays."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function lockClassListPane()", js)
        self.assertIn('for (const id of [', js)
        self.assertIn('"session-timer"', js)
        self.assertIn('"class-list-pane"', js)
        self.assertIn("lockClassListPane();", js)
        self.assertIn("Same hidden-only swap for JOIN, TEAMS, MEET, ROUND, PLAY, and Prev", js)
        self.assertIn("card.hidden = false;", js)
        self.assertIn("paintSurfacePublishing();", js)
        self.assertIn("teams.hidden = configured;", js)
        self.assertIn("if (meet) meet.hidden = true;", js)
        self.assertIn("optionCardHasVisibleControls(round)", js)
        self.assertIn("optionCardHasVisibleControls(play)", js)
        self.assertIn("function advanceLivePage(", js)
        self.assertIn("advanceLivePage(-1)", js)
        self.assertIn("advanceLivePage(1)", js)
        self.assertNotIn('id="class-list-pane").innerHTML', js)
        self.assertNotIn('id="live-shell-left").innerHTML', js)
        self.assertNotIn("$(\"class-list-pane\").innerHTML", js)
        self.assertNotIn("$(\"live-shell-left\").innerHTML", js)
        self.assertNotIn("class-list-pane\").remove(", js)
        self.assertNotIn("live-shell-left\").remove(", js)
        paint = js.split("function paintTeacherShell()")[1].split("function paintHeaderDate()")[0]
        self.assertIn("lockClassListPane();", paint)
        self.assertNotIn("innerHTML", paint)
        option = js.split("function paintOptionCard()")[1].split("function paintFrames()")[0]
        self.assertNotIn("innerHTML", option)
        self.assertNotIn("replaceChildren", option)
        self.assertIn("lockClassListPane();", option)

    def test_beat2_join_chip_is_banner_open_affordance(self) -> None:
        """Beat 2: banner chip is centered and height-capped; code opens the strip."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="ap-join-billboard"', html)
        self.assertIn('id="ap-join-billboard-code"', html)
        self.assertIn('id="ap-join-billboard-copy"', html)
        self.assertIn('id="ap-join-strip"', html)
        self.assertIn(">Copy<", html)
        self.assertIn('title="Open join strip"', html)
        self.assertIn('aria-label="Open join strip"', html)
        self.assertIn('id="ap-join-billboard-code"', html)
        self.assertRegex(
            html,
            r'<button\s+type="button"[^>]*id="ap-join-billboard-code"',
        )
        self.assertNotIn("Open join strip</button>", html)
        self.assertNotIn('id="ap-open-overlay"', html)
        header_i = html.index('id="live-header"')
        chip_i = html.index('id="ap-join-billboard"')
        body_i = html.index('class="live-shell-body"')
        self.assertLess(header_i, chip_i)
        self.assertLess(chip_i, body_i)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(
            "--live-header-chip-max-h: calc(var(--live-header-h) - (var(--live-header-pad-y) * 2))",
            css,
        )
        join_css = css.split("body.staff-shell .live-header-join {")[1].split(
            "body.staff-shell .live-header .ap-join-billboard {"
        )[0]
        self.assertIn("align-items: center", join_css)
        self.assertIn("align-self: center", join_css)
        self.assertIn("max-height: var(--live-header-chip-max-h)", join_css)
        chip_css = css.split("body.staff-shell .live-header .ap-join-billboard {")[1].split(
            "body.staff-shell .live-header .ap-join-billboard-label {"
        )[0]
        self.assertIn("align-items: center", chip_css)
        self.assertIn("max-height: var(--live-header-chip-max-h)", chip_css)
        code_css = css.split("body.staff-shell .live-header .ap-join-billboard-code {")[1].split(
            "body.staff-shell .live-header .ap-join-billboard-copy {"
        )[0]
        self.assertIn("white-space: nowrap", code_css)
        self.assertIn("overflow: visible", code_css)
        self.assertNotIn("text-overflow: ellipsis", code_css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function openJoinStrip(", js)
        self.assertIn(
            '$("ap-join-billboard-code")?.addEventListener("click", openJoinStrip)',
            js,
        )
        self.assertIn('$("ap-join-billboard-copy")?.addEventListener("click"', js)
        self.assertIn("copyJoinBillboardCode()", js)
        self.assertNotIn("$(\"ap-open-overlay\")", js)
        self.assertNotIn("Open join strip", js)
        code_click = js.split('$("ap-join-billboard-code")?.addEventListener("click"')[1].split(";")[0]
        self.assertIn("openJoinStrip", code_click)
        self.assertNotIn("copyJoinBillboardCode", code_click)

    def test_mc_reveal_lives_in_results_strip_only(self) -> None:
        """v2.1: LIVE/REVEAL is the ResultsStrip primary slot, not a second card."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="mc-results-slot"', html)
        self.assertIn('id="mc-reveal-bars"', html)
        self.assertIn("Reveal results", html)
        q_i = html.index('id="question-artifact-zone"')
        r_i = html.index('id="results-strip"')
        mc_i = html.index('id="mc-results-slot"')
        score_i = html.index('id="ap-panel-score"')
        self.assertLess(q_i, r_i)
        self.assertLess(r_i, mc_i)
        self.assertLess(mc_i, score_i)
        self.assertEqual(html.count('id="results-strip"'), 1)
        self.assertEqual(html.count('id="mc-results-slot"'), 1)
        self.assertNotIn('id="mystery-results"', html)
        self.assertNotIn('id="mc-results-card"', html)
        header = html[html.index('id="live-header"') : html.index('class="live-shell-body"')]
        self.assertNotIn('id="mc-results-slot"', header)
        left = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="mc-results-slot"', left)
        self.assertNotIn('id="results-strip"', left)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell #mc-results-slot {", css)
        slot_css = css.split("body.staff-shell #mc-results-slot {")[1].split("}")[0]
        self.assertIn("max-height: 14rem", slot_css)
        self.assertIn("overflow-y: auto", slot_css)
        self.assertIn("body.staff-shell #mc-results-slot [hidden] {", css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function paintMcResultsSlot()", js)
        self.assertIn("function applyMcTally(", js)
        self.assertIn("function patchMcReveal(", js)
        self.assertIn("desiredSessionPollMs()", js)
        self.assertIn("return lastMcTally ? 1000 : 2000", js)
        reveal = js.split("function patchMcReveal(")[1].split("function ")[0]
        self.assertIn('teacherState.stage || "") === "join"', reveal)
        self.assertIn("reveal_to_students: joinShare", reveal)
        self.assertIn("poll_closed: joinShare || alreadyClosed", reveal)
        self.assertNotIn("cue_id", reveal)
        self.assertIn('patchTeacherState({', reveal)
        self.assertIn("mc_ui:", reveal)
        self.assertNotIn("innerHTML", js.split("function paintTeacherShell()")[1].split("function paintHeaderDate()")[0])

    def test_lifecycle_cards_suppress_legacy_mc_results_slot(self) -> None:
        """Lifecycle question cards own results while scoring stays separate."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        helper = js.split(
            "function currentStageHasLifecycleQuestionCards()"
        )[1].split("function paintMcResultsSlot()")[0]
        self.assertIn("currentPageQuestionRows()", helper)
        paint = js.split("function paintMcResultsSlot()")[1].split(
            "function applyMcTally("
        )[0]
        self.assertIn("slot.hidden = true", paint)
        self.assertNotIn("lastMcTally", paint)
        results = js.split("function paintResultsStrip()")[1].split(
            "function mcBindKey("
        )[0]
        self.assertIn("const showScore =", results)
        self.assertIn("scorePanel.hidden = !showScore", results)
        self.assertNotIn("const showMc =", results)

    def test_beat3_teams_strip_is_one_condensed_row(self) -> None:
        """Round Options swaps setup controls for persisted group toggles."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="teams-option-card"', html)
        self.assertIn("live-teams-strip", html)
        self.assertIn('id="live-groups-setup"', html)
        self.assertIn('id="live-groups-configured"', html)
        self.assertIn('id="ap-n-teams"', html)
        self.assertIn('min="2"', html)
        self.assertIn('id="ap-n-teams-down"', html)
        self.assertIn('id="ap-n-teams-up"', html)
        self.assertIn('id="live-teams-assign"', html)
        self.assertIn(">Balanced<", html)
        self.assertIn(">Random<", html)
        self.assertIn(">Manual<", html)
        self.assertIn(">Set Up<", html)
        self.assertIn('id="live-run-as-group"', html)
        self.assertIn('id="ap-scoreboard-toggle"', html)
        self.assertIn(">Scoreboard<", html)
        self.assertIn('id="ap-teams-rename"', html)
        self.assertIn(">Rename Teams<", html)
        self.assertIn('id="ap-manual-assign"', html)
        self.assertIn('id="ap-panel-names"', html)
        self.assertIn('id="team-assign-pane"', html)
        unlocks_html = html.split('id="live-unlocks-strip"')[1].split('id="teams-option-card"')[0]
        self.assertIn('id="session-timer"', unlocks_html)
        self.assertIn('id="live-run-as-group"', unlocks_html)
        self.assertIn('id="ap-scoreboard-toggle"', unlocks_html)
        self.assertIn('id="ap-teams-rename"', unlocks_html)
        self.assertLess(unlocks_html.find('id="session-timer"'), unlocks_html.find('id="live-groups-configured"'))
        self.assertLess(unlocks_html.find('id="ap-scoreboard-toggle"'), unlocks_html.find('id="live-groups-configured"'))
        self.assertLess(unlocks_html.find('id="ap-scoreboard-preview-wrap"'), unlocks_html.find('id="live-groups-configured"'))
        self.assertLess(unlocks_html.find('id="live-run-as-group"'), unlocks_html.find('id="ap-teams-rename"'))
        strip_html = html.split('id="teams-option-card"')[1].split('id="meet-option-card"')[0]
        self.assertIn('id="ap-n-teams"', strip_html)
        self.assertIn('id="ap-assign-balanced"', strip_html)
        self.assertNotIn('id="ap-scoreboard-toggle"', strip_html)
        self.assertNotIn('id="ap-scoreboard-preview-wrap"', strip_html)
        self.assertNotIn('id="live-run-as-group"', strip_html)
        self.assertLess(strip_html.find('id="ap-n-teams"'), strip_html.find('id="live-teams-assign"'))
        self.assertLess(strip_html.find('id="live-teams-assign"'), strip_html.find('id="live-teams-start"'))
        body_i = html.index('class="live-shell-body"')
        self.assertLess(html.index('id="team-assign-pane"'), body_i)
        self.assertLess(html.index('id="class-list-pane"'), html.index('id="live-active-content"'))
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .live-groups-setup", css)
        self.assertIn("body.staff-shell .live-groups-configured", css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function currentTeamCount()", js)
        self.assertIn("function paintGlobalGroupControls()", js)
        self.assertIn("function paintTeamsStripEnabled()", js)
        self.assertIn("teams.hidden = configured;", js)
        self.assertIn("function openTeamsPop(", js)
        self.assertIn("groups_configured", js)
        self.assertIn("run_as_group", js)
        self.assertIn("scoreboard_visible", js)
        self.assertNotIn("lloves-scoreboard-", js)
        self.assertIn("card.hidden = false;", js)
        self.assertIn('id="live-unlocks-strip"', html)
        self.assertRegex(
            html,
            r'<section[^>]*id="live-option-card"[^>]*\bhidden\b',
        )

    def test_beat11_team_count_max_and_division_meter(self) -> None:
        """Beat 11: max = present count; compact 3-band meter beside +/-."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        strip_html = html.split('id="teams-option-card"')[1].split('id="meet-option-card"')[0]
        self.assertIn('id="ap-n-teams"', strip_html)
        self.assertIn('id="ap-n-teams-down"', strip_html)
        self.assertIn('id="ap-n-teams-up"', strip_html)
        self.assertIn('id="ap-division-meter"', strip_html)
        self.assertIn('id="ap-division-meter-label"', strip_html)
        self.assertIn("live-division-meter", strip_html)
        self.assertIn("live-division-bar", strip_html)
        self.assertEqual(strip_html.count('class="live-division-bar"'), 3)
        self.assertIn('role="meter"', strip_html)
        self.assertIn("Division strength", strip_html)
        self.assertLess(strip_html.find('id="ap-n-teams-up"'), strip_html.find('id="ap-division-meter"'))
        self.assertLess(strip_html.find('id="ap-division-meter"'), strip_html.find('id="live-teams-assign"'))
        self.assertGreater(html.index('id="class-list-pane"'), html.index('id="live-shell-left"'))
        self.assertLess(html.index('id="class-list-pane"'), html.index('id="live-active-content"'))
        left_html = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="ap-division-meter"', left_html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function presentCountForTeams()", js)
        self.assertIn("function nTeamsBounds()", js)
        self.assertIn("function divisionStrength(presentCount, teamCount)", js)
        self.assertIn("function paintDivisionMeter()", js)
        self.assertIn("return { min: 2, max: Math.max(2, present) }", js)
        bounds = js.split("function nTeamsBounds()")[1].split("function divisionStrength(")[0]
        self.assertIn("presentCountForTeams()", bounds)
        present_fn = js.split("function presentCountForTeams()")[1].split(
            "function nTeamsBounds()"
        )[0]
        self.assertIn("selectedPresent().length", present_fn)
        strength = js.split("function divisionStrength(presentCount, teamCount)")[1].split(
            "function paintDivisionMeter()"
        )[0]
        self.assertIn('if (k <= 1) return "individuals"', strength)
        self.assertIn('if (low < 2) return "not_recommended"', strength)
        self.assertIn("if (n >= 4 && remainder <= 1) return \"optimal\"", strength)
        self.assertIn('return "okay"', strength)
        paint = js.split("function paintDivisionMeter()")[1].split("function paintTeamsStripEnabled()")[
            0
        ]
        self.assertIn('band === "individuals"', paint)
        self.assertIn("meter.hidden = true", paint)
        self.assertIn("Optimal", paint)
        self.assertIn("Okay", paint)
        self.assertIn("Not recommended", paint)
        self.assertIn("paintDivisionMeter();", js.split("function paintTeamsStripEnabled()")[1].split(
            "function paintRoundStrip()"
        )[0])
        self.assertIn(
            "paintDivisionMeter();",
            js.split("function renderAttendanceList()")[1].split("function updateAttCount()")[0],
        )
        self.assertIn("syncSetupEnabled", js)
        self.assertIn("previewRosterTeams", js)
        self.assertIn("function previewRosterPool(", js)
        self.assertIn("classList.toggle(\"is-selected\", on)", js)
        self.assertIn("classList.toggle(\"is-active\", on)", js)
        self.assertIn("closeLiveSessionOverlay", js)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(".live-round-pick.is-selected", css)
        self.assertIn('.live-round-pick[aria-pressed="true"]', css)
        self.assertIn("body.staff-shell .live-teams-strip .live-division-meter {", css)
        meter_css = css.split("body.staff-shell .live-teams-strip .live-division-meter {")[1].split(
            "body.staff-shell .live-teams-strip .live-division-meter[hidden] {"
        )[0]
        self.assertIn("max-height: 1.75rem", meter_css)
        self.assertIn("flex: 0 0 auto", meter_css)
        self.assertIn("body.staff-shell .live-teams-strip {", css)
        teams_css = css.split("body.staff-shell .live-teams-strip {")[1].split(
            "body.staff-shell .live-teams-strip .live-teams-count {"
        )[0]
        self.assertIn("flex-wrap: nowrap", teams_css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("--live-left-width: clamp(220px, 24%, 320px)", css)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)

    def test_beat23_unlocks_any_stage_and_canvas_align(self) -> None:
        """Beat 23: Media/Canvas flags on every stage; condensed alignment modes."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn('id="live-unlocks-strip"', html)
        self.assertIn('id="live-view-media"', html)
        self.assertIn('id="live-view-canvas"', html)
        self.assertIn('id="live-view-slides"', html)
        self.assertNotIn('id="live-view-questions"', html)
        self.assertIn('id="live-question-list"', html)
        self.assertIn("data-question-view", js)
        self.assertNotIn('id="live-canvas-align"', html)
        self.assertNotIn("Student View", html)
        self.assertIn("Publish mode", html)
        self.assertIn(">Individual<", html)
        self.assertIn(">Shared within Group<", html)
        play_html = html.split('id="play-option-card"')[1].split("</section>")[0]
        self.assertNotIn('id="live-view-media"', play_html)
        self.assertIn("card.hidden = false;", js)
        self.assertIn("patchStudentViewFromControl", js)
        self.assertIn("canvas-presence", js)
        self.assertIn("body.staff-shell .live-unlocks-strip {", css)
        unlocks_css = css.split("body.staff-shell .live-unlocks-strip {")[1].split(
            "body.staff-shell .live-canvas-align {"
        )[0]
        # Timer, Run as Group, Scoreboard, and Rename stay on one row after Set Up
        self.assertIn("flex-wrap: nowrap", unlocks_css)
        self.assertIn(
            "body.staff-shell .live-option-card-body.live-unlocks-strip",
            css,
        )
        specific_unlocks = css.split(
            "body.staff-shell .live-option-card-body.live-unlocks-strip"
        )[1].split("}")[0]
        self.assertIn("flex-wrap: nowrap", specific_unlocks)
        self.assertIn("max-height: none", unlocks_css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("function paintStudentCanvas(", student_js)
        self.assertIn("/api/student/canvas-presence", student_js)
        self.assertIn("canvasAlign", student_js)
        self.assertIn("student_view", student_js)
        self.assertIn("questionsMode", student_js)
        self.assertNotIn('mediaMode !== "none" && stage !== "round"', student_js)
        self.assertIn('canvasPane.classList.toggle("is-readonly"', student_js)
        self.assertIn('lastAlign !== "teacher"', student_js)
        self.assertIn('id="student-canvas"', 
            (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        )
        home_html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="media-pane"', home_html)
        self.assertIn('id="canvas-pane"', home_html)
        self.assertIn('id="slides-pane"', home_html)

    def test_beat29_uncheck_collapses_student_frames(self) -> None:
        """Beat 29: student JS hides Media/Canvas when unlocks are off."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn("student_view", js)
        self.assertIn("questionsMode", js)
        self.assertIn("mediaMode !== \"none\"", js)
        self.assertIn('canvasPane.classList.toggle("is-readonly"', js)
        self.assertIn(".canvas-pane[hidden]", css)
        self.assertIn(".media-pane[hidden]", css)

    def test_beat22_presence_updates_team_max_live(self) -> None:
        """Beat 22: ClassList join/leave clamps team max and refreshes the meter."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        ticks = js.split("async function applySessionPresentTicks(")[1].split(
            "async function pollLiveSessionAttendees("
        )[0]
        self.assertIn("sessionPresentIds = next", ticks)
        self.assertNotIn("sessionPresentIds.add(id)", ticks)
        self.assertIn("setNTeams(currentTeamCount())", ticks)
        self.assertIn("renderAttendanceList()", ticks)
        self.assertIn("No second poll", js)
        self.assertIn("TEAMS max (= presentCount)", js)
        bounds = js.split("function nTeamsBounds()")[1].split("function divisionStrength(")[0]
        self.assertIn("presentCountForTeams()", bounds)
        self.assertIn("return { min: 2, max: Math.max(2, present) }", js)
        paint = js.split("function paintDivisionMeter()")[1].split(
            "function paintTeamsStripEnabled()"
        )[0]
        self.assertIn("presentCountForTeams()", paint)
        self.assertIn("divisionStrength(present, teamCount)", paint)
        poll = js.split("async function pollLiveSessionAttendees(")[1].split(
            "function startLiveSessionPolling("
        )[0]
        self.assertIn("applySessionPresentTicks(", poll)
        self.assertIn("!row?.left_at", poll)
        self.assertIn("sessionPollInFlight", poll)
        self.assertIn("?light=1", poll)
        self.assertIn("paintLiveQuestionCards()", poll)
        self.assertNotIn("setInterval", ticks)

    def test_beat22b_teams_classlist_present_only(self) -> None:
        """Class List stays full until durable Hide Absent is checked."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function adoptClassListRows(", js)
        self.assertIn("lastClassListFull", js)
        visible = js.split("function classListVisibleStudents(")[1].split(
            "function projectedClassListStudents("
        )[0]
        self.assertIn("Array.isArray(students)", visible)
        self.assertIn("teacherState.hide_absent", visible)
        self.assertIn("sessionPresentIds", visible)
        self.assertIn("function projectedClassListStudents()", js)
        render = js.split("function renderAttendanceList()")[1].split(
            "function updateAttCount()"
        )[0]
        self.assertIn("classListVisibleStudents(", render)
        self.assertIn('dataset.presentOnly', render)
        self.assertIn("classListRosterOrder(classListVisibleStudents(", render)
        self.assertIn("teacherState.hide_absent", render)
        ticks = js.split("async function applySessionPresentTicks(")[1].split(
            "async function pollLiveSessionAttendees("
        )[0]
        self.assertIn("renderAttendanceList()", ticks)
        self.assertIn("setNTeams(currentTeamCount())", ticks)
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="live-hide-absent"', html)
        self.assertIn(">Hide Absent<", html)
        shell = js.split("function paintTeacherShell()")[1].split(
            "function paintHeaderDate()"
        )[0]
        self.assertIn("renderAttendanceList()", shell)

    def test_beat4_rename_is_portaled_modal(self) -> None:
        """Beat 4: Rename is a body-portaled dialog, not an OptionsStrip popover."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="ap-teams-rename-dialog"', html)
        self.assertIn('id="ap-panel-names"', html)
        self.assertIn('id="ap-name-list"', html)
        self.assertIn('id="ap-teams-rename-done"', html)
        self.assertIn('id="ap-teams-rename"', html)
        self.assertGreater(html.index('id="ap-teams-rename-dialog"'), html.index('id="ap-root"'))
        self.assertGreater(html.index('id="ap-teams-rename-dialog"'), html.index('id="live-active-content"'))
        self.assertGreater(html.index('id="ap-panel-names"'), html.index('id="ap-teams-rename-dialog"'))
        strip_html = html.split('id="live-option-card"')[1].split('class="live-shell-body"')[0]
        self.assertIn('id="ap-teams-rename"', strip_html)
        self.assertNotIn('id="ap-teams-rename-dialog"', strip_html)
        self.assertNotIn('id="ap-panel-names"', strip_html)
        self.assertNotIn('id="ap-name-list"', strip_html)
        left_html = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="ap-teams-rename-dialog"', left_html)
        self.assertNotIn('id="ap-panel-names"', left_html)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .live-rename-dialog[open]", css)
        self.assertIn("body.staff-shell .live-rename-dialog::backdrop", css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn(
            "body.staff-shell .live-options-strip {\n  position: relative;\n  z-index: 0;",
            css,
        )
        self.assertNotIn("body.staff-shell .live-options-strip {\n  position: absolute;", css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function mountTeamsRenameDialog()", js)
        self.assertIn("document.body.appendChild(dialog)", js)
        self.assertIn("function openTeamsRenameModal()", js)
        self.assertIn("function closeTeamsRenameModal(", js)
        self.assertIn("function restoreTeamsRenameFocus()", js)
        self.assertIn("dialog.showModal()", js)
        self.assertIn('dialog.addEventListener("cancel"', js)
        self.assertIn("event.target === event.currentTarget", js)
        self.assertIn("mountTeamsRenameDialog();", js)
        self.assertIn("openTeamsRenameModal()", js)
        rename_click = js.split('$("ap-teams-rename")?.addEventListener("click"')[1].split(
            '$("ap-teams-rename-done")'
        )[0]
        self.assertIn("openTeamsRenameModal()", rename_click)
        self.assertNotIn("openTeamsPop", rename_click)
        done_click = js.split('$("ap-teams-rename-done")?.addEventListener("click"')[1].split(";")[0]
        self.assertIn("closeTeamsRenameModal({ save: true })", done_click)
        save = js.split("async function saveTeamNamesFromPop()")[1].split(
            "function renderDraftNamesPanel"
        )[0]
        self.assertIn("renderAttendanceList()", save)
        self.assertIn("updateStepSummaries()", save)
        self.assertNotIn("paintTeacherShell", save)
        self.assertNotIn("innerHTML", save)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)

    def test_beat14_session_timer_sits_above_class_list(self) -> None:
        """One stateful SessionTimer lives inside Round Options."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="session-timer"', html)
        self.assertIn("live-session-timer", html)
        self.assertIn('id="ap-meet-stepper"', html)
        self.assertIn('id="ap-meet-minutes"', html)
        self.assertIn('id="ap-meet-live-clock"', html)
        self.assertIn('id="ap-meet-start"', html)
        self.assertIn(">Start<", html)
        self.assertNotIn('id="meet-chain-skip-c"', html)
        self.assertNotIn('id="meet-chain-end"', html)
        self.assertNotIn("Skip C", html)
        self.assertNotIn("End Meet", html)
        self.assertNotIn("Meet timer (min)", html)
        self.assertNotIn("Start Meet", html)
        left_html = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="session-timer"', left_html)
        options_html = html.split('id="live-option-card"')[1].split(
            'class="live-shell-body"'
        )[0]
        self.assertIn('id="session-timer"', options_html)
        self.assertIn('id="ap-meet-start"', options_html)
        self.assertNotIn('id="live-timer-start"', html)
        self.assertEqual(html.count('id="ap-meet-start"'), 1)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell #session-timer {", css)
        timer_css = css.split("body.staff-shell #session-timer {")[1].split(
            "body.staff-shell #session-timer .live-meet-count {"
        )[0]
        self.assertIn("flex-wrap: nowrap", timer_css)
        self.assertIn("max-height: var(--live-options-row-h)", timer_css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        clock_css = css.split("body.staff-shell .ap-meet-live-clock {")[1].split("}")[0]
        self.assertIn("font-size: 0.95rem", clock_css)
        self.assertIn("white-space: nowrap", clock_css)
        self.assertNotIn("font-size: 1.65rem", css)
        self.assertNotIn(
            "body.staff-shell #ap-meet-stepper[hidden] {\n  display: none !important;",
            css,
        )
        self.assertNotIn(
            "body.staff-shell .ap-meet-live-clock[hidden] {\n  display: none !important;",
            css,
        )
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function applySessionTimerUi(", js)
        self.assertIn("function persistPausedTimerMinutes(", js)
        self.assertIn("function startSessionTimer(", js)
        self.assertIn("function sessionTimerDefaultMinutes(", js)
        apply = js.split("function applySessionTimerUi(")[1].split(
            "function paintSessionClock("
        )[0]
        self.assertIn("lockClassListPane();", apply)
        self.assertIn('btn.textContent = "Start"', apply)
        self.assertIn('btn.textContent = "Pause"', apply)
        self.assertIn('btn.textContent = "Resume"', apply)
        self.assertIn("clock.textContent = formatCountdown", apply)
        self.assertIn("Boolean(game.round_ends_at_ms)", apply)
        self.assertIn("const freeze = running", apply)
        self.assertNotIn("running || paused", apply)
        self.assertNotIn('phase === "meet_teams"', apply)
        self.assertNotIn("stepper.hidden = running", apply)
        self.assertNotIn("clock.hidden = !(running", apply)
        self.assertNotIn("paintQuestionArtifact", apply)
        self.assertNotIn("paintTeacherShell", apply)
        self.assertNotIn("innerHTML", apply)
        self.assertNotIn("replaceChildren", apply)
        start = js.split('$("ap-meet-start")?.addEventListener("click"')[1].split(
            "function availableRoundKinds("
        )[0]
        self.assertIn("applySessionTimerUi(overlayState)", start)
        self.assertIn("startSessionTimer()", start)
        self.assertNotIn('patchTeacherState({ stage: "meet" })', start)
        self.assertNotIn("paintTeacherShell", start)
        self.assertNotIn("meet-chain-skip-c", js)
        self.assertNotIn("meet-chain-end", js)
        self.assertNotIn('meet_action: "skip_c"', js)
        self.assertNotIn('meet_action: "clear"', js)
        paint = js.split("function paintSessionClock()")[1].split(
            '$("ap-assign-random")'
        )[0]
        self.assertIn('btn?.dataset.meetState !== "running"', paint)
        self.assertNotIn("clock.hidden", paint)
        self.assertIn("lockClassListPane();", js)
        option = js.split("function paintOptionCard()")[1].split("function paintFrames()")[0]
        self.assertIn("applySessionTimerUi();", option)
        self.assertIn("applySessionTimerStageDefaults();", option)
        self.assertIn("lockClassListPane();", option)
        defaults = js.split("function sessionTimerDefaultMinutes(")[1].split(
            "function sessionTimerMinutes("
        )[0]
        self.assertIn('stage === "play" ? 5 : 3', defaults)

    def test_beat12_join_to_teams_clears_mc_ghosts(self) -> None:
        """Beat 12: JOIN→TEAMS drops JOIN tally chrome; Left lock stays."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        adopt = js.split("function adoptTeacherState(")[1].split(
            "function paintStageRail()"
        )[0]
        self.assertIn("paintResultsStrip()", adopt)
        self.assertIn("paintTeacherShell()", adopt)
        apply = js.split("function applyMcTally(")[1].split(
            "function currentMcPromptRef("
        )[0]
        self.assertIn('teacherState.stage !== "teams"', apply)
        self.assertIn("lastMcTally = null", apply)
        paint = js.split("function paintTeacherShell()")[1].split("function paintHeaderDate()")[0]
        self.assertIn("lockClassListPane();", paint)
        self.assertNotIn("innerHTML", paint)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("function isJoinMindsOnPrompt(", student)
        self.assertIn("function isJoinMindsOnPrompt(", student)
        self.assertIn("hideFeedbackPanel()", student)
        self.assertIn("const seqChanged = lastStateSeq !== prevSeq", student)

    def test_beat25_teams_shared_spark_question_frame(self) -> None:
        """Beat 25: TEAMS Question frame hosts one shared spark, not Meet/Minds-On."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="teams-spark-card"', html)
        self.assertIn('id="teams-spark-prompt"', html)
        self.assertIn('id="teams-spark-key"', html)
        self.assertIn('id="teams-spark-reveal"', html)
        q_html = html.split('id="question-artifact-zone"')[1].split(
            'id="results-strip"'
        )[0]
        self.assertIn('id="teams-spark-card"', q_html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function paintTeamsSparkCard(", js)
        self.assertIn("Waiting room · 2", js)
        self.assertIn("keepQuestionBody", js)
        self.assertIn("lastTeamsSpark", js)
        self.assertIn("payload?.teams_spark", js)
        self.assertIn('currentMcPromptRef()', js)
        self.assertIn('stage || "") === "teams") return "teams-spark"', js)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("function isTeamsSparkPrompt(", student)
        self.assertIn("cue.teams_spark", student)
        self.assertIn("student_feedback_after_reveal", student)
        self.assertIn("isSpark", student)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .teams-spark-card {", css)

    def test_beat13_teams_next_assigns_without_breaking_pane(self) -> None:
        """Stage navigation never creates or resets session groups."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="live-teams-assign-error"', html)
        strip_html = html.split('id="teams-option-card"')[1].split('id="meet-option-card"')[0]
        self.assertIn('id="live-teams-assign-error"', strip_html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        next_click = js.split('$("live-stage-next")?.addEventListener("click"')[1].split(
            '$("meet-chain-next")'
        )[0]
        self.assertIn("advanceLivePage(1)", next_click)
        self.assertNotIn("advanceTeamsToMeet()", next_click)
        setup = js.split('$("live-teams-start")?.addEventListener("click"')[1].split(
            '$("live-run-as-group")'
        )[0]
        self.assertIn("startTeamsForCurrentStage()", setup)

    def test_beat6_student_feedback_stays_off_staff_chrome(self) -> None:
        """Beat 6: Good-work panel is student Question-frame only; Reveal stays."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        html = self.client.get(f"/staff/class/{self.class_id}?tab=live").get_data(
            as_text=True
        )
        self.assertNotIn("prompt-feedback", js)
        self.assertNotIn("Good work.", js)
        self.assertNotIn("Not that one — stay with the picture.", js)
        self.assertNotIn("prompt-feedback", css)
        self.assertNotIn("Good work.", html)
        self.assertNotIn('id="prompt-feedback"', html)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("function patchMcReveal(", js)
        self.assertIn("function paintMcResultsSlot()", js)
        self.assertIn('id="mc-results-slot"', html)
        self.assertIn('id="results-strip"', html)
        paint = js.split("function paintTeacherShell()")[1].split("function paintHeaderDate()")[0]
        self.assertIn("lockClassListPane();", paint)
        self.assertNotIn("innerHTML", paint)

    def test_beat15_student_js_projects_meet_question_frame(self) -> None:
        """Beat 15: student Question frame follows student_frames + meet_chain."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("questionFrame.hidden = !proj.questions", js)
        self.assertIn('proj.stage === "meet" && Boolean(ts.meet_chain)', js)
        self.assertIn("Waiting-room keeps Wonder's line", js)
        self.assertNotIn("Waiting for your teacher to start scoring.", js)
        self.assertNotIn("meet-chain-skip-c", js)
        self.assertNotIn("End Meet", js)

    def test_beat10_join_reveal_shares_summary_in_question_frame(self) -> None:
        """Beat 10: JOIN Reveal shares bars in the student Question frame."""
        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        student_css = (LMS_DIR / "static" / "student-portal.css").read_text(
            encoding="utf-8"
        )
        html = self.client.get(f"/staff/class/{self.class_id}?tab=live").get_data(
            as_text=True
        )
        reveal = staff_js.split("function patchMcReveal(")[1].split("function ")[0]
        self.assertIn("joinShare", reveal)
        self.assertIn("poll_closed", reveal)
        self.assertNotIn("cue_id", reveal)
        self.assertNotIn("innerHTML", reveal)
        paint = staff_js.split("function paintTeacherShell()")[1].split(
            "function paintHeaderDate()"
        )[0]
        self.assertIn("lockClassListPane();", paint)
        self.assertNotIn("innerHTML", paint)
        self.assertEqual(html.count('id="mc-results-slot"'), 1)
        self.assertNotIn('id="mc-results-card"', html)
        self.assertIn("function studentMcSummary(", student_js)
        self.assertIn("function studentPollClosed(", student_js)
        closed = student_js.split("function studentPollClosed(")[1].split(
            "function studentSummarySig("
        )[0]
        self.assertIn("payload.poll_closed", closed)
        self.assertNotIn("ui.poll_closed", closed)
        self.assertIn("function publishedJoinCatalogueActive(", student_js)
        self.assertIn("function isLeftoverJoinMindsOnCard(", student_js)
        leftover = student_js.split("function isLeftoverJoinMindsOnCard(")[1].split(
            "function paintLifecycleQuestionStack("
        )[0]
        self.assertIn("hasPublishedJoinCatalogue", leftover)
        self.assertIn("function mcRevealBarsHtml(", student_js)
        self.assertIn("hideFeedbackPanel()", student_js)
        self.assertIn("student-mc-reveal-bars", student_js)
        self.assertIn("summarySig !== lastSummarySig", student_js)
        self.assertIn(".question-frame .mc-reveal-row", student_css)
        self.assertIn(".question-frame .mc-reveal-fill", student_css)
        self.assertNotIn("id=\"mc-results-card\"", student_js)

    def test_beat17_class_list_groups_by_team_when_assigned(self) -> None:
        """Beat 17: assigned teams>1 get name separators; count=1 stays flat."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('id="class-list-pane"', html)
        self.assertIn('id="ap-att-list"', html)
        self.assertIn("function assignedRosterTeams()", js)
        self.assertIn("function classListGroupsByTeam()", js)
        self.assertIn("function classListRosterOrder(", js)
        self.assertIn("function appendAttendanceStudentRow(", js)
        gate = js.split("function classListGroupsByTeam()")[1].split("function ")[0]
        self.assertIn("teacherState.run_as_group", gate)
        self.assertIn("assignedRosterTeams().length >= 1", gate)
        order = js.split("function classListRosterOrder(")[1].split(
            "function appendAttendanceStudentRow("
        )[0]
        self.assertIn("if (!classListGroupsByTeam())", order)
        self.assertIn('key: "flat"', order)
        self.assertIn("team.name || `Team ${sortOrder + 1}`", order)
        render = js.split("function renderAttendanceList()")[1].split(
            "function updateAttCount()"
        )[0]
        self.assertIn("classListGroupsByTeam()", render)
        self.assertIn("classListRosterOrder(", render)
        self.assertIn('sep.className = "ap-att-team-sep"', render)
        self.assertIn('sep.setAttribute("role", "separator")', render)
        self.assertIn("grouped && group.name", render)
        self.assertIn('list.dataset.grouped = grouped ? "1" : "0"', render)
        self.assertIn("appendAttendanceStudentRow(", render)
        self.assertIn("sessionGuests", render)
        row_fn = js.split("function appendAttendanceStudentRow(")[1].split(
            "function renderAttendanceList()"
        )[0]
        self.assertIn("has-team-color", row_fn)
        self.assertIn("studentTeamColor(", row_fn)
        self.assertIn("ap-att-check", row_fn)
        self.assertIn("#ap-att-list .ap-att-row.is-present", js)
        self.assertNotIn("ap-att-team-sep.is-present", js)
        assign = js.split("async function assign(mode)")[1].split(
            "function sessionTimerDefaultMinutes("
        )[0]
        self.assertIn("renderAttendanceList()", assign)
        advance = js.split("async function advanceTeamsToMeet()")[1].split(
            '$("ap-teams-next")'
        )[0]
        self.assertIn("renderAttendanceList()", advance)
        save = js.split("async function saveTeamNamesFromPop()")[1].split(
            "function renderDraftNamesPanel"
        )[0]
        self.assertIn("renderAttendanceList()", save)
        self.assertNotIn("paintTeacherShell", save)
        done_click = js.split('$("ap-teams-rename-done")?.addEventListener("click"')[1].split(";")[0]
        self.assertIn("closeTeamsRenameModal({ save: true })", done_click)
        set_n = js.split("function setNTeams(value)")[1].split("function renderTeamsPanel()")[0]
        self.assertIn("renderAttendanceList()", set_n)
        sep_css = css.split("body.staff-shell #class-list-pane .ap-att-team-sep {")[1].split("}")[0]
        self.assertIn("max-width: 100%", sep_css)
        self.assertIn("text-overflow: ellipsis", sep_css)
        self.assertIn("white-space: nowrap", sep_css)
        self.assertIn("color: var(--team, #334155)", sep_css)
        self.assertIn("#class-list-pane .ap-att-row.has-team-color .ap-att-name", css)
        self.assertIn("--live-left-width: clamp(220px, 24%, 320px)", css)
        self.assertIn(
            "grid-template-columns: var(--live-left-width) minmax(0, 1fr)",
            css,
        )
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)
        left_html = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="session-timer"', left_html)
        self.assertIn("function applySessionTimerUi(", js)
        self.assertIn("async function advanceTeamsToMeet()", js)

    def test_beat33_round_type_dropdown_always_visible(self) -> None:
        """Page-4+ round chrome is gone; leftover listeners stay optional."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        strip_html = html.split('id="round-option-card"')[1].split('id="play-option-card"')[0]
        self.assertNotIn('id="live-round-type"', html)
        self.assertNotIn('id="live-round-set"', html)
        self.assertNotIn('id="live-edit-layout"', html)
        self.assertNotIn('id="live-layout-presets"', html)
        self.assertNotIn('id="live-round-picks"', html)
        self.assertNotIn(">SET<", strip_html)
        self.assertNotIn('id="live-round-minds-on"', strip_html)
        self.assertNotIn("Always 3 rounds", strip_html)
        self.assertNotIn("Keep teams", strip_html)
        self.assertNotIn("Reassign teams", strip_html)
        self.assertNotIn("Start Round 1", strip_html)
        self.assertNotIn("live-team-keep", strip_html)
        self.assertLess(html.index('id="round-option-card"'), html.index('class="live-shell-body"'))
        self.assertGreater(html.index('id="class-list-pane"'), html.index('id="live-shell-left"'))
        self.assertLess(html.index('id="class-list-pane"'), html.index('id="live-active-content"'))
        left_html = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="live-round-set"', left_html)
        self.assertIn("function paintRoundStrip()", js)
        self.assertIn("function optionCardHasVisibleControls(", js)
        self.assertIn("function readRoundFlags()", js)
        self.assertIn("function commitRoundType()", js)
        self.assertIn('$("live-round-type")?.addEventListener("change"', js)
        self.assertIn('$("live-round-set")?.addEventListener("click"', js)
        self.assertIn("function readRoundTypeFromState()", js)
        paint = js.split("function paintRoundStrip()")[1].split("function ")[0]
        self.assertNotIn("currentTeamCount()", paint)
        self.assertNotIn("picks.hidden = !team", js)
        option = js.split("function paintOptionCard()")[1].split("function paintFrames()")[0]
        self.assertIn("paintRoundStrip();", option)
        self.assertIn("optionCardHasVisibleControls(round)", option)
        self.assertIn("optionCardHasVisibleControls(play)", option)
        self.assertIn("rounds.hidden = true", option)
        self.assertNotIn("innerHTML", option)
        self.assertIn("paintRoundStrip();", js.split("function paintTeamsStripEnabled()")[1].split("function paintRoundStrip()")[0])
        strip_enabled = js.split("function paintTeamsStripEnabled()")[1].split("function paintRoundStrip()")[0]
        self.assertIn("closeTeamsPops({ keepRename: true })", strip_enabled)
        self.assertIn("const pointsButton =", js)
        piece = js.split("function interimQuestionControlPiece(")[1].split(
            "function interimQuestionControlMenu("
        )[0]
        self.assertIn('id === "responses"', piece)
        self.assertIn("!active && !closed", piece)
        self.assertIn("body.staff-shell .live-round-strip {", css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn(
            "grid-template-columns: var(--live-left-width) minmax(0, 1fr)",
            css,
        )
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)
        self.assertIn("function applySessionTimerUi(", js)
        self.assertIn("async function advanceTeamsToMeet()", js)
        self.assertIn("function classListGroupsByTeam()", js)

    def test_beat20_student_display_time_sits_above_name(self) -> None:
        """Beat 20: one reserved display-time slot above the student name."""
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('id="me-display-time"', home)
        self.assertIn('id="me-board"', home)
        self.assertLess(home.index('id="me-display-time"'), home.index('id="me-board"'))
        self.assertEqual(home.count("me-display-time"), 2)
        for name in ("join.html", "mood.html", "character.html", "pick.html", "waiting.html"):
            page = (LMS_DIR / "templates" / "student" / name).read_text(encoding="utf-8")
            self.assertNotIn("me-display-time", page)
            self.assertNotIn("display-time", page)
        landing = (LMS_DIR / "templates" / "landing.html").read_text(encoding="utf-8")
        self.assertNotIn("me-display-time", landing)
        slot_css = css.split(".student-chrome-top .me-display-time {")[1].split("}")[0]
        self.assertIn("min-height: 1.25rem", slot_css)
        self.assertIn("font-variant-numeric: tabular-nums", slot_css)
        self.assertIn("function paintDisplayTime(", js)
        self.assertIn("function tickDisplayTime(", js)
        self.assertIn("function formatDisplayClock(", js)
        paint = js.split("function paintDisplayTime(")[1].split(
            "function tickDisplayTime("
        )[0]
        self.assertIn("payload.display_time", paint)
        self.assertIn("dt.ends_at_ms", paint)
        self.assertIn('dataset.state = "running"', paint)
        self.assertIn('dataset.state = "paused"', paint)
        self.assertIn('dataset.state = "idle"', paint)
        self.assertNotIn("innerHTML", paint)
        self.assertNotIn("replaceChildren", paint)
        self.assertNotIn("hidden", paint)
        me = js.split("function paintMe(")[1].split("function paintBoard(")[0]
        self.assertNotIn("display-time", me)
        self.assertNotIn("displayTime", me)
        self.assertIn("paintDisplayTime(data)", js)
        self.assertIn("paintMe(data)", js)
        self.assertLess(js.index("paintDisplayTime(data)"), js.index("paintMe(data)"))
        idle = self.school.live_session_display_time(self.class_id)
        self.assertFalse(idle["running"])
        self.assertEqual(idle["label"], "—")
        self.school.game.start_session_timer(self.class_id, 5)
        running = self.school.live_session_display_time(self.class_id)
        self.assertTrue(running["running"])
        self.assertIsInstance(running["ends_at_ms"], int)
        self.assertGreaterEqual(running["remaining_sec"], 290)
        self.school.game.pause_round_timer(self.class_id)
        paused = self.school.live_session_display_time(self.class_id)
        self.assertTrue(paused["paused"])
        self.assertFalse(paused["running"])
        self.assertIsNone(paused["ends_at_ms"])
        self.assertGreater(paused["remaining_sec"], 0)

    def test_beat32_save_work_under_name_not_timer(self) -> None:
        """Beat 32: Save View is under the name row, not the timer."""
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertLess(home.index('id="me-display-time"'), home.index('id="me-name"'))
        self.assertLess(home.index('id="me-name-row"'), home.index('id="me-save-slot"'))
        self.assertLess(home.index('id="me-save-slot"'), home.index('id="save-work"'))
        self.assertLess(home.index('id="save-work"'), home.index('id="me-stats"'))
        self.assertNotIn('id="me-card-footer"', home)
        self.assertIn(">Save View<", home)
        self.assertIn("Saved to your downloads.", js)
        self.assertIn("Nothing to save yet.", js)
        self.assertIn("function saveStudentWork()", js)
        self.assertNotIn("meEl.innerHTML", js.replace("gameShowWelcomeEl.innerHTML", ""))

    def test_beat21_next_stops_timer_without_auto_starting_next_stage(self) -> None:
        """Beat 21: Next stops timers; every destination remains opt-in."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="session-timer"', html)
        self.assertLess(html.index('id="session-timer"'), html.index('id="class-list-pane"'))
        self.assertNotIn('id="meet-chain-skip-c"', html)
        self.assertNotIn('id="meet-chain-end"', html)
        self.assertNotIn("Skip C", html)
        self.assertNotIn("End Meet", html)
        patch = js.split("async function patchTeacherState(")[1].split(
            '$("mc-reveal-btn")'
        )[0]
        self.assertIn("applySessionTimerUi(overlayState)", patch)
        self.assertIn("function applySessionTimerUi(", js)
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        started = self.school.game.start_session_timer(self.class_id, 8)
        self.assertTrue(started["game"].get("round_ends_at_ms"))
        teams = self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(teams.status_code, 200)
        self.assertEqual(teams.get_json()["teacher_state"]["stage"], "teams")
        idle_teams = teams.get_json().get("game") or {}
        self.assertFalse((idle_teams.get("game") or {}).get("round_ends_at_ms"))
        self.assertFalse((idle_teams.get("game") or {}).get("timer_paused"))
        display_teams = self.school.live_session_display_time(self.class_id)
        self.assertFalse(display_teams["running"])
        self.assertFalse(display_teams["paused"])
        meet = self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(meet.get_json()["teacher_state"]["stage"], "meet")
        meet_game = (meet.get_json().get("game") or {}).get("game") or {}
        self.assertFalse(meet_game.get("round_ends_at_ms"))
        self.assertFalse(meet_game.get("timer_paused"))
        display_meet = self.school.live_session_display_time(self.class_id)
        self.assertFalse(display_meet["running"])
        self.assertFalse(display_meet["paused"])
        rnd = self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(rnd.get_json()["teacher_state"]["stage"], "round")
        round_game = (rnd.get_json().get("game") or {}).get("game") or {}
        self.assertFalse(round_game.get("round_ends_at_ms"))
        play = self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(play.get_json()["teacher_state"]["stage"], "play")
        play_game = (play.get_json().get("game") or {}).get("game") or {}
        self.assertFalse(play_game.get("round_ends_at_ms"))
        self.assertFalse(play_game.get("timer_paused"))

    def _aspen_id(self) -> int:
        """Roster id for the seeded Aspen student."""
        row = self.school.game.find_student_by_codename(self.class_id, "Aspen")
        assert row is not None
        return int(row["id"])

    def _open_live_with_aspen_answers(self) -> tuple[int, int]:
        """Start a live session, join Aspen, answer Minds-On plus a Meet tap."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        student_id = self._aspen_id()
        self.school.join_live_class_session(sid, student_id, codename="Aspen")
        self.school.ensure_waiting_room_minds_on(sid)
        prompt = self.school.get_active_live_prompt(sid)
        assert prompt is not None
        self.school.submit_live_prompt_response(
            int(prompt["id"]),
            student_id,
            {"choice": MINDS_ON_CHOICES[0]},
        )
        self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"advance": "next"},
        )
        self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"advance": "next"},
        )
        self.school.record_meet_chain_pick(
            sid, student_id=student_id, choice="This sparks something"
        )
        meet_prompt = self.school.set_live_session_prompt(
            sid,
            slide_index=901,
            kind="mc",
            payload=meet_team_prompt_payload(),
            activate=False,
        )
        self.school.submit_live_prompt_response(
            int(meet_prompt["id"]),
            student_id,
            {"choice": "This sparks something"},
        )
        return sid, student_id

    def _ended_score_row(self, student_id: int) -> dict | None:
        """Latest ended session_scores row for one student, if any."""
        with self.school.game._lock:
            row = self.school.game.conn.execute(
                """
                SELECT ss.present, ss.points, ss.points_r1
                FROM session_scores ss
                JOIN sessions s ON s.id = ss.session_id
                WHERE s.class_id = ? AND ss.student_id = ? AND s.status = 'ended'
                ORDER BY s.id DESC
                LIMIT 1
                """,
                (self.class_id, int(student_id)),
            ).fetchone()
        return dict(row) if row else None

    def test_beat19_end_class_persists_quit_discards(self) -> None:
        """Beat 24: Save and End Class keeps A&P; Quit stores neither."""
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('id="live-quit-class"', html)
        self.assertIn("/quit-live", html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("live-quit-class-form", js)
        sid, student_id = self._open_live_with_aspen_answers()
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        self.assertIn("tab=ap", ended.headers.get("Location", ""))
        self.assertIn("view=attendance", ended.headers.get("Location", ""))
        leftover = self.school.get_live_session(sid)
        self.assertIsNotNone(leftover)
        self.assertEqual(leftover["status"], "ended")
        self.assertTrue(self.school.session_is_celebrating(sid))
        saved = self._ended_score_row(student_id)
        self.assertIsNotNone(saved)
        self.assertEqual(int(saved["present"]), 1)
        self.assertEqual(int(saved["points"]), 1)
        self.assertEqual(int(saved["points_r1"]), 1)
        with self.school.game._lock:
            ended_before = int(
                self.school.game.conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE class_id = ? AND status = 'ended'",
                    (self.class_id,),
                ).fetchone()["n"]
            )
        sid2, student_id = self._open_live_with_aspen_answers()
        quit = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(quit.status_code, 302)
        self.assertIsNone(self.school.get_live_session(sid2))
        self.assertEqual(self.school.list_live_sessions_for_class(self.class_id), [])
        with self.school.game._lock:
            ended_after = int(
                self.school.game.conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE class_id = ? AND status = 'ended'",
                    (self.class_id,),
                ).fetchone()["n"]
            )
        self.assertEqual(ended_after, ended_before)
        after_quit = self._ended_score_row(student_id)
        self.assertIsNotNone(after_quit)
        self.assertEqual(int(after_quit["present"]), 1)
        self.assertEqual(int(after_quit["points"]), 1)

    def test_beat26_teams_to_meet_starts_scoreboard(self) -> None:
        """Beat 26: TEAMS→MEET keeps scores on the live overlay, no ESPN popup."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        advance = js.split("async function advanceTeamsToMeet()")[1].split(
            '$("ap-teams-next")'
        )[0]
        self.assertNotIn("openScoreboardOverlay()", advance)
        self.assertIn("pendingScoreboard = false", advance)
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="ap-scoreboard-toggle"', html)

    def test_beat27_classlist_course_game_team_cols(self) -> None:
        """Beat 27: ClassList shows compact Course / Game / Team headers."""
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn('id="ap-att-cols"', html)
        self.assertIn(">Course<", html)
        self.assertIn(">Game<", html)
        self.assertNotIn(">Team<", html)
        self.assertIn("ap-att-team-sep-name", js)
        self.assertIn('data-kind="team"', js)
        self.assertIn("ap-att-course", js)
        self.assertIn("ap-att-game", js)
        self.assertIn("function studentTeamLabel(", js)
        self.assertIn("body.staff-shell #class-list-pane .ap-att-cols", css)
        self.assertIn('classListVisibleStudents', js)
        self.assertIn('=== "teams"', js)

    def test_beat31_participation_is_per_question(self) -> None:
        """Beat 31: QH credits still count; submit does not award game points."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        student_id = self._aspen_id()
        self.school.join_live_class_session(sid, student_id, codename="Aspen")
        self.school.ensure_waiting_room_minds_on(sid)
        minds = self.school.get_active_live_prompt(sid)
        assert minds is not None
        self.school.submit_live_prompt_response(
            int(minds["id"]), student_id, {"choice": MINDS_ON_CHOICES[0]}
        )
        spark = self.school.set_live_session_prompt(
            sid,
            slide_index=850,
            kind="mc",
            payload=teams_spark_prompt_payload(),
            activate=False,
        )
        self.school.submit_live_prompt_response(
            int(spark["id"]), student_id, {"choice": "9"}
        )
        extra = self.school.set_live_session_prompt(
            sid,
            slide_index=860,
            kind="mc",
            payload={
                "item_id": "C1-QH-2",
                "kind": "mc",
                "prompt": "Second live QH",
                "choices": ["A", "B"],
            },
            activate=False,
        )
        self.school.submit_live_prompt_response(
            int(extra["id"]), student_id, {"choice": "A"}
        )
        meet_prompt = self.school.set_live_session_prompt(
            sid,
            slide_index=901,
            kind="mc",
            payload=meet_team_prompt_payload(),
            activate=False,
        )
        self.school.submit_live_prompt_response(
            int(meet_prompt["id"]),
            student_id,
            {"choice": "This sparks something"},
        )
        credits = self.school.participation_question_credits_for_class(
            self.class_id
        )
        self.assertEqual(credits.get(student_id), 3)
        state = self.school.get_live_session_state(sid)
        self.assertEqual(
            int((state.get("game_points") or {}).get(str(student_id), 0)), 0
        )
        join_prompt = state.get("join_prompt") or {}
        self.assertTrue(join_prompt.get("id"), state)

    def test_teacher_award_points_replaces_saved_assignment(self) -> None:
        """Responses and Points commits a replaceable assignment, not submit."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        self.school.game.begin_game(self.class_id)
        aspen = self._aspen_id()
        birch = int(
            self.school.game.find_student_by_codename(self.class_id, "Birch")["id"]
        )
        self.school.join_live_class_session(sid, aspen, codename="Aspen")
        self.school.join_live_class_session(sid, birch, codename="Birch")
        self.school.setup_live_session_groups(
            sid,
            n_teams=2,
            mode="balanced",
            present_ids=[aspen, birch],
        )
        extra = self.school.set_live_session_prompt(
            sid,
            slide_index=880,
            kind="mc",
            payload={
                "item_id": "C1-QH-AWARD",
                "kind": "mc",
                "prompt": "Award later",
                "choices": ["A", "B"],
                "key": "A",
            },
            activate=True,
        )
        prompt_id = int(extra["id"])
        self.school.submit_live_prompt_response(
            prompt_id, aspen, {"choice": "A"}
        )
        self.school.submit_live_prompt_response(
            prompt_id, birch, {"choice": "B"}
        )
        state = self.school.get_live_session_state(sid)
        self.assertEqual(int((state.get("game_points") or {}).get(str(aspen), 0)), 0)
        first = self.school.award_live_prompt_points(
            sid, prompt_id, mode="manual", student_ids=[aspen], amount=1
        )
        self.assertEqual(first["awarded_student_ids"], [aspen])
        awarded = {
            int(row["student_id"]): int(row.get("awarded_points") or 0)
            for row in first["responses"]
            if row.get("student_id") not in (None, "")
        }
        self.assertEqual(awarded.get(aspen), 1)
        self.assertEqual(awarded.get(birch), 0)
        state = self.school.get_live_session_state(sid)
        self.assertEqual(int((state.get("game_points") or {}).get(str(aspen), 0)), 1)
        second = self.school.award_live_prompt_points(
            sid, prompt_id, mode="manual", student_ids=[birch], amount=1
        )
        awarded = {
            int(row["student_id"]): int(row.get("awarded_points") or 0)
            for row in second["responses"]
            if row.get("student_id") not in (None, "")
        }
        self.assertEqual(awarded.get(aspen), 0)
        self.assertEqual(awarded.get(birch), 1)
        state = self.school.get_live_session_state(sid)
        self.assertEqual(int((state.get("game_points") or {}).get(str(aspen), 0)), 0)
        self.assertEqual(int((state.get("game_points") or {}).get(str(birch), 0)), 1)

    def test_award_live_prompt_points_without_teams(self) -> None:
        """Join-page awards credit a present student who is not on a team."""

        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        aspen = self._aspen_id()
        self.school.game.begin_game(self.class_id)
        self.school.join_live_class_session(sid, aspen, codename="Aspen")
        extra = self.school.set_live_session_prompt(
            sid,
            slide_index=881,
            kind="mc",
            payload={
                "item_id": "C1-QH-NOTEAM",
                "kind": "mc",
                "prompt": "No team yet",
                "choices": ["A", "B"],
                "key": "A",
            },
            activate=True,
        )
        prompt_id = int(extra["id"])
        self.school.submit_live_prompt_response(
            prompt_id, aspen, {"choice": "A"}
        )
        result = self.school.award_live_prompt_points(
            sid, prompt_id, mode="manual", student_ids=[aspen], amount=1
        )
        self.assertEqual(result["awarded_student_ids"], [aspen])
        awarded = {
            int(row["student_id"]): int(row.get("awarded_points") or 0)
            for row in result["responses"]
            if row.get("student_id") not in (None, "")
        }
        self.assertEqual(awarded.get(aspen), 1)

    def test_team_shared_questions_do_not_auto_score(self) -> None:
        """Shared-within-Group answers never add participation game points."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        student_id = self._aspen_id()
        self.school.join_live_class_session(sid, student_id, codename="Aspen")
        self.school.set_live_session_teacher_state(
            sid, student_view={"questions": "team"}
        )
        extra = self.school.set_live_session_prompt(
            sid,
            slide_index=870,
            kind="mc",
            payload={
                "item_id": "C1-QH-TEAM",
                "kind": "mc",
                "prompt": "Team-only QH",
                "choices": ["A", "B"],
            },
            activate=True,
        )
        self.school.submit_live_prompt_response(
            int(extra["id"]), student_id, {"choice": "A"}
        )
        credits = self.school.participation_question_credits_for_class(
            self.class_id
        )
        self.assertEqual(credits.get(student_id, 0), 0)
        state = self.school.get_live_session_state(sid)
        self.assertNotIn(str(student_id), state.get("game_points") or {})

    def _bind_aspen_live_session(self, sid: int, student_id: int) -> None:
        """Attach Aspen's live keys to the staff test client session."""
        attendees = self.school.list_live_session_attendees(sid)
        uuid = str(attendees[0]["participant_uuid"]) if attendees else ""
        with self.client.session_transaction() as sess:
            sess["student_offering_id"] = int(self.offering["id"])
            sess["student_class_id"] = int(self.class_id)
            sess["student_id"] = int(student_id)
            sess["student_codename"] = "Aspen"
            sess["student_live_session_id"] = int(sid)
            sess["student_participant_uuid"] = uuid

    def test_beat28_exit_feedback_on_quit_and_save(self) -> None:
        """End Live Class keeps student celebration + How-was-class; Quit wipes."""
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=attendance"
        ).get_data(as_text=True)
        self.assertIn("view=feedback", html)
        self.assertIn(">Feedback<", html)
        sid, student_id = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid, student_id)
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        pending = self.school.pending_exit_feedback(
            class_id=self.class_id, student_id=student_id
        )
        self.assertIsNotNone(pending)
        state = self.client.get("/api/student/state")
        self.assertEqual(state.status_code, 200)
        body = state.get_json()
        self.assertTrue(body.get("celebrate"))
        self.assertTrue((body.get("winner") or {}).get("name"))
        self.assertTrue((body.get("exit_feedback") or {}).get("pending"))
        self.assertNotEqual(body.get("status"), "ended")
        sent = self.client.post(
            "/api/student/exit-feedback",
            json={"mood": "ok", "comment": "Clear lesson", "token": pending["token"]},
        )
        self.assertEqual(sent.status_code, 200)
        self.assertTrue(sent.get_json().get("ok"))
        self.assertIsNone(
            self.school.pending_exit_feedback(
                class_id=self.class_id, student_id=student_id
            )
        )
        rows = self.school.list_exit_feedback_for_class(self.class_id)
        self.assertTrue(any(row.get("comment") == "Clear lesson" for row in rows))
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=feedback"
        )
        self.assertIn("Clear lesson", page.get_data(as_text=True))

        sid2, student_id2 = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid2, student_id2)
        quit = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(quit.status_code, 302)
        self.assertIsNone(
            self.school.pending_exit_feedback(
                class_id=self.class_id, student_id=student_id2
            )
        )
        gone = self.client.get("/api/student/state")
        gone_body = gone.get_json()
        self.assertIn(gone_body.get("status"), {"ended", "waiting"})
        self.assertFalse(gone_body.get("celebrate"))

    def test_end_then_quit_closes_student_celebration(self) -> None:
        """Quit after End Live drops celebration and unsubmitted How-was-class."""
        sid, student_id = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid, student_id)
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        self.assertIsNotNone(
            self.school.pending_exit_feedback(
                class_id=self.class_id, student_id=student_id
            )
        )
        self.assertTrue(self.client.get("/api/student/state").get_json().get("celebrate"))
        quit = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(quit.status_code, 302)
        self.assertIsNone(
            self.school.pending_exit_feedback(
                class_id=self.class_id, student_id=student_id
            )
        )
        gone = self.client.get("/api/student/state").get_json()
        self.assertIn(gone.get("status"), {"ended", "waiting"})
        self.assertFalse(gone.get("celebrate"))


    def test_beat30_module_and_live_class_dropdown(self) -> None:
        """Beat 30 interim: Module + Live class picks load the C-slot pack."""
        self._use_legacy_live_metadata()
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="live-pack-strip"', html)
        self.assertNotIn('id="ap-validate-apply"', html)
        self.assertNotIn('id="ap-validate-cancel"', html)
        self.assertIn("ap-att-plus", (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8"))
        self.assertIn("bindWhiteboard", (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8"))
        self.assertIn("setupPhase", (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8"))
        self.assertIn('id="live-module-select"', html)
        self.assertIn('id="live-class-select"', html)
        self.assertIn(">Module<", html)
        self.assertIn(">Live class<", html)
        self.assertIn('value="M1"', html)
        self.assertIn('value="C1"', html)
        self.assertIn('value="C2"', html)
        self.assertIn('value="C3"', html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("applyLivePackChoice", js)
        self.assertIn("live_module", js)
        start = self.school.start_live_class_session(
            self.class_id,
            int(self.teacher["id"]),
            live_module="M1",
            live_slot="C2",
        )
        sid = int(start["id"])
        state = self.school.live_session_teacher_state_payload(sid)
        self.assertEqual(state["live_module"], "M1")
        self.assertEqual(state["live_slot"], "C2")
        prompt = self.school.get_active_live_prompt(sid)
        assert prompt is not None
        payload = prompt.get("payload") or {}
        self.assertEqual(payload.get("live_slot"), "C2")
        self.assertEqual(payload.get("live_module"), "M1")
        self.assertEqual(payload.get("item_id"), "minds_on")
        patched = self.client.post(
            f"/api/live-sessions/{sid}/teacher-state",
            json={"live_module": "M1", "live_slot": "C3"},
        )
        self.assertEqual(patched.status_code, 200)
        body = patched.get_json()["teacher_state"]
        self.assertEqual(body["live_slot"], "C3")
        again = self.school.get_active_live_prompt(sid)
        assert again is not None
        self.assertEqual((again.get("payload") or {}).get("live_slot"), "C3")

    def test_run_live_opens_on_set_class_validate(self) -> None:
        """Fresh Run Live Class starts on Set Class, not the join/attendance shell."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live&run=1")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        root = html[html.index('id="ap-root"') : html.index('id="ap-overlay-error"')]
        self.assertIn("is-set-class", root)
        self.assertIn('data-setup="1"', root)
        self.assertIn('data-run="1"', root)
        self.assertIn('id="ap-panel-validate"', html)
        self.assertIn("Set Class", html)
        self.assertIn('id="live-module-select"', html)
        self.assertIn('id="live-class-select"', html)
        self.assertIn('id="ap-day-grid"', html)
        validate = html[html.index('id="ap-panel-validate"') : html.index('id="live-shell-left"')]
        self.assertNotIn(" hidden", validate.split(">", 1)[0])
        self.assertIn('data-step="validate"', validate)
        self.assertIn("is-current", validate.split(">", 1)[0])

        run = self.client.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        location = run.headers.get("Location", "")
        self.assertIn("tab=live", location)
        self.assertIn("run=1", location)
        reminted = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(reminted)
        assert reminted is not None
        remint_html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
            f"&live_session_id={int(reminted['id'])}"
        ).get_data(as_text=True)
        remint_root = remint_html[
            remint_html.index('id="ap-root"') : remint_html.index('id="ap-overlay-error"')
        ]
        self.assertIn("is-set-class", remint_root)
        self.assertIn('id="ap-panel-validate"', remint_html)
        teacher = self.school.live_session_teacher_state_payload(int(reminted["id"]))
        self.assertFalse(teacher.get("class_set"))

        picker = LMS_DIR / "static" / "bank_mc_picker.js"
        self.assertTrue(picker.is_file(), "bank-import picker must stay on disk")
        picker_js = picker.read_text(encoding="utf-8")
        self.assertIn("export async function openBankMcPicker", picker_js)
        self.assertIn('aria-label="Bank scope"', picker_js)
        self.assertIn("Course Wide", picker_js)
        self.assertIn("data-bank-mc-kind", picker_js)
        self.assertIn(">Warmup</option>", picker_js)
        served = self.client.get("/static/bank_mc_picker.js")
        self.assertEqual(served.status_code, 200)
        common = self.client.get("/static/common.js").get_data(as_text=True)
        self.assertIn("export function formatQuestionHtml", common)
        self.assertIn("export function questionFieldHtml", common)
        self.assertIn("export function questionImageHtml", common)
        self.assertIn("export async function renderLiveQuestionMath", common)
        self.assertIn("/static/vendor/katex", common)
        self.assertTrue(
            (LMS_DIR / "static" / "vendor" / "katex" / "katex.min.js").is_file()
        )

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertNotIn(
            'import { openBankMcPicker } from "/static/bank_mc_picker.js"', js
        )
        self.assertIn('import("/static/bank_mc_picker.js")', js)
        self.assertIn("function stripEquationLatex(", js)
        self.assertIn("Couldn't render this equation — check the TeX.", js)
        self.assertIn("No equation yet.", js)
        self.assertIn("live-add-q-bank-scope-select", js)
        self.assertIn('let currentStep = "validate"', js)
        self.assertIn("let setupPhase = true", js)
        self.assertIn("function classSetIsComplete(", js)
        self.assertIn("function enterSetClassPhase()", js)
        self.assertIn("function wantsFreshSetClass()", js)
        self.assertIn("class_set: true", js)
        boot = js.split("setSessionTimerMinutes(3);")[1].split(
            "function spawnScorePop("
        )[0]
        self.assertIn("enterSetClassPhase()", boot)
        self.assertIn("paintTeacherShell()", boot)
        self.assertIn("await openRunLiveClass()", boot)
        self.assertIn("if (setupPhase) return", boot)
        self.assertIn("await ensureC1MediaSeeded()", boot)
        resume = js.split("async function resumeLiveClassIfNeeded()")[1].split(
            "export async function openLogParticipation()"
        )[0]
        self.assertIn("function clearFreshSetClassFromUrl()", js)
        self.assertIn("clearFreshSetClassFromUrl()", js)
        self.assertIn(
            "if (wantsFreshSetClass() && !classSetIsComplete(status, teacher)) return false",
            resume,
        )
        self.assertIn("if (!classSetIsComplete(status, teacher)) return false", resume)
        self.assertIn("paintTeacherShell()", resume)
        self.assertIn("pollLiveSessionAttendees({ full: true, force: true })", resume)
        self.assertIn("await loadSavedLiveLesson(", resume)
        self.assertIn("await ensureC1MediaSeeded()", resume)
        self.assertIn("function persistActiveMediaCopy(", js)
        self.assertIn("function scheduleActiveMediaCopyAutosave()", js)
        self.assertIn("deck?fresh=1", js)
        bind = js.split("function bindActiveMediaControls(")[1].split(
            "window.addEventListener(\"message\""
        )[0]
        self.assertNotIn("ensureC1MediaSeeded()", bind)
        self.assertNotIn("/api/classes/${classId}/begin", resume)
        open_fn = js.split("export async function openRunLiveClass()")[1].split(
            "export async function openTakeAttendance()"
        )[0]
        self.assertIn("enterSetClassPhase()", open_fn)
        self.assertIn('showPanel("validate")', open_fn)
        begin = js.split("async function proceedRunLiveBegin(")[1].split(
            "export async function openRunLiveClass()"
        )[0]
        self.assertIn("await markClassSetComplete()", begin)
        self.assertIn("exitSetClassPhase()", begin)
        self.assertLess(
            begin.index("await ensureLiveSessionMinted"),
            begin.index("exitSetClassPhase()"),
        )


    def test_feedback_grid_before_after_mood_score(self) -> None:
        """Feedback tab lists the roster with before/after mood and no When column."""
        self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        live = self.school.get_active_live_session_for_class(self.class_id)
        aspen = next(
            row
            for row in self.school.game.dashboard(self.class_id)["students"]
            if row["codename"] == "Aspen"
        )
        self.school.game.set_mood(self.class_id, int(aspen["id"]), "low")
        self.school.join_live_class_session(
            int(live["id"]), int(aspen["id"]), codename="Aspen"
        )
        self.school.open_exit_feedback_for_class(self.class_id)
        pending = self.school.pending_exit_feedback(
            class_id=self.class_id, student_id=int(aspen["id"])
        )
        self.assertIsNotNone(pending)
        self.school.submit_exit_feedback(
            pending["token"], mood="good", comment="Great energy"
        )
        grid = self.school.feedback_grid_for_class(self.class_id)
        self.assertEqual(self.school.mood_jump_score("low", "good"), 2)
        self.assertEqual(self.school.mood_jump_score("good", "ok"), -1)
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=feedback"
        )
        html = page.get_data(as_text=True)
        self.assertNotIn(">When<", html)
        self.assertIn("Before", html)
        self.assertIn("After", html)
        self.assertIn("View", html)
        self.assertIn("Great energy", html)
        self.assertTrue(grid["students"])
        aspen_row = next(
            row for row in grid["students"] if row["codename"] == "Aspen"
        )
        self.assertEqual(aspen_row["total"], 2)

    def test_quit_closes_live_session_overlay(self) -> None:
        """Header Quit submit closes the stored overlay; overlay self-closes when gone."""
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="live-quit-class-form"', html)
        self.assertIn("Quit without saving attendance or participation?", html)
        self.assertNotIn(
            'onsubmit="return confirm(this.dataset.confirm);"', html
        )

        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn(
            '$("live-quit-class-form")?.addEventListener("submit"', staff_js
        )
        submit = staff_js.split(
            '$("live-quit-class-form")?.addEventListener("submit"'
        )[1].split("for (const id of")[0]
        self.assertIn("confirmQuitAndCloseOverlay", submit)
        self.assertIn("closeLiveSessionOverlay", staff_js.split(
            "function confirmQuitAndCloseOverlay"
        )[1].split("$(\"live-quit-class-form\")")[0])
        self.assertIn("event.preventDefault()", submit)
        cancel = staff_js.split(
            'for (const id of ["ap-validate-cancel", "ap-score-cancel"])'
        )[1].split('$("ap-allow-guests")')[0]
        self.assertIn("confirmQuitAndCloseOverlay", cancel)
        self.assertIn("form.submit()", cancel)
        track = staff_js.split("document.querySelectorAll(\"[data-track-nav='quit']\")")[
            1
        ].split('$("ap-allow-guests")')[0]
        self.assertIn("confirmQuitAndCloseOverlay", track)
        self.assertIn("form.submit()", track)

        common = (
            REPO_ROOT / "tools" / "math-game-show" / "static" / "common.js"
        ).read_text(encoding="utf-8")
        self.assertIn("let liveSessionOverlayWindow", common)
        self.assertIn("function rememberLiveSessionOverlay(", common)
        reserve = common.split("export function reserveLiveSessionOverlay()")[1].split(
            "export function openScoreboardOverlay"
        )[0]
        self.assertIn("rememberLiveSessionOverlay", reserve)
        opened = common.split("export function openLiveSessionOverlay(")[1].split(
            "export function closeLiveSessionOverlay"
        )[0]
        self.assertIn("rememberLiveSessionOverlay", opened)
        close_fn = common.split("export function closeLiveSessionOverlay()")[1]
        stored_i = close_fn.find("liveSessionOverlayWindow")
        named_i = close_fn.find('window.open("", LIVE_SESSION_OVERLAY_NAME)')
        self.assertGreaterEqual(stored_i, 0)
        self.assertGreaterEqual(named_i, 0)
        self.assertLess(stored_i, named_i)
        self.assertIn("tryCloseWindow(stored)", close_fn)

        overlay = (
            REPO_ROOT / "tools" / "math-game-show" / "static" / "live_session_overlay.js"
        ).read_text(encoding="utf-8")
        self.assertIn("function fetchLiveSessionState(", overlay)
        self.assertIn("/state?light=1", overlay)
        self.assertIn("response.status === 404", overlay)
        self.assertIn("function dismissOverlayWindow(", overlay)
        tick = overlay.split("async function tick()")[1].split(
            "codeEl?.addEventListener"
        )[0]
        self.assertIn("result.missing", tick)
        self.assertIn("dismissOverlayWindow({ blankIfStillOpen: true })", tick)
        paint = overlay.split("function paintSession(state)")[1].split(
            "function paintTeamScores"
        )[0]
        self.assertIn('phase === "ended"', paint)
        self.assertIn('session?.status === "ended"', paint)
        self.assertIn("dismissOverlayWindow()", paint)
        self.assertIn("window.close()", overlay)
        self.assertIn('location.replace("about:blank")', overlay)

        node = REPO_ROOT / "tools" / "math-game-show" / "test_live_overlay_window.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)


    def test_staff_poll_skips_when_in_flight_and_uses_light(self) -> None:
        """Interval polls do not stack and default to the light /state query."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("sessionPollInFlight", js)
        self.assertIn("staffStateNeedsFull", js)
        self.assertIn("optimisticTeacherState(", js)
        self.assertIn("?light=1", js)
        self.assertIn("if (!wantFull) throw", js)
        self.assertIn("hasOwnProperty.call(payload || {}, \"active_media\")", js)
        self.assertIn("function setLiveReconnectBanner(", js)
        self.assertIn("Reconnecting…", js)
        self.assertIn("setLiveReconnectBanner(true)", js)
        self.assertIn('payload?.error === "state unavailable"', js)
        cards = js.split("async function refreshLiveQuestionCards(")[1].split(
          "function questionCardsFromMetadata("
        )[0]
        self.assertIn("setLiveReconnectBanner(true)", cards)
        self.assertNotIn("lastQuestionCards = []", cards)
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("id=\"live-reconnect\"", course)
        self.assertIn("live-reconnect-retry", course)
        self.assertIn("function teacherStateNeedsQuestionRefresh(", js)
        self.assertNotIn("lastLiveItems = payload.live_metadata.items", js)
        handler = js.split('$("live-run-as-group")')[1].split(
            '$("live-hide-absent")'
        )[0]
        self.assertNotIn("pollLiveSessionAttendees()", handler)
        hide = js.split('$("live-hide-absent")?.addEventListener("change"')[1]
        hide = hide.split("function availableRoundKinds(")[0]
        self.assertIn("renderAttendanceList()", hide)
        self.assertNotIn("pollLiveSessionAttendees()", hide)
        patch = js.split("async function patchTeacherState(")[1].split(
            '$("mc-reveal-btn")'
        )[0]
        self.assertIn("teacherStateNeedsQuestionRefresh(body)", patch)
        self.assertIn("pollLiveSessionAttendees({ full: true, force: true })", patch)

    def test_student_poll_keeps_last_frame_and_shows_reconnect(self) -> None:
        """Failed student /state keeps the last paint and shows Reconnecting…."""

        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("function setStudentReconnectBanner(", js)
        self.assertIn("Reconnecting…", home)
        self.assertIn("student-reconnect-retry", home)
        tick = js.split("async function tick()")[1].split(
            "document.getElementById(\"live-response\")"
        )[0]
        self.assertIn("if (!res.ok)", tick)
        self.assertIn('data.error === "state unavailable"', tick)
        self.assertIn("setStudentReconnectBanner(true)", tick)
        self.assertIn("setStudentReconnectBanner(false)", tick)
        self.assertNotIn("innerHTML = \"\"", tick.split("if (data.celebrate)")[0])

    def test_playlist_move_options_list_every_rail_page(self) -> None:
        """Relocate options use 1-based rail index plus page name for every page."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function playlistMovePageOptions(", js)
        body = js.split("function playlistMovePageOptions(")[1].split(
            "function applyLocalPlaylistCardChange("
        )[0]
        self.assertIn("${pageIndex}. ${name}", body)
        self.assertIn("currentLivePageIndex()", js)
        self.assertNotIn(
            "playlistMovePageOptions(currentLivePageNumber())",
            js,
        )
        self.assertNotIn("if (pageIndex === currentPageIndex) return \"\";", body)

    def test_open_relocate_dialog_fills_pages_for_engine_ride(self) -> None:
        """(Re)move always lists rail pages, including for teams_spark."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function openRelocateDialog(", js)
        body = js.split("function openRelocateDialog(")[1].split(
            "function closeRelocateDialog("
        )[0]
        self.assertNotIn("select.innerHTML = engineRide", body)
        self.assertNotIn('select.innerHTML = ""', body)
        self.assertIn(
            "playlistMovePageOptions(currentLivePageIndex() + 1)",
            body,
        )
        self.assertNotIn("moveSection.hidden = engineRide", body)
        self.assertNotIn("moveBtn.disabled =\n      engineRide", body)

    def test_relocate_adopts_playlist_metadata_payload(self) -> None:
        """Move/remove JS adopts API live_metadata instead of only filtering."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        body = js.split("async function relocatePlaylistItem(")[1].split(
            "function playlistPageLabel("
        )[0]
        self.assertIn("applyLiveMcImportPayload(result)", body)
        self.assertIn("if (!result?.live_metadata)", body)
        self.assertIn("refreshLessonDeckMetadata()", body)
        self.assertIn("questionCardsFromMetadata(", js)

    def test_current_page_rows_honor_overlay_page_number(self) -> None:
        """Custom overlay pages still show questions bound to their page_number."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        body = js.split("function currentPageQuestionRows()")[1].split(
            "function liveQuestionEquationHtml("
        )[0]
        self.assertNotIn("if (isBlankOverlayLivePage()) return [];", body)
        self.assertIn("if (pageIndex > 0 && cardPage > 0) return cardPage === pageIndex", body)
        self.assertIn("if (blankOverlay) return false", body)

if __name__ == "__main__":
    unittest.main()
