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
        self.assertIn('id="join-options-hint"', html)
        self.assertIn('id="teams-option-card"', html)
        self.assertIn('id="meet-option-card"', html)
        self.assertIn('id="round-option-card"', html)
        self.assertIn('id="play-option-card"', html)
        self.assertIn('id="live-unlock-media"', html)
        self.assertIn('id="live-unlock-canvas"', html)
        self.assertIn("Minds on", html)
        self.assertIn("Consolidation", html)
        self.assertIn("Keep teams", html)
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
            "meet-chain-chrome",
            "meet-chain-next",
            "meet-chain-skip-c",
            "meet-chain-end",
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
        """Header End Class posts to the dashboard wipe route; semantics stay #49."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn("All session data will be lost", html)
        self.assertIn(f"/staff/class/{self.class_id}/end-live", html)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('aria-label="End Live Class"', html)
        self.assertIn("live-end-class-form", html)
        self.assertNotIn('class="live-header-end danger live-legacy-control"', html)
        self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        self.assertIn("End Live Class", home_html)
        self.assertIn("All session data will be lost", home_html)
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
        self.assertIn("grid-template-columns: 1.25rem minmax(6rem, 1fr) auto", css)

    def test_beat1_geometry_locks_three_rows_and_stretch(self) -> None:
        """Beat 1: header / height-capped OptionsStrip / Left|Right stretch."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("--live-options-max-h: calc(var(--live-options-row-h) * 2 + 1.1rem)", css)
        self.assertIn("max-height: var(--live-options-max-h)", css)
        self.assertIn("grid-template-rows: auto auto auto minmax(12rem, 1fr)", css)
        self.assertIn("body.staff-shell .live-shell-ia-v2 > .live-header {\n  grid-row: 1;", css)
        self.assertIn(
            "body.staff-shell .live-shell-ia-v2 > .live-options-strip {\n  grid-row: 2;",
            css,
        )
        self.assertIn("body.staff-shell .live-shell-ia-v2 > .live-shell-body {\n  grid-row: 4;", css)
        self.assertIn("body.staff-shell .live-shell-body {\n  display: grid;", css)
        self.assertIn("align-items: stretch", css)
        self.assertIn("body.staff-shell .live-active-content {\n  flex: 1 1 auto;\n  min-height: 100%;", css)
        self.assertIn("body.staff-shell #class-list-pane {\n  flex: 1 1 auto;\n  min-height: 100%;", css)
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
        self.assertEqual(html.count('id="live-shell-left"'), 1)

    def test_beat1_stage_swaps_keep_class_list_mounted(self) -> None:
        """Prev/Next and every stage only swap OptionsStrip; Left chrome stays."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function lockClassListPane()", js)
        self.assertIn('for (const id of ["live-shell-left", "class-list-pane"', js)
        self.assertIn("lockClassListPane();", js)
        self.assertIn("Same hidden-only swap for JOIN, TEAMS, MEET, ROUND, PLAY, and Prev", js)
        self.assertIn('if (hint) hint.hidden = stage !== "join";', js)
        self.assertIn('if (teams) teams.hidden = stage !== "teams";', js)
        self.assertIn('if (meet) meet.hidden = stage !== "meet";', js)
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
        self.assertIn("reveal_to_students: false", reveal)
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
        self.assertIn("grid-template-columns: 1.25rem minmax(6rem, 1fr) auto", css)
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
        self.assertIn("return { min: 1, max: Math.max(2, present) }", js)
        self.assertIn('selectAssignMode(lastAssignMode || "balanced")', js)
        self.assertIn("has-team-color", js)
        self.assertNotIn('id="ap-gamify-no"', js)
        self.assertNotIn("$(\"ap-gamify-yes\")", js)
        self.assertNotIn("Team assign unlocks when Tracking is Team", js)
        self.assertIn("function lockClassListPane()", js)
        self.assertIn("lockClassListPane();", js)


if __name__ == "__main__":
    unittest.main()
