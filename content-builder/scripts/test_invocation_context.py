"""Invocation packer: omit unchanged bodies, digest history, token guard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from invocation_context import (  # noqa: E402
    DEFAULT_TOKEN_LIMIT,
    audit_system_prompt,
    build_invocation,
    compact_turn_text,
    estimate_tokens,
    guard_text,
    mark_unchanged,
    pack_path,
    save_pack,
    sha256_text,
    strip_inline_file_bodies,
    summarize_history,
)


def test_estimate_tokens_is_bytes_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 8) == 2


def test_compact_turn_drops_fences_and_user_query_wrapper() -> None:
    text = (
        "<user_query>Keep the fountain hook\n"
        "```json\n{\"huge\": true}\n```\n"
        "</user_query>"
    )
    gist = compact_turn_text(text)
    assert "fountain" in gist
    assert "```" not in gist
    assert "[omitted code/file fence]" in gist


def test_summarize_history_digests_older_turns() -> None:
    turns = [
        {"role": "user", "text": "first request with a lot of padding " * 20},
        {"role": "assistant", "text": '{"lesson_id": "' + ("x" * 900) + '"}'},
        {"role": "user", "text": "use the fountain hook"},
        {"role": "assistant", "text": "selected fountain-arc-photo"},
    ]
    packed = summarize_history(turns, keep_recent=2)
    assert packed["omitted_turns"] == 2
    assert packed["digest"][1]["gist"] == "[omitted JSON body]"
    assert packed["recent"][-1]["text"] == "selected fountain-arc-photo"
    assert "padding" in packed["digest"][0]["gist"]


def test_mark_unchanged_omits_matching_hash() -> None:
    current = {"path": "a.json", "status": "present", "sha256": "abc", "bytes": 12}
    previous = {"path": "a.json", "status": "new", "sha256": "abc", "bytes": 12}
    tagged = mark_unchanged(current, previous)
    assert tagged["status"] == "unchanged"
    assert tagged["omit_body"] is True


def test_strip_inline_file_bodies_replaces_pasted_json() -> None:
    body = '{"central_question": "where is the vertex?"}\n'
    prompt = "Here is the brief:\n" + body + "\nNow revise it."
    files = [
        {
            "relpath": "content-builder/lessons/MCF3M/M4-L1-vertex-form/lesson-brief.json",
            "sha256": sha256_text(body),
            "body": body,
        }
    ]
    out = strip_inline_file_bodies(prompt, files)
    assert body not in out
    assert "lesson-brief.json" in out
    assert "unchanged" in out


def test_guard_text_warns_then_truncates() -> None:
    # 50 tokens of payload; warn at 80% of 60, truncate above 40.
    text = "word " * 40
    warned = guard_text(text, token_limit=60)
    assert warned["truncated"] is False
    assert any("80%" in w for w in warned["warnings"])
    bulky = text * 20
    cut = guard_text(bulky, token_limit=40)
    assert cut["truncated"] is True
    assert cut["token_estimate"] < estimate_tokens(bulky)
    assert "[truncated to token budget]" in cut["text"]


def test_build_invocation_lists_paths_not_bodies(tmp_path: Path) -> None:
    repo = tmp_path / "LLOVES-School"
    builder = repo / "content-builder"
    lesson = builder / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    lesson.mkdir(parents=True)
    brief = {"schema_version": "lesson-brief.v2", "central_question": "Where is the vertex?"}
    (lesson / "lesson-brief.json").write_text(json.dumps(brief), encoding="utf-8")
    (lesson / "resolved-context.lesson-director.json").write_text(
        json.dumps({"role": "lesson-director"}), encoding="utf-8"
    )
    (builder / "catalogue" / "contracts").mkdir(parents=True)
    (builder / "catalogue" / "contracts" / "lesson-brief.schema.json").write_text(
        "{}", encoding="utf-8"
    )
    seed = repo / "lms" / "seeds"
    seed.mkdir(parents=True)
    (seed / "mcf3m_expectations.json").write_text("[]", encoding="utf-8")

    first = build_invocation(
        repo=repo,
        builder=builder,
        course="MCF3M",
        lesson="M4-L1-vertex-form",
        role="lesson-director",
        decisions=["Keep A2.7 and A2.10 quotes."],
        turns=[
            {
                "role": "user",
                "text": "<user_query>Revise the brief</user_query>\n```json\n"
                + json.dumps(brief)
                + "\n```",
            },
            {"role": "assistant", "text": "I pasted the whole brief again."},
        ],
        token_limit=DEFAULT_TOKEN_LIMIT,
    )
    assert first["prompt"].count("Where is the vertex?") == 0
    assert "lesson-brief.json" in first["prompt"]
    assert "Keep A2.7" in first["prompt"]
    assert "already in your system prompt" in first["prompt"]
    assert first["files"]
    assert json.dumps(brief) not in first["prompt"]
    save_pack(builder, first)
    dest = pack_path(builder, "MCF3M", "M4-L1-vertex-form", "lesson-director")
    assert dest.is_file()

    second = build_invocation(
        repo=repo,
        builder=builder,
        course="MCF3M",
        lesson="M4-L1-vertex-form",
        role="lesson-director",
        previous=first,
    )
    assert "content-builder/lessons/MCF3M/M4-L1-vertex-form/lesson-brief.json" in (
        second.get("unchanged_paths") or []
    )
    assert "unchanged sha256=" in second["prompt"]


def test_history_shrinks_when_over_tiny_budget(tmp_path: Path) -> None:
    repo = tmp_path / "LLOVES-School"
    builder = repo / "content-builder"
    lesson = builder / "lessons" / "MCF3M" / "M4-L1-vertex-form"
    lesson.mkdir(parents=True)
    (lesson / "lesson-brief.json").write_text("{}", encoding="utf-8")
    turns = [
        {"role": "user", "text": f"turn {i} " + ("padding " * 80)}
        for i in range(8)
    ]
    pack = build_invocation(
        repo=repo,
        builder=builder,
        course="MCF3M",
        lesson="M4-L1-vertex-form",
        role="lesson-director",
        turns=turns,
        token_limit=80,
    )
    assert pack["truncated"] is True
    assert pack["token_estimate"] <= 80 or "[truncated to token budget]" in pack["prompt"]
    assert pack["warnings"]


def test_audit_system_prompt_lists_injected_paths(tmp_path: Path) -> None:
    repo = tmp_path / "school"
    repo.mkdir()
    audit = audit_system_prompt(repo, "lesson-director")
    rels = [row["relpath"] for row in audit["files"]]
    assert ".cursor/agents/lesson-director.md" in rels
    assert "AGENTS.md" in rels
    assert all(row["status"] == "missing" for row in audit["files"])
    assert "Do not paste" in audit["note"]


def test_audit_system_prompt_on_this_repo() -> None:
    repo = ROOT.parent
    audit = audit_system_prompt(repo, "lesson-director")
    present = [row for row in audit["files"] if row["status"] == "present"]
    assert any(
        str(row.get("relpath") or "").endswith("lesson-director.md") for row in present
    )
    assert audit["bytes"] > 0


def test_unknown_role_rejected(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        build_invocation(
            repo=tmp_path,
            builder=tmp_path / "content-builder",
            course="MCF3M",
            lesson="M4-L1-vertex-form",
            role="curriculum-mapper",
        )
