"""Per-question whiteboard answers (MCK-31).

A poll can be answered by typing, by a board, or by both. Each published
question gets its own board run key so two questions in one class never
share ink. Submit stores one compact, name-free copy and points every
student row at it. Existing tables are not altered and old rows are not
rewritten. ``answer_with`` lives in the question JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

COPY_CAP_BYTES = 65536
TEXT_CAP = 500
DRAFT_MIN_SECONDS = 2
SIZE_LINE = "Your board is too full to submit. Erase a little and try again."
DOWN_LINE = "That didn't go through. Your drawing is safe, so press Submit again."
CLOSED_LINE = "This question is closed."
EMPTY_LINE = "Add an answer before submitting."
GATE_LINE = "Everyone on the team adds something to the board before Submit."
TEXT_LINE = "That text is too long."
FEEDBACK_CAP = 500
ROUND_DIGITS = 3

_CATCHUP_RE = re.compile(r"^(?P<base>[^~]+)~cu~q(?P<n>[0-9]+)$")
_QUESTION_RE = re.compile(r"^(?P<base>[^~]+)~q(?P<n>[0-9]+)$")
_FRESH_RE = re.compile(r"^(?P<base>[^~]+)~g(?P<n>[0-9]+)$")
_ANSWER_WITH = frozenset({"text", "board", "both"})


class QuestionBoardError(Exception):
    """A question-board request the server refused.

    ``status`` is the HTTP status. ``message`` is the line the student
    sees. No size and no code is included.

    Args:
        message: Calm line for the phone or the teacher.
        status: HTTP status. Default 400.
    """

    def __init__(self, message: str, status: int = 400, *, stale_run: bool = False) -> None:
        """Record the line and the HTTP status.

        Args:
            message: Calm line.
            status: HTTP status.
            stale_run: True when the client named a run this class has left.
        """
        super().__init__(message)
        self.status = int(status)
        self.stale_run = bool(stale_run)


@dataclass(frozen=True)
class BoardRunKey:
    """One parsed board run key.

    Attributes:
        base: Session or catch-up link run key, with no suffix.
        kind: ``session``, ``fresh``, ``question``, or ``catchup``.
        n: Generation, prompt id, or participation item id.
    """

    base: str
    kind: str
    n: int | None


def parse_board_run_key(raw: Any) -> BoardRunKey:
    """Parse a board run key into base, kind, and suffix number.

    Recognised forms are the bare session key, ``<base>~g<n>``,
    ``<base>~q<prompt id>``, and ``<base>~cu~q<item id>``. Anything else
    is refused. ``~g`` and ``~q`` never combine.

    Args:
        raw: Client or stored run key.

    Returns:
        The parsed key.

    Raises:
        ValueError: The key is empty or uses an unknown suffix.
    """
    token = str(raw or "").strip()
    if not token:
        raise ValueError("missing board run key")
    if "~" not in token:
        return BoardRunKey(token, "session", None)
    match = _CATCHUP_RE.match(token)
    if match:
        return BoardRunKey(match.group("base"), "catchup", int(match.group("n")))
    match = _QUESTION_RE.match(token)
    if match:
        return BoardRunKey(match.group("base"), "question", int(match.group("n")))
    match = _FRESH_RE.match(token)
    if match:
        return BoardRunKey(match.group("base"), "fresh", int(match.group("n")))
    raise ValueError("unrecognised board run key")


def assert_board_run_key(raw: Any) -> str:
    """Return a recognised run key, or raise ``ValueError``.

    Args:
        raw: Candidate run key.

    Returns:
        The stripped key.
    """
    parsed = parse_board_run_key(raw)
    if parsed.kind == "session":
        return parsed.base
    if parsed.kind == "fresh":
        return f"{parsed.base}~g{int(parsed.n or 0)}"
    if parsed.kind == "question":
        return f"{parsed.base}~q{int(parsed.n or 0)}"
    return f"{parsed.base}~cu~q{int(parsed.n or 0)}"


def session_lock_key(raw: Any) -> str:
    """Return the ``board_sessions`` row a write must lock.

    A question board locks the live session's base key. A catch-up
    board locks ``<link run key>~cu``. Fresh boards and the session
    board lock themselves. ``~q`` keys never get their own session row.

    Args:
        raw: Run key the write will be stored under.

    Returns:
        The key to lock.
    """
    parsed = parse_board_run_key(raw)
    if parsed.kind == "question":
        return parsed.base
    if parsed.kind == "catchup":
        return f"{parsed.base}~cu"
    if parsed.kind == "fresh":
        return f"{parsed.base}~g{int(parsed.n or 0)}"
    return parsed.base


def question_run_key(base: str, prompt_id: int) -> str:
    """Build the live question board key.

    Args:
        base: Session run key without a suffix.
        prompt_id: ``live_session_prompts.id``.

    Returns:
        ``<base>~q<prompt id>``.
    """
    return f"{str(base).strip()}~q{int(prompt_id)}"


def catchup_run_key(link_run_key: str, item_id: int) -> str:
    """Build the catch-up question board key.

    Args:
        link_run_key: Catch-up link run key.
        item_id: ``participation_items.id``.

    Returns:
        ``<link run key>~cu~q<item id>``.
    """
    return f"{str(link_run_key).strip()}~cu~q{int(item_id)}"


def purge_like_params(base: str) -> tuple[str, str, str, str]:
    """Bound values for ``= base`` and the ``~g`` / ``~q`` / ``~cu`` likes.

    Args:
        base: Session run key without a suffix.

    Returns:
        Four copies of the base, one per comparison.
    """
    token = str(base or "").strip()
    return (token, token, token, token)


def run_key_match_sql(column: str = "run_key") -> str:
    """SQL fragment matching a base key and its recognised suffixes.

    Args:
        column: Column name. Callers pass a fixed identifier.

    Returns:
        A parenthesised predicate with four ``?`` placeholders.
    """
    col = column
    return (
        f"({col} = ? OR {col} LIKE ? || '~g%' "
        f"OR {col} LIKE ? || '~q%' OR {col} LIKE ? || '~cu%')"
    )


def answer_with_of(payload: Any) -> str:
    """Return ``text``, ``board``, or ``both``.

    A missing or unknown value is ``text``, which is how every poll
    already on disk behaves.

    Args:
        payload: Question JSON, or anything else.

    Returns:
        The stored answer mode.
    """
    if not isinstance(payload, dict):
        return "text"
    raw = str(payload.get("answer_with") or "").strip().lower()
    if raw in _ANSWER_WITH:
        return raw
    return "text"


def uses_board(payload: Any) -> bool:
    """True when this question collects a drawing.

    Args:
        payload: Question JSON.
    """
    return answer_with_of(payload) in {"board", "both"}


def image_is_background(payload: Any) -> bool:
    """True when the question image is locked under the ink.

    The default is on once an image and a board are both set. An
    explicit false stays off.

    Args:
        payload: Question JSON.
    """
    if not isinstance(payload, dict) or not uses_board(payload):
        return False
    if not str(payload.get("image_url") or "").strip():
        return False
    flag = payload.get("image_as_board_background")
    if flag is None:
        return True
    return bool(flag)


def _round_coord(value: Any) -> float | None:
    """Round one 0–1 coordinate, or return None when it is not a number.

    Args:
        value: A coordinate from a stored point.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return round(number, ROUND_DIGITS)


def _round_points(points: Any) -> list[list[float]]:
    """Round points and drop consecutive duplicates.

    Args:
        points: Stored ``[x, y]`` pairs.
    """
    if not isinstance(points, list):
        return []
    out: list[list[float]] = []
    for item in points:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        x = _round_coord(item[0])
        y = _round_coord(item[1])
        if x is None or y is None:
            continue
        pair = [x, y]
        if out and out[-1] == pair:
            continue
        out.append(pair)
    return out


def _op_of(row: dict[str, Any]) -> dict[str, Any]:
    """Decode one stored board row's op.

    Args:
        row: A ``board_ops`` row.
    """
    raw = row.get("op")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def compact_saved_copy(rows: list[dict[str, Any]], board_seq: int) -> dict[str, Any]:
    """Compact finished student ink into the saved-copy shape.

    Points are rounded and consecutive duplicates dropped. Name keys are
    removed. Cursors and unfinished strokes are left out. Label text is
    kept exactly as written. Teacher ink is not part of the copy.

    Args:
        rows: Board rows for one board, any order.
        board_seq: Highest sequence included. Later rows are ignored.

    Returns:
        ``{strokes, texts}`` ready to store.
    """
    from board_ops import fold_one_board
    from formative_live_board import scrub_op

    limit = int(board_seq)
    kept: list[dict[str, Any]] = []
    finished: set[str] = set()
    for row in rows:
        try:
            seq = int(row.get("board_seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if seq > limit:
            continue
        kept.append(row)
        op = _op_of(row)
        if str(op.get("type") or "") != "stroke_end":
            continue
        sid = str(op.get("id") or row.get("stroke_id") or "").strip()
        if sid:
            finished.add(sid)
    view = fold_one_board(kept, include_feedback=True)
    strokes: list[dict[str, Any]] = []
    for index, stroke in enumerate(view.get("strokes") or []):
        if not isinstance(stroke, dict):
            continue
        if str(stroke.get("owner") or "") == "teacher":
            continue
        sid = str(stroke.get("id") or "")
        if sid not in finished:
            continue
        points = _round_points(stroke.get("points"))
        if not points:
            continue
        item = scrub_op({"id": f"c{index}", "points": points, "color": stroke.get("color") or ""})
        item.pop("owner", None)
        item.pop("name", None)
        item.pop("codename", None)
        if not str(item.get("color") or "").strip():
            item.pop("color", None)
        strokes.append(item)
    texts: list[dict[str, Any]] = []
    for index, label in enumerate(view.get("texts") or []):
        if not isinstance(label, dict):
            continue
        if str(label.get("owner") or "") == "teacher":
            continue
        body = str(label.get("text") or "")
        if not body:
            continue
        x = _round_coord(label.get("x"))
        y = _round_coord(label.get("y"))
        item = scrub_op(
            {
                "id": f"t{index}",
                "text": body,
                "x": 0 if x is None else x,
                "y": 0 if y is None else y,
                "color": label.get("color") or "",
            }
        )
        item.pop("owner", None)
        item.pop("name", None)
        item.pop("codename", None)
        texts.append(item)
    return {"strokes": strokes, "texts": texts}


def copy_json(copy: dict[str, Any]) -> str:
    """Serialize a compact copy with stable, short JSON.

    Args:
        copy: Compact ``{strokes, texts}``.

    Returns:
        UTF-8 JSON text.
    """
    return json.dumps(copy, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def copy_byte_size(copy: dict[str, Any]) -> int:
    """UTF-8 length of a compact copy.

    Args:
        copy: Compact ``{strokes, texts}``.
    """
    return len(copy_json(copy).encode("utf-8"))


def answer_pointer(
    *,
    answer_with: str,
    text: str,
    board_key: str,
    board_seq: int,
    stroke_count: int,
    submitted_by: int | None,
    copy_run_key: str,
    copy_board_key: str,
) -> dict[str, Any]:
    """Build the answer JSON. Ids only. No strokes and no names.

    Args:
        answer_with: ``text``, ``board``, or ``both``.
        text: Typed part, already capped.
        board_key: ``solo:<id>`` or ``team:<n>``.
        board_seq: Sequence the copy was taken from.
        stroke_count: Finished student strokes in the copy.
        submitted_by: Roster id of the student who pressed Submit.
        copy_run_key: Question or catch-up run key of the one copy.
        copy_board_key: Board key of that copy.

    Returns:
        The object stored in ``response_json`` and ``answer_json``.
    """
    who: int | None
    try:
        who = int(submitted_by) if submitted_by not in (None, "") else None
    except (TypeError, ValueError):
        who = None
    return {
        "answer_with": answer_with if answer_with in _ANSWER_WITH else "text",
        "text": str(text or ""),
        "board_key": str(board_key or ""),
        "board_seq": int(board_seq),
        "stroke_count": int(stroke_count),
        "submitted_by": who,
        "copy_run_key": str(copy_run_key or ""),
        "copy_board_key": str(copy_board_key or ""),
    }


def _loads(raw: Any) -> dict[str, Any]:
    """Parse a JSON object, or return an empty object.

    Args:
        raw: Stored JSON text or dict.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _begin_immediate(school: Any) -> bool:
    """Open a write transaction unless the caller already holds one.

    Committing a caller's transaction breaks migrations that wrap several
    steps in one ``BEGIN``. A nested call joins that transaction instead.

    Args:
        school: Open school database.

    Returns:
        True when this call opened the transaction and must finish it.
    """
    if school.conn.in_transaction:
        return False
    school.conn.execute("BEGIN IMMEDIATE")
    return True


def _finish_write(school: Any, started: bool) -> None:
    """Commit a transaction this call opened.

    Args:
        school: Open school database.
        started: True when ``_begin_immediate`` opened the transaction.
    """
    if started and school.conn.in_transaction:
        school.conn.execute("COMMIT")


def _abort_write(school: Any, started: bool) -> None:
    """Roll back a transaction this call opened.

    Args:
        school: Open school database.
        started: True when ``_begin_immediate`` opened the transaction.
    """
    if started and school.conn.in_transaction:
        school.conn.execute("ROLLBACK")


def _now_stamp(school: Any) -> str:
    """Current timestamp from the school module.

    Args:
        school: Open school database. Unused; kept so callers read clearly.
    """
    del school
    from school_db import _now

    return _now()


def _session_base(school: Any, session_id: int) -> tuple[dict[str, Any], str]:
    """Return the session row and its unsuffixed run key.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.

    Raises:
        QuestionBoardError: The session is missing or has no run key.
    """
    row = school.get_live_session(int(session_id))
    if row is None:
        raise QuestionBoardError("Session not found", 404)
    try:
        base, _gen = school._live_board_base_run_key(int(session_id))
    except Exception as exc:  # noqa: BLE001 - KeyError or a closed run
        raise QuestionBoardError("Session not found", 404) from exc
    token = str(base or "").strip()
    if not token:
        raise QuestionBoardError("Session not found", 404)
    return row, token


def _question_item(school: Any, session_id: int, prompt_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the prompt dict and the lifecycle row for one question.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.

    Raises:
        QuestionBoardError: The prompt is not in this session.
    """
    with school._lock:
        prompt = school.conn.execute(
            "SELECT * FROM live_session_prompts WHERE id = ? AND live_session_id = ?",
            (int(prompt_id), int(session_id)),
        ).fetchone()
        item = school.conn.execute(
            """
            SELECT * FROM live_session_items
            WHERE live_session_id = ? AND prompt_id = ?
            ORDER BY id ASC LIMIT 1
            """,
            (int(session_id), int(prompt_id)),
        ).fetchone()
    if prompt is None or item is None:
        raise QuestionBoardError("Question not found", 404)
    prompt_body = dict(prompt)
    prompt_body["payload"] = _loads(prompt_body.get("payload"))
    item_body = dict(item)
    item_body["item"] = _loads(item_body.get("item_json"))
    return prompt_body, item_body


def _require_open_item(item: dict[str, Any]) -> None:
    """Refuse a write when the question is not published and open.

    Args:
        item: Lifecycle row.

    Raises:
        QuestionBoardError: The question is closed or not published.
    """
    status = str(item.get("status") or "")
    if status == "active":
        return
    if status == "closed":
        raise QuestionBoardError(CLOSED_LINE, 409)
    raise QuestionBoardError(CLOSED_LINE, 409)


def _board_rows(school: Any, run_key: str, board_key: str) -> tuple[list[dict[str, Any]], int]:
    """Read one board's fold source. A store failure is a 503.

    Args:
        school: Open school database.
        run_key: Question or catch-up run key.
        board_key: ``solo:`` or ``team:`` key.

    Returns:
        Rows and the highest sequence, or 0 when the board is empty.
    """
    boards = getattr(school, "boards", None)
    if boards is None:
        raise QuestionBoardError(DOWN_LINE, 503)
    try:
        rows = list(boards.load_keys(str(run_key), [str(board_key)]))
    except Exception as exc:  # noqa: BLE001 - Postgres down or a closed pool
        raise QuestionBoardError(DOWN_LINE, 503) from exc
    seq = 0
    for row in rows:
        try:
            seq = max(seq, int(row.get("board_seq") or 0))
        except (TypeError, ValueError):
            continue
    return rows, seq


def _activity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise student ink versus teacher ink on one board.

    Args:
        rows: Stored rows for one board.
    """
    finished: set[str] = set()
    student_points: set[str] = set()
    label = False
    teacher_seq = 0
    for row in rows:
        op = _op_of(row)
        owner = str(row.get("owner") or op.get("owner") or "")
        try:
            seq = int(row.get("board_seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        kind = str(op.get("type") or "")
        sid = str(op.get("id") or row.get("stroke_id") or "")
        if owner == "teacher":
            if kind in {"stroke_add", "pts_append", "text_upsert", "stroke_end"}:
                teacher_seq = max(teacher_seq, seq)
            continue
        if kind == "stroke_end" and sid:
            finished.add(sid)
        elif kind in {"stroke_add", "pts_append"} and sid:
            student_points.add(sid)
        elif kind == "text_upsert" and str(op.get("text") or "").strip():
            label = True
    return {
        "finished": bool(finished & student_points) or bool(finished),
        "label": label,
        "teacher_seq": teacher_seq,
    }


def _status(
    *,
    activity: dict[str, Any],
    submitted_at: str | None,
    board_seq: int,
    draft_text: str,
) -> str:
    """Return Not started, Drawing, Submitted, or Teacher wrote.

    Teacher ink after the latest submit is Teacher wrote. A later
    resubmit moves the stored sequence forward and returns to Submitted.
    Teacher ink alone never counts as Drawing.

    Args:
        activity: Output of ``_activity``.
        submitted_at: Copy timestamp, or None when nobody has submitted.
        board_seq: Sequence stored on the copy.
        draft_text: Unsent typed text for a team box.
    """
    if submitted_at:
        if int(activity.get("teacher_seq") or 0) > int(board_seq or 0):
            return "Teacher wrote"
        return "Submitted"
    started = bool(activity.get("finished") or activity.get("label") or str(draft_text or "").strip())
    if started:
        return "Drawing"
    return "Not started"


def _cap_text(raw: Any) -> str:
    """Return typed text, or refuse anything over 500 characters.

    Args:
        raw: Client text.

    Raises:
        QuestionBoardError: The text is longer than 500 characters.
    """
    text = str(raw or "")
    if len(text) > TEXT_CAP:
        raise QuestionBoardError(TEXT_LINE, 400)
    return text


def contributor_ids(rows: list[dict[str, Any]]) -> set[int]:
    """Roster ids that left a stroke or a text label on one board.

    Teacher ink does not count. A stroke that was erased is gone from
    the fold, so that student is waiting again. An unfinished stroke
    still counts: they have added to the board.

    Args:
        rows: Stored rows for one board.
    """
    from board_ops import fold_one_board

    view = fold_one_board(rows, include_feedback=False)
    found: set[int] = set()
    for stroke in view.get("strokes") or []:
        if not isinstance(stroke, dict) or not stroke.get("points"):
            continue
        owner = str(stroke.get("owner") or "")
        if owner.isdigit():
            found.add(int(owner))
    for label in view.get("texts") or []:
        if not isinstance(label, dict) or not str(label.get("text") or "").strip():
            continue
        owner = str(label.get("owner") or "")
        if owner.isdigit():
            found.add(int(owner))
    return found


def team_id_for_sort(school: Any, session_id: int, sort_order: int) -> int | None:
    """Game team id whose board key is ``team:<sort_order>``.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        sort_order: Board suffix, including ``0``.
    """
    session = school.get_live_session(int(session_id)) or {}
    class_id = int(session.get("class_id") or 0)
    if not class_id:
        return None
    orders = school._open_game_team_sort_orders(class_id)
    for team_id, sort in orders.items():
        try:
            if int(sort) == int(sort_order):
                return int(team_id)
        except (TypeError, ValueError):
            continue
    return None


def _rows_without_relock(school: Any, run_key: str, board_key: str) -> list[dict[str, Any]]:
    """Read one board without taking the school lock twice.

    Skip already holds ``school._lock`` inside ``BEGIN IMMEDIATE``.
    The board store uses that same lock, so a nested read would stall.

    Args:
        school: Open school database.
        run_key: Question run key.
        board_key: ``team:`` or ``solo:`` key.
    """

    def work() -> list[dict[str, Any]]:
        """Fetch stored ops for one board."""
        fetched = school.conn.execute(
            """
            SELECT board_key, board_seq, op, owner, created_at, stroke_id
            FROM board_ops
            WHERE run_key = ? AND board_key = ?
            ORDER BY board_seq ASC
            """,
            (str(run_key), str(board_key)),
        ).fetchall()
        return [dict(row) for row in fetched]

    if school.conn.in_transaction:
        return work()
    with school._lock:
        return work()


def group_board_blocked(
    school: Any, session_id: int, item: dict[str, Any], team_id: int
) -> list[int]:
    """Present teammates who have not added to this team's board.

    Skipped students are left out. A submitted copy blocks nobody.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        item: Lifecycle row.
        team_id: ``game_teams.id``.
    """
    if team_copy_submitted(school, item, int(team_id)):
        return []
    prompt_id = int(item.get("prompt_id") or 0)
    if not prompt_id:
        return []
    try:
        session, base = _base_run_sharing_lock(school, int(session_id))
    except QuestionBoardError:
        return []
    orders = school._open_game_team_sort_orders(int(session["class_id"]))
    sort = orders.get(int(team_id))
    try:
        sort_n = int(sort) if sort is not None else None
    except (TypeError, ValueError):
        sort_n = None
    if sort_n is None:
        return []
    run_key = question_run_key(base, prompt_id)
    sort = sort_n
    try:
        rows = _rows_without_relock(school, run_key, f"team:{int(sort)}")
    except Exception:  # noqa: BLE001 - a missing table should not stall Skip
        return []
    contributed = contributor_ids(rows)
    return [
        sid
        for sid in school._present_without_skips(item, int(team_id))
        if int(sid) not in contributed
    ]


def _base_run_sharing_lock(school: Any, session_id: int) -> tuple[dict[str, Any], str]:
    """Session row and run key without locking again inside a write.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.

    Raises:
        QuestionBoardError: The session is missing or has no run key.
    """

    def work() -> tuple[dict[str, Any], str]:
        """Read the session on the caller's transaction."""
        row = school.conn.execute(
            "SELECT * FROM live_class_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        if row is None:
            raise QuestionBoardError("Session not found", 404)
        item = dict(row)
        token = str(item.get("run_key") or "").strip()
        if not token:
            raise QuestionBoardError("Session not found", 404)
        return item, token

    if school.conn.in_transaction:
        return work()
    with school._lock:
        return work()


def team_copy_submitted(school: Any, item: dict[str, Any], team_id: int) -> bool:
    """True when this team's question board has been submitted.

    Args:
        school: Open school database.
        item: Lifecycle row.
        team_id: ``game_teams.id``.
    """
    session_id = int(item.get("live_session_id") or 0)
    prompt_id = int(item.get("prompt_id") or 0)
    if not session_id or not prompt_id:
        return False
    try:
        session, base = _base_run_sharing_lock(school, session_id)
    except QuestionBoardError:
        return False
    orders = school._open_game_team_sort_orders(int(session["class_id"]))
    sort = orders.get(int(team_id))
    try:
        sort_n = int(sort) if sort is not None else None
    except (TypeError, ValueError):
        sort_n = None
    if sort_n is None:
        return False
    run_key = question_run_key(base, prompt_id)
    sort = sort_n

    def work() -> bool:
        """True when this team's copy has been submitted."""
        row = school.conn.execute(
            """
            SELECT submitted_at FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(session["class_id"]), run_key, f"team:{int(sort)}"),
        ).fetchone()
        return bool(row and str(row["submitted_at"] or "").strip())

    if school.conn.in_transaction:
        return work()
    with school._lock:
        return work()


def _present_roster_ids(school: Any, session_id: int) -> set[int]:
    """Roster ids still in the live class.

    A teammate with ``left_at`` set is absent. Group questions wait on
    present members only, and the board gate uses that same set.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
    """
    present: set[int] = set()
    for row in school.list_live_session_attendees(int(session_id), present_only=True):
        if row.get("student_id") in (None, ""):
            continue
        try:
            present.add(int(row["student_id"]))
        except (TypeError, ValueError):
            continue
    return present


def _live_award_ids(
    school: Any, item: dict[str, Any], member_ids: list[int]
) -> list[int]:
    """Roster ids who receive a live answer.

    A skipped teammate, and a teammate who has left, finish the question
    alone in catch-up. Writing either a live row would make that catch-up
    a 409. Absent teammates are the same present-member set the gate uses.

    Args:
        school: Open school database.
        item: Lifecycle row.
        member_ids: Roster ids on the board at submit time.
    """
    skipped = school._skipped_ids_for_live_item(int(item["id"]))
    mode = str(item.get("response_mode") or item.get("publish_mode") or "")
    group = mode == "group_shared" or str(item.get("publish_mode") or "") == "group_shared"
    session_id = int(item.get("live_session_id") or 0)
    present = _present_roster_ids(school, session_id) if session_id and group else None
    awarded = []
    for sid in member_ids:
        if int(sid) in skipped:
            continue
        if present is not None and int(sid) not in present:
            continue
        awarded.append(int(sid))
    return awarded


def _gate_members(
    school: Any,
    item: dict[str, Any],
    member_ids: list[int],
    rows: list[dict[str, Any]],
) -> list[int]:
    """Members who still block Submit. Skipped and absent members do not.

    Args:
        school: Open school database.
        item: Lifecycle row.
        member_ids: Roster ids on this board.
        rows: Stored ink for the board.
    """
    skipped = school._skipped_ids_for_live_item(int(item["id"]))
    session_id = int(item.get("live_session_id") or 0)
    present = _present_roster_ids(school, session_id) if session_id else None
    contributed = contributor_ids(rows)
    blocked = []
    for sid in member_ids:
        if present is not None and int(sid) not in present:
            continue
        if int(sid) in skipped:
            continue
        if int(sid) not in contributed:
            blocked.append(int(sid))
    return blocked


def _member_ids(school: Any, session_id: int, *, group: bool, student_id: int, board_key: str) -> list[int]:
    """Roster ids who share this board at submit time.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        group: True for one shared team board.
        student_id: The student who pressed Submit.
        board_key: ``team:<n>`` when ``group`` is true.
    """
    if not group:
        return [int(student_id)]
    try:
        order = int(str(board_key).split(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise QuestionBoardError("Pick a team before submitting.", 400) from exc
    rows = school.list_live_session_team_members(int(session_id), order)
    ids = []
    for row in rows:
        try:
            ids.append(int(row["student_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    if int(student_id) not in ids:
        ids.append(int(student_id))
    return ids


def _resolve_board_key(
    school: Any,
    session_id: int,
    student_id: int,
    item: dict[str, Any],
    *,
    catchup: bool = False,
) -> tuple[str, bool]:
    """Return ``(board_key, group)`` for this student and question.

    Catch-up is always a solo board. Guests have no roster id and cannot
    submit. A group question uses ``team:<sort order>``.

    Args:
        school: Open school database.
        session_id: Live session, ignored for the catch-up key itself.
        student_id: Roster id.
        item: Lifecycle row.
        catchup: Force a solo board.

    Raises:
        QuestionBoardError: The student has no board.
    """
    del session_id
    if student_id in (None, ""):
        raise QuestionBoardError("Join with your class name before submitting.", 403)
    try:
        sid = int(student_id)
    except (TypeError, ValueError) as exc:
        raise QuestionBoardError("Join with your class name before submitting.", 403) from exc
    if catchup:
        return f"solo:{sid}", False
    mode = str(item.get("response_mode") or item.get("publish_mode") or "")
    group = mode == "group_shared" or str(item.get("publish_mode") or "") == "group_shared"
    if not group:
        return f"solo:{sid}", False
    rows = school.list_live_session_team_members(int(item["live_session_id"]), None)
    order = None
    for row in rows:
        try:
            if int(row["student_id"]) != sid:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if row.get("team_sort_order") is not None:
            order = int(row["team_sort_order"])
            break
    if order is None:
        raise QuestionBoardError("Join a team before submitting.", 400)
    return f"team:{order}", True


def _upsert_copy(
    school: Any,
    *,
    class_id: int,
    run_key: str,
    board_key: str,
    strokes_json: str,
    byte_size: int,
    board_seq: int,
    text: str,
    submitted_by: int,
    member_ids: list[int],
    now: str,
) -> bool:
    """Insert or replace one copy when the new sequence is not older.

    The statement is an atomic upsert. A slower request whose fold is
    behind the stored sequence does not replace the ink. Caller holds
    the write transaction.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        run_key: Question or catch-up run key.
        board_key: ``solo:`` or ``team:`` key.
        strokes_json: Compact JSON.
        byte_size: UTF-8 length of that JSON.
        board_seq: Sequence the fold reached.
        text: Typed part.
        submitted_by: Roster id.
        member_ids: Roster ids on the board at submit time.
        now: Timestamp.

    Returns:
        True when this request's sequence is the one now stored.
    """
    school.conn.execute(
        """
        INSERT INTO board_answer_copies (
            class_id, run_key, board_key, strokes_json, byte_size, board_seq,
            text, draft_text, submitted_by, member_ids_json, submitted_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
        ON CONFLICT(class_id, run_key, board_key) DO UPDATE SET
            strokes_json = excluded.strokes_json,
            byte_size = excluded.byte_size,
            board_seq = excluded.board_seq,
            text = excluded.text,
            submitted_by = excluded.submitted_by,
            member_ids_json = excluded.member_ids_json,
            submitted_at = COALESCE(board_answer_copies.submitted_at, excluded.submitted_at),
            updated_at = excluded.updated_at
        WHERE excluded.board_seq >= board_answer_copies.board_seq
        """,
        (
            int(class_id),
            str(run_key),
            str(board_key),
            strokes_json,
            int(byte_size),
            int(board_seq),
            str(text or ""),
            int(submitted_by),
            json.dumps([int(sid) for sid in member_ids]),
            now,
            now,
        ),
    )
    row = school.conn.execute(
        """
        SELECT board_seq FROM board_answer_copies
        WHERE class_id = ? AND run_key = ? AND board_key = ?
        """,
        (int(class_id), str(run_key), str(board_key)),
    ).fetchone()
    if row is None:
        return False
    return int(row["board_seq"] or 0) == int(board_seq)


def _write_response_rows(
    school: Any,
    *,
    prompt_id: int,
    pointer: dict[str, Any],
    member_ids: list[int],
    now: str,
) -> None:
    """Upsert one live response per teammate. Caller holds the transaction.

    Args:
        school: Open school database.
        prompt_id: ``live_session_prompts.id``.
        pointer: Answer JSON. The same object for every teammate.
        member_ids: Roster ids who receive a row.
        now: Timestamp.
    """
    body = json.dumps(pointer)
    for sid in member_ids:
        existing = school.conn.execute(
            """
            SELECT id FROM live_session_responses
            WHERE prompt_id = ? AND student_id = ?
            """,
            (int(prompt_id), int(sid)),
        ).fetchone()
        if existing is None:
            school.conn.execute(
                """
                INSERT INTO live_session_responses (
                    prompt_id, student_id, participant_uuid, response_json,
                    awarded_points, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, NULL, ?, ?)
                """,
                (int(prompt_id), int(sid), body, now, now),
            )
        else:
            school.conn.execute(
                """
                UPDATE live_session_responses
                SET response_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (body, now, int(existing["id"])),
            )


def submit_live_board(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    text: Any = "",
) -> dict[str, Any]:
    """Submit one question board and write a row for every student on it.

    Order: read the fold, compact, strip names, check the cap, then write
    the copy and the answer rows in one transaction. An older fold cannot
    replace a newer copy. A closed question changes nothing.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id of the student who pressed Submit.
        text: Typed part. Empty for a board-only question.

    Returns:
        Pointer JSON plus ``updated`` and ``group``.

    Raises:
        QuestionBoardError: Closed, empty, over the cap, or the store is down.
    """
    _session, base = _session_base(school, int(session_id))
    if str(_session.get("status") or "") != "active":
        raise QuestionBoardError(CLOSED_LINE, 409)
    _prompt, item = _question_item(school, int(session_id), int(prompt_id))
    payload = _prompt.get("payload") if isinstance(_prompt.get("payload"), dict) else {}
    if not payload:
        payload = item.get("item") if isinstance(item.get("item"), dict) else {}
    if not uses_board(payload):
        raise QuestionBoardError("This question does not use a board.", 400)
    _require_open_item(item)
    typed = _cap_text(text)
    run_key = question_run_key(base, int(prompt_id))
    board_key, group = _resolve_board_key(
        school, int(session_id), int(student_id), item
    )
    rows, seq = _board_rows(school, run_key, board_key)
    copy = compact_saved_copy(rows, seq)
    size = copy_byte_size(copy)
    if size > COPY_CAP_BYTES:
        raise QuestionBoardError(SIZE_LINE, 413)
    strokes = copy.get("strokes") or []
    labels = copy.get("texts") or []
    if not strokes and not labels and not typed.strip():
        raise QuestionBoardError(EMPTY_LINE, 400)
    mode = answer_with_of(payload)
    if mode == "board" and not strokes and not labels:
        raise QuestionBoardError(EMPTY_LINE, 400)
    members = _member_ids(
        school, int(session_id), group=group, student_id=int(student_id), board_key=board_key
    )
    if group and _gate_members(school, item, members, rows):
        raise QuestionBoardError(GATE_LINE, 409)
    now = _now_stamp(school)
    blob = copy_json(copy)
    prior = None
    with school._lock:
        prior = school.conn.execute(
            """
            SELECT submitted_at, board_seq FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(_session["class_id"]), run_key, board_key),
        ).fetchone()
        started = _begin_immediate(school)
        try:
            fresh = school.conn.execute(
                "SELECT status FROM live_session_items WHERE id = ?",
                (int(item["id"]),),
            ).fetchone()
            if fresh is None or str(fresh["status"] or "") != "active":
                _abort_write(school, started)
                raise QuestionBoardError(CLOSED_LINE, 409)
            landed = _upsert_copy(
                school,
                class_id=int(_session["class_id"]),
                run_key=run_key,
                board_key=board_key,
                strokes_json=blob,
                byte_size=size,
                board_seq=seq,
                text=typed,
                submitted_by=int(student_id),
                member_ids=members,
                now=now,
            )
            pointer = answer_pointer(
                answer_with=mode,
                text=typed,
                board_key=board_key,
                board_seq=seq,
                stroke_count=len(strokes),
                submitted_by=int(student_id),
                copy_run_key=run_key,
                copy_board_key=board_key,
            )
            if landed:
                _write_response_rows(
                    school,
                    prompt_id=int(prompt_id),
                    pointer=pointer,
                    member_ids=_live_award_ids(school, item, members),
                    now=now,
                )
            _finish_write(school, started)
        except QuestionBoardError:
            raise
        except Exception:
            _abort_write(school, started)
            raise
    updated = bool(prior and str(prior["submitted_at"] or "").strip())
    return {
        "ok": True,
        "updated": updated and landed,
        "landed": landed,
        "group": group,
        "answer": pointer,
        "submitted_by": int(student_id),
    }


def save_team_draft(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    text: Any,
) -> dict[str, Any]:
    """Store the shared team text box, at most once every two seconds.

    An empty question that was never submitted keeps ``submitted_at``
    null so End and Quit can drop the draft. Over-long text is refused
    and the stored draft is left as it was.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id of the typist.
        text: Current text box.
    """
    from datetime import datetime

    session, base = _session_base(school, int(session_id))
    _prompt, item = _question_item(school, int(session_id), int(prompt_id))
    _require_open_item(item)
    typed = _cap_text(text)
    board_key, group = _resolve_board_key(
        school, int(session_id), int(student_id), item
    )
    if not group:
        return {"ok": True, "saved": False}
    run_key = question_run_key(base, int(prompt_id))
    now = _now_stamp(school)
    with school._lock:
        row = school.conn.execute(
            """
            SELECT id, updated_at, submitted_at FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(session["class_id"]), run_key, board_key),
        ).fetchone()
        if row is not None and not str(row["submitted_at"] or "").strip():
            try:
                previous = datetime.fromisoformat(str(row["updated_at"] or ""))
                current = datetime.fromisoformat(now)
                if (current - previous).total_seconds() < DRAFT_MIN_SECONDS:
                    return {"ok": True, "saved": False}
            except ValueError:
                pass
        if row is None:
            school.conn.execute(
                """
                INSERT INTO board_answer_copies (
                    class_id, run_key, board_key, strokes_json, byte_size, board_seq,
                    text, draft_text, submitted_by, member_ids_json, submitted_at, updated_at
                ) VALUES (?, ?, ?, '{}', 0, 0, '', ?, NULL, '[]', NULL, ?)
                """,
                (int(session["class_id"]), run_key, board_key, typed, now),
            )
        else:
            school.conn.execute(
                """
                UPDATE board_answer_copies
                SET draft_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (typed, now, int(row["id"])),
            )
    return {"ok": True, "saved": True}


def _cards_for_question(
    school: Any, session_id: int, item: dict[str, Any]
) -> list[dict[str, Any]]:
    """One tile per joined student, or one tile per team with a member.

    Guests (no roster id) are omitted. Away students who joined stay.
    The test student stays. Names come from the roster at render time.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        item: Lifecycle row.
    """
    session = school.get_live_session(int(session_id)) or {}
    class_id = int(session.get("class_id") or 0)
    names = school._student_display_names(class_id)
    group = str(item.get("response_mode") or "") == "group_shared" or str(
        item.get("publish_mode") or ""
    ) == "group_shared"
    attendees = [
        row
        for row in school._formative_attendees(int(session_id))
        if row.get("student_id") not in (None, "")
    ]
    joined = {int(row["student_id"]) for row in attendees}
    if not group:
        cards = []
        for row in attendees:
            sid = int(row["student_id"])
            cards.append(
                {
                    "board_key": f"solo:{sid}",
                    "name": names.get(sid, ""),
                    "member_names": [],
                    "student_ids": [sid],
                    "away": bool(str(row.get("left_at") or "").strip()),
                }
            )
        return cards
    team_names = school._team_display_names(class_id)
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in school.list_live_session_team_members(int(session_id), None):
        if row.get("team_sort_order") is None:
            continue
        try:
            sid = int(row["student_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if sid not in joined:
            continue
        buckets.setdefault(int(row["team_sort_order"]), []).append(row)
    cards = []
    for order in sorted(buckets):
        members = buckets[order]
        member_ids = [int(row["student_id"]) for row in members]
        cards.append(
            {
                "board_key": f"team:{order}",
                "name": team_names.get(order, ""),
                "member_names": [names.get(sid, "") for sid in member_ids],
                "student_ids": member_ids,
                "team_sort_order": order,
                "away": all(bool(str(row.get("left_at") or "").strip()) for row in members),
            }
        )
    return cards


def question_board_legend(
    school: Any, session_id: int, card: dict[str, Any]
) -> list[dict[str, str]]:
    """Colour chips for one question tile.

    Names stay here. MCK-280 strips them on the student projection and
    keeps them on the teacher panel.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        card: Tile from ``_cards_for_question``.
    """
    from formative_live_board import legend_entries

    return legend_entries(
        school,
        int(session_id),
        {
            "board_key": str(card.get("board_key") or ""),
            "team_sort_order": card.get("team_sort_order"),
            "student_ids": list(card.get("student_ids") or []),
        },
    )


def question_dashboard(
    school: Any,
    session_id: int,
    prompt_id: int,
    cursors: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Teacher tile list for one published board question.

    Tiles use the live gallery's since-seq shape so the existing teacher
    board can paint them. Status counts stay on the tile.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        cursors: Board key to the last sequence the gallery has applied.

    Returns:
        Header counts, filter counts, and one tile per board.
    """
    from formative_live_board import build_gallery_tile

    session, base = _session_base(school, int(session_id))
    if str(session.get("status") or "") != "active":
        return {"ok": True, "ended": True, "visible": False, "tiles": []}
    prompt, item = _question_item(school, int(session_id), int(prompt_id))
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    if not uses_board(payload):
        payload = item.get("item") if isinstance(item.get("item"), dict) else payload
    if not uses_board(payload):
        raise QuestionBoardError("This question does not use a board.", 400)
    mode = answer_with_of(payload)
    run_key = question_run_key(base, int(prompt_id))
    cards = _cards_for_question(school, int(session_id), item)
    keys = [str(card["board_key"]) for card in cards]
    supplied = cursors or {}
    query = {key: int(supplied.get(key) or 0) for key in keys}
    try:
        deltas = school._formative_deltas(
            int(session_id), query, include_feedback=True, run_key=run_key
        )
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError(DOWN_LINE, 503) from exc
    by_key: dict[str, list[dict[str, Any]]] = {}
    if keys:
        try:
            for row in school.boards.load_keys(run_key, keys):
                by_key.setdefault(str(row.get("board_key") or ""), []).append(row)
        except Exception as exc:  # noqa: BLE001
            raise QuestionBoardError(DOWN_LINE, 503) from exc
    names = school._student_display_names(int(session["class_id"]))
    copies: dict[str, dict[str, Any]] = {}
    with school._lock:
        stored = school.conn.execute(
            """
            SELECT * FROM board_answer_copies
            WHERE class_id = ? AND run_key = ?
            """,
            (int(session["class_id"]), run_key),
        ).fetchall()
    for row in stored:
        copies[str(row["board_key"])] = dict(row)
    tiles = []
    for card in cards:
        key = str(card["board_key"])
        activity = _activity(by_key.get(key) or [])
        copy = copies.get(key) or {}
        submitted_at = str(copy.get("submitted_at") or "").strip() or None
        draft = str(copy.get("draft_text") or "")
        if submitted_at:
            draft = ""
        status = _status(
            activity=activity,
            submitted_at=submitted_at,
            board_seq=int(copy.get("board_seq") or 0),
            draft_text=draft,
        )
        who = copy.get("submitted_by")
        try:
            who_id = int(who) if who not in (None, "") else None
        except (TypeError, ValueError):
            who_id = None
        text = str(copy.get("text") or "")
        if not submitted_at:
            text = draft
        tile = build_gallery_tile(
            board_key=key,
            name=str(card.get("name") or ""),
            team_sort_order=card.get("team_sort_order"),
            away=bool(card.get("away")),
            delta=deltas.get(key),
            student_ids=list(card.get("student_ids") or []),
        )
        tile["status"] = status
        tile["text"] = text
        tile["member_names"] = list(card.get("member_names") or [])
        tile["submitted_by"] = who_id
        tile["submitted_by_name"] = names.get(who_id, "") if who_id is not None else ""
        tile["teacher_wrote"] = status == "Teacher wrote"
        tile["feedback"] = str(copy.get("feedback_text") or "")
        tile["run_key"] = run_key
        sort_order = card.get("team_sort_order")
        if sort_order is not None:
            team_id = team_id_for_sort(school, int(session_id), int(sort_order))
            if team_id is not None:
                tile["question_waits"] = school._question_wait_rows(
                    item, int(team_id), int(session["class_id"])
                )
                tile["live_item_id"] = int(item["id"])
                tile["team_id"] = int(team_id)
        tiles.append(tile)
    order = {"Not started": 0, "Drawing": 1, "Submitted": 2, "Teacher wrote": 3}
    tiles.sort(key=lambda tile: (order.get(str(tile["status"]), 9), str(tile["name"]).casefold()))
    counts = {"Not started": 0, "Drawing": 0, "Submitted": 0, "Teacher wrote": 0}
    for tile in tiles:
        counts[str(tile["status"])] = counts.get(str(tile["status"]), 0) + 1
    label = "Typing and whiteboard" if mode == "both" else "Whiteboard"
    stem = str(payload.get("text") or payload.get("prompt") or "").strip()
    submitted = counts["Submitted"] + counts["Teacher wrote"]
    teacher = school.live_session_teacher_state_payload(int(session_id))
    projected = ""
    if str(teacher.get("whiteboard_projected_run") or "") == run_key:
        projected = str(teacher.get("whiteboard_projected_board") or "")
    return {
        "ok": True,
        "visible": True,
        "ended": False,
        "run_key": run_key,
        "answer_with": mode,
        "label": label,
        "stem": stem,
        "context": stem,
        "total": len(tiles),
        "submitted": submitted,
        "drawing": counts["Drawing"],
        "summary": f"{submitted} of {len(tiles)} submitted · {counts['Drawing']} drawing",
        "projected_board": projected,
        "filters": [
            {"id": "all", "label": f"All {len(tiles)}", "count": len(tiles)},
            {
                "id": "Not started",
                "label": f"Not started {counts['Not started']}",
                "count": counts["Not started"],
            },
            {"id": "Drawing", "label": f"Drawing {counts['Drawing']}", "count": counts["Drawing"]},
            {
                "id": "Submitted",
                "label": f"Submitted {counts['Submitted']}",
                "count": counts["Submitted"],
            },
            {
                "id": "Teacher wrote",
                "label": f"Teacher wrote {counts['Teacher wrote']}",
                "count": counts["Teacher wrote"],
            },
        ],
        "tiles": tiles,
        "image_url": str(payload.get("image_url") or "") if image_is_background(payload) else "",
    }


def _legend_card(board_key: str, student_id: int) -> dict[str, Any]:
    """Card shape ``question_board_legend`` reads for one board key.

    Args:
        board_key: ``solo:<id>`` or ``team:<n>``.
        student_id: Roster id of the viewer. Used for a solo card.
    """
    key = str(board_key or "")
    if key.startswith("team:"):
        try:
            order = int(key.split(":", 1)[1])
        except (TypeError, ValueError):
            order = None
        return {"board_key": key, "team_sort_order": order, "student_ids": []}
    return {"board_key": key, "team_sort_order": None, "student_ids": [int(student_id)]}


def _since_view(
    school: Any, run_key: str, board_key: str, since: int, view: dict[str, Any]
) -> dict[str, Any]:
    """Since-seq fields for one board, using the folded view on a snapshot.

    Args:
        school: Open school database.
        run_key: Question or catch-up run key.
        board_key: Board the caller may read.
        since: Last sequence the caller has applied.
        view: Full fold, used when the gap is a snapshot.
    """
    delta = school.boards.since(run_key, board_key, int(since))
    snapshot = bool(delta.get("snapshot")) or int(since) <= 0
    body = {
        "board_seq": int(delta.get("board_seq") or 0),
        "snapshot": snapshot,
        "ops": [] if snapshot else list(delta.get("ops") or []),
    }
    if snapshot:
        body["canvas_view"] = view
    return body


def read_student_question_board(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    since: int | None = None,
) -> dict[str, Any]:
    """Whole board for one student, including their own ink.

    After Close the phone is read-only and shows the saved copy when one
    exists. Teacher ink is included immediately. ``since`` selects the
    same since-seq shape the live whiteboard poll uses.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id.
        since: Last sequence the phone has applied. Omit for a snapshot.
    """
    from board_ops import fold_one_board

    session, base = _session_base(school, int(session_id))
    prompt, item = _question_item(school, int(session_id), int(prompt_id))
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    board_key, group = _resolve_board_key(
        school, int(session_id), int(student_id), item
    )
    run_key = question_run_key(base, int(prompt_id))
    closed = str(item.get("status") or "") != "active"
    rows, seq = _board_rows(school, run_key, board_key)
    view = fold_one_board(rows, include_feedback=True)
    with school._lock:
        copy = school.conn.execute(
            """
            SELECT * FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(session["class_id"]), run_key, board_key),
        ).fetchone()
    saved = dict(copy) if copy is not None else {}
    submitted_at = str(saved.get("submitted_at") or "").strip()
    if closed and submitted_at:
        view = _loads(saved.get("strokes_json"))
        if "strokes" not in view:
            view = {"strokes": [], "texts": []}
    names = school._student_display_names(int(session["class_id"]))
    who = saved.get("submitted_by")
    try:
        who_id = int(who) if who not in (None, "") else None
    except (TypeError, ValueError):
        who_id = None
    who_name = names.get(who_id, "") if who_id is not None else ""
    line = ""
    if submitted_at and group and who_name:
        line = f"Submitted by {who_name}. Anyone on your team can update it."
        if str(saved.get("updated_at") or "") != submitted_at:
            line = f"Updated by {who_name}."
    elif submitted_at:
        line = "Submitted. You can still make changes."
    body = {
        "ok": True,
        "run_key": run_key,
        "board_key": board_key,
        "board_seq": seq,
        "snapshot": True,
        "canvas_view": view,
        "read_only": closed or str(session.get("status") or "") != "active",
        "group": group,
        "viewer_id": int(student_id),
        "answer_with": answer_with_of(payload),
        "text": str(saved.get("text") or saved.get("draft_text") or ""),
        "status_line": line,
        "image_url": str(payload.get("image_url") or "") if image_is_background(payload) else "",
        "stem": str(payload.get("text") or payload.get("prompt") or ""),
        "feedback": str(saved.get("feedback_text") or ""),
        "submit_ready": (not group) or not _gate_members(
            school,
            item,
            _member_ids(
                school,
                int(session_id),
                group=True,
                student_id=int(student_id),
                board_key=board_key,
            ),
            rows,
        ),
        "formative": {
            "members": question_board_legend(
                school, int(session_id), _legend_card(board_key, int(student_id))
            ),
            "always_show_names": False,
        },
    }
    if since is not None and not (closed and submitted_at):
        body.update(_since_view(school, run_key, board_key, int(since), view))
    return body


def append_question_board_ops(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    ops: list[dict[str, Any]],
    *,
    client_batch_id: str | None = None,
    owner: str = "",
    board_key: str = "",
) -> dict[str, Any]:
    """Append ink on this question's board and nowhere else.

    The server chooses a student's board key. A teacher passes the tile
    key. A closed question is a 409 and stores nothing.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id. Unused when ``owner`` is ``teacher``.
        ops: Client ops. Names are stripped before they are stored.
        client_batch_id: Retry token.
        owner: ``teacher`` or a roster id string.
        board_key: Tile key for a teacher write.
    """
    from formative_live_board import scrub_op

    _session, base = _session_base(school, int(session_id))
    _prompt, item = _question_item(school, int(session_id), int(prompt_id))
    _require_open_item(item)
    if owner == "teacher":
        if not str(board_key).startswith(("solo:", "team:")):
            raise QuestionBoardError("Board not found", 404)
    else:
        board_key, _group = _resolve_board_key(
            school, int(session_id), int(student_id), item
        )
    run_key = question_run_key(base, int(prompt_id))
    cleaned = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        item_op = scrub_op(op)
        item_op.pop("layer", None)
        cleaned.append(item_op)
    who = "teacher" if owner == "teacher" else str(int(student_id))
    return school.append_live_board_ops(
        int(session_id),
        board_key,
        owner=who,
        ops=cleaned,
        client_batch_id=client_batch_id,
        claimed_run_key=run_key,
    )


def body_is_presence(body: Any) -> bool:
    """True when a write is a live-whiteboard presence batch, not an op list.

    The gallery posts ``ops``. Phones post points, text, or stroke_remove,
    the same body ``/api/student/canvas-presence`` accepts.

    Args:
        body: JSON object from the client.
    """
    if not isinstance(body, dict):
        return False
    ops = body.get("ops")
    if isinstance(ops, list) and ops:
        return False
    if str(body.get("text_id") or "").strip():
        return True
    kind = str(body.get("op") or body.get("type") or "")
    if kind == "stroke_remove":
        return True
    return isinstance(body.get("points"), list)


def _require_claimed_run(claimed: Any, current: str) -> str:
    """Refuse a run key that is not the board the server is writing.

    Args:
        claimed: Run key from the client. Empty means the current key.
        current: Run key this write belongs on.

    Returns:
        ``current``.

    Raises:
        QuestionBoardError: The client named a different run. Status 409.
    """
    token = str(claimed or "").strip()
    if token and token != current:
        raise QuestionBoardError("That board was restarted.", 409, stale_run=True)
    return current


def _presence_delta(
    school: Any, run_key: str, board_key: str, since: Any
) -> dict[str, Any]:
    """Since-seq reply after a presence write.

    Args:
        school: Open school database.
        run_key: Generation the write landed on.
        board_key: Board that was written.
        since: Cursor from the client. Missing means a snapshot.
    """
    from board_ops import fold_one_board

    if since in (None, ""):
        rows = list(school.boards.load_keys(run_key, [board_key]))
        view = fold_one_board(rows, include_feedback=True)
        seq = 0
        for row in rows:
            try:
                seq = max(seq, int(row.get("board_seq") or 0))
            except (TypeError, ValueError):
                continue
        return {
            "ok": True,
            "run_key": run_key,
            "board_key": board_key,
            "board_seq": seq,
            "snapshot": True,
            "canvas_view": view,
            "ops_since": [],
        }
    try:
        cursor = int(since)
    except (TypeError, ValueError):
        cursor = 0
    delta = school.boards.since(run_key, board_key, cursor)
    snapshot = bool(delta.get("snapshot"))
    view = None
    if snapshot:
        rows = list(school.boards.load_keys(run_key, [board_key]))
        view = fold_one_board(rows, include_feedback=True)
    return {
        "ok": True,
        "run_key": run_key,
        "board_key": board_key,
        "board_seq": int(delta.get("board_seq") or 0),
        "snapshot": snapshot,
        "canvas_view": view,
        "ops_since": [] if snapshot else list(delta.get("ops") or []),
    }


def apply_question_presence(
    school: Any,
    session_id: int,
    prompt_id: int,
    student_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Store one presence batch on this question's board.

    Points, text, and undo use the same board store as the live
    whiteboard. The question run key keeps this ink off the deck board.
    Names are not copied onto the stroke.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id.
        body: Presence JSON.
    """
    from board_ops import BoardOpRejected, BoardSessionClosed

    _session, base = _session_base(school, int(session_id))
    _prompt, item = _question_item(school, int(session_id), int(prompt_id))
    _require_open_item(item)
    board_key, _group = _resolve_board_key(
        school, int(session_id), int(student_id), item
    )
    run_key = _require_claimed_run(body.get("run_key"), question_run_key(base, int(prompt_id)))
    boards = getattr(school, "boards", None)
    if boards is None:
        raise QuestionBoardError(DOWN_LINE, 503)
    who = str(int(student_id))
    color = school.ink_color_for_new_stroke(int(session_id), board_key, who)
    batch = str(body.get("client_batch_id") or "").strip() or None
    try:
        text_id = str(body.get("text_id") or "").strip()
        if text_id:
            boards.append_text(
                run_key,
                board_key,
                owner=who,
                name="",
                text_id=text_id,
                text=str(body.get("text") if body.get("text") is not None else ""),
                x=body.get("x"),
                y=body.get("y"),
                color=color,
                team_id=None,
                client_batch_id=batch,
            )
        elif str(body.get("op") or body.get("type") or "") == "stroke_remove":
            boards.append_remove(
                run_key,
                board_key,
                owner=who,
                stroke_id=str(body.get("stroke_id") or body.get("id") or ""),
                client_batch_id=batch,
            )
        else:
            points = body.get("points") if isinstance(body.get("points"), list) else None
            boards.append_ink(
                run_key,
                board_key,
                owner=who,
                name="",
                color=color,
                team_id=None,
                stroke_id=str(body.get("stroke_id") or "") or None,
                points=points,
                point=body.get("point"),
                ended=bool(body.get("ended")),
                batched=isinstance(points, list),
                x=body.get("x"),
                y=body.get("y"),
                client_batch_id=batch,
            )
    except BoardSessionClosed as exc:
        raise QuestionBoardError(CLOSED_LINE, 409) from exc
    except BoardOpRejected as exc:
        raise QuestionBoardError("That stroke is not yours.", 403) from exc
    except QuestionBoardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError(DOWN_LINE, 503) from exc
    return _presence_delta(school, run_key, board_key, body.get("since"))


def question_open_board(
    school: Any, session_id: int, prompt_id: int, board_key: str, *, since: int = 0
) -> dict[str, Any]:
    """Teacher side panel for one question board.

    The payload matches the live gallery panel: name, legend, and a
    since-seq delta. Names stay here. The student projection strips them.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        board_key: ``solo:<id>`` or ``team:<n>``.
        since: Last sequence the panel has applied.
    """
    from board_ops import fold_one_board
    from formative_live_board import gallery_board_key, public_board_payload

    _session, base = _session_base(school, int(session_id))
    _prompt, item = _question_item(school, int(session_id), int(prompt_id))
    try:
        key = gallery_board_key(board_key)
    except ValueError as exc:
        raise QuestionBoardError("Board not found", 404) from exc
    card = next(
        (row for row in _cards_for_question(school, int(session_id), item) if str(row.get("board_key") or "") == key),
        None,
    )
    if card is None:
        raise QuestionBoardError("Board not found", 404)
    run_key = question_run_key(base, int(prompt_id))
    rows = list(school.boards.load_keys(run_key, [key]))
    view = fold_one_board(rows, include_feedback=True)
    delta = school.boards.since(run_key, key, int(since))
    snapshot = bool(delta.get("snapshot")) or int(since) <= 0
    if snapshot and not delta.get("canvas_view"):
        delta_view = view
    else:
        delta_view = delta.get("canvas_view") if isinstance(delta.get("canvas_view"), dict) else view
    return public_board_payload(
        {
            "ok": True,
            "board_key": key,
            "name": str(card.get("name") or ""),
            "run_key": run_key,
            "board_seq": int(delta.get("board_seq") or 0),
            "snapshot": snapshot,
            "ops": [] if snapshot else list(delta.get("ops") or []),
            "canvas_view": delta_view if snapshot else None,
            "legend": question_board_legend(school, int(session_id), card),
        }
    )


def clear_question_board(
    school: Any, session_id: int, prompt_id: int, board_key: str
) -> dict[str, Any]:
    """Clear student ink on one question board. Teacher ink stays.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        board_key: Tile key.
    """
    from formative_live_board import gallery_board_key

    _session, base = _session_base(school, int(session_id))
    _prompt, item = _question_item(school, int(session_id), int(prompt_id))
    _require_open_item(item)
    try:
        key = gallery_board_key(board_key)
    except ValueError as exc:
        raise QuestionBoardError("Board not found", 404) from exc
    found = any(
        str(row.get("board_key") or "") == key
        for row in _cards_for_question(school, int(session_id), item)
    )
    if not found:
        raise QuestionBoardError("Board not found", 404)
    return school.clear_whiteboard_board(
        int(session_id), key, run_key=question_run_key(base, int(prompt_id))
    )


def _board_write(school: Any, work) -> None:
    """Run board SQL without ending a school transaction on the shared connection.

    Sqlite keeps board rows on the school connection. Starting a board
    transaction there commits the caller's ``BEGIN``. When that transaction
    is already open, the statements join it. Postgres uses its own
    connection and can keep its own transaction.

    Args:
        school: Open school database.
        work: Callable taking an ``execute(sql, params)`` function.

    Raises:
        QuestionBoardError: The board store is missing or the write failed.
    """
    boards = getattr(school, "boards", None)
    if boards is None:
        raise QuestionBoardError("Couldn't update that board. Try again.", 503)
    shared = getattr(boards, "conn", None) is school.conn
    try:
        if shared and school.conn.in_transaction:
            work(school.conn.execute)
            return
        boards._transaction(lambda exec_: work(exec_))
    except QuestionBoardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError("Couldn't update that board. Try again.", 503) from exc


def _purge_prefix_sql(exec_: Any, base: str) -> None:
    """Close ``base`` and delete ``<base>~q`` ink. Caller owns the transaction.

    Args:
        exec_: ``execute(sql, params)`` for the board connection.
        base: Session run key or a catch-up lock key.
    """
    sid = str(base)
    pattern = sid + "~q%"
    exec_(
        """
        INSERT INTO board_sessions (run_key, closed)
        VALUES (?, 1)
        ON CONFLICT (run_key) DO UPDATE SET closed = 1
        """,
        (sid,),
    )
    exec_("DELETE FROM board_ops WHERE run_key LIKE ?", (pattern,))
    exec_("DELETE FROM board_counters WHERE run_key LIKE ?", (pattern,))
    exec_("DELETE FROM board_batches WHERE run_key LIKE ?", (pattern,))
    exec_("DELETE FROM board_sessions WHERE run_key LIKE ?", (pattern,))


def purge_question_ink(school: Any, base: str, prompt_id: int) -> None:
    """Delete one question's ink on both stores. Copies are separate.

    A store failure raises so the caller can refuse the removal instead
    of reusing the prompt id over leftover ink.

    Args:
        school: Open school database.
        base: Session run key.
        prompt_id: ``live_session_prompts.id``.

    Raises:
        QuestionBoardError: The board store could not delete the ink.
    """
    run_key = question_run_key(base, int(prompt_id))

    def work(exec_: Any) -> None:
        """Delete this question's ink without leaving a session row."""
        exec_("DELETE FROM board_ops WHERE run_key = ?", (run_key,))
        exec_("DELETE FROM board_counters WHERE run_key = ?", (run_key,))
        exec_("DELETE FROM board_batches WHERE run_key = ?", (run_key,))
        exec_("DELETE FROM board_sessions WHERE run_key = ?", (run_key,))

    try:
        _board_write(school, work)
    except QuestionBoardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError(
            "Couldn't remove that question's board. Try again.", 503
        ) from exc


def delete_question_copies(school: Any, class_id: int, run_key: str) -> None:
    """Delete saved copies and drafts for one question run key.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        run_key: Exact question or catch-up run key.
    """
    with school._lock:
        school.conn.execute(
            """
            DELETE FROM board_answer_copies
            WHERE class_id = ? AND run_key = ?
            """,
            (int(class_id), str(run_key)),
        )


def purge_run_copies(school: Any, session_id: int, *, keep_submitted: bool) -> None:
    """Delete this run's copies. End keeps submitted rows and drops drafts.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        keep_submitted: True on End. False on Quit and test-class delete.
    """
    try:
        session, base = _session_base(school, int(session_id))
    except QuestionBoardError:
        return
    clause = run_key_match_sql("run_key")
    params = purge_like_params(base)
    sql = f"DELETE FROM board_answer_copies WHERE class_id = ? AND {clause}"
    values: list[Any] = [int(session["class_id"]), *params]
    if keep_submitted:
        sql += " AND submitted_at IS NULL AND TRIM(COALESCE(feedback_text, '')) = ''"
    with school._lock:
        school.conn.execute(sql, values)


def count_run_copies(school: Any, class_id: int, base: str) -> int:
    """Count copies and drafts still stored for one session run key.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        base: Session run key.
    """
    clause = run_key_match_sql("run_key")
    with school._lock:
        row = school.conn.execute(
            f"SELECT COUNT(*) AS n FROM board_answer_copies WHERE class_id = ? AND {clause}",
            (int(class_id), *purge_like_params(base)),
        ).fetchone()
    return int(row["n"] or 0) if row is not None else 0


def sync_catchup_board_gate(school: Any, link_run_key: str, *, open_board: bool) -> None:
    """Open or close the catch-up board lock, and purge ink on close.

    Saved copies stay. The board starts empty when the link is opened
    again because the ink was purged.

    Args:
        school: Open school database.
        link_run_key: Catch-up link run key.
        open_board: True to mark ``<run key>~cu`` open.
    """
    lock = f"{str(link_run_key).strip()}~cu"

    def work(exec_: Any) -> None:
        """Open the catch-up lock, or purge its question ink and close it."""
        if open_board:
            exec_(
                """
                INSERT INTO board_sessions (run_key, closed)
                VALUES (?, 0)
                ON CONFLICT (run_key) DO UPDATE SET closed = 0
                """,
                (lock,),
            )
            return
        _purge_prefix_sql(exec_, lock)

    try:
        _board_write(school, work)
    except QuestionBoardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError(
            "Couldn't update that catch-up board. Try again.", 503
        ) from exc


def discard_catchup_boards(school: Any, class_id: int, link_run_key: str) -> None:
    """Remove catch-up ink, copies, and catch-up answer rows for one link.

    Live answers are not updated. Only rows whose JSON points at a
    ``~cu`` copy are removed.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        link_run_key: Catch-up link run key.
    """
    token = str(link_run_key or "").strip()
    if not token:
        return
    lock = f"{token}~cu"

    def work(exec_: Any) -> None:
        """Drop catch-up ink and the lock row."""
        _purge_prefix_sql(exec_, lock)
        exec_("DELETE FROM board_sessions WHERE run_key = ?", (lock,))

    try:
        _board_write(school, work)
    except QuestionBoardError as exc:
        if getattr(school, "boards", None) is not None:
            raise
        del exc
    with school._lock:
        school.conn.execute(
            """
            DELETE FROM board_answer_copies
            WHERE class_id = ? AND run_key LIKE ? || '~cu%'
            """,
            (int(class_id), token),
        )
        school.conn.execute(
            """
            DELETE FROM participation_answers
            WHERE class_id = ? AND run_key = ? AND source = 'catchup'
              AND answer_json LIKE '%~cu~q%'
            """,
            (int(class_id), token),
        )


def submit_catchup_board(
    school: Any,
    token: str,
    student_id: int,
    item_id: int,
    text: Any = "",
) -> dict[str, Any]:
    """Submit a solo catch-up board without touching a live answer.

    Args:
        school: Open school database.
        token: Catch-up link token. Not logged.
        student_id: Roster id from the catch-up login.
        item_id: ``participation_items.id``.
        text: Typed part.

    Raises:
        QuestionBoardError: The link is closed, the answer was given live,
            or the drawing is over the cap.
    """
    from catchup_landing import link_still_open

    link = school.get_catchup_link(str(token or ""))
    if link is None or not link_still_open(school, {**link, "class_id": int(link["class_id"])}):
        raise QuestionBoardError(CLOSED_LINE, 409)
    typed = _cap_text(text)
    with school._lock:
        item = school.conn.execute(
            """
            SELECT * FROM participation_items
            WHERE id = ? AND class_id = ? AND run_key = ?
            """,
            (int(item_id), int(link["class_id"]), str(link["run_key"])),
        ).fetchone()
    if item is None:
        raise QuestionBoardError("Question not found", 404)
    payload = _loads(item["item_json"])
    if not uses_board(payload):
        raise QuestionBoardError("This question does not use a board.", 400)
    question_key = str(item["question_key"])
    with school._lock:
        existing = school.conn.execute(
            """
            SELECT id, source, answer_json FROM participation_answers
            WHERE class_id = ? AND run_key = ? AND question_key = ? AND student_id = ?
            """,
            (int(link["class_id"]), str(link["run_key"]), question_key, int(student_id)),
        ).fetchone()
    if existing is not None and str(existing["source"] or "") == "live":
        raise QuestionBoardError("That answer was saved in class.", 409)
    run_key = catchup_run_key(str(link["run_key"]), int(item_id))
    board_key = f"solo:{int(student_id)}"
    rows, seq = _board_rows(school, run_key, board_key)
    copy = compact_saved_copy(rows, seq)
    size = copy_byte_size(copy)
    if size > COPY_CAP_BYTES:
        raise QuestionBoardError(SIZE_LINE, 413)
    strokes = copy.get("strokes") or []
    labels = copy.get("texts") or []
    if not strokes and not labels and not typed.strip():
        raise QuestionBoardError(EMPTY_LINE, 400)
    now = _now_stamp(school)
    pointer = answer_pointer(
        answer_with=answer_with_of(payload),
        text=typed,
        board_key=board_key,
        board_seq=seq,
        stroke_count=len(strokes),
        submitted_by=int(student_id),
        copy_run_key=run_key,
        copy_board_key=board_key,
    )
    body = json.dumps(pointer)
    with school._lock:
        started = _begin_immediate(school)
        try:
            again = school.conn.execute(
                """
                SELECT id, source FROM participation_answers
                WHERE class_id = ? AND run_key = ? AND question_key = ? AND student_id = ?
                """,
                (int(link["class_id"]), str(link["run_key"]), question_key, int(student_id)),
            ).fetchone()
            if again is not None and str(again["source"] or "") == "live":
                _abort_write(school, started)
                raise QuestionBoardError("That answer was saved in class.", 409)
            landed = _upsert_copy(
                school,
                class_id=int(link["class_id"]),
                run_key=run_key,
                board_key=board_key,
                strokes_json=copy_json(copy),
                byte_size=size,
                board_seq=seq,
                text=typed,
                submitted_by=int(student_id),
                member_ids=[int(student_id)],
                now=now,
            )
            if landed:
                if again is None:
                    school.conn.execute(
                        """
                        INSERT INTO participation_answers (
                            class_id, run_key, live_module, question_key, student_id,
                            source, answer_json, answered_at
                        ) VALUES (?, ?, ?, ?, ?, 'catchup', ?, ?)
                        """,
                        (
                            int(link["class_id"]),
                            str(link["run_key"]),
                            str(link["live_module"] or ""),
                            question_key,
                            int(student_id),
                            body,
                            now,
                        ),
                    )
                else:
                    school.conn.execute(
                        """
                        UPDATE participation_answers
                        SET answer_json = ?, answered_at = ?, source = 'catchup'
                        WHERE id = ? AND source = 'catchup'
                        """,
                        (body, now, int(again["id"])),
                    )
            _finish_write(school, started)
        except QuestionBoardError:
            raise
        except Exception:
            _abort_write(school, started)
            raise
    return {"ok": True, "answer": pointer, "status_line": "Submitted. You can still make changes."}


def _solo_phone(student_id: int) -> dict[str, Any]:
    """Viewer id and a nameless colour chip for one solo phone.

    Args:
        student_id: Roster id.
    """
    from live_canvas import cursor_color_for

    who = str(int(student_id))
    return {
        "viewer_id": int(student_id),
        "formative": {
            "members": [{"id": who, "name": "", "color": cursor_color_for(who)}],
            "always_show_names": False,
        },
    }


def read_catchup_board(
    school: Any,
    token: str,
    student_id: int,
    item_id: int,
    since: int | None = None,
) -> dict[str, Any]:
    """Solo catch-up board, or the saved live drawing when one exists.

    Args:
        school: Open school database.
        token: Catch-up link token. Not logged.
        student_id: Roster id.
        item_id: ``participation_items.id``.
        since: Last sequence the phone has applied. Omit for a snapshot.
    """
    from board_ops import fold_one_board
    from catchup_landing import link_still_open

    link = school.get_catchup_link(str(token or ""))
    if link is None:
        raise QuestionBoardError("Question not found", 404)
    with school._lock:
        item = school.conn.execute(
            """
            SELECT * FROM participation_items
            WHERE id = ? AND class_id = ? AND run_key = ?
            """,
            (int(item_id), int(link["class_id"]), str(link["run_key"])),
        ).fetchone()
        existing = None
        if item is not None:
            existing = school.conn.execute(
                """
                SELECT source, answer_json FROM participation_answers
                WHERE class_id = ? AND run_key = ? AND question_key = ? AND student_id = ?
                """,
                (
                    int(link["class_id"]),
                    str(link["run_key"]),
                    str(item["question_key"]),
                    int(student_id),
                ),
            ).fetchone()
    if item is None:
        raise QuestionBoardError("Question not found", 404)
    open_link = link_still_open(school, {**dict(link), "class_id": int(link["class_id"])})
    live = existing is not None and str(existing["source"] or "") == "live"
    if live:
        pointer = _loads(existing["answer_json"])
        copy_run = str(pointer.get("copy_run_key") or "")
        copy_board = str(pointer.get("copy_board_key") or "")
        with school._lock:
            copy = school.conn.execute(
                """
                SELECT strokes_json, text, feedback_text FROM board_answer_copies
                WHERE class_id = ? AND run_key = ? AND board_key = ?
                """,
                (int(link["class_id"]), copy_run, copy_board),
            ).fetchone()
        view = _loads(copy["strokes_json"]) if copy is not None else {"strokes": [], "texts": []}
        return {
            "ok": True,
            "read_only": True,
            "snapshot": True,
            "canvas_view": view,
            "text": str(pointer.get("text") or ""),
            "feedback": str(copy["feedback_text"] or "") if copy is not None else "",
            "status_line": "",
            "run_key": copy_run,
            "board_key": f"solo:{int(student_id)}",
            **_solo_phone(int(student_id)),
        }
    run_key = catchup_run_key(str(link["run_key"]), int(item_id))
    board_key = f"solo:{int(student_id)}"
    if not open_link:
        with school._lock:
            copy = school.conn.execute(
                """
                SELECT strokes_json, text, feedback_text FROM board_answer_copies
                WHERE class_id = ? AND run_key = ? AND board_key = ?
                """,
                (int(link["class_id"]), run_key, board_key),
            ).fetchone()
        view = _loads(copy["strokes_json"]) if copy is not None else {"strokes": [], "texts": []}
        return {
            "ok": True,
            "read_only": True,
            "snapshot": True,
            "canvas_view": view,
            "text": str(copy["text"] or "") if copy is not None else "",
            "feedback": str(copy["feedback_text"] or "") if copy is not None else "",
            "status_line": "Submitted. You can still make changes." if copy is not None else "",
            "run_key": run_key,
            "board_key": board_key,
            **_solo_phone(int(student_id)),
        }
    rows, seq = _board_rows(school, run_key, board_key)
    with school._lock:
        copy = school.conn.execute(
            """
            SELECT text, submitted_at, feedback_text FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(link["class_id"]), run_key, board_key),
        ).fetchone()
    submitted = copy is not None and str(copy["submitted_at"] or "").strip()
    view = fold_one_board(rows, include_feedback=False)
    body = {
        "ok": True,
        "read_only": False,
        "snapshot": True,
        "canvas_view": view,
        "board_seq": seq,
        "text": str(copy["text"] or "") if submitted else "",
        "feedback": str(copy["feedback_text"] or "") if copy is not None else "",
        "status_line": "Submitted. You can still make changes." if submitted else "",
        "run_key": run_key,
        "board_key": board_key,
        **_solo_phone(int(student_id)),
    }
    if since is not None:
        body.update(_since_view(school, run_key, board_key, int(since), view))
    return body


def append_catchup_board_ops(
    school: Any,
    token: str,
    student_id: int,
    item_id: int,
    ops: list[dict[str, Any]],
    *,
    client_batch_id: str | None = None,
) -> dict[str, Any]:
    """Append ink on this student's catch-up board only.

    The key is forced to ``solo:<id>`` under ``<link>~cu~q<item id>``.
    A closed link stores nothing.

    Args:
        school: Open school database.
        token: Catch-up link token. Not logged.
        student_id: Roster id.
        item_id: ``participation_items.id``.
        ops: Client ops.
        client_batch_id: Retry token.
    """
    from catchup_landing import link_still_open
    from formative_live_board import scrub_op

    link = school.get_catchup_link(str(token or ""))
    if link is None or not link_still_open(school, {**link, "class_id": int(link["class_id"])}):
        raise QuestionBoardError(CLOSED_LINE, 409)
    with school._lock:
        item = school.conn.execute(
            """
            SELECT question_key FROM participation_items
            WHERE id = ? AND class_id = ? AND run_key = ?
            """,
            (int(item_id), int(link["class_id"]), str(link["run_key"])),
        ).fetchone()
        existing = None
        if item is not None:
            existing = school.conn.execute(
                """
                SELECT source FROM participation_answers
                WHERE class_id = ? AND run_key = ? AND question_key = ? AND student_id = ?
                """,
                (
                    int(link["class_id"]),
                    str(link["run_key"]),
                    str(item["question_key"]),
                    int(student_id),
                ),
            ).fetchone()
    if existing is not None and str(existing["source"] or "") == "live":
        raise QuestionBoardError("That answer was saved in class.", 409)
    run_key = catchup_run_key(str(link["run_key"]), int(item_id))
    board_key = f"solo:{int(student_id)}"
    cleaned = [scrub_op(op) for op in ops if isinstance(op, dict)]
    boards = getattr(school, "boards", None)
    if boards is None:
        raise QuestionBoardError(DOWN_LINE, 503)
    from board_ops import BoardSessionClosed

    try:
        assert_board_run_key(run_key)
        return boards.append_ops(
            run_key,
            board_key,
            owner=str(int(student_id)),
            ops=cleaned,
            client_batch_id=client_batch_id,
        )
    except QuestionBoardError:
        raise
    except BoardSessionClosed as exc:
        raise QuestionBoardError(CLOSED_LINE, 409) from exc
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError(DOWN_LINE, 503) from exc


def apply_catchup_presence(
    school: Any,
    token: str,
    student_id: int,
    item_id: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Store one presence batch on a catch-up board.

    The board is ``solo:<id>`` under the ``~cu`` key. A live answer is
    left untouched. Names are not stored.

    Args:
        school: Open school database.
        token: Catch-up link token. Not logged.
        student_id: Roster id.
        item_id: ``participation_items.id``.
        body: Presence JSON.
    """
    from board_ops import BoardOpRejected, BoardSessionClosed
    from catchup_landing import link_still_open
    from live_canvas import cursor_color_for

    link = school.get_catchup_link(str(token or ""))
    if link is None or not link_still_open(school, {**link, "class_id": int(link["class_id"])}):
        raise QuestionBoardError(CLOSED_LINE, 409)
    with school._lock:
        item = school.conn.execute(
            """
            SELECT question_key FROM participation_items
            WHERE id = ? AND class_id = ? AND run_key = ?
            """,
            (int(item_id), int(link["class_id"]), str(link["run_key"])),
        ).fetchone()
        existing = None
        if item is not None:
            existing = school.conn.execute(
                """
                SELECT source FROM participation_answers
                WHERE class_id = ? AND run_key = ? AND question_key = ? AND student_id = ?
                """,
                (
                    int(link["class_id"]),
                    str(link["run_key"]),
                    str(item["question_key"]),
                    int(student_id),
                ),
            ).fetchone()
    if item is None:
        raise QuestionBoardError("Question not found", 404)
    if existing is not None and str(existing["source"] or "") == "live":
        raise QuestionBoardError("That answer was saved in class.", 409)
    run_key = _require_claimed_run(
        body.get("run_key"), catchup_run_key(str(link["run_key"]), int(item_id))
    )
    board_key = f"solo:{int(student_id)}"
    boards = getattr(school, "boards", None)
    if boards is None:
        raise QuestionBoardError(DOWN_LINE, 503)
    who = str(int(student_id))
    color = cursor_color_for(who)
    batch = str(body.get("client_batch_id") or "").strip() or None
    try:
        assert_board_run_key(run_key)
        text_id = str(body.get("text_id") or "").strip()
        if text_id:
            boards.append_text(
                run_key,
                board_key,
                owner=who,
                name="",
                text_id=text_id,
                text=str(body.get("text") if body.get("text") is not None else ""),
                x=body.get("x"),
                y=body.get("y"),
                color=color,
                team_id=None,
                client_batch_id=batch,
            )
        elif str(body.get("op") or body.get("type") or "") == "stroke_remove":
            boards.append_remove(
                run_key,
                board_key,
                owner=who,
                stroke_id=str(body.get("stroke_id") or body.get("id") or ""),
                client_batch_id=batch,
            )
        else:
            points = body.get("points") if isinstance(body.get("points"), list) else None
            boards.append_ink(
                run_key,
                board_key,
                owner=who,
                name="",
                color=color,
                team_id=None,
                stroke_id=str(body.get("stroke_id") or "") or None,
                points=points,
                point=body.get("point"),
                ended=bool(body.get("ended")),
                batched=isinstance(points, list),
                x=body.get("x"),
                y=body.get("y"),
                client_batch_id=batch,
            )
    except BoardSessionClosed as exc:
        raise QuestionBoardError(CLOSED_LINE, 409) from exc
    except BoardOpRejected as exc:
        raise QuestionBoardError("That stroke is not yours.", 403) from exc
    except QuestionBoardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise QuestionBoardError(DOWN_LINE, 503) from exc
    return _presence_delta(school, run_key, board_key, body.get("since"))


def copy_group_participation(school: Any, session_id: int) -> None:
    """Copy group board-poll answers into participation. Caller holds the lock.

    Individual board answers are already inserted as empty JSON. This
    fills those from the response pointer and adds group-shared board
    polls, which End used to skip.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
    """
    session = school.conn.execute(
        "SELECT * FROM live_class_sessions WHERE id = ?",
        (int(session_id),),
    ).fetchone()
    if session is None:
        return
    run_key = str(session["run_key"] or "")
    if not run_key:
        return
    class_id = int(session["class_id"])
    module = ""
    try:
        teacher = json.loads(session["teacher_state_json"] or "{}")
    except (TypeError, ValueError):
        teacher = {}
    if isinstance(teacher, dict):
        module = str(teacher.get("live_module") or "")
    now = _now_stamp(school)
    school.conn.execute(
        """
        INSERT OR IGNORE INTO participation_items (
            class_id, run_key, live_module, live_slot, question_key, item_json,
            prompt_kind, offered_at
        )
        SELECT ?, ?, ?, ?, 'item:' || i.placement_key,
               CASE WHEN TRIM(COALESCE(pr.payload, '')) NOT IN ('', '{}')
                    THEN pr.payload ELSE i.item_json END,
               COALESCE(pr.kind, ''), COALESCE(i.published_at, ?)
        FROM live_session_items i
        LEFT JOIN live_session_prompts pr ON pr.id = i.prompt_id
        WHERE i.live_session_id = ? AND i.published_at IS NOT NULL
          AND i.response_mode = 'group_shared'
          AND (
            json_extract(pr.payload, '$.answer_with') IN ('board', 'both')
            OR json_extract(i.item_json, '$.answer_with') IN ('board', 'both')
          )
          AND EXISTS (
            SELECT 1 FROM live_session_responses r
            WHERE r.prompt_id = i.prompt_id AND r.student_id IS NOT NULL
          )
        """,
        (
            class_id,
            run_key,
            module,
            str(teacher.get("live_slot") or "") if isinstance(teacher, dict) else "",
            now,
            int(session_id),
        ),
    )
    school.conn.execute(
        """
        INSERT OR IGNORE INTO participation_answers (
            class_id, run_key, live_module, question_key, student_id, source,
            answer_json, answered_at
        )
        SELECT ?, ?, ?, 'item:' || i.placement_key, r.student_id, 'live',
               r.response_json, r.updated_at
        FROM live_session_responses r
        INNER JOIN live_session_items i ON i.prompt_id = r.prompt_id
        LEFT JOIN live_session_prompts pr ON pr.id = i.prompt_id
        WHERE i.live_session_id = ? AND r.student_id IS NOT NULL
          AND i.response_mode = 'group_shared'
          AND (
            json_extract(pr.payload, '$.answer_with') IN ('board', 'both')
            OR json_extract(i.item_json, '$.answer_with') IN ('board', 'both')
          )
          AND NOT EXISTS (
            SELECT 1 FROM live_question_skips s
            WHERE s.live_session_id = i.live_session_id
              AND s.student_id = r.student_id
              AND s.question_key = ('item:' || i.placement_key)
          )
        """,
        (class_id, run_key, module, int(session_id)),
    )
    school.conn.execute(
        """
        UPDATE participation_answers
        SET answer_json = (
            SELECT r.response_json
            FROM live_session_responses r
            INNER JOIN live_session_items i ON i.prompt_id = r.prompt_id
            LEFT JOIN live_session_prompts pr ON pr.id = i.prompt_id
            WHERE i.live_session_id = ?
              AND r.student_id = participation_answers.student_id
              AND ('item:' || i.placement_key) = participation_answers.question_key
              AND (
                json_extract(pr.payload, '$.answer_with') IN ('board', 'both')
                OR json_extract(i.item_json, '$.answer_with') IN ('board', 'both')
              )
            LIMIT 1
        )
        WHERE class_id = ? AND run_key = ? AND source = 'live'
          AND EXISTS (
            SELECT 1
            FROM live_session_responses r
            INNER JOIN live_session_items i ON i.prompt_id = r.prompt_id
            LEFT JOIN live_session_prompts pr ON pr.id = i.prompt_id
            WHERE i.live_session_id = ?
              AND r.student_id = participation_answers.student_id
              AND ('item:' || i.placement_key) = participation_answers.question_key
              AND (
                json_extract(pr.payload, '$.answer_with') IN ('board', 'both')
                OR json_extract(i.item_json, '$.answer_with') IN ('board', 'both')
              )
          AND NOT EXISTS (
            SELECT 1 FROM live_question_skips s
            WHERE s.live_session_id = i.live_session_id
              AND s.student_id = r.student_id
              AND s.question_key = ('item:' || i.placement_key)
          )
          )
        """,
        (int(session_id), class_id, run_key, int(session_id)),
    )


def drop_unreferenced_copies(school: Any) -> None:
    """Delete submitted copies nothing still points at.

    Used after the snapshot retention prune. A copy stays while a live
    response, a snapshot, or a participation row names its run key.

    Args:
        school: Open school database.
    """
    with school._lock:
        school.conn.execute(
            """
            DELETE FROM board_answer_copies
            WHERE submitted_at IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM live_session_responses r
                WHERE r.response_json LIKE '%' || board_answer_copies.run_key || '%'
              )
              AND NOT EXISTS (
                SELECT 1 FROM live_result_snapshots s
                WHERE s.class_id = board_answer_copies.class_id
                  AND s.answer_json LIKE '%' || board_answer_copies.run_key || '%'
              )
              AND NOT EXISTS (
                SELECT 1 FROM participation_answers a
                WHERE a.class_id = board_answer_copies.class_id
                  AND a.answer_json LIKE '%' || board_answer_copies.run_key || '%'
              )
            """
        )


def _cap_feedback(raw: Any) -> str:
    """Return a teacher note, or refuse anything over 500 characters.

    Args:
        raw: Note from the teacher panel.

    Raises:
        QuestionBoardError: The note is longer than 500 characters.
    """
    text = str(raw or "")
    if len(text) > FEEDBACK_CAP:
        raise QuestionBoardError(TEXT_LINE, 400)
    return text


def save_copy_feedback(
    school: Any, class_id: int, run_key: str, board_key: str, text: Any
) -> dict[str, Any]:
    """Store one teacher note on the compact copy for a board.

    The drawing is unchanged. A missing copy gets an empty stroke row so
    the note has a place to live. End keeps that row. Quit still deletes it.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        run_key: Question or catch-up copy run key.
        board_key: ``solo:`` or ``team:`` key.
        text: Teacher note. Empty clears it.
    """
    note = _cap_feedback(text)
    key = str(board_key or "")
    if not key.startswith(("solo:", "team:")):
        raise QuestionBoardError("Question not found", 404)
    try:
        assert_board_run_key(run_key)
    except ValueError as exc:
        raise QuestionBoardError("Question not found", 404) from exc
    now = _now_stamp(school)
    with school._lock:
        row = school.conn.execute(
            """
            SELECT id FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(class_id), str(run_key), key),
        ).fetchone()
        if row is None:
            school.conn.execute(
                """
                INSERT INTO board_answer_copies (
                    class_id, run_key, board_key, strokes_json, byte_size, board_seq,
                    text, draft_text, submitted_by, member_ids_json, submitted_at,
                    updated_at, feedback_text
                ) VALUES (?, ?, ?, '{}', 2, 0, '', '', NULL, '[]', NULL, ?, ?)
                """,
                (int(class_id), str(run_key), key, now, note),
            )
        else:
            school.conn.execute(
                """
                UPDATE board_answer_copies
                SET feedback_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (note, now, int(row["id"])),
            )
        if school.conn.in_transaction:
            school.conn.commit()
    return {"ok": True, "feedback": note}


def student_live_feedback(
    school: Any, session_id: int, prompt_id: int, student_id: int
) -> str:
    """Teacher note for the board this student is on, or empty.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        prompt_id: ``live_session_prompts.id``.
        student_id: Roster id.
    """
    try:
        session, base = _session_base(school, int(session_id))
        _prompt, item = _question_item(school, int(session_id), int(prompt_id))
        board_key, _group = _resolve_board_key(
            school, int(session_id), int(student_id), item
        )
    except QuestionBoardError:
        return ""
    run_key = question_run_key(base, int(prompt_id))
    with school._lock:
        row = school.conn.execute(
            """
            SELECT feedback_text FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(session["class_id"]), run_key, board_key),
        ).fetchone()
    if row is None:
        return ""
    return str(row["feedback_text"] or "")


def _archive_tile(
    school: Any,
    *,
    class_id: int,
    copy_run: str,
    board_key: str,
    names: dict[int, str],
    source: str,
) -> dict[str, Any] | None:
    """One read-only gallery tile from a stored copy.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        copy_run: Run key of the copy.
        board_key: ``solo:`` or ``team:`` key.
        names: Roster id to codename.
        source: ``live`` or ``catchup``.
    """
    with school._lock:
        row = school.conn.execute(
            """
            SELECT * FROM board_answer_copies
            WHERE class_id = ? AND run_key = ? AND board_key = ?
            """,
            (int(class_id), str(copy_run), str(board_key)),
        ).fetchone()
    if row is None:
        return None
    view = _loads(row["strokes_json"])
    if "strokes" not in view:
        view = {"strokes": [], "texts": []}
    label = ""
    student_ids: list[int] = []
    if str(board_key).startswith("solo:"):
        try:
            sid = int(str(board_key).split(":", 1)[1])
        except (TypeError, ValueError):
            sid = 0
        if sid:
            student_ids = [sid]
            label = names.get(sid, "")
    else:
        try:
            members = json.loads(row["member_ids_json"] or "[]")
        except (TypeError, ValueError):
            members = []
        if isinstance(members, list):
            for sid in members:
                try:
                    student_ids.append(int(sid))
                except (TypeError, ValueError):
                    continue
        label = ", ".join(names.get(sid, "") for sid in student_ids if names.get(sid, ""))
    gallery_key = f"{copy_run}|{board_key}"
    return {
        "board_key": gallery_key,
        "copy_run_key": str(copy_run),
        "copy_board_key": str(board_key),
        "name": label,
        "away": False,
        "drawing": False,
        "status": "Submitted" if str(row["submitted_at"] or "").strip() else "Drawing",
        "text": str(row["text"] or ""),
        "feedback": str(row["feedback_text"] or ""),
        "source": source,
        "student_ids": student_ids,
        "board_seq": int(row["board_seq"] or 0),
        "snapshot": True,
        "ops": [],
        "canvas_view": view,
        "team_sort_order": None,
        "team_label": "",
    }


def lesson_submission_gallery(
    school: Any, class_id: int, run_key: str, question_key: str
) -> dict[str, Any]:
    """Submitted boards for one class question, including catch-up copies.

    Tiles use the live gallery shape so ``mountTeacherGrid`` can paint
    them. The drawing is the compact copy, not a raster image.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        run_key: Participation run key, with no ``~q`` suffix.
        question_key: ``item:`` key.
    """
    key = str(run_key or "").strip()
    qkey = str(question_key or "").strip()
    with school._lock:
        item = school.conn.execute(
            """
            SELECT id, item_json FROM participation_items
            WHERE class_id = ? AND run_key = ? AND question_key = ?
            """,
            (int(class_id), key, qkey),
        ).fetchone()
        answers = []
        if item is not None:
            answers = school.conn.execute(
                """
                SELECT student_id, source, answer_json FROM participation_answers
                WHERE class_id = ? AND run_key = ? AND question_key = ?
                """,
                (int(class_id), key, qkey),
            ).fetchall()
    if item is None:
        raise QuestionBoardError("Question not found", 404)
    payload = _loads(item["item_json"])
    if not uses_board(payload):
        raise QuestionBoardError("This question does not use a board.", 400)
    names = school._student_display_names(int(class_id))
    seen: set[tuple[str, str]] = set()
    tiles: list[dict[str, Any]] = []
    for answer in answers:
        pointer = _loads(answer["answer_json"])
        copy_run = str(pointer.get("copy_run_key") or "")
        copy_board = str(pointer.get("copy_board_key") or pointer.get("board_key") or "")
        if not copy_run or not copy_board:
            continue
        pair = (copy_run, copy_board)
        if pair in seen:
            continue
        seen.add(pair)
        tile = _archive_tile(
            school,
            class_id=int(class_id),
            copy_run=copy_run,
            board_key=copy_board,
            names=names,
            source=str(answer["source"] or ""),
        )
        if tile is not None:
            tiles.append(tile)
    stem = str(payload.get("text") or payload.get("prompt") or "").strip()
    return {
        "ok": True,
        "visible": True,
        "ended": False,
        "archive": True,
        "read_only": True,
        "run_key": key,
        "question_key": qkey,
        "context": stem,
        "summary": f"{len(tiles)} submitted",
        "filters": [{"id": "all", "label": f"All {len(tiles)}", "count": len(tiles)}],
        "tiles": tiles,
        "projected_board": "",
    }


def lesson_submission_board(
    school: Any, class_id: int, gallery_key: str
) -> dict[str, Any]:
    """One stored board for the Lesson Slides side panel.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        gallery_key: ``<copy run key>|<board key>`` from the tile.
    """
    token = str(gallery_key or "")
    if "|" not in token:
        raise QuestionBoardError("Question not found", 404)
    copy_run, board_key = token.split("|", 1)
    names = school._student_display_names(int(class_id))
    tile = _archive_tile(
        school,
        class_id=int(class_id),
        copy_run=copy_run,
        board_key=board_key,
        names=names,
        source="",
    )
    if tile is None:
        raise QuestionBoardError("Question not found", 404)
    return {
        "ok": True,
        "read_only": True,
        "snapshot": True,
        "board_key": tile["board_key"],
        "name": tile["name"],
        "run_key": copy_run,
        "board_seq": tile["board_seq"],
        "canvas_view": tile["canvas_view"],
        "feedback": tile["feedback"],
        "ops": [],
    }


def split_gallery_key(raw: str) -> tuple[str, str]:
    """Split a Lesson Slides tile key into copy run key and board key.

    Args:
        raw: ``<copy run key>|<board key>``.

    Returns:
        ``(copy_run_key, board_key)``.

    Raises:
        QuestionBoardError: The key is not a stored board.
    """
    token = str(raw or "")
    if "|" not in token:
        raise QuestionBoardError("Question not found", 404)
    copy_run, board_key = token.split("|", 1)
    if not copy_run or not board_key.startswith(("solo:", "team:")):
        raise QuestionBoardError("Question not found", 404)
    return copy_run, board_key
