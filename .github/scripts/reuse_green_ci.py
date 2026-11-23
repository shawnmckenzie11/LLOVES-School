#!/usr/bin/env python3
"""Skip deploy test shards when this SHA already has a full green ci.yml run.

A full run is one where every shard, the LMS test suite aggregate, the
fan-out guard, the 30-seat load budget, and the slide-forward load finished
with conclusion ``success``. A skipped job is not success. A copy-only pull
request skips those load jobs, so it cannot satisfy this check.

The script never prints tokens. HTTP failures mean the shards still run.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REQUIRED_JOBS = (
    "LMS tests shard 1 of 4",
    "LMS tests shard 2 of 4",
    "LMS tests shard 3 of 4",
    "LMS tests shard 4 of 4",
    "LMS test suite",
    "fan-out guard",
    "30-seat load budget",
    "slide forward load",
)


def full_green(jobs: list[dict]) -> tuple[bool, str]:
    """Return whether ``jobs`` is a complete successful CI run.

    Args:
        jobs: GitHub Actions job objects with ``name`` and ``conclusion``.

    Returns:
        ``(True, reason)`` only when every required job concluded ``success``.
        A missing or skipped job is named in the reason.
    """
    by_name: dict[str, list[str]] = {}
    for job in jobs:
        name = str(job.get("name") or "")
        conclusion = str(job.get("conclusion") or "")
        by_name.setdefault(name, []).append(conclusion)
    gaps: list[str] = []
    for name in REQUIRED_JOBS:
        conclusions = by_name.get(name) or []
        if not conclusions:
            gaps.append(f"{name} missing")
        elif any(item != "success" for item in conclusions):
            shown = ",".join(conclusions)
            gaps.append(f"{name} {shown}")
    if gaps:
        return False, "; ".join(gaps)
    return True, "all shards, fan-out guard, 30-seat load budget, and slide forward load succeeded"


def choose_reuse(runs: list[dict]) -> tuple[bool, str]:
    """Pick a full green run from CI runs of one SHA.

    Each run dict has ``conclusion``, ``status``, ``html_url`` or ``id``, and
    ``jobs``. In-progress and unsuccessful workflow runs are ignored. The
    newest full green run wins. A newer failed or skipped run does not erase
    an older full green run of the same SHA.

    Args:
        runs: Newest first.

    Returns:
        Whether deploy may skip the test shards, and a one-line reason.
    """
    saw: list[str] = []
    for run in runs:
        if str(run.get("status") or "") not in ("completed", ""):
            continue
        if str(run.get("conclusion") or "") != "success":
            continue
        ok, detail = full_green(list(run.get("jobs") or []))
        label = str(run.get("id") or run.get("html_url") or "run")
        if ok:
            return True, f"ci run {label} is green ({detail})"
        saw.append(f"ci run {label}: {detail}")
    if not saw:
        return False, "no successful ci.yml run for this SHA"
    return False, saw[0]


def _emit(skip: bool, reason: str) -> None:
    """Print the decision and record ``skip`` for the workflow.

    Args:
        skip: Whether the test shards may be skipped.
        reason: One line with no secrets.
    """
    decision = "skip tests" if skip else "run tests"
    print(f"reuse: {decision}; {reason}", flush=True)
    dest = os.environ.get("GITHUB_OUTPUT", "")
    if not dest:
        return
    with open(dest, "a", encoding="utf-8") as handle:
        handle.write(f"skip={'true' if skip else 'false'}\n")


def _api_json(url: str, token: str) -> dict:
    """GET one GitHub API URL and return the JSON object.

    Args:
        url: Absolute API URL.
        token: Installation or workflow token. It is not logged.

    Returns:
        Parsed JSON object.

    Raises:
        urllib.error.URLError: The request failed.
        ValueError: The body was not an object.
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "lloves-reuse-green-ci",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("GitHub API returned a non-object")
    return body


def fetch_ci_runs(repo: str, sha: str, token: str) -> list[dict]:
    """Load completed ci.yml runs for ``sha``, newest first, with their jobs.

    Args:
        repo: ``owner/name``.
        sha: Full commit SHA.
        token: Workflow token.

    Returns:
        Run objects that include a ``jobs`` list.
    """
    runs: list[dict] = []
    for page in range(1, 4):
        url = (
            f"https://api.github.com/repos/{repo}/actions/workflows/ci.yml/runs"
            f"?head_sha={sha}&per_page=20&page={page}"
        )
        payload = _api_json(url, token)
        batch = payload.get("workflow_runs") or []
        if not isinstance(batch, list) or not batch:
            break
        runs.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 20:
            break
    runs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    loaded: list[dict] = []
    for run in runs:
        if str(run.get("conclusion") or "") != "success":
            continue
        run_id = run.get("id")
        jobs: list[dict] = []
        for page in range(1, 4):
            url = (
                f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
                f"?per_page=100&page={page}"
            )
            payload = _api_json(url, token)
            batch = payload.get("jobs") or []
            if not isinstance(batch, list):
                break
            jobs.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                break
        loaded.append({**run, "jobs": jobs})
    return loaded


def _self_test() -> None:
    """Fail if a copy-only or partial run would skip the deploy shards."""
    success = [{"name": name, "conclusion": "success"} for name in REQUIRED_JOBS]
    ok, _reason = full_green(success)
    if not ok:
        raise SystemExit("a complete success must reuse")
    skipped = [
        {**job, "conclusion": "skipped"} if job["name"] == "fan-out guard" else job
        for job in success
    ]
    ok, reason = full_green(skipped)
    if ok or "fan-out guard skipped" not in reason:
        raise SystemExit(f"skipped fan-out guard must not reuse ({reason})")
    copy_only = [
        {**job, "conclusion": "skipped"}
        if job["name"] in {"fan-out guard", "30-seat load budget", "slide forward load"}
        else job
        for job in success
    ]
    ok, reason = full_green(copy_only)
    if ok or "skipped" not in reason:
        raise SystemExit(f"copy-only skip must not reuse ({reason})")
    missing_shard = [job for job in success if job["name"] != "LMS tests shard 4 of 4"]
    ok, reason = full_green(missing_shard)
    if ok or "shard 4" not in reason:
        raise SystemExit(f"missing shard must not reuse ({reason})")
    mixed = [
        {"id": 1, "conclusion": "success", "status": "completed", "jobs": copy_only},
        {"id": 2, "conclusion": "success", "status": "completed", "jobs": success},
    ]
    skip, _reason = choose_reuse(mixed)
    if not skip:
        raise SystemExit("an older full green run must still reuse")
    only_copy = [{"id": 9, "conclusion": "success", "status": "completed", "jobs": copy_only}]
    skip, reason = choose_reuse(only_copy)
    if skip:
        raise SystemExit(f"copy-only run must run tests ({reason})")
    if choose_reuse([])[0]:
        raise SystemExit("no runs must run tests")
    print("reuse_green_ci self-test ok")


def main(argv: list[str] | None = None) -> int:
    """Record ``skip=true`` only for a verified full green ci.yml run.

    Network and API errors record ``skip=false`` and exit 0 so the shards run.

    Args:
        argv: CLI arguments, without the program name.

    Returns:
        0, including when the shards must still run.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        _self_test()
        return 0
    sha = os.environ.get("GITHUB_SHA", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "--sha" in args:
        sha = args[args.index("--sha") + 1]
    if "--repo" in args:
        repo = args[args.index("--repo") + 1]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not sha or not repo or not token:
        _emit(False, "missing SHA, repository, or token")
        return 0
    try:
        runs = fetch_ci_runs(repo, sha, token)
        skip, reason = choose_reuse(runs)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        _emit(False, f"GitHub API unavailable ({exc.__class__.__name__})")
        return 0
    _emit(skip, reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
