"""Per-session 200k prompt token budget hook."""

from __future__ import annotations

from pathlib import Path

from session_token_budget import (
    DEFAULT_TOKEN_BUDGET,
    compact_task_input,
    compact_turn_text,
    estimate_tokens,
    handle_hook,
    prompt_token_total,
    sliding_window_text,
)


CONV = "11111111-2222-3333-4444-555555555555"


def _stop_payload(**extra):
    payload = {
        "hook_event_name": "stop",
        "conversation_id": CONV,
        "status": "completed",
        "input_tokens": 198_435,
        "cache_read_tokens": 196_864,
        "cache_write_tokens": 0,
        "output_tokens": 393,
    }
    payload.update(extra)
    return payload


def test_estimate_tokens_is_bytes_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 8) == 2


def test_prompt_token_total_adds_cache_buckets() -> None:
    total, uncached, cache_read = prompt_token_total(
        {
            "input_tokens": 762_854,
            "cache_read_tokens": 656_640,
            "cache_write_tokens": 0,
        }
    )
    assert uncached == 762_854
    assert cache_read == 656_640
    assert total == 1_419_494


def test_compact_turn_drops_fences() -> None:
    gist = compact_turn_text(
        "<user_query>Keep the fountain hook\n```json\n{\"huge\": true}\n```\n</user_query>"
    )
    assert "fountain" in gist
    assert "```" not in gist
    assert "[omitted code/file fence]" in gist


def test_sliding_window_digests_older_chunks() -> None:
    chunks = [f"chunk {i} " + ("padding " * 40) for i in range(6)]
    text = "\n\n".join(chunks)
    out = sliding_window_text(text, keep_recent=2, token_limit=5_000)
    assert "sliding-window" in out
    assert "chunk 5" in out
    assert "chunk 0" in out
    assert "[recent]" in out


def test_guard_truncates_task_prompt_to_budget() -> None:
    bulky = ("word " * 80 + "\n\n") * 40
    out = sliding_window_text(bulky, token_limit=40)
    assert estimate_tokens(out) <= 50
    assert "[truncated to token budget]" in out


def test_stop_records_tokens_then_warns_on_post_tool(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    result = handle_hook(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": CONV,
            "tool_name": "Shell",
            "tool_input": {"command": "true"},
        },
        state_dir=tmp_path,
        environ={},
    )
    assert "additional_context" in result
    assert "395,299" in result["additional_context"]
    assert "/ 200,000" in result["additional_context"]
    again = handle_hook(
        {
            "hook_event_name": "postToolUse",
            "conversation_id": CONV,
            "tool_name": "Shell",
            "tool_input": {"command": "true"},
        },
        state_dir=tmp_path,
        environ={},
    )
    assert again.get("additional_context") is None


def test_before_submit_blocks_only_when_enforced(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    allowed = handle_hook(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "conversation_id": CONV,
            "prompt": "continue",
        },
        state_dir=tmp_path,
        environ={},
    )
    assert allowed == {"continue": True}
    blocked = handle_hook(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "conversation_id": CONV,
            "prompt": "continue",
        },
        state_dir=tmp_path,
        environ={"CURSOR_SESSION_TOKEN_ENFORCE": "1"},
    )
    assert blocked["continue"] is False
    assert "395,299" in blocked["user_message"]


def test_pretool_task_is_compacted_when_session_hot(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    prompt = "\n\n".join(
        [f"turn {i}\n```python\nprint({i})\n```\n" + ("x" * 200) for i in range(5)]
    )
    result = handle_hook(
        {
            "hook_event_name": "preToolUse",
            "conversation_id": CONV,
            "tool_name": "Task",
            "tool_input": {"prompt": prompt, "description": "director", "subagent_type": "lesson-director"},
        },
        state_dir=tmp_path,
        environ={},
    )
    updated = result["updated_input"]
    assert "```" not in updated["prompt"]
    assert "sliding-window" in updated["prompt"]
    assert updated["subagent_type"] == "lesson-director"
    assert result["permission"] == "allow"


def test_small_task_untouched_when_session_cold(tmp_path: Path) -> None:
    result = handle_hook(
        {
            "hook_event_name": "preToolUse",
            "conversation_id": CONV,
            "tool_name": "Task",
            "tool_input": {"prompt": "Read lesson-brief.json", "description": "tiny"},
        },
        state_dir=tmp_path,
        environ={},
    )
    assert result == {"permission": "allow"}


def test_reread_denied_after_warn_line(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    path = "/tmp/lesson-brief.json"
    first = handle_hook(
        {
            "hook_event_name": "beforeReadFile",
            "conversation_id": CONV,
            "file_path": path,
            "content": '{"ok": true}',
        },
        state_dir=tmp_path,
        environ={},
    )
    assert first == {"permission": "allow"}
    second = handle_hook(
        {
            "hook_event_name": "beforeReadFile",
            "conversation_id": CONV,
            "file_path": path,
            "content": '{"ok": true}',
        },
        state_dir=tmp_path,
        environ={},
    )
    assert second["permission"] == "deny"
    assert "already read" in second["user_message"]


def test_large_new_read_denied_when_over_budget(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    result = handle_hook(
        {
            "hook_event_name": "beforeReadFile",
            "conversation_id": CONV,
            "file_path": "/tmp/huge.py",
            "content": "a" * (16_000 * 4 + 100),
        },
        state_dir=tmp_path,
        environ={},
    )
    assert result["permission"] == "deny"
    assert "large read" in result["user_message"]


def test_precompact_resets_last_tokens(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    handle_hook(
        {
            "hook_event_name": "preCompact",
            "conversation_id": CONV,
            "context_tokens": 12_000,
            "trigger": "auto",
        },
        state_dir=tmp_path,
        environ={},
    )
    allowed = handle_hook(
        {
            "hook_event_name": "beforeSubmitPrompt",
            "conversation_id": CONV,
            "prompt": "go",
        },
        state_dir=tmp_path,
        environ={"CURSOR_SESSION_TOKEN_ENFORCE": "1"},
    )
    assert allowed == {"continue": True}


def test_session_end_clears_state(tmp_path: Path) -> None:
    handle_hook(_stop_payload(), state_dir=tmp_path, environ={})
    handle_hook(
        {
            "hook_event_name": "sessionEnd",
            "conversation_id": CONV,
            "reason": "user_close",
        },
        state_dir=tmp_path,
        environ={},
    )
    files = list(tmp_path.glob("session_*.json"))
    assert files == []


def test_compact_task_input_preserves_other_keys() -> None:
    bulky = "\n\n".join(["alpha " * 50, "beta " * 50, "gamma " * 50, "delta now"])
    out = compact_task_input(
        {"prompt": bulky, "subagent_type": "explore", "model": "inherit"},
        token_limit=2_000,
    )
    assert out["subagent_type"] == "explore"
    assert "delta now" in out["prompt"]
