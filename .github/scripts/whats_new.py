#!/usr/bin/env python3
"""What's new notes from PR bodies (MCK-182). Standard library only.

Two commands:

``check``
    PR gate, run from ``pr-linear-id.yml``. The PR body (env ``PR_BODY``)
    needs at least one ``What's new:`` line. ``What's new: none`` marks
    housekeeping. Every other line follows Wonder's rules: 30 words or fewer,
    no ticket ids, PR numbers or SHAs, plain text, and it names a place in
    the app (:data:`PLACES`).

``build``
    Deploy step, run from ``deploy.yml``. It rebuilds the deployed
    ``releases.json`` from the committed file plus one entry per successful
    Deploy run since the newest committed release, including the commit being
    deployed now. Each entry collects the ``What's new:`` lines of the PRs
    merged in that deploy::

        {"id": "<sha7>", "sha": "<sha>", "deployed_at": "<ISO, Toronto>",
         "day": "YYYY-MM-DD", "items": [{"text", "audience", "refs"}]}

    A deploy whose PRs only say ``none`` (or nothing) writes no entry. Any
    GitHub API failure keeps the committed file as it is, and the command
    always exits 0, so it can never block a deploy. The notes are also
    written to the run summary for the Production approval.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_WORDS = 30
LOOKBACK_RUNS = 30
NOTE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?What[’']s new\s*(?:\(\s*(?P<aud>both|teacher)\s*\))?\s*:\s*(?P<text>.*?)\s*$",
    re.IGNORECASE,
)
NONE_RE = re.compile(r"^(?:none|n/a|no)\.?$", re.IGNORECASE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
ID_RES = (
    (re.compile(r"\bMCK-\d+\b", re.IGNORECASE), "a ticket id"),
    (re.compile(r"(?<![\w&])#\d+\b"), "a PR number"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"), "a SHA"),
)
JARGON = ("endpoint", "snapshot", "migration", "flag")
# Real labels at 65d69db plus plain place words (Wonder rule 2). Wonder can
# add a place here when a new screen ships.
PLACES = (
    "run live class",
    "live class",
    "set class",
    "import from bank",
    "add new",
    "question bank",
    "attendance & participation",
    "attendance and participation",
    "take attendance",
    "end live class",
    "dashboard",
    "what's new",
    "whiteboard",
    "projector",
    "phone",
    "students' screens",
    "student screens",
    "student sign-in",
    "sign-in page",
    "class list",
    "edit roster",
    "populate class",
    "grades",
    "gradebook",
    "lesson slides",
    "celebrations",
    "admin",
)
SUMMARY_TITLE = "## What's new for this deploy"


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------
def parse_notes(body: str | None) -> list[dict[str, Any]]:
    """Return every ``What's new:`` line in a PR body, comments removed.

    Args:
        body: PR body markdown.

    Returns:
        ``[{text, audience, none, blank}]`` in body order. ``none`` is True
        for a blank, ``none`` or ``n/a`` line; ``blank`` only for an empty
        one (the PR gate asks for ``none`` instead). ``**bold**`` is dropped.
    """
    notes: list[dict[str, Any]] = []
    for raw in COMMENT_RE.sub("", body or "").splitlines():
        match = NOTE_RE.match(raw)
        if not match:
            continue
        text = match.group("text").replace("**", "").replace("`", "").strip()
        text = re.sub(r"\s+", " ", text)
        audience = "Both" if (match.group("aud") or "").lower() == "both" else "Teacher"
        notes.append(
            {
                "text": text,
                "audience": audience,
                "none": not text or bool(NONE_RE.match(text)),
                "blank": not text,
            }
        )
    return notes


def note_problems(text: str) -> list[str]:
    """Wonder's rules for one teacher-facing line. Empty list = fine.

    Args:
        text: The note text (already stripped of markdown).

    Returns:
        Plain-English problems.
    """
    problems: list[str] = []
    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"is {words} words; keep it to {MAX_WORDS} or fewer")
    for pattern, label in ID_RES:
        if pattern.search(text):
            problems.append(f"has {label}; teachers never see ids")
    if re.search(r"[<>]", text):
        problems.append("has < or >; plain text only")
    low = text.lower().replace("’", "'")
    jargon = [w for w in JARGON if re.search(rf"\b{w}s?\b", low)]
    if jargon:
        problems.append(f"uses {', '.join(jargon)}; use plain words")
    if not any(place in low for place in PLACES):
        problems.append(
            "doesn't say where in the app it lives (e.g. Run Live Class, Import from bank, "
            "Attendance & Participation, Dashboard, whiteboard, students' phones)"
        )
    return problems


def check_body(body: str | None) -> list[str]:
    """PR gate: errors for a PR body, or an empty list when it passes.

    Args:
        body: PR body markdown.

    Returns:
        Error lines for the job log.
    """
    notes = parse_notes(body)
    if not notes:
        return [
            "Add a \"What's new:\" line under ## What's new: one line per change a teacher "
            "can see, saying where it is in the app, or \"What's new: none\" for housekeeping."
        ]
    errors: list[str] = []
    for note in notes:
        if note["blank"]:
            errors.append(
                "A \"What's new:\" line is empty. Say what changed and where, or write \"none\"."
            )
            continue
        if note["none"]:
            continue
        for problem in note_problems(note["text"]):
            errors.append(f"\"What's new: {note['text']}\" {problem}.")
    return errors


def refs_for(pr: dict[str, Any]) -> str:
    """``#243 · MCK-171`` from a PR's number, title and body (data only)."""
    ids: list[str] = []
    for source in (pr.get("title") or "", pr.get("body") or ""):
        for found in re.findall(r"\bMCK-\d+\b", source, flags=re.IGNORECASE):
            key = found.upper()
            if key not in ids:
                ids.append(key)
    head = f"#{pr['number']}"
    return f"{head} · {', '.join(ids[:3])}" if ids else head


def items_from_prs(prs: list[dict[str, Any]], warn: Callable[[str], None]) -> list[dict[str, Any]]:
    """Teacher-facing items for one deploy, in merge order.

    Args:
        prs: PR dicts with ``number``, ``title``, ``body``.
        warn: Called with a message for every dropped line.

    Returns:
        ``[{text, audience, refs}]``. ``none`` lines and lines that break the
        rules are left out.
    """
    items: list[dict[str, Any]] = []
    for pr in prs:
        for note in parse_notes(pr.get("body")):
            if note["none"]:
                continue
            problems = note_problems(note["text"])
            if problems:
                warn(f"#{pr['number']}: dropped \"{note['text']}\" ({'; '.join(problems)})")
                continue
            items.append({"text": note["text"], "audience": note["audience"], "refs": refs_for(pr)})
    return items


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------
def toronto(moment: datetime) -> datetime:
    """``moment`` in America/Toronto (UTC if tzdata is missing)."""
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo("America/Toronto"))
    except Exception:  # noqa: BLE001 - no tzdata: UTC still sorts right
        return moment.astimezone(timezone.utc)


def parse_iso(value: str) -> datetime:
    """Parse a GitHub ``...Z`` timestamp."""
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def release_entry(sha: str, moment: datetime, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Schema 2 release for one deploy."""
    local = toronto(moment).replace(microsecond=0)
    return {
        "id": sha[:7],
        "sha": sha,
        "deployed_at": local.isoformat(),
        "day": local.date().isoformat(),
        "items": items,
    }


def sort_key(release: dict[str, Any]) -> str:
    """Newest-first key: ``deployed_at``, or a legacy date read as 23:59 UTC-4."""
    stamp = release.get("deployed_at")
    if stamp:
        try:
            return parse_iso(stamp).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    day = str(release.get("day") or release.get("date") or release.get("id") or "")[:10]
    try:
        legacy = datetime.fromisoformat(f"{day}T23:59:00-04:00")
    except ValueError:
        return ""
    return legacy.astimezone(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------
class GitHub:
    """Tiny read-only REST client (``GITHUB_TOKEN``)."""

    def __init__(self, repo: str, token: str, api: str = "https://api.github.com") -> None:
        self.repo = repo
        self.token = token
        self.api = api.rstrip("/")

    def get(self, path: str) -> Any:
        """GET ``/repos/<repo><path>`` and return parsed JSON."""
        request = urllib.request.Request(
            f"{self.api}/repos/{self.repo}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "lloves-whats-new",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as resp:  # noqa: S310 - fixed host
            return json.loads(resp.read().decode("utf-8"))


class GhCli:
    """Read-only client over ``gh api`` for local dry runs (``--gh-cli``)."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def get(self, path: str) -> Any:
        """``gh api repos/<repo><path>``."""
        done = subprocess.run(
            ["gh", "api", f"repos/{self.repo}{path}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if done.returncode != 0:
            raise RuntimeError(f"gh api failed: {done.stderr.strip()[:200]}")
        return json.loads(done.stdout)


class WithExtraNotes:
    """Dry-run aid: append ``What's new:`` lines to chosen PR bodies."""

    def __init__(self, inner: Any, extra: dict[str, str]) -> None:
        self.inner = inner
        self.extra = {str(k).lstrip("#"): v for k, v in extra.items()}

    def get(self, path: str) -> Any:
        data = self.inner.get(path)
        if path.startswith("/pulls?"):
            for pr in data:
                add = self.extra.get(str(pr.get("number")))
                if add:
                    pr["body"] = f"{pr.get('body') or ''}\n{add}"
        return data


def deployed_runs(gh: Any, workflow: str, exclude_run_id: str = "") -> list[dict[str, Any]]:
    """Successful main runs of the Deploy workflow, newest first.

    A run only succeeds when its Deploy to Fly job did, so each one is a
    real Fly release. Cancelled or rejected runs are left out; their PRs
    roll into the next successful deploy.
    """
    data = gh.get(
        f"/actions/workflows/{workflow}/runs?branch=main&status=success&per_page={LOOKBACK_RUNS}"
    )
    runs = []
    for run in data.get("workflow_runs") or []:
        if str(run.get("id")) == str(exclude_run_id):
            continue
        runs.append({"sha": run["head_sha"], "at": run.get("updated_at") or run.get("created_at")})
    return runs


def prs_between(gh: Any, base: str, head: str, merged_prs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PRs whose merge commit landed in ``base..head``, in merge order."""
    if base == head:
        return []
    compare = gh.get(f"/compare/{base}...{head}")
    shas = {c["sha"] for c in compare.get("commits") or []}
    hits = [pr for pr in merged_prs if pr.get("merge_commit_sha") in shas]
    return sorted(hits, key=lambda pr: pr.get("merged_at") or "")


def build_releases(
    committed: dict[str, Any],
    *,
    gh: Any,
    sha: str,
    now: datetime,
    workflow: str = "deploy.yml",
    run_id: str = "",
    warn: Callable[[str], None] = lambda _m: None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Committed releases plus generated ones since the newest committed sha.

    Args:
        committed: Parsed committed ``releases.json``.
        gh: Client with ``get(path)``.
        sha: Commit being deployed now.
        now: Deploy time (``deployed_at`` of the current release).
        workflow: Deploy workflow file name.
        run_id: This run's id, left out of the history.
        warn: Called for every dropped note.

    Returns:
        ``(data, generated)``. ``generated`` holds the new entries, newest
        first. ``data`` is the full file to ship.
    """
    releases = [r for r in committed.get("releases") or [] if isinstance(r, dict)]
    known = {r.get("sha") for r in releases if r.get("sha")}
    baseline = next((r["sha"] for r in releases if r.get("sha")), None)

    history = deployed_runs(gh, workflow, exclude_run_id=run_id)
    chain: list[dict[str, Any]] = [{"sha": sha, "moment": now}]
    for run in history:
        if run["sha"] == chain[-1]["sha"]:
            continue  # a redeploy of the same commit
        chain.append({"sha": run["sha"], "moment": parse_iso(run["at"])})
        if baseline and (run["sha"] == baseline or run["sha"].startswith(baseline)):
            break
    if len(chain) < 2:
        return {**committed, "schema": 2, "releases": releases}, []

    merged = [
        pr
        for pr in gh.get("/pulls?state=closed&sort=updated&direction=desc&per_page=100")
        if pr.get("merged_at")
    ]
    generated: list[dict[str, Any]] = []
    for newer, older in zip(chain, chain[1:]):
        if newer["sha"] in known or any(k and newer["sha"].startswith(k) for k in known):
            continue
        items = items_from_prs(prs_between(gh, older["sha"], newer["sha"], merged), warn)
        if items:
            generated.append(release_entry(newer["sha"], newer["moment"], items))
    combined = sorted(generated + releases, key=sort_key, reverse=True)
    return {**committed, "schema": 2, "releases": combined}, generated


def summary_markdown(generated: list[dict[str, Any]], sha: str, note: str = "") -> str:
    """Run-summary text for the Production approval."""
    lines = [SUMMARY_TITLE, ""]
    current = next((r for r in generated if r["sha"] == sha), None)
    if current:
        for item in current["items"]:
            chip = " (students see this too)" if item["audience"] == "Both" else ""
            lines.append(f"- {item['text']}{chip} — {item['refs']}")
    else:
        lines.append("Nothing teachers will see (housekeeping). No entry is written.")
    older = [r for r in generated if r["sha"] != sha]
    if older:
        lines += ["", f"Also rebuilt {len(older)} earlier deploy entr{'y' if len(older) == 1 else 'ies'}."]
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def run_build(args: argparse.Namespace, gh: Any | None = None, now: datetime | None = None) -> int:
    """``build`` command. Always returns 0."""
    src = Path(args.releases)
    committed = json.loads(src.read_text(encoding="utf-8"))
    sha = args.sha or os.getenv("GITHUB_SHA", "")
    note = ""
    data, generated = committed, []
    try:
        if not sha:
            raise RuntimeError("no commit sha")
        if gh is None and getattr(args, "gh_cli", False):
            gh = GhCli(getattr(args, "repo", "") or os.getenv("GITHUB_REPOSITORY", ""))
        if gh is None:
            token = os.getenv("GITHUB_TOKEN", "")
            repo = os.getenv("GITHUB_REPOSITORY", "")
            if not token or not repo:
                raise RuntimeError("GITHUB_TOKEN / GITHUB_REPOSITORY missing")
            gh = GitHub(repo, token, os.getenv("GITHUB_API_URL", "https://api.github.com"))
        if getattr(args, "extra_notes", ""):
            gh = WithExtraNotes(gh, json.loads(Path(args.extra_notes).read_text(encoding="utf-8")))
        data, generated = build_releases(
            committed,
            gh=gh,
            sha=sha,
            now=now or datetime.now(timezone.utc),
            workflow=args.workflow,
            run_id=os.getenv("GITHUB_RUN_ID", ""),
            warn=lambda m: print(f"::warning title=What's new::{m}"),
        )
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        note = f"GitHub lookup failed ({type(exc).__name__}); shipping the committed releases.json."
        print(f"::warning title=What's new::{note}")
        data, generated = committed, []
    out = Path(args.out)
    if data is not committed or out.resolve() != src.resolve():
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = summary_markdown(generated, sha, note)
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0


def run_check(body: str | None) -> int:
    """``check`` command: 0 when the PR body passes, else 1."""
    errors = check_body(body)
    for error in errors:
        print(f"::error title=What's new line::{error}")
    if not errors:
        print("What's new line(s) OK.")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check PR_BODY")
    build = sub.add_parser("build", help="write releases.json for this deploy")
    build.add_argument("--releases", default="lms/static/whats-new/releases.json")
    build.add_argument("--out", default="lms/static/whats-new/releases.json")
    build.add_argument("--summary", default=os.getenv("GITHUB_STEP_SUMMARY", ""))
    build.add_argument("--sha", default="")
    build.add_argument("--workflow", default="deploy.yml")
    build.add_argument("--gh-cli", action="store_true", help="dry run: read GitHub through `gh api`")
    build.add_argument("--repo", default="", help="owner/name for --gh-cli")
    build.add_argument("--extra-notes", default="", help='dry run: JSON {"243": "What\'s new: ..."}')
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return run_check(os.getenv("PR_BODY", ""))
    try:
        return run_build(args)
    except Exception as exc:  # noqa: BLE001 - never block a deploy
        print(f"::warning title=What's new::build skipped ({type(exc).__name__}); committed file ships.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
