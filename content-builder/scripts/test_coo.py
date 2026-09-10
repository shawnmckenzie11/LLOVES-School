"""COO cancel/resume, budgets, and rebuild order."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from coo import (  # noqa: E402
    advance,
    default_budget,
    dependency_rebuild_order,
    new_job,
    request_cancel,
    resume,
    save_job,
)


def test_cancel_blocks_and_resume_returns() -> None:
    job = new_job("M4-L1-vertex-form", note="test")
    advance(job)
    request_cancel(job)
    assert job["state"] == "blocked"
    assert job["cancel_requested"] is True
    resume(job)
    assert job["cancel_requested"] is False
    assert job["state"] == "planned"


def test_cancel_stops_advance() -> None:
    job = new_job("M5-L1-trig-ratios")
    job["cancel_requested"] = True
    advance(job)
    assert job["state"] == "blocked"


def test_budget_and_order() -> None:
    budget = default_budget()
    assert budget["max_concurrent_specialists"] == 3
    assert budget["production_passes"] == 1
    assert dependency_rebuild_order(
        ["M7-L3-exponential-functions", "M4-L1-vertex-form", "M5-L1-trig-ratios"]
    )[0] == "M4-L1-vertex-form"


def test_save_roundtrip(tmp_path: Path) -> None:
    job = new_job("M4-L1-vertex-form")
    path = save_job(job, root=tmp_path)
    assert path.is_file()
    assert path.parent.name == "jobs"
