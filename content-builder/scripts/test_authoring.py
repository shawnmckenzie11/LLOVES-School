"""Student-content authoring: three tabs, patch, save → rebuild → preview."""

from __future__ import annotations

import json
import shutil
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from serve_review import handler_for  # noqa: E402
from student_authoring import (  # noqa: E402
    content_revision,
    dump_json,
    find_block,
    iter_blocks,
    load_json,
    prepare_canonical_document,
    save_patch,
    write_revision,
)
from serve_review import list_review_lessons  # noqa: E402

FIXTURE = ROOT / "fixtures" / "M4-L1-vertex-form-before-enrichment" / "lesson" / "student-content.json"
LIVE_LESSON = ROOT / "lessons" / "MCF3M" / "M4-L1-vertex-form"
MARKER = "SAVE_PREVIEW_MARKER_9f3c"


def _copy_live_assets(dest: Path) -> None:
    for name in ("interaction-spec.json", "student-feedback.json", "student-practice-sets.json"):
        shutil.copy2(LIVE_LESSON / name, dest / name)
    media = LIVE_LESSON / "media"
    if media.is_dir():
        shutil.copytree(media, dest / "media")


@pytest.fixture()
def isolated(tmp_path: Path) -> Path:
    """Builder tree with a canonical three-tab student-content document."""
    builder = tmp_path / "content-builder"
    lesson = builder / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    lesson.mkdir(parents=True)
    doc = load_json(FIXTURE)
    prepare_canonical_document(doc)
    dump_json(lesson / "student-content.json", doc)
    write_revision(lesson, doc)
    _copy_live_assets(lesson)
    (builder / "components").symlink_to(ROOT / "components")
    (builder / "review").mkdir()
    shutil.copy2(ROOT / "review" / "index.html", builder / "review" / "index.html")
    return builder


def test_fixture_still_has_hook_tab() -> None:
    doc = load_json(FIXTURE)
    ids = [p["id"] for p in doc["parts"]]
    assert "hook" in ids
    assert len(ids) == 4


def test_prepare_folds_hook_and_assigns_ids() -> None:
    doc = prepare_canonical_document(load_json(FIXTURE))
    assert [p["id"] for p in doc["parts"]] == ["minds-on", "action", "consolidation"]
    assert [p["nav_label"] for p in doc["parts"]] == ["Minds On", "Action", "Consolidation"]
    ids = [b["id"] for b in iter_blocks(doc)]
    assert len(ids) == len(set(ids))
    assert any(b.get("type") == "figure" for b in doc["parts"][0]["blocks"])


def test_save_rebuild_preview_shows_change(isolated: Path) -> None:
    lesson = isolated / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    doc = load_json(lesson / "student-content.json")
    block = next(b for b in iter_blocks(doc) if b.get("type") == "p" and b.get("text"))
    out = isolated / "build" / "MCF3M" / "M4-L1-vertex-form"
    result = save_patch(
        lesson,
        [{"op": "replace", "block_id": block["id"], "field": "text", "value": MARKER}],
        base_revision=content_revision(doc),
        out_dir=out,
        components=isolated / "components",
    )
    html = Path(result["rebuilt"]).read_text(encoding="utf-8")
    assert MARKER in html
    assert 'id="tab-minds-on"' in html
    assert 'id="tab-action"' in html
    assert 'id="tab-consolidation"' in html
    assert 'id="tab-hook"' not in html
    assert "The water" not in html


def test_locked_block_refused(isolated: Path) -> None:
    lesson = isolated / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    doc = load_json(lesson / "student-content.json")
    block = next(b for b in iter_blocks(doc) if b.get("type") == "p")
    dump_json(
        lesson / "locks.json",
        {"lesson_id": "M4-L1-vertex-form", "locked_keys": [f"block:{block['id']}"]},
    )
    with pytest.raises(PermissionError):
        save_patch(
            lesson,
            [{"block_id": block["id"], "field": "text", "value": "nope"}],
            rebuild=False,
        )
    assert find_block(load_json(lesson / "student-content.json"), block["id"])["text"] == block["text"]


def test_stale_revision_refused(isolated: Path) -> None:
    lesson = isolated / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    block = next(b for b in iter_blocks(load_json(lesson / "student-content.json")) if b.get("type") == "p")
    with pytest.raises(RuntimeError, match="stale revision"):
        save_patch(
            lesson,
            [{"block_id": block["id"], "field": "text", "value": "x"}],
            base_revision="not-the-current-hash",
            rebuild=False,
        )


def test_instruction_retired_and_http_save_reaches_preview(isolated: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(isolated))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = HTTPConnection(host, port, timeout=30)
        conn.request("POST", "/api/instruction", body="{}", headers={"Content-Type": "application/json"})
        retired = json.loads(conn.getresponse().read())
        assert retired["ok"] is False
        conn.close()

        lesson = isolated / "lessons" / "MCF3M" / "M4-L1-vertex-form"
        doc = load_json(lesson / "student-content.json")
        block = next(b for b in iter_blocks(doc) if b.get("type") == "p" and b.get("text"))
        payload = {
            "course": "MCF3M",
            "lesson_id": "M4-L1-vertex-form",
            "base_revision": content_revision(doc),
            "ops": [{"op": "replace", "block_id": block["id"], "field": "text", "value": MARKER}],
        }
        conn = HTTPConnection(host, port, timeout=30)
        conn.request(
            "POST",
            "/api/student-content",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        res = conn.getresponse()
        body = json.loads(res.read())
        conn.close()
        assert res.status == 200, body
        assert body["ok"] is True
        conn = HTTPConnection(host, port, timeout=30)
        conn.request("GET", body["preview"])
        html = conn.getresponse().read().decode("utf-8")
        conn.close()
        assert MARKER in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_list_lessons_and_approve(isolated: Path) -> None:
    rows = list_review_lessons(isolated, "MCF3M")
    assert any(r["internal_id"] == "M4-L1-vertex-form" and r["has_student"] for r in rows)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(isolated))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        conn = HTTPConnection(host, port, timeout=30)
        conn.request("GET", "/api/lessons?course=MCF3M")
        body = json.loads(conn.getresponse().read())
        conn.close()
        assert body["ok"] is True
        assert any(r["internal_id"] == "M4-L1-vertex-form" for r in body["lessons"])
        conn = HTTPConnection(host, port, timeout=30)
        conn.request(
            "POST",
            "/api/approve",
            body=json.dumps({"course": "MCF3M", "lesson_id": "M4-L1-vertex-form"}),
            headers={"Content-Type": "application/json"},
        )
        approved = json.loads(conn.getresponse().read())
        conn.close()
        assert approved["ok"] is True
        conn = HTTPConnection(host, port, timeout=30)
        conn.request("GET", "/api/diff?course=MCF3M&lesson=M4-L1-vertex-form")
        diff = json.loads(conn.getresponse().read())
        conn.close()
        assert diff["ok"] is True
        assert diff["against"] == "approved"
        assert diff["rows"] == []
        conn = HTTPConnection(host, port, timeout=30)
        conn.request("GET", "/api/coo/jobs")
        jobs = json.loads(conn.getresponse().read())
        conn.close()
        assert jobs["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
