#!/usr/bin/env python3
"""MCK-182: What's new lines from PR bodies, written at deploy time.

Covers ``.github/scripts/whats_new.py`` (the generator and the PR check)
with a fake GitHub, plus the workflow and PR-template wiring. Fast: no app,
no network.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))

import whats_new as wn  # noqa: E402

GOOD = "In Run Live Class, you can reopen a closed whiteboard as your Last board or a Fresh board."
BOTH = "On students' phones, rank questions start shuffled."
SHA_NEW = "c" * 40
SHA_MID = "b" * 40
SHA_OLD = "a" * 40  # newest committed release (the baseline)
NOW = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)


def committed() -> dict:
    return {
        "schema": 2,
        "releases": [
            {
                "id": SHA_OLD[:7],
                "sha": SHA_OLD,
                "deployed_at": "2026-10-04T11:56:00-04:00",
                "day": "2026-10-04",
                "items": [{"text": GOOD, "audience": "Both", "refs": "#1"}],
            },
            {"id": "2026-10-02", "date": "2026-10-02", "items": [{"title": "T", "line": "L"}]},
        ],
    }


def pr(number: int, sha: str, body: str, merged_at: str = "2026-10-05T12:00:00Z", title: str = "") -> dict:
    return {
        "number": number,
        "title": title or f"MCK-{number}: change",
        "body": body,
        "merge_commit_sha": sha,
        "merged_at": merged_at,
    }


class FakeGitHub:
    """Answers the three endpoints the generator reads."""

    def __init__(self, runs: list[dict], commits: dict[tuple[str, str], list[str]], prs: list[dict]):
        self.runs, self.commits, self.prs = runs, commits, prs
        self.calls: list[str] = []

    def get(self, path: str):
        self.calls.append(path)
        if path.startswith("/actions/workflows/"):
            return {"workflow_runs": self.runs}
        if path.startswith("/pulls?"):
            return self.prs
        match = re.match(r"^/compare/(\w+)\.\.\.(\w+)$", path)
        if match:
            return {"commits": [{"sha": s} for s in self.commits.get(match.groups(), [])]}
        raise AssertionError(path)


class Broken:
    def get(self, path: str):
        raise urllib.error.URLError("api down")


def run(sha: str, at: str = "2026-10-04T15:56:00Z", rid: int = 1) -> dict:
    return {"id": rid, "head_sha": sha, "updated_at": at}


class ParseAndCheckTests(unittest.TestCase):
    def test_parse_lines_audience_none_and_comments(self) -> None:
        body = (
            "## What's new\n<!-- What's new: In Run Live Class, example only. -->\n"
            f"- What's new: **{GOOD}**\nWhat’s new (Both): {BOTH}\nWhat's new: none\nWhat's new:\n"
        )
        notes = wn.parse_notes(body)
        self.assertEqual([n["text"] for n in notes], [GOOD, BOTH, "none", ""])
        self.assertEqual([n["audience"] for n in notes], ["Teacher", "Both", "Teacher", "Teacher"])
        self.assertEqual([n["none"] for n in notes], [False, False, True, True])
        self.assertEqual([n["blank"] for n in notes], [False, False, False, True])

    def test_check_passes_good_and_none(self) -> None:
        self.assertEqual(wn.check_body(f"What's new: {GOOD}"), [])
        self.assertEqual(wn.check_body("What's new: none"), [])
        self.assertEqual(wn.check_body(f"What's new (Both): {BOTH}\nWhat's new: none"), [])

    def test_check_requires_a_line_and_rejects_blank(self) -> None:
        self.assertEqual(len(wn.check_body("## What changed\nStuff")), 1)
        template = (REPO_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        errors = wn.check_body(template)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("empty", errors[0])
        # The template's examples sit in a comment and never count.
        self.assertEqual(len(wn.parse_notes(template)), 1)

    def test_check_enforces_wonder_rules(self) -> None:
        cases = {
            "In Run Live Class, MCK-171 adds teams.": "ticket id",
            "In Run Live Class, #243 adds teams.": "PR number",
            "In Run Live Class, 65d69db adds teams.": "SHA",
            "Rank questions are now shuffled.": "where in the app",
            "In Run Live Class, the endpoint is faster.": "plain words",
            "In Run Live Class, <b>bold</b> works.": "plain text",
            "In Run Live Class " + "word " * 30: "30 or fewer",
        }
        for text, expect in cases.items():
            with self.subTest(text=text[:40]):
                errors = wn.check_body(f"What's new: {text}")
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(expect, errors[0])

    def test_wonder_backfill_lines_name_a_place(self) -> None:
        data = json.loads((REPO_ROOT / "lms/static/whats-new/releases.json").read_text(encoding="utf-8"))
        for release in data["releases"]:
            for item in release["items"]:
                if "text" in item:
                    problems = [p for p in wn.note_problems(item["text"]) if "words" not in p]
                    self.assertEqual(problems, [], item["text"])

    def test_run_check_exit_codes(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(wn.run_check(f"What's new: {GOOD}"), 0)
            self.assertEqual(wn.run_check("nothing here"), 1)


class BuildTests(unittest.TestCase):
    def build(self, gh, sha=SHA_NEW, data=None):
        warnings: list[str] = []
        out, generated = wn.build_releases(
            data or committed(), gh=gh, sha=sha, now=NOW, run_id="99", warn=warnings.append
        )
        return out, generated, warnings

    def test_notes_become_one_release_in_mobbin_shape(self) -> None:
        gh = FakeGitHub(
            [run(SHA_OLD)],
            {(SHA_OLD, SHA_NEW): ["m1", "m2", "x"]},
            [
                pr(243, "m2", f"What's new (Both): {BOTH}", "2026-10-05T12:10:00Z", "MCK-171: Team challenge"),
                pr(244, "m1", f"What's new: {GOOD}\nWhat's new: none", "2026-10-05T12:00:00Z"),
                pr(9, "elsewhere", f"What's new: {GOOD}"),
                {**pr(10, "x", f"What's new: {GOOD}"), "merged_at": None},
            ],
        )
        out, generated, warnings = self.build(gh)
        self.assertEqual(warnings, [])
        self.assertEqual(len(generated), 1)
        entry = generated[0]
        self.assertEqual(
            entry,
            {
                "id": "ccccccc",
                "sha": SHA_NEW,
                "deployed_at": "2026-10-05T09:30:00-04:00",
                "day": "2026-10-05",
                "items": [
                    {"text": GOOD, "audience": "Teacher", "refs": "#244 · MCK-244"},
                    {"text": BOTH, "audience": "Both", "refs": "#243 · MCK-171"},
                ],
            },
        )
        self.assertEqual(out["schema"], 2)
        self.assertEqual([r["id"] for r in out["releases"]], ["ccccccc", "aaaaaaa", "2026-10-02"])

    def test_none_only_deploy_writes_nothing(self) -> None:
        gh = FakeGitHub(
            [run(SHA_OLD)],
            {(SHA_OLD, SHA_NEW): ["m1"]},
            [pr(246, "m1", "What's new: none"), pr(247, "m9", "no notes at all")],
        )
        out, generated, _ = self.build(gh)
        self.assertEqual(generated, [])
        self.assertEqual(out["releases"], committed()["releases"])
        self.assertIn("No entry is written", wn.summary_markdown(generated, SHA_NEW))

    def test_bad_lines_are_dropped_with_a_warning(self) -> None:
        gh = FakeGitHub(
            [run(SHA_OLD)],
            {(SHA_OLD, SHA_NEW): ["m1"]},
            [pr(5, "m1", f"What's new: Fixed MCK-5 somewhere.\nWhat's new: {GOOD}")],
        )
        _, generated, warnings = self.build(gh)
        self.assertEqual([i["text"] for i in generated[0]["items"]], [GOOD])
        self.assertEqual(len(warnings), 1)
        self.assertIn("#5", warnings[0])

    def test_history_rebuilds_deploys_since_the_baseline(self) -> None:
        """Earlier generated entries survive later deploys (nothing is committed)."""
        gh = FakeGitHub(
            [
                run(SHA_NEW, "2026-10-05T13:00:00Z", 98),  # an earlier run of this same commit
                run(SHA_MID, "2026-10-05T01:00:00Z", 97),
                run(SHA_OLD, "2026-10-04T15:56:00Z", 96),
                run("d" * 40, "2026-10-03T22:00:00Z", 95),  # older than the baseline: never read
            ],
            {(SHA_MID, SHA_NEW): ["m2"], (SHA_OLD, SHA_MID): ["m1"]},
            [pr(2, "m2", "What's new: none"), pr(1, "m1", f"What's new: {GOOD}", "2026-10-05T00:50:00Z")],
        )
        out, generated, _ = self.build(gh)
        self.assertEqual([g["id"] for g in generated], ["bbbbbbb"])
        self.assertEqual(generated[0]["deployed_at"], "2026-10-04T21:00:00-04:00")
        self.assertEqual([r["id"] for r in out["releases"]], ["bbbbbbb", "aaaaaaa", "2026-10-02"])
        self.assertFalse(any("dddddd" in c for c in gh.calls), gh.calls)

    def test_committed_sha_is_never_regenerated(self) -> None:
        gh = FakeGitHub([run(SHA_OLD)], {}, [])
        out, generated, _ = self.build(gh, sha=SHA_OLD)
        self.assertEqual(generated, [])
        self.assertEqual(out["releases"], committed()["releases"])
        self.assertFalse(any(c.startswith("/compare") for c in gh.calls))


class RunBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "releases.json"
        self.src.write_text(json.dumps(committed(), indent=2) + "\n", encoding="utf-8")
        self.summary = Path(self.tmp.name) / "summary.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def args(self, **extra) -> argparse.Namespace:
        base = dict(releases=str(self.src), out=str(self.src), summary=str(self.summary), sha=SHA_NEW, workflow="deploy.yml")
        base.update(extra)
        return argparse.Namespace(**base)

    def test_api_failure_ships_the_committed_file(self) -> None:
        before = self.src.read_bytes()
        with redirect_stdout(io.StringIO()) as log:
            code = wn.run_build(self.args(), gh=Broken(), now=NOW)
        self.assertEqual(code, 0)
        self.assertEqual(self.src.read_bytes(), before)
        self.assertIn("::warning", log.getvalue())
        self.assertIn("shipping the committed releases.json", self.summary.read_text(encoding="utf-8"))

    def test_missing_token_never_blocks(self) -> None:
        before = self.src.read_bytes()
        with redirect_stdout(io.StringIO()):
            code = wn.main(["build", "--releases", str(self.src), "--out", str(self.src), "--summary", "", "--sha", SHA_NEW])
        self.assertEqual(code, 0)
        self.assertEqual(self.src.read_bytes(), before)

    def test_writes_the_file_and_the_summary(self) -> None:
        gh = FakeGitHub([run(SHA_OLD)], {(SHA_OLD, SHA_NEW): ["m1"]}, [pr(7, "m1", f"What's new (Both): {BOTH}")])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(wn.run_build(self.args(), gh=gh, now=NOW), 0)
        data = json.loads(self.src.read_text(encoding="utf-8"))
        self.assertEqual(data["releases"][0]["items"][0]["text"], BOTH)
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn(wn.SUMMARY_TITLE, summary)
        self.assertIn(f"{BOTH} (students see this too)", summary)


class WiringTests(unittest.TestCase):
    """The workflows and template call the script; guards are untouched."""

    def test_deploy_writes_notes_after_the_guards_and_before_flyctl(self) -> None:
        text = (REPO_ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
        deploy = text[text.index("  deploy:\n"):]
        order = [
            deploy.index("Require approval in this run attempt"),
            deploy.index("Refuse a stale main SHA"),
            deploy.index("Guard against dev login"),
            deploy.index("Write What's new for this deploy"),
            deploy.index("flyctl deploy --remote-only"),
        ]
        self.assertEqual(order, sorted(order))
        step = deploy[deploy.index("Write What's new"):deploy.index("setup-flyctl")]
        self.assertIn("continue-on-error: true", step)
        self.assertIn("whats_new.py build", step)
        self.assertNotIn("contents: write", text)
        self.assertEqual(text.count("environment:\n      name: production"), 1)
        test_job = text[text.index("  test:\n"):text.index("  approve:\n")]
        self.assertIn("Preview What's new", test_job)
        self.assertIn('--out "${RUNNER_TEMP}/releases.preview.json"', test_job)

    def test_pr_gate_runs_the_check(self) -> None:
        text = (REPO_ROOT / ".github/workflows/pr-linear-id.yml").read_text(encoding="utf-8")
        self.assertIn("whats_new.py check", text)
        self.assertIn("PR_BODY: ${{ github.event.pull_request.body }}", text)

    def test_template_has_the_section(self) -> None:
        text = (REPO_ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
        self.assertIn("## What's new\n", text)
        self.assertIn("What's new: none", text)


if __name__ == "__main__":
    unittest.main()
