"""Staff bank bot: soft-remove listings and ranking drafts.

Generation calls Claude only when ``ANTHROPIC_API_KEY`` is set, and the
request body is question content (stem, options, correct choice, module).
Drafts live in ``bank_rank_drafts`` until a teacher approves one into the
existing live-bank rank format. Nothing here runs on the student poll.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

try:
    from bank_edit import truncate_stem
    from live_rank import build_rank_options, parse_rank_key
    from question_math import html_to_plain
except ImportError:
    from lms.bank_edit import truncate_stem
    from lms.live_rank import build_rank_options, parse_rank_key
    from lms.question_math import html_to_plain

NOT_CONFIGURED_MESSAGE = (
    "Ranking generation is not configured. Set ANTHROPIC_API_KEY on the server. "
    "Removing questions still works."
)
GENERATE_PER_MINUTE = 4
GENERATE_WINDOW_S = 60.0
GENERATE_LIMIT_MESSAGE = "Wait a minute before generating more ranking questions."
_generate_lock = threading.Lock()
_generate_hits: dict[int, list[float]] = {}
MAX_SOURCE_QUESTIONS = 12
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_DEFAULT_MODEL = "claude-sonnet-4-5"
_MC_ITEM_TYPES = frozenset(
    {"multiple_choice_question", "multiple_choice", "mc", "true_false_question"}
)
_LETTERS = "ABCDEFGH"


def reset_generate_limit() -> None:
    """Clear the in-memory generate window.

    Tests call this so one case does not use another teacher's minute.
    """
    with _generate_lock:
        _generate_hits.clear()


def allow_generate(user_id: int, *, now: float | None = None) -> bool:
    """Return True when this teacher may start another ranking generation.

    Four calls per rolling minute. A call that is refused does not start
    Claude. The clock is ``time.monotonic`` unless ``now`` is passed, and
    the window lives in this process.

    Args:
        user_id: Staff user id.
        now: Optional timestamp in the same units as ``time.monotonic``.
    """
    stamp = time.monotonic() if now is None else float(now)
    with _generate_lock:
        recent = [
            hit
            for hit in _generate_hits.get(int(user_id), [])
            if stamp - hit < GENERATE_WINDOW_S
        ]
        if len(recent) >= GENERATE_PER_MINUTE:
            _generate_hits[int(user_id)] = recent
            return False
        recent.append(stamp)
        _generate_hits[int(user_id)] = recent
        return True


def ranking_generation_configured() -> bool:
    """Return True when a Claude key is present in the environment.

    Read on each check. The value is never returned or logged. Deploy
    injects ``ANTHROPIC_API_KEY``; this module does not load it itself.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def request_claude(prompt: str) -> str:
    """Send question content to Claude and return the text reply.

    The key is ``os.environ["ANTHROPIC_API_KEY"]`` at this call, not a
    value cached at import.

    Args:
        prompt: Question stems and options only.

    Raises:
        KeyError: When ``ANTHROPIC_API_KEY`` is unset.
        ValueError: When the call fails. The client message has no key
            and no response body.
    """
    key = os.environ["ANTHROPIC_API_KEY"].strip()
    if not key:
        raise ValueError(NOT_CONFIGURED_MESSAGE)
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or _DEFAULT_MODEL
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        _ANTHROPIC_URL,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise ValueError("Ranking generation failed.") from exc
    parts = raw.get("content") if isinstance(raw, dict) else None
    texts: list[str] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and part.get("type", "text") == "text":
                texts.append(str(part.get("text") or ""))
    text = "\n".join(texts).strip()
    if not text:
        raise ValueError("Ranking generation failed.")
    return text


def content_for_model(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return plain question content, or None when the row is not a live MC.

    Only ``module``, ``stem``, ``options``, and ``correct`` are kept.
    Ids, notes, and any other stored fields are dropped.

    Args:
        row: Question row with ``payload_json`` or ``payload``, plus optional
            overlay ``stem_text``, ``options_json``, and ``correct_answer``.
    """
    if row.get("removed_at"):
        return None
    payload = row.get("payload")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    kind = str(payload.get("type") or payload.get("kind") or "").strip().lower()
    if kind in {"rank", "poll", "numeric", "team_draw"}:
        return None
    item_type = str(row.get("item_type") or "").strip().lower()
    if item_type == "true_false_question":
        return None
    if item_type and item_type not in _MC_ITEM_TYPES and kind != "mc":
        return None
    stem = html_to_plain(str(row.get("stem_text") or "")).strip()
    if not stem:
        stem = html_to_plain(
            str(payload.get("stem_html") or payload.get("text") or "")
        ).strip()
    if not stem:
        stem = str(row.get("title") or "").strip()
    options = _option_texts(row.get("options_json"), payload)
    if len(options) < 2 or not stem:
        return None
    correct = str(row.get("correct_answer") or "").strip().upper()[:1]
    if correct not in _LETTERS:
        correct = ""
    module = str(row.get("bank_scope") or "").strip()
    return {
        "module": module,
        "stem": " ".join(stem.split())[:500],
        "options": options,
        "correct": correct,
    }


def prompt_from_contents(contents: list[dict[str, Any]]) -> str:
    """Build the Claude prompt from already-filtered question content.

    Args:
        contents: Dicts from :func:`content_for_model`.
    """
    lines = [
        "Write ranking questions for an Ontario secondary math class.",
        "Use only the question content below.",
        "Return JSON only, with no markdown:",
        '{"items":[{"stem":"...","options":["...","...","..."],"rank_key":[0,1,2]}]}',
        "Each item needs 3 to 6 unique options.",
        "rank_key is the correct order as zero-based indexes into options,",
        "a permutation of every option.",
        "",
        "Questions:",
    ]
    for index, item in enumerate(contents, start=1):
        module = str(item.get("module") or "").strip() or "unspecified"
        lines.append(f"{index}. Module {module}")
        lines.append(f"Stem: {item.get('stem') or ''}")
        correct = str(item.get("correct") or "")
        options = item.get("options") if isinstance(item.get("options"), list) else []
        for opt_index, option in enumerate(options):
            letter = _LETTERS[opt_index] if opt_index < len(_LETTERS) else "?"
            mark = " (correct)" if letter == correct else ""
            lines.append(f"  {letter}. {option}{mark}")
        lines.append("")
    return "\n".join(lines).strip()


def parse_ranking_items(text: str) -> list[dict[str, Any]]:
    """Validate Claude's JSON into rank drafts.

    Args:
        text: Model reply. A surrounding ``` fence is ignored.

    Returns:
        Draft dicts with ``stem``, ``options``, and integer ``rank_key``.

    Raises:
        ValueError: When nothing usable came back.
    """
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model did not return ranking questions.")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("The model did not return ranking questions.") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("The model did not return ranking questions.")
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cleaned.append(validate_rank_draft(item))
        except ValueError:
            continue
    if not cleaned:
        raise ValueError("The model did not return a usable ranking question.")
    return cleaned


def validate_rank_draft(item: dict[str, Any]) -> dict[str, Any]:
    """Check one draft against the live rank option rules.

    Args:
        item: ``stem``, ``options``, and ``rank_key``.

    Returns:
        Stem, option labels, and a zero-based ``rank_key`` permutation.
    """
    stem = " ".join(str(item.get("stem") or "").split())
    if not stem:
        raise ValueError("stem required")
    raw_options = item.get("options") or []
    if not isinstance(raw_options, list):
        raise ValueError("options required")
    options: list[str] = []
    for option in raw_options:
        text = " ".join(str(option or "").split())[:240]
        if text:
            options.append(text)
    built = build_rank_options(options)
    key = _coerce_rank_key(item.get("rank_key"), [row["label"] for row in built])
    parse_rank_key(key, built)
    return {"stem": stem[:500], "options": [row["label"] for row in built], "rank_key": key}


def removed_question_views(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape recently removed rows for the staff list.

    Args:
        rows: :meth:`SchoolDB.list_removed_library_questions` rows.
    """
    views: list[dict[str, Any]] = []
    for row in rows:
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(row.get("payload_json") or "{}")
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, json.JSONDecodeError):
            payload = {}
        stem = html_to_plain(str(row.get("stem_text") or "")).strip()
        if not stem:
            stem = html_to_plain(str(payload.get("stem_html") or "")).strip()
        if not stem:
            stem = str(row.get("title") or "").strip()
        views.append(
            {
                "question_id": int(row["id"]),
                "bank_id": int(row["bank_id"]),
                "stem": truncate_stem(stem),
                "bank_scope": str(row.get("bank_scope") or ""),
                "removed_at": str(row.get("removed_at") or ""),
            }
        )
    return views


def generate_ranking_drafts(
    school: Any,
    *,
    library_id: int,
    class_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Create ranking drafts from selected MCs or a module MC filter.

    Args:
        school: School database.
        library_id: ``content_libraries.id`` for this class.
        class_id: Staff class id stored on the draft. Not sent to Claude.
        body: ``question_ids``, or ``bank_scope`` plus optional ``question_type``.

    Returns:
        ``drafts`` and ``truncated`` when the filter was longer than the cap.

    Raises:
        ValueError: When the selection is empty, not multiple choice, or the
            model reply cannot be saved.
    """
    if not ranking_generation_configured():
        raise ValueError(NOT_CONFIGURED_MESSAGE)
    question_ids = _requested_ids(body.get("question_ids") or body.get("questionIds"))
    scope = str(body.get("bank_scope") or body.get("bankScope") or "").strip()
    truncated = False
    if question_ids:
        if len(question_ids) > MAX_SOURCE_QUESTIONS:
            raise ValueError(f"Select at most {MAX_SOURCE_QUESTIONS} questions.")
        loaded = [_load_question(school, int(library_id), qid) for qid in question_ids]
        rows = [row for row in loaded if row is not None]
    else:
        wanted = str(body.get("question_type") or body.get("questionType") or "mc").strip().lower()
        if wanted not in {"", "mc", "multiple_choice"}:
            raise ValueError("Ranking generation uses multiple-choice questions.")
        scope = scope or "M1"
        rows, truncated = _mc_rows_for_scope(
            school,
            library_id=int(library_id),
            class_id=int(class_id),
            bank_scope=scope,
            kind=str(body.get("kind") or "").strip().lower(),
        )
    contents: list[dict[str, Any]] = []
    source_ids: list[int] = []
    for row in rows:
        content = content_for_model(row)
        if content is None:
            continue
        contents.append(content)
        source_ids.append(int(row["id"]))
        if len(contents) >= MAX_SOURCE_QUESTIONS:
            break
    if not contents:
        raise ValueError("Select multiple-choice questions.")
    if not scope:
        scope = str(contents[0].get("module") or "").strip() or "M1"
    prompt = prompt_from_contents(contents)
    drafts = parse_ranking_items(request_claude(prompt))
    saved = [
        _insert_draft(
            school,
            library_id=int(library_id),
            class_id=int(class_id),
            bank_scope=scope,
            draft=draft,
            source_ids=source_ids,
        )
        for draft in drafts
    ]
    return {"drafts": saved, "truncated": bool(truncated and not question_ids)}


def list_rank_drafts(school: Any, library_id: int) -> list[dict[str, Any]]:
    """Return unapproved ranking drafts for one course library.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
    """
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT id, bank_scope, stem_text, options_json, rank_key_json,
                   source_json, status, created_at
            FROM bank_rank_drafts
            WHERE library_id = ? AND status = 'draft'
            ORDER BY id DESC
            """,
            (int(library_id),),
        ).fetchall()
    return [_draft_view(row) for row in rows]


def update_rank_draft(
    school: Any,
    *,
    library_id: int,
    draft_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Save teacher edits on a draft that is not in the bank yet.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        draft_id: ``bank_rank_drafts.id``.
        body: Optional ``stem``, ``options``, and ``rank_key``.
    """
    current = _get_draft(school, int(library_id), int(draft_id))
    if current is None or str(current["status"]) != "draft":
        raise KeyError(f"draft {draft_id}")
    merged = {
        "stem": body.get("stem", body.get("stem_text", current["stem_text"])),
        "options": body.get("options", _loads_list(current["options_json"])),
        "rank_key": body.get("rank_key", body.get("rankKey", _loads_list(current["rank_key_json"]))),
    }
    cleaned = validate_rank_draft(merged)
    stamp = _stamp(school)
    with school._lock:
        school.conn.execute(
            """
            UPDATE bank_rank_drafts
            SET stem_text = ?, options_json = ?, rank_key_json = ?, updated_at = ?
            WHERE id = ? AND library_id = ? AND status = 'draft'
            """,
            (
                cleaned["stem"],
                json.dumps(cleaned["options"]),
                json.dumps(cleaned["rank_key"]),
                stamp,
                int(draft_id),
                int(library_id),
            ),
        )
        school.conn.commit()
    updated = _get_draft(school, int(library_id), int(draft_id))
    if updated is None:
        raise KeyError(f"draft {draft_id}")
    return _draft_view(updated)


def approve_rank_draft(
    school: Any,
    *,
    library_id: int,
    class_id: int,
    draft_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one draft into the live bank as a rank question.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        class_id: Class id passed to the existing live-bank insert.
        draft_id: ``bank_rank_drafts.id``.
        body: Optional last edits applied before the insert.

    Returns:
        The inserted staff question plus ``draft_id``.
    """
    try:
        from live_bank import create_live_bank_question
    except ImportError:
        from lms.live_bank import create_live_bank_question

    if body:
        update_rank_draft(
            school,
            library_id=int(library_id),
            draft_id=int(draft_id),
            body=body,
        )
    current = _get_draft(school, int(library_id), int(draft_id))
    if current is None or str(current["status"]) != "draft":
        raise KeyError(f"draft {draft_id}")
    options = _loads_list(current["options_json"])
    rank_key = _loads_list(current["rank_key_json"])
    scope = str(current["bank_scope"] or "M1")
    question = create_live_bank_question(
        school,
        library_id=int(library_id),
        bank_scope=scope,
        class_id=int(class_id),
        body={
            "type": "rank",
            "stem_text": str(current["stem_text"] or ""),
            "options": options,
            "rank_key": rank_key,
        },
    )
    stamp = _stamp(school)
    with school._lock:
        school.conn.execute(
            """
            UPDATE bank_rank_drafts
            SET status = 'approved', question_id = ?, updated_at = ?
            WHERE id = ? AND library_id = ? AND status = 'draft'
            """,
            (int(question["id"]), stamp, int(draft_id), int(library_id)),
        )
        school.conn.commit()
    question["draft_id"] = int(draft_id)
    return question


def discard_rank_draft(school: Any, *, library_id: int, draft_id: int) -> None:
    """Drop a draft without writing a bank question.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        draft_id: ``bank_rank_drafts.id``.
    """
    stamp = _stamp(school)
    with school._lock:
        cur = school.conn.execute(
            """
            UPDATE bank_rank_drafts
            SET status = 'discarded', updated_at = ?
            WHERE id = ? AND library_id = ? AND status = 'draft'
            """,
            (stamp, int(draft_id), int(library_id)),
        )
        school.conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"draft {draft_id}")


def require_class_library(
    school: Any,
    user: dict[str, Any] | None,
    class_id: int,
    ready_library: Any,
) -> tuple[int | None, tuple[dict[str, Any], int] | None]:
    """Resolve the course library when this teacher or admin owns the class.

    Args:
        school: School database.
        user: Signed-in staff user.
        class_id: Staff class id.
        ready_library: ``_ready_library`` from the app.

    Returns:
        ``(library_id, None)`` or ``(None, (payload, status))``.
    """
    if user is None or not school.teacher_owns_class(int(user["id"]), int(class_id)):
        return None, ({"ok": False, "error": "Forbidden"}, 403)
    cls = school.enrich_class(school.game.get_class(int(class_id)))
    library_id, error = ready_library(school, cls)
    if not library_id:
        return None, ({"ok": False, "error": error or "No module pack"}, 404)
    return int(library_id), None


def _option_texts(raw_overlay: Any, payload: dict[str, Any]) -> list[str]:
    """Plain option labels from an overlay list or payload choices."""
    options: list[str] = []
    overlay = raw_overlay
    if isinstance(overlay, str):
        try:
            overlay = json.loads(overlay or "[]")
        except json.JSONDecodeError:
            overlay = []
    if isinstance(overlay, list) and overlay:
        for item in overlay:
            text = html_to_plain(str(item or "")).strip()
            if text:
                options.append(" ".join(text.split())[:200])
    if len(options) >= 2:
        return options[:8]
    options = []
    choices = payload.get("choices") or payload.get("options") or []
    if isinstance(choices, list):
        for item in choices:
            if isinstance(item, dict):
                text = html_to_plain(str(item.get("html") or item.get("text") or "")).strip()
            else:
                text = html_to_plain(str(item or "")).strip()
            if text:
                options.append(" ".join(text.split())[:200])
    return options[:8]


def _coerce_rank_key(raw: Any, labels: list[str]) -> list[int]:
    """Return zero-based indexes, accepting labels in answer order."""
    if not isinstance(raw, list):
        raise ValueError("answer order must list every option once")
    if raw and all(isinstance(item, str) and not str(item).strip().isdigit() for item in raw):
        indexes: list[int] = []
        for item in raw:
            label = " ".join(str(item).split())
            if label not in labels or labels.index(label) in indexes:
                raise ValueError("answer order must list every option once")
            indexes.append(labels.index(label))
        return indexes
    indexes = []
    for item in raw:
        token = str(item if item is not None else "").strip()
        if not token.isdigit():
            raise ValueError("answer order must list every option once")
        indexes.append(int(token))
    return indexes


def _requested_ids(raw: Any) -> list[int]:
    """Parse a question id list. An empty list means use the filter."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("question_ids must be a list")
    ids: list[int] = []
    for item in raw:
        try:
            qid = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("question_ids must be a list") from exc
        if qid > 0 and qid not in ids:
            ids.append(qid)
    return ids


def _load_question(school: Any, library_id: int, question_id: int) -> dict[str, Any] | None:
    """Load one question in this library, including soft-removed rows."""
    with school._lock:
        row = school.conn.execute(
            """
            SELECT q.id, q.bank_id, q.item_type, q.title, q.payload_json,
                   q.bank_scope, q.removed_at,
                   o.stem_text, o.options_json, o.correct_answer
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN library_question_overlays o
                ON o.library_id = b.library_id AND o.question_id = q.id
            WHERE q.id = ? AND b.library_id = ?
            """,
            (int(question_id), int(library_id)),
        ).fetchone()
    return dict(row) if row else None


def _mc_rows_for_scope(
    school: Any,
    *,
    library_id: int,
    class_id: int,
    bank_scope: str,
    kind: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Load MC rows for a module filter through the existing bank search."""
    try:
        from bank_mc_normalize import parse_module_token
    except ImportError:
        from lms.bank_mc_normalize import parse_module_token

    token = str(bank_scope or "M1").strip() or "M1"
    number = parse_module_token(token.upper()) or 1
    found = school.search_bank_scope_mcs(
        int(library_id),
        token,
        int(number),
        "",
        limit=MAX_SOURCE_QUESTIONS,
        class_id=int(class_id),
        kind=kind or None,
        question_type="mc",
        staff_types=True,
    )
    items = list(found.get("items") or [])
    truncated = int(found.get("filtered") or 0) > len(items)
    rows: list[dict[str, Any]] = []
    for item in items:
        try:
            qid = int(item.get("question_id") or 0)
        except (TypeError, ValueError):
            continue
        row = _load_question(school, int(library_id), qid) if qid else None
        if row is not None:
            rows.append(row)
    return rows, truncated


def _insert_draft(
    school: Any,
    *,
    library_id: int,
    class_id: int,
    bank_scope: str,
    draft: dict[str, Any],
    source_ids: list[int],
) -> dict[str, Any]:
    """Insert one draft and return its staff view."""
    try:
        from live_defaults import normalize_scope_token
    except ImportError:
        from lms.live_defaults import normalize_scope_token

    try:
        scope = normalize_scope_token(bank_scope) or "M1"
    except ValueError:
        scope = "M1"
    stamp = _stamp(school)
    with school._lock:
        cur = school.conn.execute(
            """
            INSERT INTO bank_rank_drafts (
                library_id, class_id, bank_scope, stem_text, options_json,
                rank_key_json, source_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                int(library_id),
                int(class_id),
                scope,
                draft["stem"],
                json.dumps(draft["options"]),
                json.dumps(draft["rank_key"]),
                json.dumps([int(qid) for qid in source_ids]),
                stamp,
                stamp,
            ),
        )
        school.conn.commit()
        draft_id = int(cur.lastrowid)
    row = _get_draft(school, int(library_id), draft_id)
    if row is None:
        raise KeyError(f"draft {draft_id}")
    return _draft_view(row)


def _get_draft(school: Any, library_id: int, draft_id: int) -> dict[str, Any] | None:
    """Return one draft row in this library."""
    with school._lock:
        row = school.conn.execute(
            """
            SELECT id, library_id, bank_scope, stem_text, options_json,
                   rank_key_json, source_json, status, question_id, created_at
            FROM bank_rank_drafts
            WHERE id = ? AND library_id = ?
            """,
            (int(draft_id), int(library_id)),
        ).fetchone()
    return dict(row) if row else None


def _draft_view(row: Any) -> dict[str, Any]:
    """JSON view of a draft. Status stays ``draft`` until approval."""
    data = dict(row)
    return {
        "id": int(data["id"]),
        "bank_scope": str(data.get("bank_scope") or ""),
        "stem": str(data.get("stem_text") or ""),
        "options": _loads_list(data.get("options_json")),
        "rank_key": _loads_list(data.get("rank_key_json")),
        "source_count": len(_loads_list(data.get("source_json"))),
        "status": str(data.get("status") or "draft"),
        "created_at": str(data.get("created_at") or ""),
    }


def _loads_list(raw: Any) -> list[Any]:
    """Parse a JSON list, or return an empty list."""
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _stamp(school: Any) -> str:
    """Use the school clock helper when it is present."""
    try:
        from school_db import _now
    except ImportError:
        from lms.school_db import _now

    del school
    return _now()
