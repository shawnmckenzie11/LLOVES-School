#!/usr/bin/env python3
"""Teacher Run Live Class IA v2 shell markup and existing control IDs."""

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
        self.assertIn('data-stage="join"', html)
        self.assertIn('data-stage="teams"', html)
        self.assertIn('data-stage="meet"', html)
        self.assertIn('data-stage="round"', html)
        self.assertIn('data-stage="play"', html)
        self.assertNotIn('data-stage="challenge"', html)
        self.assertNotIn('data-stage="freeze"', html)
        self.assertIn("live-stage-pill", html)
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
        self.assertIn('id="live-unlock-media"', html)
        self.assertIn('id="live-unlock-canvas"', html)
        self.assertIn("Minds-On", html)
        self.assertIn("Consolidation", html)
        self.assertIn(">SET<", html)
        self.assertIn('id="class-list-pane"', html)
        self.assertIn('id="team-assign-pane"', html)
        self.assertIn('id="live-active-content"', html)
        self.assertIn('id="live-content-tabs"', html)
        self.assertIn("Active Media", html)
        self.assertIn("Question(s)", html)
        self.assertIn("Canvas/Slides", html)
        self.assertIn('id="live-edit-layout"', html)
        self.assertIn('id="live-layout-presets"', html)
        self.assertIn('id="live-frames"', html)
        self.assertIn('data-frame="A"', html)
        self.assertIn('data-frame="B"', html)
        self.assertIn('data-frame="C"', html)
        self.assertIn('id="live-canvas-stub"', html)
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
        self.assertIn("/static/live-media/m1c1-c1-real-slice.html?role=teacher", html)
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
            "ap-rank-toggle",
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

    def test_live_tab_end_class_is_placement_only(self) -> None:
        """Header Save and End Class and Quit are distinct finish routes."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn("Save attendance & participation, then end?", html)
        self.assertIn("Save attendance and end without participation?", html)
        self.assertIn(f"/staff/class/{self.class_id}/end-live", html)
        self.assertIn(f"/staff/class/{self.class_id}/quit-live", html)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('id="live-quit-class"', html)
        self.assertIn('aria-label="Save and End Class"', html)
        self.assertIn(">Save and End Class<", html)
        self.assertIn('aria-label="Quit"', html)
        self.assertIn("live-end-class-form", html)
        self.assertIn("live-quit-class-form", html)
        self.assertNotIn("All session data will be lost", html)
        self.assertNotIn('class="live-header-end danger live-legacy-control"', html)
        self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        self.assertIn("Save and End Class", home_html)
        self.assertIn("Save attendance & participation, then end?", home_html)
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
        self.assertIn("grid-template-rows: auto auto auto auto minmax(12rem, 1fr)", css)
        self.assertIn("body.staff-shell .live-shell-ia-v2 > .live-header {\n  grid-row: 1;", css)
        self.assertIn(
            "body.staff-shell .live-shell-ia-v2 > .live-pack-strip {\n  grid-row: 2;",
            css,
        )
        self.assertIn(
            "body.staff-shell .live-shell-ia-v2 > .live-options-strip {\n  grid-row: 3;",
            css,
        )
        self.assertIn("body.staff-shell .live-shell-ia-v2 > .live-shell-body {\n  grid-row: 5;", css)
        self.assertIn("body.staff-shell .live-shell-body {\n  display: grid;", css)
        self.assertIn("align-items: stretch", css)
        self.assertIn("body.staff-shell .live-active-content {\n  flex: 1 1 auto;\n  min-height: 100%;", css)
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
        self.assertLess(strip_i, body_i)
        self.assertLess(body_i, left_i)
        self.assertLess(left_i, timer_i)
        self.assertLess(timer_i, list_i)
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
        self.assertIn("live-canvas-align", js)
        self.assertIn('if (teams) teams.hidden = stage !== "teams";', js)
        self.assertIn("if (meet) meet.hidden = true;", js)
        self.assertIn('if (round) round.hidden = stage !== "round";', js)
        self.assertIn('if (play) play.hidden = stage !== "play";', js)
        self.assertIn('patchTeacherState({ advance: "prev" })', js)
        self.assertIn('patchTeacherState({ advance: "next" })', js)
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
        self.assertIn("Reveal results", html)
        self.assertIn("Hide reveal · keep collecting", html)
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

    def test_beat3_teams_strip_is_one_condensed_row(self) -> None:
        """Beat 3: TEAMS OptionsStrip is count / assign / track / rename only."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="teams-option-card"', html)
        self.assertIn("live-teams-strip", html)
        self.assertIn('id="ap-n-teams"', html)
        self.assertIn('min="1"', html)
        self.assertIn('id="ap-n-teams-down"', html)
        self.assertIn('id="ap-n-teams-up"', html)
        self.assertIn('id="live-teams-assign"', html)
        self.assertIn(">Balanced<", html)
        self.assertIn(">Random<", html)
        self.assertIn(">Manual<", html)
        self.assertIn('id="ap-scoreboard-toggle"', html)
        self.assertIn('id="ap-rank-toggle"', html)
        self.assertIn(">Scoreboard<", html)
        self.assertIn(">Rank<", html)
        self.assertIn('id="ap-teams-rename"', html)
        self.assertIn(">Rename<", html)
        self.assertIn('id="ap-manual-assign"', html)
        self.assertIn('id="ap-panel-names"', html)
        self.assertIn('id="team-assign-pane"', html)
        self.assertNotIn("Individual (1) vs teams", html)
        self.assertNotIn('id="ap-gamify-no"', html)
        self.assertNotIn('id="ap-gamify-yes"', html)
        self.assertNotIn("Team assign unlocks when Tracking is Team", html)
        self.assertNotIn("Number of teams", html)
        self.assertNotIn("Assign Balanced", html)
        self.assertNotIn("Assign Randomly", html)
        self.assertNotIn("Assign Manually", html)
        self.assertNotIn("Run as Game", html)
        strip_html = html.split('id="teams-option-card"')[1].split('id="meet-option-card"')[0]
        self.assertIn('id="ap-n-teams"', strip_html)
        self.assertIn('id="ap-assign-balanced"', strip_html)
        self.assertIn('id="ap-scoreboard-toggle"', strip_html)
        self.assertIn('id="ap-rank-toggle"', strip_html)
        self.assertIn('id="ap-teams-rename"', strip_html)
        self.assertLess(strip_html.find('id="ap-n-teams"'), strip_html.find('id="live-teams-assign"'))
        self.assertLess(strip_html.find('id="live-teams-assign"'), strip_html.find('id="ap-track-game-opts"'))
        self.assertLess(strip_html.find('id="ap-track-game-opts"'), strip_html.find('id="ap-teams-rename"'))
        self.assertLess(strip_html.find('id="ap-teams-rename"'), strip_html.find('id="team-assign-pane"'))
        body_i = html.index('class="live-shell-body"')
        self.assertLess(html.index('id="team-assign-pane"'), body_i)
        self.assertLess(html.index('id="class-list-pane"'), html.index('id="live-active-content"'))
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .live-teams-strip {", css)
        teams_css = css.split("body.staff-shell .live-teams-strip {")[1].split(
            "body.staff-shell .live-teams-strip .live-teams-count {"
        )[0]
        self.assertIn("flex-wrap: nowrap", teams_css)
        self.assertIn("--live-left-width: clamp(220px, 24%, 320px)", css)
        self.assertIn(
            "grid-template-columns: 1.25rem minmax(4.5rem, 1fr) 2.4rem 2.2rem auto",
            css,
        )
        pop_css = css.split("body.staff-shell .live-options-strip #team-assign-pane {")[1].split(
            "body.staff-shell #team-assign-pane[hidden]"
        )[0]
        self.assertIn("position: fixed", pop_css)
        self.assertNotIn("flex: 1 1 auto", pop_css)
        self.assertIn("#class-list-pane .ap-att-row.has-team-color .ap-att-name", css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function currentTeamCount()", js)
        self.assertIn("function paintTeamsStripEnabled()", js)
        self.assertIn("function openTeamsPop(", js)
        self.assertIn("getBoundingClientRect()", js)
        self.assertIn("function studentTeamColor(", js)
        self.assertIn("return { min: 1, max: Math.max(1, present) }", js)
        self.assertIn('selectAssignMode(lastAssignMode || "balanced")', js)
        self.assertIn("has-team-color", js)
        self.assertNotIn('id="ap-gamify-no"', js)
        self.assertNotIn("$(\"ap-gamify-yes\")", js)
        self.assertNotIn("Team assign unlocks when Tracking is Team", js)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)
        self.assertNotIn("Waiting for students to join.", html)
        self.assertNotIn("join-options-hint", html)
        self.assertNotIn("live-options-hint", css)
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
        self.assertIn("return { min: 1, max: Math.max(1, present) }", js)
        self.assertNotIn("return { min: 1, max: Math.max(2, present) }", js)
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
        self.assertIn('selectAssignMode(lastAssignMode || "balanced")', js)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
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
        self.assertIn('id="live-unlock-media"', html)
        self.assertIn('id="live-unlock-canvas"', html)
        self.assertIn('id="live-canvas-align"', html)
        self.assertIn("Frozen to teacher", html)
        self.assertIn("Unique per student", html)
        self.assertIn("Shared within group", html)
        play_html = html.split('id="play-option-card"')[1].split("</section>")[0]
        self.assertNotIn('id="live-unlock-media"', play_html)
        self.assertIn("card.hidden = false;", js)
        self.assertIn("patchTeacherState({ unlocks: { media:", js)
        self.assertIn("patchTeacherState({ canvas_align:", js)
        self.assertIn("canvas-presence", js)
        self.assertIn("body.staff-shell .live-unlocks-strip {", css)
        unlocks_css = css.split("body.staff-shell .live-unlocks-strip {")[1].split(
            "body.staff-shell .live-canvas-align {"
        )[0]
        self.assertIn("flex-wrap: nowrap", unlocks_css)
        self.assertIn("max-height: var(--live-options-row-h)", unlocks_css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("function paintStudentCanvas(", student_js)
        self.assertIn("/api/student/canvas-presence", student_js)
        self.assertIn("canvasAlign", student_js)
        self.assertIn("media: Boolean(unlocks.media)", student_js)
        self.assertIn("canvas: Boolean(unlocks.canvas)", student_js)
        self.assertIn('canvasPane.classList.toggle("is-readonly"', student_js)
        self.assertIn('if (lastAlign === "teacher") return;', student_js)
        self.assertIn('id="student-canvas"', 
            (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        )
        home_html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="media-pane"', home_html)
        self.assertIn('id="canvas-pane"', home_html)

    def test_beat29_uncheck_collapses_student_frames(self) -> None:
        """Beat 29: student JS hides Media/Canvas when unlocks are off."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn("media: Boolean(unlocks.media)", js)
        self.assertIn("canvas: Boolean(unlocks.canvas)", js)
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
        self.assertIn("return { min: 1, max: Math.max(1, present) }", js)
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
        self.assertNotIn("setInterval", ticks)

    def test_beat22b_teams_classlist_present_only(self) -> None:
        """Beat 22b: TEAMS ClassList is present-only; joiners appear via the same poll."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        visible = js.split("function classListVisibleStudents(")[1].split(
            "function classListRosterOrder("
        )[0]
        self.assertIn('stage || "").toLowerCase() !== "teams"', visible)
        self.assertIn("sessionPresentIds.has(Number(stu.id))", visible)
        self.assertIn("stu.guest", visible)
        render = js.split("function renderAttendanceList()")[1].split(
            "function updateAttCount()"
        )[0]
        self.assertIn("classListVisibleStudents(", render)
        self.assertIn('dataset.presentOnly', render)
        self.assertIn("classListRosterOrder(classListVisibleStudents(", render)
        ticks = js.split("async function applySessionPresentTicks(")[1].split(
            "async function pollLiveSessionAttendees("
        )[0]
        self.assertIn("renderAttendanceList()", ticks)
        self.assertIn("setNTeams(currentTeamCount())", ticks)
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
        """Beat 14: one SessionTimer above ClassList; no Skip / End Meet."""
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
        self.assertIn('id="session-timer"', left_html)
        self.assertIn('id="ap-meet-start"', left_html)
        self.assertIn('id="ap-meet-live-clock"', left_html)
        self.assertLess(left_html.index('id="session-timer"'), left_html.index('id="class-list-pane"'))
        self.assertLess(html.index('id="ap-meet-start"'), html.index('id="class-list-pane"'))
        strip_html = html.split('id="meet-option-card"')[1].split(
            'id="round-option-card"'
        )[0]
        self.assertNotIn('id="ap-meet-start"', strip_html)
        self.assertNotIn('id="ap-meet-live-clock"', strip_html)
        self.assertNotIn("Skip C", strip_html)
        self.assertNotIn("End Meet", strip_html)
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
        self.assertIn('teacherState.stage === "teams"', adopt)
        self.assertIn("lastMcTally = null", adopt)
        self.assertIn("paintResultsStrip()", adopt)
        apply = js.split("function applyMcTally(")[1].split(
            "function currentMcPromptRef("
        )[0]
        self.assertIn('teacherState.stage === "teams"', apply)
        self.assertIn('ref === "minds_on"', apply)
        self.assertIn("lastMcTally = null", apply)
        paint = js.split("function paintTeacherShell()")[1].split("function paintHeaderDate()")[0]
        self.assertIn("lockClassListPane();", paint)
        self.assertNotIn("innerHTML", paint)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("function isJoinMindsOnPrompt(", student)
        self.assertIn('ts.stage || "") === "teams" && isJoinMindsOnPrompt', student)
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
        self.assertIn('teacherState.stage === "teams"', js)
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
        """Beat 13: TEAMS→Meet Next is assign+stage; errors stay in the strip."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn('id="live-teams-assign-error"', html)
        strip_html = html.split('id="teams-option-card"')[1].split('id="meet-option-card"')[0]
        self.assertIn('id="live-teams-assign-error"', strip_html)
        self.assertLess(html.index('id="live-teams-assign-error"'), html.index('class="live-shell-body"'))
        self.assertGreater(html.index('id="live-active-content"'), html.index('id="class-list-pane"'))
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .live-teams-strip .live-strip-error {", css)
        overlay_css = css.split("body.staff-shell .live-shell-ia-v2 > #ap-overlay-error {")[1].split("}")[0]
        self.assertIn("position: absolute", overlay_css)
        self.assertNotIn("grid-row: 1", overlay_css)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("async function advanceTeamsToMeet()", js)
        self.assertIn("function showTeamsAssignError(", js)
        self.assertIn('showError("#live-teams-assign-error"', js)
        self.assertIn('errorSelector: "#live-teams-assign-error"', js)
        self.assertIn('if (teacherState.stage === "teams")', js)
        self.assertIn("advanceTeamsToMeet()", js)
        next_click = js.split('$("live-stage-next")?.addEventListener("click"')[1].split(
            '$("meet-chain-next")'
        )[0]
        self.assertIn("advanceTeamsToMeet()", next_click)
        self.assertIn('teacherState.stage === "teams"', next_click)
        teams_next = js.split('$("ap-teams-next")?.addEventListener("click"')[1].split(
            '$("ap-manual-list")'
        )[0]
        self.assertIn("advanceTeamsToMeet()", teams_next)
        self.assertNotIn('showError("#ap-overlay-error"', teams_next)
        advance = js.split("async function advanceTeamsToMeet()")[1].split(
            '$("ap-teams-next")'
        )[0]
        self.assertIn("body.assign", advance)
        self.assertIn("n_teams: nTeams", advance)
        self.assertIn('body.teams_mode = "individual"', advance)
        self.assertIn("showTeamsAssignError", advance)
        self.assertNotIn("innerHTML", advance)
        self.assertNotIn("replaceChildren", advance)
        self.assertNotIn("paintTeacherShell", advance)
        self.assertNotIn('showError("#ap-overlay-error"', advance)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)

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
        self.assertIn("currentTeamCount() > 1", gate)
        self.assertIn("assignedRosterTeams().length >= 2", gate)
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
        self.assertLess(left_html.index('id="session-timer"'), left_html.index('id="class-list-pane"'))
        self.assertIn("function applySessionTimerUi(", js)
        self.assertIn("async function advanceTeamsToMeet()", js)

    def test_beat33_round_type_dropdown_always_visible(self) -> None:
        """Beat 33: ROUND OptionsStrip is a type dropdown + SET, even at teams=1."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        strip_html = html.split('id="round-option-card"')[1].split('id="play-option-card"')[0]
        self.assertIn('id="live-round-picks"', strip_html)
        self.assertIn('id="live-round-type"', strip_html)
        self.assertIn('id="live-round-set"', strip_html)
        self.assertIn(">Minds-On<", strip_html)
        self.assertIn(">Action<", strip_html)
        self.assertIn(">Consolidation<", strip_html)
        self.assertIn(">SET<", strip_html)
        self.assertIn('value="minds_on"', strip_html)
        self.assertIn('value="action"', strip_html)
        self.assertIn('value="consolidation"', strip_html)
        self.assertNotIn('type="checkbox"', strip_html)
        self.assertNotIn("data-round=", strip_html)
        self.assertNotIn('id="live-round-minds-on"', strip_html)
        self.assertLess(strip_html.find('id="live-round-type"'), strip_html.find('id="live-round-set"'))
        self.assertIn('id="live-round-picks"', html)
        self.assertNotRegex(
            html,
            r'<div class="live-round-picks"[^>]*id="live-round-picks"[^>]*\bhidden\b',
        )
        self.assertNotIn("Always 3 rounds", strip_html)
        self.assertNotIn("Keep teams", strip_html)
        self.assertNotIn("Reassign teams", strip_html)
        self.assertNotIn("Start Round 1", strip_html)
        self.assertNotIn("Start Round", strip_html)
        self.assertNotIn("Set up one round at a time", strip_html)
        self.assertNotIn("live-team-keep", strip_html)
        self.assertNotIn('class="live-round-pick is-active"', strip_html)
        self.assertLess(html.index('id="round-option-card"'), html.index('class="live-shell-body"'))
        self.assertGreater(html.index('id="class-list-pane"'), html.index('id="live-shell-left"'))
        self.assertLess(html.index('id="class-list-pane"'), html.index('id="live-active-content"'))
        left_html = html[html.index('id="live-shell-left"') : html.index('id="live-shell-right"')]
        self.assertNotIn('id="live-round-picks"', left_html)
        self.assertNotIn('id="live-round-set"', left_html)
        self.assertIn("function paintRoundStrip()", js)
        self.assertIn("function readRoundFlags()", js)
        self.assertIn("function readRoundTypeFromState()", js)
        paint = js.split("function paintRoundStrip()")[1].split("function ")[0]
        self.assertIn("picks.hidden = false", paint)
        self.assertNotIn("currentTeamCount()", paint)
        self.assertNotIn("picks.hidden = !team", js)
        self.assertIn('$("live-round-set")?.addEventListener("click"', js)
        set_click = js.split('$("live-round-set")?.addEventListener("click"')[1].split(
            '$("text-ride-freeze")'
        )[0]
        self.assertNotIn("currentTeamCount() <= 1", set_click)
        self.assertIn("round_flags: flags", set_click)
        self.assertIn("round: selected", set_click)
        self.assertIn("patchTeacherState({ round:", set_click)
        self.assertNotIn("cue_id", set_click)
        option = js.split("function paintOptionCard()")[1].split("function paintFrames()")[0]
        self.assertIn("paintRoundStrip();", option)
        self.assertIn("rounds.hidden = true", option)
        self.assertNotIn("innerHTML", option)
        self.assertIn("paintRoundStrip();", js.split("function paintTeamsStripEnabled()")[1].split("function paintRoundStrip()")[0])
        self.assertIn("body.staff-shell .live-round-strip {", css)
        strip_css = css.split("body.staff-shell .live-round-strip {")[1].split("}")[0]
        self.assertIn("flex-wrap: nowrap", strip_css)
        self.assertIn("max-height: var(--live-options-row-h)", strip_css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("body.staff-shell .live-round-picks[hidden] {", css)
        self.assertIn("body.staff-shell .live-round-strip #live-round-type", css)
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

    def test_beat21_next_stops_timer_and_applies_stage_preset(self) -> None:
        """Beat 21: Next stops a running timer; MEET/PLAY presets; else idle."""
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
        self.assertTrue(meet_game.get("round_ends_at_ms"))
        self.assertGreaterEqual(int(meet_game.get("round_remaining_sec") or 0), 170)
        self.assertLessEqual(int(meet_game.get("round_remaining_sec") or 0), 180)
        display_meet = self.school.live_session_display_time(self.class_id)
        self.assertTrue(display_meet["running"])
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
        self.assertTrue(play_game.get("round_ends_at_ms"))
        self.assertGreaterEqual(int(play_game.get("round_remaining_sec") or 0), 290)
        self.assertLessEqual(int(play_game.get("round_remaining_sec") or 0), 300)

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
        """Beat 24: Save and End Class keeps A&P; Quit keeps attendance only."""
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
        self.assertIsNone(self.school.get_live_session(sid))
        self.assertEqual(self.school.list_live_sessions_for_class(self.class_id), [])
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
        self.assertEqual(ended_after, ended_before + 1)
        after_quit = self._ended_score_row(student_id)
        self.assertIsNotNone(after_quit)
        self.assertEqual(int(after_quit["present"]), 1)
        self.assertEqual(int(after_quit["points"]), 0)

    def test_beat26_teams_to_meet_starts_scoreboard(self) -> None:
        """Beat 26: TEAMS→MEET opens the ESPN popup and meet_teams overlay."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        advance = js.split("async function advanceTeamsToMeet()")[1].split(
            '$("ap-teams-next")'
        )[0]
        self.assertIn("openScoreboardOverlay()", advance)
        self.assertIn("pendingScoreboard", advance)
        self.assertIn("ap-scoreboard-toggle", advance)
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
        self.assertIn(">Team<", html)
        self.assertIn("ap-att-course", js)
        self.assertIn("ap-att-game", js)
        self.assertIn("function studentTeamLabel(", js)
        self.assertIn("body.staff-shell #class-list-pane .ap-att-cols", css)
        self.assertIn('classListVisibleStudents', js)
        self.assertIn('=== "teams"', js)

    def test_beat31_participation_is_per_question(self) -> None:
        """Beat 31: +1 per real QH; Meet social taps do not count."""
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
        self.assertEqual(int(state["game_points"][str(student_id)]), 3)

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
        """Beat 28: students rate class after Quit or Save; staff sees Feedback."""
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=attendance"
        ).get_data(as_text=True)
        self.assertIn("view=feedback", html)
        self.assertIn(">Feedback<", html)
        sid, student_id = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid, student_id)
        quit = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(quit.status_code, 302)
        pending = self.school.pending_exit_feedback(
            class_id=self.class_id, student_id=student_id
        )
        self.assertIsNotNone(pending)
        ended = self.client.get("/api/student/state")
        self.assertEqual(ended.status_code, 200)
        body = ended.get_json()
        self.assertEqual(body.get("status"), "ended")
        self.assertTrue(body.get("feedback"))
        self.assertIn("/student/exit", body.get("redirect") or "")
        page = self.client.get("/student/exit")
        self.assertEqual(page.status_code, 200)
        exit_html = page.get_data(as_text=True)
        self.assertIn("How was class?", exit_html)
        self.assertIn("/static/mood/good.svg", exit_html)
        self.assertIn('name="comment"', exit_html)
        sent = self.client.post(
            "/student/exit",
            data={"mood": "ok", "comment": "Clear lesson"},
            follow_redirects=False,
        )
        self.assertEqual(sent.status_code, 302)
        self.assertIsNone(
            self.school.pending_exit_feedback(
                class_id=self.class_id, student_id=student_id
            )
        )
        rows = self.school.list_exit_feedback_for_class(self.class_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mood"], "ok")
        self.assertEqual(rows[0]["comment"], "Clear lesson")
        tab = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=feedback"
        )
        self.assertEqual(tab.status_code, 200)
        tab_html = tab.get_data(as_text=True)
        self.assertIn("How was class?", tab_html)
        self.assertIn("Clear lesson", tab_html)
        self.assertIn("/static/mood/ok.svg", tab_html)
        sid2, student_id2 = self._open_live_with_aspen_answers()
        self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            follow_redirects=False,
        )
        pending_save = self.school.pending_exit_feedback(
            class_id=self.class_id, student_id=student_id2
        )
        self.assertIsNotNone(pending_save)

    def test_beat30_module_and_live_class_dropdown(self) -> None:
        """Beat 30 interim: Module + Live class picks load the C-slot pack."""
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="live-pack-strip"', html)
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


if __name__ == "__main__":
    unittest.main()
