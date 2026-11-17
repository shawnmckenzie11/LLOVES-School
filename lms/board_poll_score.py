"""Participation points for an individual whiteboard poll.

A text poll stays 1/1: publish offers 1, and submit records 1. A
whiteboard poll offers the points set when the question was created.
The first stroke or text note earns those points once. A later stroke
does not add more. The teacher can then move that student's earned
points up or down by one from the whiteboard panel.

Removing the question from the deck deletes both the earned points and
the points it offered, for every student. Other questions stay.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

SCORE_KINDS = (
    "item.offered",
    "answer.submit",
    "answer.change",
    "points.student",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS board_poll_credit (
    run_key TEXT NOT NULL,
    prompt_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    question_key TEXT NOT NULL DEFAULT '',
    worth INTEGER NOT NULL DEFAULT 1,
    completed INTEGER NOT NULL DEFAULT 0,
    delta INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_key, prompt_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_board_poll_credit_question
    ON board_poll_credit (run_key, question_key);
"""


def ensure_schema(conn: Any) -> None:
    """Create the credit table. Safe on every boot.

    Args:
        conn: Open school sqlite connection.
    """
    conn.executescript(_SCHEMA)


def _stamp() -> str:
    """Local ISO time without microseconds, matching other school rows."""
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_json(raw: Any) -> dict[str, Any]:
    """Return a dict from stored JSON. Anything else is empty.

    Args:
        raw: JSON text or an already-parsed mapping.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def question_key_for(item: dict[str, Any] | None) -> str:
    """Participation key for one lifecycle row.

    Args:
        item: ``live_session_items`` mapping, or None.
    """
    if not isinstance(item, dict):
        return ""
    placement = str(item.get("placement_key") or "").strip()
    if placement:
        return f"item:{placement}"
    token = str(item.get("item_id") or "").strip()
    if token:
        return f"item:{token}"
    return ""


def _worth_for_prompt(school: Any, prompt_id: int) -> tuple[int | None, dict[str, Any] | None]:
    """Points and lifecycle row for one prompt, when it is an individual board poll.

    Args:
        school: Open school database.
        prompt_id: ``live_session_prompts.id``.

    Returns:
        ``(worth, item)``. ``worth`` is None when this prompt is not an
        individual whiteboard poll. Text polls are None.
    """
    from participation_weight import board_poll_worth

    with school._lock:
        prompt = school.conn.execute(
            "SELECT kind, payload FROM live_session_prompts WHERE id = ?",
            (int(prompt_id),),
        ).fetchone()
        item = school.conn.execute(
            """
            SELECT id, item_id, placement_key, kind, item_json,
                   publish_mode, response_mode, prompt_id
            FROM live_session_items
            WHERE prompt_id = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (int(prompt_id),),
        ).fetchone()
    if prompt is None:
        return None, None
    row = dict(item) if item is not None else None
    mode = ""
    if row is not None:
        mode = str(row.get("response_mode") or row.get("publish_mode") or "").strip().lower()
    if mode not in {"", "individual"}:
        return None, row
    payload = _parse_json(prompt["payload"])
    worth = board_poll_worth(str(prompt["kind"] or ""), payload)
    if worth is None and row is not None:
        worth = board_poll_worth(str(row.get("kind") or ""), _parse_json(row.get("item_json")))
    return worth, row


def _run_key(school: Any, session_id: int) -> str:
    """Current run key for a live session.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
    """
    with school._lock:
        row = school.conn.execute(
            "SELECT run_key FROM live_class_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
    if row is None:
        return ""
    return str(row["run_key"] or "")


def _earned(worth: int, completed: bool, delta: int) -> int:
    """Earned points for one student on one board poll. Never below zero.

    Args:
        worth: Points the question offers.
        completed: True after a stroke or text note.
        delta: Teacher up/down steps.
    """
    base = int(worth) if completed else 0
    return max(0, base + int(delta))


def note_contribution(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    *,
    emit: bool = True,
) -> None:
    """Award full whiteboard-poll points for one student's first contribution.

    A second stroke or note does not add points. Group boards and text
    polls return without writing. The live response row is what End
    copies into Attendance & Participation.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id.
        emit: When True, log one completion event if the log does not
            have one yet. Submit already logs its own event.
    """
    if student_id in (None, ""):
        return
    worth, item = _worth_for_prompt(school, int(prompt_id))
    if worth is None:
        return
    run_key = _run_key(school, int(session_id))
    if not run_key:
        return
    qkey = question_key_for(item)
    now = _stamp()
    already = False
    with school._lock:
        prior = school.conn.execute(
            """
            SELECT completed FROM board_poll_credit
            WHERE run_key = ? AND prompt_id = ? AND student_id = ?
            """,
            (run_key, int(prompt_id), int(student_id)),
        ).fetchone()
        already = prior is not None and int(prior["completed"] or 0) == 1
        if already:
            return
        school.conn.execute(
            """
            INSERT INTO board_poll_credit (
                run_key, prompt_id, student_id, question_key, worth,
                completed, delta, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, 0, ?)
            ON CONFLICT(run_key, prompt_id, student_id) DO UPDATE SET
                completed = 1,
                worth = excluded.worth,
                question_key = CASE
                    WHEN board_poll_credit.question_key = '' THEN excluded.question_key
                    ELSE board_poll_credit.question_key
                END,
                updated_at = excluded.updated_at
            """,
            (run_key, int(prompt_id), int(student_id), qkey, int(worth), now),
        )
        if not already:
            found = school.conn.execute(
                """
                SELECT id FROM live_session_responses
                WHERE prompt_id = ? AND student_id = ?
                """,
                (int(prompt_id), int(student_id)),
            ).fetchone()
            if found is None:
                school.conn.execute(
                    """
                    INSERT INTO live_session_responses (
                        prompt_id, student_id, participant_uuid, response_json,
                        awarded_points, created_at, updated_at
                    ) VALUES (?, ?, NULL, '{}', NULL, ?, ?)
                    """,
                    (int(prompt_id), int(student_id), now, now),
                )
        school.conn.commit()
    if not emit or already:
        return
    store = getattr(school, "engagement", None)
    if store is not None and store.has_event(
        run_key, int(student_id), int(prompt_id), ("answer.submit", "answer.change")
    ):
        return
    school.record_engagement_event(
        "answer.submit",
        session_id=int(session_id),
        student_id=int(student_id),
        item_ref=int(prompt_id),
        payload={"worth": int(worth)},
    )


def adjust(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    delta: int,
) -> dict[str, Any]:
    """Move one student's earned points on this board poll by one step.

    Earned points do not go below zero. The points offered stay the
    question's value.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id.
        delta: ``1`` or ``-1``.

    Returns:
        ``earned``, ``possible``, and the stored step total.

    Raises:
        ValueError: The step is not 1 or -1, or this is not a board poll.
        KeyError: The prompt is not on this session.
    """
    step = int(delta)
    if step not in {-1, 1}:
        raise ValueError("delta must be 1 or -1")
    worth, item = _worth_for_prompt(school, int(prompt_id))
    if worth is None or item is None:
        raise ValueError("This question does not use a whiteboard poll.")
    if int(item.get("id") or 0) and _session_of_item(school, int(item["id"])) != int(session_id):
        raise KeyError("prompt is not on this session")
    run_key = _run_key(school, int(session_id))
    if not run_key:
        raise KeyError("live session has no run")
    qkey = question_key_for(item)
    now = _stamp()
    with school._lock:
        prior = school.conn.execute(
            """
            SELECT worth, completed, delta FROM board_poll_credit
            WHERE run_key = ? AND prompt_id = ? AND student_id = ?
            """,
            (run_key, int(prompt_id), int(student_id)),
        ).fetchone()
        stored_worth = int(worth if prior is None else prior["worth"])
        completed = bool(prior is not None and int(prior["completed"] or 0))
        current = 0 if prior is None else int(prior["delta"] or 0)
        base = stored_worth if completed else 0
        nxt = current + step
        if base + nxt < 0:
            nxt = -base
        school.conn.execute(
            """
            INSERT INTO board_poll_credit (
                run_key, prompt_id, student_id, question_key, worth,
                completed, delta, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_key, prompt_id, student_id) DO UPDATE SET
                delta = excluded.delta,
                question_key = CASE
                    WHEN board_poll_credit.question_key = '' THEN excluded.question_key
                    ELSE board_poll_credit.question_key
                END,
                updated_at = excluded.updated_at
            """,
            (
                run_key,
                int(prompt_id),
                int(student_id),
                qkey,
                stored_worth,
                1 if completed else 0,
                nxt,
                now,
            ),
        )
        school.conn.commit()
    return {
        "ok": True,
        "student_id": int(student_id),
        "earned": _earned(stored_worth, completed, nxt),
        "possible": stored_worth,
        "delta": nxt,
    }


def _session_of_item(school: Any, item_id: int) -> int:
    """Session id that owns a lifecycle row.

    Args:
        school: Open school database.
        item_id: ``live_session_items.id``.
    """
    with school._lock:
        row = school.conn.execute(
            "SELECT live_session_id FROM live_session_items WHERE id = ?",
            (int(item_id),),
        ).fetchone()
    if row is None:
        return 0
    return int(row["live_session_id"] or 0)


def rows_for_prompt(school: Any, session_id: int, prompt_id: int) -> dict[int, dict[str, int]]:
    """Earned and possible points per student for one open board poll.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.

    Returns:
        Student id → ``earned``, ``possible``, ``delta``.
    """
    worth, _item = _worth_for_prompt(school, int(prompt_id))
    if worth is None:
        return {}
    run_key = _run_key(school, int(session_id))
    if not run_key:
        return {}
    with school._lock:
        stored = school.conn.execute(
            """
            SELECT student_id, worth, completed, delta
            FROM board_poll_credit
            WHERE run_key = ? AND prompt_id = ?
            """,
            (run_key, int(prompt_id)),
        ).fetchall()
    out: dict[int, dict[str, int]] = {}
    for row in stored:
        possible = int(row["worth"] if row["worth"] not in (None, "") else worth)
        completed = bool(int(row["completed"] or 0))
        delta = int(row["delta"] or 0)
        out[int(row["student_id"])] = {
            "earned": _earned(possible, completed, delta),
            "possible": possible,
            "delta": delta,
        }
    return out


def attach_participation(
    school: Any,
    session_id: int,
    prompt_id: int,
    tiles: list[dict[str, Any]],
) -> None:
    """Add each tile's per-student points. Names stay on the tile.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        tiles: Teacher gallery tiles, updated in place.
    """
    worth, _item = _worth_for_prompt(school, int(prompt_id))
    if worth is None:
        return
    stored = rows_for_prompt(school, int(session_id), int(prompt_id))
    for tile in tiles:
        ids = []
        for raw in tile.get("student_ids") or []:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        tile["participation"] = [
            {
                "student_id": sid,
                "earned": int((stored.get(sid) or {}).get("earned") or 0),
                "possible": int((stored.get(sid) or {}).get("possible") or worth),
            }
            for sid in ids
        ]


def live_card_bonus(school: Any, session_id: int, student_id: int, events: list[dict[str, Any]]) -> int:
    """Points to add on the live card beyond the event rollup.

    A logged completion already includes the question's points. This
    adds that value only when the log missed it, plus the teacher's
    up/down steps.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        student_id: Roster id.
        events: Engagement events for this run.
    """
    run_key = _run_key(school, int(session_id))
    if not run_key:
        return 0
    with school._lock:
        stored = school.conn.execute(
            """
            SELECT prompt_id, worth, completed, delta
            FROM board_poll_credit
            WHERE run_key = ? AND student_id = ?
            """,
            (run_key, int(student_id)),
        ).fetchall()
    if not stored:
        return 0
    seen: dict[str, int] = {}
    for event in events:
        if int(event.get("student_id") or 0) != int(student_id):
            continue
        if str(event.get("kind") or "") not in {"answer.submit", "answer.change"}:
            continue
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            payload = _parse_json(payload)
        if not isinstance(payload, dict) or "worth" not in payload:
            continue
        ref = str(event.get("item_ref") or "")
        try:
            amount = int(float(payload.get("worth")))
        except (TypeError, ValueError):
            amount = 0
        seen[ref] = max(seen.get(ref, 0), amount)
    bonus = 0
    for row in stored:
        ref = str(int(row["prompt_id"]))
        completed = bool(int(row["completed"] or 0))
        worth = int(row["worth"] or 0)
        if completed and ref not in seen:
            bonus += worth
        bonus += int(row["delta"] or 0)
    return bonus


def shift_fraction(text: str, add: int) -> str:
    """Add earned points onto an ``earned/possible`` label.

    Args:
        text: Current label, such as ``1/5``.
        add: Points to add to the numerator. Negative lowers it.

    Returns:
        The same shape. The numerator does not go below zero.
    """
    raw = str(text or "0/0")
    if "/" not in raw or not add:
        return raw
    left, right = raw.split("/", 1)
    try:
        earned = int(left)
        possible = int(right)
    except ValueError:
        return raw
    return f"{max(0, earned + int(add))}/{possible}"


def credit_question_keys(school: Any, run_key: str) -> set[str]:
    """Question keys a board poll should count, even before a text answer.

    A stroke or a teacher step is enough for the question to enter the
    Attendance & Participation denominator.

    Args:
        school: Open school database.
        run_key: Live run key.
    """
    key = str(run_key or "").strip()
    if not key:
        return set()
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT question_key FROM board_poll_credit
            WHERE run_key = ? AND question_key != ''
              AND (completed = 1 OR delta != 0)
            """,
            (key,),
        ).fetchall()
    return {str(row["question_key"]) for row in rows if row["question_key"]}


def module_adjustment(school: Any, class_id: int, live_module: str, student_id: int) -> int:
    """Teacher up/down steps for one student in one module.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        live_module: Module code such as ``M1``.
        student_id: Roster id.
    """
    from participation_exclusion import sql_exclude_non_counted_participation_run

    mod = str(live_module or "").strip().upper()
    if not mod:
        return 0
    real = sql_exclude_non_counted_participation_run("p.class_id", "p.run_key")
    with school._lock:
        row = school.conn.execute(
            f"""
            SELECT COALESCE(SUM(c.delta), 0) AS n
            FROM board_poll_credit c
            INNER JOIN participation_items p
                ON p.run_key = c.run_key AND p.question_key = c.question_key
            WHERE p.class_id = ? AND p.live_module = ? AND c.student_id = ?
            {real}
            """,
            (int(class_id), mod, int(student_id)),
        ).fetchone()
    return int(row["n"] or 0) if row else 0


def _key_matches(school: Any, question_key: str, item_id: str, placements: set[str]) -> bool:
    """True when a stored participation key is the removed playlist item.

    Args:
        school: Open school database.
        question_key: ``participation_items.question_key``.
        item_id: Playlist item id just removed.
        placements: Placement keys seen on that item.
    """
    token = str(question_key or "")
    bare = token[5:] if token.startswith("item:") else token
    if school._same_live_item_id(bare, item_id) or school._same_live_item_id(token, item_id):
        return True
    for placement in placements:
        if token == f"item:{placement}" or bare == placement:
            return True
    return False


def wipe_removed_question(school: Any, class_id: int, item_id: str) -> None:
    """Drop earned and possible points for one removed deck question.

    Every student loses that question. Other questions stay. Drawings
    and the answer rows stay, so a later import of the same question
    starts blank. Only the score rows go.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        item_id: Playlist item id from Remove completely.
    """
    token = str(item_id or "").strip()
    if not token:
        return
    with school._lock:
        lifecycle = school.conn.execute(
            """
            SELECT i.id, i.prompt_id, i.placement_key, i.item_id,
                   i.live_session_id, s.run_key, s.status
            FROM live_session_items i
            INNER JOIN live_class_sessions s ON s.id = i.live_session_id
            WHERE s.class_id = ?
            """,
            (int(class_id),),
        ).fetchall()
        part_rows = school.conn.execute(
            "SELECT question_key FROM participation_items WHERE class_id = ?",
            (int(class_id),),
        ).fetchall()
    refs: set[str] = set()
    placements: set[str] = set()
    prompt_ids: set[int] = set()
    run_keys: set[str] = set()
    ended: list[int] = []
    for row in lifecycle:
        if not (
            school._same_live_item_id(row["item_id"], token)
            or school._same_live_item_id(row["placement_key"], token)
        ):
            continue
        refs.add(str(row["id"]))
        refs.add(str(row["item_id"] or ""))
        placement = str(row["placement_key"] or "").strip()
        if placement:
            refs.add(placement)
            placements.add(placement)
        if row["prompt_id"] not in (None, ""):
            prompt_ids.add(int(row["prompt_id"]))
            refs.add(str(int(row["prompt_id"])))
        run_key = str(row["run_key"] or "").strip()
        if run_key:
            run_keys.add(run_key)
        if str(row["status"] or "") == "ended":
            ended.append(int(row["live_session_id"]))
    question_keys = {
        str(row["question_key"])
        for row in part_rows
        if _key_matches(school, str(row["question_key"] or ""), token, placements)
    }
    for placement in placements:
        question_keys.add(f"item:{placement}")
    question_keys.add(f"item:{token}")
    question_keys.discard("item:")
    store = getattr(school, "engagement", None)
    if store is not None and hasattr(store, "delete_item_events"):
        store.delete_item_events(int(class_id), sorted(run_keys), sorted(refs), SCORE_KINDS)
        if ended:
            store.delete_rollup_runs(int(class_id), sorted(run_keys))
    with school._lock:
        if prompt_ids:
            marks = ",".join("?" * len(prompt_ids))
            # Leave the answer rows. A later import of the same bank
            # question must not inherit them, and the old prompt keeps
            # the answers it already stored. Scores live in the events
            # and participation tables, which this wipe removes.
            school.conn.execute(
                f"DELETE FROM board_poll_credit WHERE prompt_id IN ({marks})",
                tuple(sorted(prompt_ids)),
            )
        if question_keys:
            marks = ",".join("?" * len(question_keys))
            keys = tuple(sorted(question_keys))
            if run_keys:
                # Bank items are shared across sections; only this class's runs.
                runs = tuple(sorted(run_keys))
                run_marks = ",".join("?" * len(runs))
                school.conn.execute(
                    f"""
                    DELETE FROM board_poll_credit
                    WHERE question_key IN ({marks}) AND run_key IN ({run_marks})
                    """,
                    (*keys, *runs),
                )
            school.conn.execute(
                f"""
                DELETE FROM participation_answers
                WHERE class_id = ? AND question_key IN ({marks})
                """,
                (int(class_id), *keys),
            )
            school.conn.execute(
                f"""
                DELETE FROM participation_items
                WHERE class_id = ? AND question_key IN ({marks})
                """,
                (int(class_id), *keys),
            )
            school.conn.execute(
                f"""
                DELETE FROM group_engagement
                WHERE class_id = ? AND question_key IN ({marks})
                """,
                (int(class_id), *keys),
            )
            school.conn.execute(
                f"""
                DELETE FROM group_engagement_items
                WHERE class_id = ? AND question_key IN ({marks})
                """,
                (int(class_id), *keys),
            )
        school.conn.commit()
    for session_id in sorted(set(ended)):
        try:
            school._rollup_engagement_for_session(int(session_id))
        except Exception:
            logger.exception("engagement rollup rebuild after remove failed")
