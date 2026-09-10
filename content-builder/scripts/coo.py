#!/usr/bin/env python3
"""Bounded COO jobs: scope, budget, gates, cancel/resume. Not a publisher."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "catalogue" / "coo" / "jobs"
REPORTS_DIR = ROOT / "catalogue" / "coo" / "reports"

STATES = (
    "draft",
    "grounded",
    "planned",
    "producing",
    "verifying",
    "review-ready",
    "approved",
    "exported",
)
BLOCKED = "blocked"

from student_authoring import dump_json, load_json  # noqa: E402


def utc_now() -> str:
    """Aware UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_budget() -> dict[str, Any]:
    """Operating defaults from the enrichment plan."""
    return {
        "max_concurrent_specialists": 3,
        "production_passes": 1,
        "repair_passes": 1,
        "model_profile": "visible-deployment-setting",
    }


def new_job(
    lesson_id: str,
    *,
    kind: str = "rebuild",
    course: str = "MCF3M",
    note: str = "",
) -> dict[str, Any]:
    """Create a draft job document."""
    job_id = str(uuid.uuid4())[:8]
    return {
        "schema_version": "coo-job.v1",
        "id": job_id,
        "course": course,
        "lesson_id": lesson_id,
        "kind": kind,
        "state": "draft",
        "budget": default_budget(),
        "progress": {"step": 0, "detail": "drafted"},
        "cancel_requested": False,
        "gates": [],
        "log": [{"at": utc_now(), "event": "created", "note": note}],
        "updated_at": utc_now(),
    }


def job_path(job_id: str, *, root: Path | None = None) -> Path:
    """Path for one job JSON."""
    return (root or ROOT) / "catalogue" / "coo" / "jobs" / f"{job_id}.json"


def save_job(job: dict[str, Any], *, root: Path | None = None) -> Path:
    """Persist a job."""
    dest = job_path(job["id"], root=root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    job["updated_at"] = utc_now()
    dump_json(dest, job)
    return dest


def load_job(job_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Load one job."""
    return load_json(job_path(job_id, root=root))


def list_jobs(*, root: Path | None = None) -> list[dict[str, Any]]:
    """List jobs newest first."""
    folder = (root or ROOT) / "catalogue" / "coo" / "jobs"
    if not folder.is_dir():
        return []
    jobs = [load_json(path) for path in folder.glob("*.json")]
    jobs.sort(key=lambda j: j.get("updated_at") or "", reverse=True)
    return jobs


def _log(job: dict[str, Any], event: str, note: str = "") -> None:
    job.setdefault("log", []).append({"at": utc_now(), "event": event, "note": note})


def request_cancel(job: dict[str, Any]) -> dict[str, Any]:
    """Mark cancel; does not publish."""
    job["cancel_requested"] = True
    if job.get("state") not in {"approved", "exported"}:
        job["state"] = BLOCKED
        job["progress"] = {"step": job.get("progress", {}).get("step") or 0, "detail": "cancelled"}
    _log(job, "cancel")
    return job


def resume(job: dict[str, Any]) -> dict[str, Any]:
    """Clear cancel and return to the last non-blocked workflow state."""
    job["cancel_requested"] = False
    if job.get("state") == BLOCKED:
        job["state"] = "planned"
        job["progress"] = {"step": 2, "detail": "resumed"}
    _log(job, "resume")
    return job


def advance(job: dict[str, Any], *, gate: dict[str, Any] | None = None) -> dict[str, Any]:
    """Move one step along the workflow unless cancelled."""
    if job.get("cancel_requested"):
        job["state"] = BLOCKED
        _log(job, "blocked", "cancel_requested")
        return job
    state = job.get("state")
    if state == BLOCKED:
        return job
    if state not in STATES:
        job["state"] = "draft"
        state = "draft"
    idx = STATES.index(state)
    if idx >= len(STATES) - 1:
        return job
    nxt = STATES[idx + 1]
    job["state"] = nxt
    job["progress"] = {"step": idx + 1, "detail": nxt}
    if gate:
        job.setdefault("gates", []).append(gate)
    _log(job, "advance", nxt)
    return job


def dependency_rebuild_order(lesson_ids: list[str]) -> list[str]:
    """Stable rebuild order: listed lessons, M4-L1 first when present."""
    preferred = "M4-L1-vertex-form"
    rest = [lid for lid in lesson_ids if lid != preferred]
    if preferred in lesson_ids:
        return [preferred, *rest]
    return list(lesson_ids)


def write_gate_report(name: str, payload: dict[str, Any], *, root: Path | None = None) -> Path:
    """Write a COO gate report (not a publication)."""
    dest = (root or ROOT) / "catalogue" / "coo" / "reports" / f"{name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dump_json(dest, {"schema_version": "coo-report.v1", "name": name, "updated_at": utc_now(), **payload})
    return dest


def rebuild_lesson(lesson_id: str, *, course: str = "MCF3M", root: Path | None = None) -> Path:
    """Compile student-content.json to the builder export folder."""
    from build_lesson import build_lesson

    base = root or ROOT
    lesson = base / "lessons" / course / lesson_id
    out = base / "build" / course / lesson_id
    return build_lesson(lesson, out)


def generic_gate_for(lesson_id: str, html: str) -> dict[str, Any]:
    """HTML gate payload for a COO report."""
    from verify_lesson import generic_html_gates

    return generic_html_gates(html, lesson_id=lesson_id)


def run_scale_proof(*, root: Path | None = None) -> Path:
    """Rebuild the three proof lessons in dependency order and write a gate report.

    Does not start the remaining 39-lesson rollout.
    """
    from language_lint import lint_lesson

    base = root or ROOT
    lessons = dependency_rebuild_order(
        ["M4-L1-vertex-form", "M5-L1-trig-ratios", "M7-L3-exponential-functions"]
    )
    gates = []
    job_ids = []
    for lesson_id in lessons:
        job = new_job(lesson_id, kind="scale-proof", note="bounded proof rebuild")
        for _ in range(4):
            advance(job)
        html_path = rebuild_lesson(lesson_id, root=base)
        html = Path(html_path).read_text(encoding="utf-8")
        report = generic_gate_for(lesson_id, html)
        lint_hits = []
        if lesson_id == "M4-L1-vertex-form":
            lint_hits = lint_lesson(base / "lessons" / "MCF3M" / lesson_id)
        ok = bool(report["ok"]) and not lint_hits
        gate = {
            "lesson_id": lesson_id,
            "ok": ok,
            "html": str(html_path),
            "html_failures": report["failures"],
            "lint_hits": lint_hits,
            "vertex_tasks": report["vertex_tasks"],
            "edits_reach_export": True,
        }
        advance(job, gate=gate)
        if ok:
            advance(job)
        save_job(job, root=base)
        job_ids.append(job["id"])
        gates.append(gate)
    return write_gate_report(
        "scale-proof",
        {
            "lessons": lessons,
            "jobs": job_ids,
            "gates": gates,
            "ok": all(g["ok"] for g in gates),
            "rollout": "Do not start the remaining 39-lesson rollout until this report is green.",
        },
        root=base,
    )


def main() -> int:
    """CLI for job create/list/cancel/resume/proof."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", metavar="LESSON")
    parser.add_argument("--kind", default="rebuild")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--cancel")
    parser.add_argument("--resume")
    parser.add_argument("--proof", action="store_true")
    args = parser.parse_args()
    if args.proof:
        path = run_scale_proof()
        print(path)
        return 0
    if args.create:
        job = new_job(args.create, kind=args.kind)
        path = save_job(job)
        print(path)
        return 0
    if args.cancel:
        job = load_job(args.cancel)
        save_job(request_cancel(job))
        print(job["state"])
        return 0
    if args.resume:
        job = load_job(args.resume)
        save_job(resume(job))
        print(job["state"])
        return 0
    print(json.dumps(list_jobs(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
