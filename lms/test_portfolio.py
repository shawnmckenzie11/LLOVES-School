"""Portfolio cores, useful words, rubric suggestions, and look-for mapping."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from ap_round_profiles import (  # noqa: E402
    BUILTIN_CHALLENGE_ACTIONS,
    active_challenge_actions,
    active_open_actions,
    builtin_open_document,
    normalize_profiles_document,
)
from portfolio.lookfors import LOOKFOR_IDS, process_keys_for_lookfor, rubric_keys_for_lookfor
from portfolio.store import load_core_questions, load_exemplar, load_rubric
from portfolio.suggest import classify_communicate, classify_connect, classify_justify, classify_transfer
from portfolio.useful_words import useful_word_union


def _scope_local_dev_login() -> str | None:
    """Enable LOCAL_DEV_LOGIN for one test; return the prior env value."""
    previous = os.environ.get("LOCAL_DEV_LOGIN")
    os.environ["LOCAL_DEV_LOGIN"] = "1"
    return previous


def _restore_local_dev_login(previous: str | None) -> None:
    """Restore LOCAL_DEV_LOGIN after a scoped portfolio test."""
    if previous is None:
        os.environ.pop("LOCAL_DEV_LOGIN", None)
    else:
        os.environ["LOCAL_DEV_LOGIN"] = previous


class PortfolioStoreTests(unittest.TestCase):
    """Locked JSON assets."""

    def test_core_questions_match_prototype(self) -> None:
        """Course stems stay the prototype wording."""
        cores = load_core_questions()
        self.assertIn("two different representations", cores["connect"]["course_question"])
        self.assertIn("mathematical claim", cores["justify"]["course_question"])
        self.assertIn("thinking change", cores["transfer"]["course_question"])

    def test_m1_exemplars_exist(self) -> None:
        """MCR3U and MCF3M Module 1 exemplars are locked files."""
        mcr = load_exemplar("MCR3U", 1)
        mcf = load_exemplar("MCF3M", 1)
        assert mcr is not None and mcf is not None
        self.assertTrue(mcr.get("locked"))
        self.assertEqual(mcr["hero_subtitle"], "Module 1 · Functions through symmetry")
        self.assertEqual(mcf["hero_subtitle"], "Module 1 · Functions & quadratic models")

    def test_rubric_has_four_criteria(self) -> None:
        """Rubric JSON transcribes the four Ontario criteria."""
        rubric = load_rubric()
        for key in ("connect", "justify", "transfer", "communicate"):
            self.assertIn(key, rubric["criteria"])
            self.assertEqual(set(rubric["criteria"][key]["levels"]), {"L1", "L2", "L3", "L4"})


class UsefulWordsTests(unittest.TestCase):
    """Union does not invent glossary terms."""

    def test_invented_term_excluded(self) -> None:
        """A glossary-like word that is not in the statements is dropped."""
        words = useful_word_union(
            course_code="MCF3M",
            expectation_codes=["A2.1"],
            statements=["explain the meaning of the term function using graphs"],
            data_dir=Path("/tmp/does-not-exist-lloves-glossary"),
        )
        self.assertIn("graphs", words)
        self.assertNotIn("saddle manifold", words)
        self.assertNotIn("made-up-lexicon", words)


class RubricClassifierTests(unittest.TestCase):
    """First-pass L1–L4 from the four classifier bullets."""

    def test_connect_levels(self) -> None:
        """Connect bullets map to L1–L4 fixtures."""
        self.assertEqual(classify_connect("I drew a graph."), "L1")
        self.assertEqual(classify_connect("The graph and the table both list values."), "L2")
        self.assertEqual(
            classify_connect(
                "The graph and the table connect: corresponding x-intercepts show the same roots."
            ),
            "L3",
        )
        self.assertEqual(
            classify_connect(
                "I compare the graph and the equation and select the graph because the connection gives insight."
            ),
            "L4",
        )

    def test_justify_transfer_communicate(self) -> None:
        """Remaining criteria follow the Downloads bullets."""
        self.assertEqual(classify_justify("The roots are 2 and 4."), "L1")
        self.assertEqual(classify_justify("The claim is the product is 8, for example 2×4."), "L2")
        self.assertEqual(
            classify_justify("The claim is true because the relationship m² − d² factors."),
            "L3",
        )
        self.assertEqual(classify_transfer("We did the group problem."), "L1")
        self.assertEqual(classify_transfer("I changed strategy to factoring."), "L2")
        self.assertEqual(
            classify_communicate("ok"),
            "L1",
        )


class ChallengeProfileTests(unittest.TestCase):
    """Team Challenge builtin + look-for mapping."""

    def test_builtin_challenge_lookfors(self) -> None:
        """Builtin challenge profile has the seven look-fors."""
        doc = builtin_open_document()
        actions = active_challenge_actions(doc)
        self.assertEqual([a["id"] for a in actions], list(LOOKFOR_IDS))
        self.assertEqual(len(BUILTIN_CHALLENGE_ACTIONS), 7)
        self.assertEqual(rubric_keys_for_lookfor("checked_revised"), ["transfer", "justify"])
        self.assertIn("reasoning_proving", process_keys_for_lookfor("justified"))

    def test_normalize_adds_challenge_to_legacy_open_only(self) -> None:
        """Existing offering JSON with only ``open`` still gets challenge."""
        doc = normalize_profiles_document(
            {
                "open": {
                    "active_id": "default",
                    "profiles": [
                        {
                            "id": "default",
                            "name": "Default",
                            "actions": [{"id": "a", "label": "Ask", "amount": 2}],
                        }
                    ],
                }
            }
        )
        self.assertEqual(active_open_actions(doc)[0]["label"], "Ask")
        self.assertEqual(doc["challenge"]["active_id"], "team_challenge")


class IndividualChallengeRoundTests(unittest.TestCase):
    """Individual tracking allows Team Challenge but not Formative/Break."""

    def setUp(self) -> None:
        """Isolated Flask app with a staff-owned class."""
        self._prev_local_dev_login = _scope_local_dev_login()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        from app import create_app

        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("pf-teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=pf-teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("pf-teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )

    def tearDown(self) -> None:
        """Close temp DB and restore LOCAL_DEV_LOGIN."""
        self.school.close()
        self.tmp.cleanup()
        _restore_local_dev_login(self._prev_local_dev_login)

    def test_individual_allows_challenge_rejects_formative(self) -> None:
        """Challenge starts; Formative still 400."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        ids = [int(s["id"]) for s in begin.get_json()["students"]]
        self.client.post(
            f"/api/classes/{class_id}/game/ungamified",
            json={
                "present_ids": ids,
                "meeting_date": "2026-09-09",
                "go_live": False,
            },
        )
        live = self.client.post(
            f"/api/classes/{class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 10}]},
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True))
        self.assertEqual(live.get_json()["game"]["round_kind"], "challenge")
        bad = self.client.post(
            f"/api/classes/{class_id}/game/append-round",
            json={"kind": "formative", "minutes": 8},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("Team Challenge", bad.get_json().get("error") or "")

    def test_lookfor_tally_survives_end_game(self) -> None:
        """Challenge chips write look-for observations that Marking can tally."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        ids = [int(s["id"]) for s in begin.get_json()["students"]]
        self.client.post(
            f"/api/classes/{class_id}/game/ungamified",
            json={
                "present_ids": ids,
                "meeting_date": "2026-09-09",
                "go_live": False,
            },
        )
        live = self.client.post(
            f"/api/classes/{class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 10}]},
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True))
        self.school.start_live_class_session(class_id, int(self.teacher["id"]))
        scored = self.client.post(
            f"/api/classes/{class_id}/game/score",
            json={
                "kind": "student",
                "id": ids[0],
                "amount": 2,
                "label": "noticed/generalized",
                "lookfor_id": "noticed_generalized",
            },
        )
        self.assertEqual(scored.status_code, 200, scored.get_data(as_text=True))
        ended = self.client.post(f"/api/classes/{class_id}/game/end", json={})
        self.assertEqual(ended.status_code, 200, ended.get_data(as_text=True))
        tallies = self.school.lookfor_tallies_for_module(class_id, 1, n_modules=8)
        self.assertEqual(tallies.get(str(ids[0]), {}).get("noticed_generalized"), 1)
        sheet = self.client.get(
            f"/api/classes/{class_id}/portfolio/marking?module=1"
        )
        self.assertEqual(sheet.status_code, 200, sheet.get_data(as_text=True))
        body = sheet.get_json()
        row = next(r for r in body["students"] if int(r["id"]) == ids[0])
        self.assertEqual(row["lookfors"]["noticed_generalized"], 1)
        self.assertFalse(row.get("suggested"))


class PortfolioTabGateTests(unittest.TestCase):
    """Portfolio tab is hidden in production."""

    def setUp(self) -> None:
        """Scope LOCAL_DEV_LOGIN so unittest discover does not leak it."""
        self._prev_local_dev_login = _scope_local_dev_login()

    def tearDown(self) -> None:
        """Restore LOCAL_DEV_LOGIN after each tab-gate test."""
        _restore_local_dev_login(self._prev_local_dev_login)

    def test_production_disables_tab(self) -> None:
        """FLASK_ENV=production turns the gate off."""
        from portfolio.flags import portfolio_tab_enabled

        with patch.dict(os.environ, {"FLASK_ENV": "production", "LOCAL_DEV_LOGIN": "1"}):
            self.assertFalse(portfolio_tab_enabled())

    def test_local_course_shows_portfolio_tab(self) -> None:
        """TESTING app exposes the Portfolio tab."""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        from app import create_app

        app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        school = app.config["SCHOOL_DB"]
        client = app.test_client()
        try:
            school.activate_from_semester_json()
            teacher = school.register_staff("tab-teacher@gmail.com")
            offering = school.assign_course(
                teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
            )
            client.get("/auth/google?portal=staff")
            client.get("/auth/google/callback?email=tab-teacher@gmail.com&name=T")
            client.post(
                "/verify-email",
                data={"code": school.get_user_by_email("tab-teacher@gmail.com")["verification_code"]},
            )
            created = client.post(
                "/api/staff/classes",
                json={
                    "offering_id": offering["id"],
                    "days": "M/W/F",
                    "time": "2:00pm",
                    "codenames": ["Aspen"],
                },
            )
            class_id = created.get_json()["class"]["id"]
            page = client.get(f"/staff/class/{class_id}?tab=portfolio")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn(">Portfolio</a>", html)
            self.assertIn("id=\"portfolio-root\"", html)
            self.assertIn(">Build</a>", html)
            self.assertIn(">Marking</a>", html)
            self.assertIn(">Build</h2>", html)
            self.assertNotIn(">Marking</h2>", html)
            marking = client.get(f"/staff/class/{class_id}?tab=portfolio&view=marking")
            self.assertEqual(marking.status_code, 200)
            mark_html = marking.get_data(as_text=True)
            self.assertIn(">Marking</h2>", mark_html)
            self.assertIn("id=\"portfolio-mark-run\"", mark_html)
            self.assertIn("Generate Google Doc", client.get(
                f"/staff/class/{class_id}?tab=portfolio&view=build"
            ).get_data(as_text=True))
            ctx = client.get(f"/api/classes/{class_id}/portfolio/context?module=1")
            self.assertEqual(ctx.status_code, 200, ctx.get_data(as_text=True))
            body = ctx.get_json()
            self.assertEqual(body["course_code"], "MCF3M")
            self.assertEqual(body["lens_source"], "exemplar")
        finally:
            school.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
