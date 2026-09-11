#!/usr/bin/env python3
"""Per-session prompt token budget for Cursor Agent Response growth.

Arize Agent Response spans in this workspace have grown without pruning
(395k → 785k → 1.42M prompt tokens, with cache_read ≈ the uncached half).
Cursor does not let ``beforeSubmitPrompt`` rewrite the model context, so
this hook:

* records last-turn prompt / cache_read totals from ``stop`` and ``preCompact``
* sliding-window + hard-truncates Task tool prompts at 200k tokens
* denies repeat file reads once the session is over the warn line
* injects a short compact reminder via ``postToolUse`` at 80% of budget
* optionally blocks the next user prompt when ``CURSOR_SESSION_TOKEN_ENFORCE=1``

Default budget is 200_000 tokens (override with ``CURSOR_SESSION_TOKEN_BUDGET``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_TOKEN_BUDGET = 200_000
WARN_RATIO = 0.8
KEEP_RECENT_CHUNKS = 3
DIGEST_CHARS = 600
CHARS_PER_TOKEN = 4
NEW_READ_TOKEN_CAP = 16_000
STATE_VERSION = 1

CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
JSON_OBJECT_RE = re.compile(r"\{[^{}]{800,}\}")
CHUNK_SPLIT_RE = re.compile(r"\n{2,}")

HOOK_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_DIR = HOOK_DIR / "state"

COMPACT_REMINDER = (
    "Session prompt tokens {tokens:,} / {budget:,} "
    "(cache_read {cache:,}). Sliding-window is in effect: do not re-read "
    "unchanged files; cite path + sha256; summarize earlier turns instead of "
    "pasting raw history or tool dumps."
)


def estimate_tokens(text: str) -> int:
    """Approximate tokens as UTF-8 bytes / 4."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def event_name(payload: dict[str, Any]) -> str:
    """Return the Cursor hook event name (IDE or CLI)."""
    return str(
        payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("event")
        or ""
    ).strip()


def conversation_id(payload: dict[str, Any]) -> str:
    """Session / conversation id from a hook payload."""
    return str(
        payload.get("conversation_id")
        or payload.get("conversationId")
        or payload.get("session_id")
        or payload.get("sessionId")
        or ""
    ).strip()


def tool_name(payload: dict[str, Any]) -> str:
    """Tool type for pre/postToolUse."""
    return str(payload.get("tool_name") or payload.get("toolName") or "").strip()


def _to_int(value: Any) -> int | None:
    """Coerce a token count; ``None`` / ``\"--\"`` stay unset."""
    try:
        return int(value) if value not in (None, "", "--") else None
    except (TypeError, ValueError):
        return None


def prompt_token_total(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Return ``(prompt_total, uncached, cache_read)`` using Cursor stop math.

    Cursor's ``input_tokens`` is the uncached remainder. OpenInference prompt
    total adds cache_read and cache_write back in.
    """
    uncached = _to_int(payload.get("input_tokens"))
    if uncached is None:
        uncached = _to_int(payload.get("inputTokens"))
    cache_read = _to_int(payload.get("cache_read_tokens"))
    if cache_read is None:
        cache_read = _to_int(payload.get("cacheReadTokens"))
    cache_write = _to_int(payload.get("cache_write_tokens"))
    if cache_write is None:
        cache_write = _to_int(payload.get("cacheWriteTokens"))
    if uncached is None:
        return None, None, cache_read
    total = uncached + (cache_read or 0) + (cache_write or 0)
    return total, uncached, cache_read


def budget_from_env(environ: dict[str, str] | None = None) -> int:
    """Token budget, default 200k."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CURSOR_SESSION_TOKEN_BUDGET") or DEFAULT_TOKEN_BUDGET)
    parsed = _to_int(raw)
    return parsed if parsed and parsed > 0 else DEFAULT_TOKEN_BUDGET


def enforce_enabled(environ: dict[str, str] | None = None) -> bool:
    """Hard-block the next user prompt when over budget.

    Off by default: this repo's always-on rules alone can exceed 200k on a
    first turn. Set ``CURSOR_SESSION_TOKEN_ENFORCE=1`` to refuse Send.
    """
    env = environ if environ is not None else os.environ
    return str(env.get("CURSOR_SESSION_TOKEN_ENFORCE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def state_dir_from_env(environ: dict[str, str] | None = None) -> Path:
    """Directory for per-session JSON (gitignored by default)."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CURSOR_SESSION_TOKEN_STATE_DIR") or "").strip()
    return Path(raw) if raw else DEFAULT_STATE_DIR


def state_path(state_dir: Path, conv_id: str) -> Path:
    """Path for one conversation's budget state."""
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", conv_id or "unknown")
    return state_dir / f"session_{safe}.json"


def empty_state(conv_id: str) -> dict[str, Any]:
    """Fresh per-session counters."""
    return {
        "version": STATE_VERSION,
        "conversation_id": conv_id,
        "last_prompt_tokens": 0,
        "last_uncached_tokens": 0,
        "last_cache_read": 0,
        "peak_prompt_tokens": 0,
        "turns": 0,
        "injected_warn": False,
        "seen_files": [],
        "compacted": False,
    }


def load_state(state_dir: Path, conv_id: str) -> dict[str, Any]:
    """Load session state, or an empty record when missing/corrupt."""
    path = state_path(state_dir, conv_id)
    if not conv_id or not path.is_file():
        return empty_state(conv_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state(conv_id)
    if not isinstance(data, dict):
        return empty_state(conv_id)
    base = empty_state(conv_id)
    base.update(data)
    if not isinstance(base.get("seen_files"), list):
        base["seen_files"] = []
    return base


def save_state(state_dir: Path, conv_id: str, state: dict[str, Any]) -> None:
    """Persist session state."""
    if not conv_id:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    dest = state_path(state_dir, conv_id)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)


def delete_state(state_dir: Path, conv_id: str) -> None:
    """Drop state when a composer session ends."""
    path = state_path(state_dir, conv_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def record_prompt_tokens(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Update last/peak prompt token counts from a stop or sessionEnd payload."""
    total, uncached, cache_read = prompt_token_total(payload)
    if total is None:
        return state
    state["last_prompt_tokens"] = total
    state["last_uncached_tokens"] = uncached or 0
    state["last_cache_read"] = cache_read or 0
    state["peak_prompt_tokens"] = max(int(state.get("peak_prompt_tokens") or 0), total)
    state["turns"] = int(state.get("turns") or 0) + 1
    return state


def over_warn(state: dict[str, Any], budget: int) -> bool:
    """True when last prompt tokens are at or above 80% of budget."""
    return int(state.get("last_prompt_tokens") or 0) >= int(budget * WARN_RATIO)


def over_budget(state: dict[str, Any], budget: int) -> bool:
    """True when last prompt tokens exceed the session cap."""
    return int(state.get("last_prompt_tokens") or 0) > budget


def compact_turn_text(text: str, *, max_chars: int = DIGEST_CHARS) -> str:
    """Drop code fences, giant JSON, and XML wrappers; keep a short gist."""
    raw = text or ""
    match = USER_QUERY_RE.search(raw)
    if match:
        raw = match.group(1)
    raw = CODE_FENCE_RE.sub("[omitted code/file fence]", raw)
    stripped = raw.strip()
    if stripped.startswith("{") and len(stripped) > 800:
        return "[omitted JSON body]"
    raw = JSON_OBJECT_RE.sub("[omitted JSON body]", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > max_chars:
        return raw[: max_chars - 1] + "…"
    return raw


def sliding_window_text(
    text: str,
    *,
    keep_recent: int = KEEP_RECENT_CHUNKS,
    token_limit: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Keep the last chunks; digest older ones; hard-truncate to the budget."""
    raw = text or ""
    chunks = [part.strip() for part in CHUNK_SPLIT_RE.split(raw) if part.strip()]
    if len(chunks) <= keep_recent:
        compacted = compact_turn_text(raw, max_chars=max(len(raw), DIGEST_CHARS))
        return guard_text(compacted, token_limit=token_limit)["text"]
    older = chunks[:-keep_recent]
    recent = chunks[-keep_recent:]
    digest_lines = [compact_turn_text(part) for part in older]
    recent_lines = [
        compact_turn_text(part, max_chars=DIGEST_CHARS * 2) for part in recent
    ]
    omitted = len(older)
    body = (
        f"[sliding-window: {omitted} earlier chunks digested]\n"
        + "\n".join(f"- {line}" for line in digest_lines if line)
        + "\n[recent]\n"
        + "\n\n".join(recent_lines)
    )
    return guard_text(body, token_limit=token_limit)["text"]


def guard_text(text: str, *, token_limit: int = DEFAULT_TOKEN_BUDGET) -> dict[str, Any]:
    """Hard-truncate a string so the estimate fits ``token_limit``."""
    tokens = estimate_tokens(text)
    truncated = False
    if tokens > token_limit:
        keep = max(0, token_limit * CHARS_PER_TOKEN)
        text = text[:keep] + "\n[truncated to token budget]\n"
        truncated = True
        tokens = estimate_tokens(text)
    return {"text": text, "token_estimate": tokens, "truncated": truncated}


def parse_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Return tool_input as a dict (Cursor sometimes JSON-stringifies it)."""
    raw = payload.get("tool_input")
    if raw is None:
        raw = payload.get("toolInput")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"prompt": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"prompt": raw}
    return {}


def compact_task_input(tool_input: dict[str, Any], *, token_limit: int) -> dict[str, Any]:
    """Sliding-window the Task prompt/task/description fields."""
    out = dict(tool_input)
    for key in ("prompt", "task", "description"):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = sliding_window_text(value, token_limit=token_limit)
    return out


def _file_path_from_payload(payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    """Absolute path for a Read / beforeReadFile event."""
    for key in ("file_path", "filePath", "path"):
        value = payload.get(key) or tool_input.get(key)
        if value:
            return str(value)
    return ""


def remember_file(state: dict[str, Any], path: str) -> dict[str, Any]:
    """Record a file that already entered context."""
    if not path:
        return state
    seen = list(state.get("seen_files") or [])
    if path not in seen:
        seen.append(path)
        if len(seen) > 400:
            seen = seen[-400:]
        state["seen_files"] = seen
    return state


def handle_hook(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the Cursor hook stdout object for this event."""
    env = environ if environ is not None else dict(os.environ)
    budget = budget_from_env(env)
    event = event_name(payload)
    conv_id = conversation_id(payload)
    state = load_state(state_dir, conv_id)

    if event == "sessionStart":
        state = empty_state(conv_id)
        save_state(state_dir, conv_id, state)
        return {}

    if event == "sessionEnd":
        delete_state(state_dir, conv_id)
        return {}

    if event == "stop":
        state = record_prompt_tokens(state, payload)
        save_state(state_dir, conv_id, state)
        return {}

    if event == "preCompact":
        context_tokens = _to_int(payload.get("context_tokens") or payload.get("contextTokens"))
        if context_tokens is not None:
            state["last_prompt_tokens"] = context_tokens
            state["compacted"] = True
            state["injected_warn"] = False
            save_state(state_dir, conv_id, state)
        return {}

    if event == "beforeSubmitPrompt":
        if enforce_enabled(env) and over_budget(state, budget):
            tokens = int(state.get("last_prompt_tokens") or 0)
            cache = int(state.get("last_cache_read") or 0)
            return {
                "continue": False,
                "user_message": (
                    f"This session's last model prompt was {tokens:,} tokens "
                    f"(cache_read {cache:,}; budget {budget:,}). Start a new chat "
                    "so Cursor drops reused context, or set "
                    "CURSOR_SESSION_TOKEN_ENFORCE=0 to continue."
                ),
            }
        return {"continue": True}

    if event == "preToolUse" and tool_name(payload).lower() == "task":
        original = parse_tool_input(payload)
        prompt_text = str(original.get("prompt") or original.get("task") or "")
        if over_warn(state, budget) or estimate_tokens(prompt_text) > budget:
            compacted = compact_task_input(original, token_limit=budget)
            if compacted != original:
                return {"permission": "allow", "updated_input": compacted}
        return {"permission": "allow"}

    if event == "beforeReadFile":
        path = _file_path_from_payload(payload, {})
        content = str(payload.get("content") or "")
        seen = set(state.get("seen_files") or [])
        if path and path in seen and over_warn(state, budget):
            return {
                "permission": "deny",
                "user_message": (
                    f"Session over {int(budget * WARN_RATIO):,} prompt tokens; "
                    f"{path} was already read this chat. Use Grep or a line offset."
                ),
            }
        if (
            path
            and over_budget(state, budget)
            and estimate_tokens(content) > NEW_READ_TOKEN_CAP
        ):
            return {
                "permission": "deny",
                "user_message": (
                    f"Session over {budget:,} prompt tokens; skipped large read of "
                    f"{path} ({estimate_tokens(content):,} est. tokens)."
                ),
            }
        remember_file(state, path)
        save_state(state_dir, conv_id, state)
        return {"permission": "allow"}

    if event == "postToolUse":
        name = tool_name(payload).lower()
        tool_input = parse_tool_input(payload)
        if name in {"read", "readfile", "read_file"}:
            remember_file(state, _file_path_from_payload(payload, tool_input))
        response: dict[str, Any] = {}
        if over_warn(state, budget) and not state.get("injected_warn"):
            response["additional_context"] = COMPACT_REMINDER.format(
                tokens=int(state.get("last_prompt_tokens") or 0),
                budget=budget,
                cache=int(state.get("last_cache_read") or 0),
            )
            state["injected_warn"] = True
        save_state(state_dir, conv_id, state)
        return response

    return {}


def main() -> int:
    """Read one hook payload from stdin and print JSON to stdout."""
    raw = sys.stdin.read() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = handle_hook(
        payload,
        state_dir=state_dir_from_env(),
        environ=dict(os.environ),
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
