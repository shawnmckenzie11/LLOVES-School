"""Onboarding import tests: re-import no-op, identity conflicts, concurrent edits."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from onboarding_lib import (  # noqa: E402
    BuilderPaths,
    apply_import,
    load_store,
    mark_rule_change,
    operational_fingerprint,
    preview_import,
)


@pytest.fixture()
def isolated(tmp_path: Path) -> BuilderPaths:
    """Copy package + pilot lesson into a throwaway builder root."""
    dest = tmp_path / "content-builder"
    (dest / "packages").mkdir(parents=True)
    shutil.copytree(ROOT / "packages" / "MCF3M-builder-input", dest / "packages" / "MCF3M-builder-input")
    src_lesson = ROOT / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    dst_lesson = dest / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    dst_lesson.mkdir(parents=True)
    for name in ("lesson-brief.json", "student-content.json"):
        shutil.copy2(src_lesson / name, dst_lesson / name)
    (dest / "catalogue" / "resources").mkdir(parents=True)
    board = ROOT / "catalogue" / "resources" / "jsxgraph-vertex-form-board.json"
    if board.is_file():
        shutil.copy2(board, dest / "catalogue" / "resources" / board.name)
    seed_src = ROOT.parent / "lms" / "seeds" / "mcf3m_expectations.json"
    if seed_src.is_file():
        seed_dst = tmp_path / "lms" / "seeds" / "mcf3m_expectations.json"
        seed_dst.parent.mkdir(parents=True)
        shutil.copy2(seed_src, seed_dst)
    return BuilderPaths(dest)


def test_second_import_is_noop(isolated: BuilderPaths) -> None:
    package = isolated.package()
    first = apply_import(isolated, package, "MCF3M")
    assert first["batch"]["outcome"] == "applied"
    assert first["counts"]["imported"] == 39
    fp = operational_fingerprint(load_store(isolated, "MCF3M"))
    second = apply_import(isolated, package, "MCF3M")
    assert second["batch"]["outcome"] == "noop"
    assert operational_fingerprint(load_store(isolated, "MCF3M")) == fp
    assert len(load_store(isolated, "MCF3M")["identities"]) == 39


def test_student_copy_untouched(isolated: BuilderPaths) -> None:
    student = isolated.lessons / "MCF3M" / "M4-L1-vertex-form" / "student-content.json"
    before = student.read_bytes()
    apply_import(isolated, isolated.package(), "MCF3M")
    assert student.read_bytes() == before


def test_ambiguous_identity_conflict(isolated: BuilderPaths) -> None:
    extra = isolated.lessons / "MCF3M" / "M4-L1-duplicate"
    extra.mkdir(parents=True)
    brief = json.loads((isolated.lessons / "MCF3M" / "M4-L1-vertex-form" / "lesson-brief.json").read_text())
    brief["lesson_id"] = "M4-L1-duplicate"
    (extra / "lesson-brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
    apply_import(isolated, isolated.package(), "MCF3M")
    conflicts = load_store(isolated, "MCF3M")["conflicts"]
    assert any(c["id"] == "conflict.identity.MCF3M-M4L1" for c in conflicts)
    keys = {i["stable_key"] for i in load_store(isolated, "MCF3M")["identities"]}
    assert "MCF3M-M4L1" not in keys
    assert "MCF3M-M1L1" in keys


def test_concurrent_edit_refuses_stale_preview(isolated: BuilderPaths) -> None:
    package = isolated.package()
    apply_import(isolated, package, "MCF3M")
    preview_import(isolated, package, "MCF3M")
    store_path = isolated.store("MCF3M") / "identities.json"
    data = json.loads(store_path.read_text())
    data[0]["display_title"] = "Teacher renamed this"
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    head = json.loads((isolated.store("MCF3M") / "HEAD.json").read_text())
    preview = json.loads((isolated.store("MCF3M") / "previews" / "latest.json").read_text())
    with pytest.raises(RuntimeError, match="Store changed after preview"):
        apply_import(
            isolated,
            package,
            "MCF3M",
            expected_fingerprint=preview["fingerprint_before"],
        )
    assert head["fingerprint"]


def test_live_slide_rule_skips_async(isolated: BuilderPaths) -> None:
    apply_import(isolated, isolated.package(), "MCF3M")
    result = mark_rule_change(isolated, "MCF3M", "rule.live-slides", "test")
    assert result["live_only"] is True
    assert result["marked"] == []
