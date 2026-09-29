"""Append-only whiteboard ops for one live session.

Ink used to live in one JSON blob rewritten on every POST. That
read-modify-write lost points across gunicorn workers. A board is now a
sequence of ops. ``board_seq`` is a per-board counter advanced with
``UPDATE … RETURNING`` inside the same transaction as the inserts, so
four workers cannot hand out the same sequence number.

Postgres is the store when live presence is attached. Sqlite is the
fallback for local dev and CI. Schema creation takes a Postgres advisory
lock and ignores a duplicate-table race so four workers can boot together.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from typing import Any, Callable

from live_canvas import (
    MAX_POINTS,
    _batch_points,
    _clean_point,
    cursor_color_for,
    default_canvas_sync,
)

# Ops returned by a since-seq read. A wider gap is a snapshot instead.
MAX_DELTA_OPS = 200
# One write cannot carry an unbounded op list.
MAX_OPS_PER_BATCH = 32
# Stable lock id for board_ops DDL. Not a presence-table lock.
_BOARD_DDL_LOCK = 87421301
_BOARD_KEY = re.compile(
    r"^(teacher|shared|team:[0-9]+|solo:[A-Za-z0-9_-]{1,80})$"
)
_OP_TYPES = frozenset(
    {"stroke_add", "pts_append", "stroke_end", "stroke_remove", "text_upsert"}
)

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS board_ops (
    session_id INTEGER NOT NULL,
    board_key TEXT NOT NULL,
    board_seq INTEGER NOT NULL,
    op TEXT NOT NULL,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL,
    client_batch_id TEXT,
    stroke_id TEXT,
    PRIMARY KEY (session_id, board_key, board_seq)
);
CREATE INDEX IF NOT EXISTS board_ops_session_key_seq
    ON board_ops (session_id, board_key, board_seq);
CREATE INDEX IF NOT EXISTS board_ops_stroke
    ON board_ops (session_id, board_key, stroke_id);
CREATE TABLE IF NOT EXISTS board_counters (
    session_id INTEGER NOT NULL,
    board_key TEXT NOT NULL,
    next_seq INTEGER NOT NULL,
    PRIMARY KEY (session_id, board_key)
);
CREATE TABLE IF NOT EXISTS board_batches (
    session_id INTEGER NOT NULL,
    board_key TEXT NOT NULL,
    owner TEXT NOT NULL,
    client_batch_id TEXT NOT NULL,
    first_seq INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    PRIMARY KEY (session_id, board_key, owner, client_batch_id)
);
CREATE TABLE IF NOT EXISTS board_sessions (
    session_id INTEGER PRIMARY KEY,
    closed INTEGER NOT NULL DEFAULT 0
);
"""

_POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS board_ops (
    session_id BIGINT NOT NULL,
    board_key TEXT NOT NULL,
    board_seq BIGINT NOT NULL,
    op TEXT NOT NULL,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL,
    client_batch_id TEXT,
    stroke_id TEXT,
    PRIMARY KEY (session_id, board_key, board_seq)
);
CREATE INDEX IF NOT EXISTS board_ops_session_key_seq
    ON board_ops (session_id, board_key, board_seq);
CREATE INDEX IF NOT EXISTS board_ops_stroke
    ON board_ops (session_id, board_key, stroke_id);
CREATE TABLE IF NOT EXISTS board_counters (
    session_id BIGINT NOT NULL,
    board_key TEXT NOT NULL,
    next_seq BIGINT NOT NULL,
    PRIMARY KEY (session_id, board_key)
);
CREATE TABLE IF NOT EXISTS board_batches (
    session_id BIGINT NOT NULL,
    board_key TEXT NOT NULL,
    owner TEXT NOT NULL,
    client_batch_id TEXT NOT NULL,
    first_seq BIGINT NOT NULL,
    last_seq BIGINT NOT NULL,
    PRIMARY KEY (session_id, board_key, owner, client_batch_id)
);
CREATE TABLE IF NOT EXISTS board_sessions (
    session_id BIGINT PRIMARY KEY,
    closed BIGINT NOT NULL DEFAULT 0
);
"""

Exec = Callable[[str, tuple[Any, ...]], Any]


class BoardOpRejected(Exception):
    """The caller cannot apply this op to the board.

    Raised when a ``stroke_remove`` names a stroke owned by someone else,
    or a stroke that is not on this board.
    """


class BoardSessionClosed(Exception):
    """Ink writes are closed because the live session is ending or ended."""


def normalize_board_key(raw: Any) -> str:
    """Return a canonical board key, or raise ``ValueError``.

    Args:
        raw: ``teacher``, ``shared``, ``team:<id>``, ``solo:<owner>``,
            or the route alias ``mine`` (resolved by the caller).

    Returns:
        The key with surrounding space removed.

    Raises:
        ValueError: The token is not a board key this store will write.
    """
    text = str(raw or "").strip()
    if not _BOARD_KEY.fullmatch(text):
        raise ValueError("Unknown board.")
    return text


def schema_race(exc: BaseException) -> bool:
    """True when DDL lost a create race against another worker.

    Postgres can raise ``UniqueViolation`` on ``pg_type`` while two
    sessions run ``CREATE TABLE IF NOT EXISTS`` together. The loser
    should continue; the table is already there.

    Args:
        exc: The driver exception from a DDL statement.
    """
    name = type(exc).__name__
    if name in {"UniqueViolation", "DuplicateTable", "DuplicateObject"}:
        return True
    code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return str(code or "") in {"23505", "42P07", "42710"}


def _now() -> str:
    """ISO timestamp stored beside each op."""
    return datetime.now().replace(microsecond=0).isoformat()


def _loads(raw: Any) -> dict[str, Any] | None:
    """Parse one stored op object.

    Args:
        raw: JSON text from ``board_ops.op``.

    Returns:
        The object, or ``None`` when the row is not a JSON object.
    """
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _row_dict(row: Any) -> dict[str, Any]:
    """Copy a sqlite or Postgres row into a plain dict.

    Args:
        row: Driver row. ``None`` stays empty.
    """
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _public_op(row: dict[str, Any]) -> dict[str, Any] | None:
    """Shape one stored row for a since-seq reply.

    Args:
        row: ``board_ops`` mapping including ``op`` JSON.

    Returns:
        Op fields plus ``board_seq``, ``board_key``, and ``owner``.
    """
    op = _loads(row.get("op"))
    if op is None:
        return None
    body = dict(op)
    body["board_seq"] = int(row.get("board_seq") or 0)
    body["board_key"] = str(row.get("board_key") or body.get("board_key") or "")
    body["owner"] = str(row.get("owner") or body.get("owner") or "")
    return body


def fold_to_public_blob(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay ops into the canvas blob shape viewers already understand.

    Strokes are not trimmed to the old 80-stroke cap. A ``stroke_remove``
    drops that stroke. Text with an empty body is deleted. The latest
    cursor on an op wins for that owner.

    Args:
        rows: Stored rows (``board_key``, ``board_seq``, ``op``, ``owner``)
            in any order.

    Returns:
        ``{strokes: {teacher, teams}, cursors, texts}``.
    """
    blob = default_canvas_sync()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("board_key") or "")
        grouped.setdefault(key, []).append(row)
    for key, items in grouped.items():
        items.sort(key=lambda item: int(item.get("board_seq") or 0))
        strokes, texts, cursors = _fold_board(items)
        if key == "teacher":
            blob["strokes"]["teacher"].extend(strokes)
        elif key == "shared" or key.startswith("team:"):
            team_key = "shared" if key == "shared" else key.split(":", 1)[1]
            bucket = blob["strokes"]["teams"].setdefault(team_key, [])
            bucket.extend(strokes)
        blob["texts"].extend(texts)
        for owner, cursor in cursors.items():
            blob["cursors"][owner] = cursor
    return blob


def _fold_board(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Replay one board's ops into strokes, texts, and cursors.

    Args:
        items: Rows for a single ``board_key``, oldest sequence first.

    Returns:
        Stroke list, text list, and cursors keyed by owner.
    """
    strokes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    texts: dict[str, dict[str, Any]] = {}
    text_order: list[str] = []
    cursors: dict[str, dict[str, Any]] = {}
    for row in items:
        op = _loads(row.get("op"))
        if op is None:
            continue
        _note_cursor(cursors, op, str(row.get("owner") or ""))
        kind = str(op.get("type") or "")
        sid = str(op.get("id") or "")
        if kind == "stroke_remove":
            if sid in strokes:
                del strokes[sid]
                order = [item for item in order if item != sid]
            continue
        if kind == "text_upsert":
            _fold_text(texts, text_order, op)
            continue
        if kind not in {"stroke_add", "pts_append", "stroke_end"} or not sid:
            continue
        _fold_stroke(strokes, order, op, sid)
    painted = [strokes[sid] for sid in order if sid in strokes and strokes[sid]["points"]]
    labels = [texts[sid] for sid in text_order if sid in texts]
    return painted, labels, cursors


def _note_cursor(cursors: dict[str, dict[str, Any]], op: dict[str, Any], owner: str) -> None:
    """Keep the latest cursor carried on an op.

    Args:
        cursors: Owner to cursor, mutated.
        op: One op object. No cursor fields means no change.
        owner: Row owner, used when the op omits one.
    """
    point = _clean_point([op.get("x"), op.get("y")])
    if point is None:
        return
    who = str(op.get("owner") or owner or "").strip()
    if not who:
        return
    team_id = op.get("team_id")
    try:
        team_n = int(team_id) if team_id not in (None, "") else None
    except (TypeError, ValueError):
        team_n = None
    cursors[who] = {
        "owner": who,
        "name": str(op.get("name") or who).strip() or who,
        "color": str(op.get("color") or "").strip() or cursor_color_for(who),
        "x": point[0],
        "y": point[1],
        "team_id": team_n,
    }


def _fold_text(
    texts: dict[str, dict[str, Any]],
    order: list[str],
    op: dict[str, Any],
) -> None:
    """Insert, replace, or delete one text label.

    Args:
        texts: Id to label, mutated.
        order: First-seen ids, mutated.
        op: ``text_upsert`` object. An empty body deletes.
    """
    sid = str(op.get("id") or "").strip()
    if not sid:
        return
    body = str(op.get("text") or "").strip()
    if not body:
        texts.pop(sid, None)
        return
    point = _clean_point([op.get("x"), op.get("y")])
    if point is None:
        return
    if sid not in texts:
        order.append(sid)
    team_id = op.get("team_id")
    try:
        team_n = int(team_id) if team_id not in (None, "") else None
    except (TypeError, ValueError):
        team_n = None
    owner = str(op.get("owner") or "").strip() or "teacher"
    texts[sid] = {
        "id": sid[:80],
        "owner": owner,
        "name": str(op.get("name") or owner).strip() or owner,
        "color": str(op.get("color") or "").strip() or cursor_color_for(owner),
        "x": point[0],
        "y": point[1],
        "team_id": team_n,
        "text": body[:240],
    }


def _fold_stroke(
    strokes: dict[str, dict[str, Any]],
    order: list[str],
    op: dict[str, Any],
    sid: str,
) -> None:
    """Create or extend one stroke. ``stroke_end`` does not add points.

    Args:
        strokes: Id to stroke, mutated.
        order: First-seen ids, mutated.
        op: Stroke op.
        sid: Stroke id.
    """
    incoming = op.get("points") if isinstance(op.get("points"), list) else []
    cleaned = _batch_points(incoming) if incoming else []
    current = strokes.get(sid)
    if current is None:
        if str(op.get("type") or "") == "stroke_end" or not cleaned:
            return
        owner = str(op.get("owner") or "").strip() or "teacher"
        team_id = op.get("team_id")
        try:
            team_n = int(team_id) if team_id not in (None, "") else None
        except (TypeError, ValueError):
            team_n = None
        strokes[sid] = {
            "id": sid,
            "owner": owner,
            "team_id": team_n,
            "color": str(op.get("color") or "").strip() or cursor_color_for(owner),
            "points": cleaned[:MAX_POINTS],
        }
        order.append(sid)
        return
    if str(op.get("type") or "") == "stroke_end":
        return
    for point in cleaned:
        if len(current["points"]) >= MAX_POINTS:
            break
        if current["points"] and current["points"][-1] == point:
            continue
        current["points"].append(point)


def _stroke_state(exec_: Exec, session_id: int, board_key: str, stroke_id: str) -> dict[str, Any] | None:
    """Replay one stroke's ops into owner, life, and point count.

    Args:
        exec_: Transaction statement runner.
        session_id: Live session id.
        board_key: Board the stroke lives on.
        stroke_id: Client stroke id.

    Returns:
        ``{owner, alive, npoints, last}``, or ``None`` when the id was
        never added on this board.
    """
    cursor = exec_(
        """
        SELECT op, owner FROM board_ops
        WHERE session_id = ? AND board_key = ? AND stroke_id = ?
        ORDER BY board_seq ASC
        """,
        (int(session_id), board_key, stroke_id),
    )
    owner = ""
    alive = False
    seen = False
    points: list[list[float]] = []
    for row in cursor.fetchall():
        item = _row_dict(row)
        op = _loads(item.get("op"))
        if op is None:
            continue
        kind = str(op.get("type") or "")
        if kind == "stroke_remove":
            alive = False
            points = []
            seen = True
            continue
        if kind == "text_upsert":
            seen = True
            owner = str(item.get("owner") or op.get("owner") or owner)
            alive = bool(str(op.get("text") or "").strip())
            continue
        if kind not in {"stroke_add", "pts_append", "stroke_end"}:
            continue
        seen = True
        if not owner:
            owner = str(item.get("owner") or op.get("owner") or "")
        if kind == "stroke_end":
            alive = True
            continue
        if not alive and kind == "stroke_add":
            points = []
        alive = True
        for point in _batch_points(op.get("points") if isinstance(op.get("points"), list) else []):
            if len(points) >= MAX_POINTS:
                break
            if points and points[-1] == point:
                continue
            points.append(point)
    if not seen:
        return None
    return {
        "owner": owner,
        "alive": alive,
        "npoints": len(points),
        "last": points[-1] if points else None,
    }


def _text_owner(exec_: Exec, session_id: int, board_key: str, text_id: str) -> str | None:
    """Return the current owner of a text label, if one is still visible.

    Args:
        exec_: Transaction statement runner.
        session_id: Live session id.
        board_key: Board the label lives on.
        text_id: Client text id.
    """
    state = _stroke_state(exec_, session_id, board_key, text_id)
    if state is None or not state.get("alive"):
        return None
    return str(state.get("owner") or "") or None


class _BoardStore:
    """Shared append, read, and fold logic for sqlite and Postgres.

    Subclasses open the transaction and run ``ensure_schema``.
    """

    def append_ink(
        self,
        session_id: int,
        board_key: str,
        *,
        owner: str,
        name: str,
        color: str | None,
        team_id: int | None,
        stroke_id: str | None,
        points: Any = None,
        point: Any = None,
        ended: bool = False,
        batched: bool = False,
        x: Any = None,
        y: Any = None,
        client_batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Turn one presence POST into stroke ops and append them.

        A legacy lift (``ended`` without a ``points`` list) does not add
        samples. A new stroke is ``stroke_add``; a later batch is
        ``pts_append``. The per-stroke point cap still applies.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: ``teacher``, ``team:<id>``, ``shared``, or ``solo:<id>``.
            owner: Writer id. ``teacher`` or a roster id string.
            name: Cursor label.
            color: Optional hex colour.
            team_id: Team stamped on the cursor and stroke.
            stroke_id: Stable id while the pointer is down.
            points: Batch of ``[x, y]`` pairs. Wins over ``point``.
            point: Legacy single sample.
            ended: True on the last batch of a stroke.
            batched: True when the client sent a ``points`` list, even empty.
            x: Cursor x in 0–1.
            y: Cursor y in 0–1.
            client_batch_id: Retry token. A repeat returns the first result.

        Returns:
            ``{board_key, board_seq, ops, duplicate}``.
        """
        key = normalize_board_key(board_key)
        who = str(owner or "").strip() or "teacher"
        sid = str(stroke_id or "").strip()[:80]
        tint = (color or "").strip() or cursor_color_for(who)
        label = str(name or who).strip() or who

        def work(exec_: Exec) -> dict[str, Any]:
            ops = self._ink_ops(
                exec_,
                session_id,
                key,
                owner=who,
                name=label,
                color=tint,
                team_id=team_id,
                stroke_id=sid,
                points=points,
                point=point,
                ended=ended,
                batched=batched,
                x=x,
                y=y,
            )
            return self._insert_ops(
                exec_,
                int(session_id),
                key,
                who,
                ops,
                client_batch_id,
            )

        return self._run(work, session_id, key, who, client_batch_id)

    def append_text(
        self,
        session_id: int,
        board_key: str,
        *,
        owner: str,
        name: str,
        text_id: str,
        text: str,
        x: Any,
        y: Any,
        color: str | None,
        team_id: int | None,
        client_batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one ``text_upsert``. Another owner's label is left as-is.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: Board the label belongs on.
            owner: Writer id.
            name: Display name stored with the label.
            text_id: Stable client id.
            text: Label body. Empty deletes.
            x: Normalized x in 0–1.
            y: Normalized y in 0–1.
            color: Optional hex colour.
            team_id: Team id when the board is shared.
            client_batch_id: Retry token.

        Returns:
            ``{board_key, board_seq, ops, duplicate}``.
        """
        key = normalize_board_key(board_key)
        who = str(owner or "").strip() or "teacher"
        sid = str(text_id or "").strip()[:80]

        def work(exec_: Exec) -> dict[str, Any]:
            ops: list[dict[str, Any]] = []
            if sid:
                current = _text_owner(exec_, int(session_id), key, sid)
                if current is not None and current != who:
                    ops = []
                else:
                    ops = [
                        {
                            "type": "text_upsert",
                            "id": sid,
                            "owner": who,
                            "name": str(name or who).strip() or who,
                            "text": str(text or ""),
                            "x": x,
                            "y": y,
                            "color": (color or "").strip() or cursor_color_for(who),
                            "team_id": team_id,
                        }
                    ]
            return self._insert_ops(
                exec_,
                int(session_id),
                key,
                who,
                ops,
                client_batch_id,
            )

        return self._run(work, session_id, key, who, client_batch_id)

    def append_remove(
        self,
        session_id: int,
        board_key: str,
        *,
        owner: str,
        stroke_id: str,
        client_batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Append ``stroke_remove`` for a stroke this owner drew.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: Board the stroke was written to.
            owner: Caller. Must match the stroke's owner.
            stroke_id: Stroke to drop.
            client_batch_id: Retry token.

        Returns:
            ``{board_key, board_seq, ops, duplicate}``.

        Raises:
            BoardOpRejected: The stroke is missing or owned by someone else.
        """
        key = normalize_board_key(board_key)
        who = str(owner or "").strip() or "teacher"
        sid = str(stroke_id or "").strip()[:80]
        op = {"type": "stroke_remove", "id": sid, "owner": who}
        return self.append_ops(
            int(session_id),
            key,
            owner=who,
            ops=[op],
            client_batch_id=client_batch_id,
        )

    def append_ops(
        self,
        session_id: int,
        board_key: str,
        *,
        owner: str,
        ops: list[dict[str, Any]],
        client_batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a caller-built op list after owner checks.

        ``stroke_remove`` must name this owner's live stroke. A repeated
        ``client_batch_id`` returns the original rows.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: Destination board.
            owner: Authenticated writer. Client-supplied owners are ignored.
            ops: Op objects. Unknown types are dropped.
            client_batch_id: Retry token.

        Returns:
            ``{board_key, board_seq, ops, duplicate}``.

        Raises:
            BoardOpRejected: A remove targets a stroke this owner does not hold.
            ValueError: The batch is larger than ``MAX_OPS_PER_BATCH``.
        """
        key = normalize_board_key(board_key)
        who = str(owner or "").strip() or "teacher"
        if len(ops) > MAX_OPS_PER_BATCH:
            raise ValueError("Too many board ops in one batch.")

        def work(exec_: Exec) -> dict[str, Any]:
            prepared = self._prepare_client_ops(exec_, int(session_id), key, who, ops)
            return self._insert_ops(
                exec_,
                int(session_id),
                key,
                who,
                prepared,
                client_batch_id,
            )

        return self._run(work, session_id, key, who, client_batch_id)

    def since(
        self, session_id: int, board_key: str, since_seq: int
    ) -> dict[str, Any]:
        """Return ops with ``since_seq < board_seq <=`` the head read first.

        The head is the counter value at the start of this read. Rows
        committed after that read are left for the next poll, so a reply
        never contains a sequence past the ``board_seq`` it reports. A gap
        wider than ``MAX_DELTA_OPS`` sets ``snapshot`` and returns no op
        list. ``since_seq`` equal to the current sequence is empty.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: One board.
            since_seq: Last sequence the caller already applied.

        Returns:
            ``{board_key, board_seq, since, ops, snapshot}``.
        """
        key = normalize_board_key(board_key)
        since_n = max(0, int(since_seq))

        def work(exec_: Exec) -> dict[str, Any]:
            current = self._current_seq(exec_, int(session_id), key)
            if current - since_n > MAX_DELTA_OPS:
                return {
                    "board_key": key,
                    "board_seq": current,
                    "since": since_n,
                    "ops": [],
                    "snapshot": True,
                }
            cursor = exec_(
                """
                SELECT board_key, board_seq, op, owner
                FROM board_ops
                WHERE session_id = ? AND board_key = ? AND board_seq > ? AND board_seq <= ?
                ORDER BY board_seq ASC
                LIMIT ?
                """,
                (int(session_id), key, since_n, current, MAX_DELTA_OPS + 1),
            )
            rows = [_row_dict(row) for row in cursor.fetchall()]
            if len(rows) > MAX_DELTA_OPS:
                return {
                    "board_key": key,
                    "board_seq": current,
                    "since": since_n,
                    "ops": [],
                    "snapshot": True,
                }
            ops = [op for op in (_public_op(row) for row in rows) if op is not None]
            return {
                "board_key": key,
                "board_seq": current,
                "since": since_n,
                "ops": ops,
                "snapshot": False,
            }

        return self._read(work)

    def current_seq(self, session_id: int, board_key: str) -> int:
        """Return the latest sequence on one board, or 0.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: One board.
        """
        key = normalize_board_key(board_key)

        def work(exec_: Exec) -> int:
            return self._current_seq(exec_, int(session_id), key)

        return int(self._read(work))

    def load_keys(
        self, session_id: int, keys: list[str] | None
    ) -> list[dict[str, Any]]:
        """Load stored rows for a session, optionally limited to board keys.

        Args:
            session_id: ``live_class_sessions.id``.
            keys: Board keys. ``None`` loads every board in the session.
                An empty list loads nothing.

        Returns:
            Rows ordered by board key then sequence.
        """
        if keys is not None and not keys:
            return []
        cleaned = [normalize_board_key(key) for key in keys] if keys is not None else None

        def work(exec_: Exec) -> list[dict[str, Any]]:
            if cleaned is None:
                cursor = exec_(
                    """
                    SELECT board_key, board_seq, op, owner, created_at, stroke_id
                    FROM board_ops
                    WHERE session_id = ?
                    ORDER BY board_key ASC, board_seq ASC
                    """,
                    (int(session_id),),
                )
            else:
                marks = ",".join("?" for _ in cleaned)
                cursor = exec_(
                    f"""
                    SELECT board_key, board_seq, op, owner, created_at, stroke_id
                    FROM board_ops
                    WHERE session_id = ? AND board_key IN ({marks})
                    ORDER BY board_key ASC, board_seq ASC
                    """,
                    (int(session_id), *cleaned),
                )
            return [_row_dict(row) for row in cursor.fetchall()]

        return self._read(work)

    def purge(self, session_id: int) -> None:
        """Close the session and delete its ops, counters, and batches.

        The closed flag and the deletes share one transaction and the
        same ``board_sessions`` row a writer locks before inserting. A
        write that races this purge either commits before the delete or
        sees the session closed and inserts nothing, so sequence 1 cannot
        be reused against an orphan row.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        sid = int(session_id)

        def work(exec_: Exec) -> None:
            exec_(
                """
                INSERT INTO board_sessions (session_id, closed)
                VALUES (?, 1)
                ON CONFLICT (session_id) DO UPDATE SET closed = 1
                """,
                (sid,),
            )
            exec_("DELETE FROM board_ops WHERE session_id = ?", (sid,))
            exec_("DELETE FROM board_counters WHERE session_id = ?", (sid,))
            exec_("DELETE FROM board_batches WHERE session_id = ?", (sid,))

        self._transaction(work)

    def _run(
        self,
        work: Callable[[Exec], dict[str, Any]],
        session_id: int,
        board_key: str,
        owner: str,
        client_batch_id: str | None,
    ) -> dict[str, Any]:
        """Run one append, returning the original batch when the id collides.

        Args:
            work: Transaction body.
            session_id: Live session id.
            board_key: Board key.
            owner: Writer.
            client_batch_id: Retry token, or ``None``.

        Returns:
            The append result. ``duplicate`` is true when the batch id
            was already stored.
        """
        try:
            return self._transaction(work)
        except Exception as exc:
            if not _is_integrity(exc) or not client_batch_id:
                raise
            existing = self._read_batch(int(session_id), board_key, owner, client_batch_id)
            if existing is None:
                raise
            existing["duplicate"] = True
            return existing

    def _read_batch(
        self,
        session_id: int,
        board_key: str,
        owner: str,
        client_batch_id: str,
    ) -> dict[str, Any] | None:
        """Return the ops already stored for one client batch.

        Args:
            session_id: Live session id.
            board_key: Board key.
            owner: Writer.
            client_batch_id: Retry token.

        Returns:
            The original append result, or ``None`` when the id is new.
        """

        def work(exec_: Exec) -> dict[str, Any] | None:
            return self._batch_result(exec_, session_id, board_key, owner, client_batch_id)

        return self._read(work)

    def _ink_ops(
        self,
        exec_: Exec,
        session_id: int,
        board_key: str,
        *,
        owner: str,
        name: str,
        color: str,
        team_id: int | None,
        stroke_id: str,
        points: Any,
        point: Any,
        ended: bool,
        batched: bool,
        x: Any,
        y: Any,
    ) -> list[dict[str, Any]]:
        """Build stroke ops for one presence body without inserting them.

        Args:
            exec_: Transaction statement runner.
            session_id: Live session id.
            board_key: Destination board.
            owner: Writer.
            name: Cursor label.
            color: Stroke colour.
            team_id: Team id stamped on the op.
            stroke_id: Stroke id. Blank yields no stroke op.
            points: Batch of samples, or ``None``.
            point: Legacy single sample.
            ended: Pointer lift.
            batched: True when ``points`` was a list.
            x: Cursor x.
            y: Cursor y.

        Returns:
            Zero, one, or two ops (stroke body, then optional ``stroke_end``).
        """
        incoming = _batch_points(points) if batched and isinstance(points, list) else []
        if not incoming and not batched:
            one = _clean_point(point)
            if one is not None:
                incoming = [one]
        legacy_end = bool(ended) and not batched
        cursor = _clean_point([x, y])
        if cursor is None and incoming:
            cursor = incoming[-1]
        base: dict[str, Any] = {
            "owner": owner,
            "name": name,
            "color": color,
            "team_id": team_id,
        }
        if cursor is not None:
            base["x"] = cursor[0]
            base["y"] = cursor[1]
        if not stroke_id:
            return []
        state = _stroke_state(exec_, int(session_id), board_key, stroke_id)
        if legacy_end or not incoming:
            if state is not None and state.get("alive") and ended:
                return [{**base, "type": "stroke_end", "id": stroke_id}]
            return []
        if state is not None and state.get("alive") and state.get("owner") not in ("", owner):
            raise BoardOpRejected("only the owner can extend a stroke")
        alive = state is not None and bool(state.get("alive"))
        room = MAX_POINTS - (int(state["npoints"]) if alive and state is not None else 0)
        last = state.get("last") if alive and state is not None else None
        kept: list[list[float]] = []
        for sample in incoming:
            if len(kept) >= max(0, room):
                break
            if last == sample:
                continue
            kept.append(sample)
            last = sample
        ops: list[dict[str, Any]] = []
        if not alive:
            if not kept:
                return []
            ops.append({**base, "type": "stroke_add", "id": stroke_id, "points": kept})
        elif kept:
            ops.append({**base, "type": "pts_append", "id": stroke_id, "points": kept})
        if ended and (ops or alive):
            ops.append({**base, "type": "stroke_end", "id": stroke_id})
        return ops

    def _prepare_client_ops(
        self,
        exec_: Exec,
        session_id: int,
        board_key: str,
        owner: str,
        ops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Check ownership and copy client ops onto this writer.

        Args:
            exec_: Transaction statement runner.
            session_id: Live session id.
            board_key: Destination board.
            owner: Authenticated writer.
            ops: Posted op objects.

        Returns:
            Ops safe to insert. Foreign text edits are dropped.

        Raises:
            BoardOpRejected: A remove or append targets someone else's stroke.
        """
        prepared: list[dict[str, Any]] = []
        for raw in ops:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("type") or "").strip()
            if kind not in _OP_TYPES:
                continue
            sid = str(raw.get("id") or raw.get("stroke_id") or "").strip()[:80]
            if not sid:
                continue
            if kind == "stroke_remove":
                state = _stroke_state(exec_, session_id, board_key, sid)
                if state is None:
                    raise BoardOpRejected("stroke is not on this board")
                if str(state.get("owner") or "") != owner:
                    raise BoardOpRejected("only the owner can remove a stroke")
                if not state.get("alive"):
                    continue
                prepared.append({"type": "stroke_remove", "id": sid, "owner": owner})
                continue
            if kind == "text_upsert":
                current = _text_owner(exec_, session_id, board_key, sid)
                if current is not None and current != owner:
                    continue
                prepared.append(
                    {
                        "type": "text_upsert",
                        "id": sid,
                        "owner": owner,
                        "name": str(raw.get("name") or owner),
                        "text": str(raw.get("text") or ""),
                        "x": raw.get("x"),
                        "y": raw.get("y"),
                        "color": str(raw.get("color") or "") or cursor_color_for(owner),
                        "team_id": raw.get("team_id"),
                    }
                )
                continue
            state = _stroke_state(exec_, session_id, board_key, sid)
            if state is not None and state.get("alive") and str(state.get("owner") or "") != owner:
                raise BoardOpRejected("only the owner can extend a stroke")
            points = raw.get("points") if isinstance(raw.get("points"), list) else []
            prepared.append(
                {
                    "type": kind,
                    "id": sid,
                    "owner": owner,
                    "name": str(raw.get("name") or owner),
                    "color": str(raw.get("color") or "") or cursor_color_for(owner),
                    "team_id": raw.get("team_id"),
                    "points": _batch_points(points),
                    "x": raw.get("x"),
                    "y": raw.get("y"),
                }
            )
        return prepared

    def _session_closed(self, exec_: Exec, session_id: int) -> bool:
        """Lock this session's board row and report whether writes are closed.

        The upsert sets ``closed`` to its current value so the row stays
        locked until the surrounding transaction ends. A purge that wants
        the same row waits. This write then either commits its ops or sees
        ``closed`` and inserts nothing.

        Args:
            exec_: Statement runner for the open transaction.
            session_id: ``live_class_sessions.id``.

        Returns:
            True when new ops must be rejected.
        """
        cursor = exec_(
            """
            INSERT INTO board_sessions (session_id, closed)
            VALUES (?, 0)
            ON CONFLICT (session_id) DO UPDATE
                SET closed = board_sessions.closed
            RETURNING closed
            """,
            (int(session_id),),
        )
        row = _row_dict(cursor.fetchone())
        return bool(int(row.get("closed") or 0))

    def _insert_ops(
        self,
        exec_: Exec,
        session_id: int,
        board_key: str,
        owner: str,
        ops: list[dict[str, Any]],
        client_batch_id: str | None,
    ) -> dict[str, Any]:
        """Allocate sequence numbers and insert ops in the open transaction.

        Args:
            exec_: Transaction statement runner.
            session_id: Live session id.
            board_key: Destination board.
            owner: Writer stored on each row.
            ops: Prepared op objects. Empty leaves the sequence unchanged.
            client_batch_id: Retry token.

        Returns:
            ``{board_key, board_seq, ops, duplicate}``.

        Raises:
            BoardSessionClosed: The session is ending or has ended.
        """
        if self._session_closed(exec_, session_id):
            raise BoardSessionClosed("session has ended")
        batch = str(client_batch_id or "").strip()[:80]
        if batch:
            existing = self._batch_result(exec_, session_id, board_key, owner, batch)
            if existing is not None:
                existing["duplicate"] = True
                return existing
        current = self._current_seq(exec_, session_id, board_key)
        if not ops:
            return {
                "board_key": board_key,
                "board_seq": current,
                "ops": [],
                "duplicate": False,
            }
        count = len(ops)
        exec_(
            """
            INSERT INTO board_counters (session_id, board_key, next_seq)
            VALUES (?, ?, 0)
            ON CONFLICT (session_id, board_key) DO NOTHING
            """,
            (session_id, board_key),
        )
        cursor = exec_(
            """
            UPDATE board_counters
            SET next_seq = next_seq + ?
            WHERE session_id = ? AND board_key = ?
            RETURNING next_seq
            """,
            (count, session_id, board_key),
        )
        updated = _row_dict(cursor.fetchone())
        high = int(updated.get("next_seq") or 0)
        if high <= 0:
            raise RuntimeError("board sequence was not allocated")
        first = high - count + 1
        stamp = _now()
        stored: list[dict[str, Any]] = []
        for offset, op in enumerate(ops):
            seq = first + offset
            sid = str(op.get("id") or "")[:80] or None
            payload = dict(op)
            payload["owner"] = owner
            exec_(
                """
                INSERT INTO board_ops (
                    session_id, board_key, board_seq, op, owner,
                    created_at, client_batch_id, stroke_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    board_key,
                    seq,
                    json.dumps(payload, separators=(",", ":")),
                    owner,
                    stamp,
                    batch or None,
                    sid,
                ),
            )
            row = {
                "board_key": board_key,
                "board_seq": seq,
                "op": payload,
                "owner": owner,
            }
            public = _public_op(row)
            if public is not None:
                stored.append(public)
        if batch:
            exec_(
                """
                INSERT INTO board_batches (
                    session_id, board_key, owner, client_batch_id, first_seq, last_seq
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, board_key, owner, batch, first, high),
            )
        return {
            "board_key": board_key,
            "board_seq": high,
            "ops": stored,
            "duplicate": False,
        }

    def _batch_result(
        self,
        exec_: Exec,
        session_id: int,
        board_key: str,
        owner: str,
        client_batch_id: str,
    ) -> dict[str, Any] | None:
        """Load a previously stored batch, or ``None``.

        Args:
            exec_: Statement runner.
            session_id: Live session id.
            board_key: Board key.
            owner: Writer.
            client_batch_id: Retry token.
        """
        cursor = exec_(
            """
            SELECT first_seq, last_seq FROM board_batches
            WHERE session_id = ? AND board_key = ? AND owner = ? AND client_batch_id = ?
            """,
            (session_id, board_key, owner, client_batch_id),
        )
        found = _row_dict(cursor.fetchone())
        if not found:
            return None
        first = int(found["first_seq"])
        last = int(found["last_seq"])
        ops_cur = exec_(
            """
            SELECT board_key, board_seq, op, owner FROM board_ops
            WHERE session_id = ? AND board_key = ? AND board_seq >= ? AND board_seq <= ?
            ORDER BY board_seq ASC
            """,
            (session_id, board_key, first, last),
        )
        ops = [op for op in (_public_op(_row_dict(row)) for row in ops_cur.fetchall()) if op]
        return {
            "board_key": board_key,
            "board_seq": last,
            "ops": ops,
            "duplicate": True,
        }

    def _current_seq(self, exec_: Exec, session_id: int, board_key: str) -> int:
        """Read the counter for one board.

        Args:
            exec_: Statement runner.
            session_id: Live session id.
            board_key: Board key.
        """
        cursor = exec_(
            """
            SELECT next_seq FROM board_counters
            WHERE session_id = ? AND board_key = ?
            """,
            (session_id, board_key),
        )
        row = _row_dict(cursor.fetchone())
        if not row:
            return 0
        return int(row.get("next_seq") or 0)

    def _transaction(self, work: Callable[[Exec], Any]) -> Any:
        """Run ``work`` inside one write transaction.

        Args:
            work: Receives a statement runner and returns the call result.
        """
        raise NotImplementedError

    def _read(self, work: Callable[[Exec], Any]) -> Any:
        """Run ``work`` on a connection that only reads.

        Args:
            work: Receives a statement runner.
        """
        raise NotImplementedError


def _is_integrity(exc: BaseException) -> bool:
    """True when an insert lost a uniqueness race.

    Args:
        exc: Exception from a write transaction.
    """
    if isinstance(exc, (BoardOpRejected, BoardSessionClosed)):
        return False
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    return type(exc).__name__ in {"UniqueViolation", "IntegrityError"}


class SqliteBoardOps(_BoardStore):
    """Board ops on the catalogue sqlite connection, or a private one.

    Writes use ``BEGIN IMMEDIATE`` so concurrent connections take sequence
    numbers in order. The optional lock serializes threads that share one
    connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        """Bind one sqlite connection.

        Args:
            conn: Connection with a row factory. The caller owns its lifetime.
            lock: Mutex for a shared connection. Private connections can
                pass their own lock.
        """
        self.conn = conn
        self.lock = lock or threading.Lock()

    def ensure_schema(self) -> None:
        """Create the sqlite board tables if they are not there yet."""
        with self.lock:
            self.conn.executescript(_SQLITE_DDL)
            self.conn.commit()

    def _transaction(self, work: Callable[[Exec], Any]) -> Any:
        """Run ``work`` under ``BEGIN IMMEDIATE``.

        Args:
            work: Statement body.
        """
        with self.lock:
            self._begin()
            try:
                result = work(self._exec)
                self._end(commit=True)
                return result
            except Exception:
                self._end(commit=False)
                raise

    def _read(self, work: Callable[[Exec], Any]) -> Any:
        """Run a read on the sqlite connection.

        Args:
            work: Statement body.
        """
        with self.lock:
            return work(self._exec)

    def _begin(self) -> None:
        """Start a reserved write transaction, closing one already open.

        The school connection runs in autocommit mode. ``Connection.commit``
        does not finish an explicit ``BEGIN`` there, so a later write would
        raise ``cannot start a transaction within a transaction``. SQL
        ``COMMIT`` closes that transaction.
        """
        if self.conn.in_transaction:
            self.conn.execute("COMMIT")
        self.conn.execute("BEGIN IMMEDIATE")

    def _end(self, *, commit: bool) -> None:
        """Finish the write transaction with SQL, not ``Connection.commit``.

        Args:
            commit: True keeps the rows. False rolls them back.
        """
        if not self.conn.in_transaction:
            return
        self.conn.execute("COMMIT" if commit else "ROLLBACK")

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Run one sqlite statement.

        Args:
            sql: SQL with ``?`` placeholders.
            params: Bind values.
        """
        return self.conn.execute(sql, params)


class PostgresBoardOps(_BoardStore):
    """Board ops on the live-presence Postgres pool.

    Sequence allocation and inserts share one transaction. DDL takes
    ``pg_advisory_xact_lock`` so concurrent workers do not collide on
    ``pg_type``. Running setup twice changes nothing.
    """

    def __init__(self, presence: Any) -> None:
        """Bind the presence store whose pool this board log borrows.

        Args:
            presence: ``LivePresenceStore``. Connections stay autocommit
                outside an explicit transaction.
        """
        self.presence = presence

    def ensure_schema(self) -> None:
        """Create board tables under the same transaction advisory lock as presence.

        A second call does not change an existing schema.
        """
        from live_presence import apply_locked_ddl

        statements = [part.strip() for part in _POSTGRES_DDL.split(";") if part.strip()]
        with self.presence._conn() as wrapped:
            apply_locked_ddl(wrapped.connection, statements, _BOARD_DDL_LOCK)

    def _transaction(self, work: Callable[[Exec], Any]) -> Any:
        """Run ``work`` in one Postgres transaction on a pooled connection.

        Args:
            work: Statement body.
        """
        with self.presence._conn() as wrapped:
            raw = wrapped.connection
            with raw.transaction():
                return work(_pg_exec(raw))

    def _read(self, work: Callable[[Exec], Any]) -> Any:
        """Run a read on a pooled autocommit connection.

        Args:
            work: Statement body.
        """
        with self.presence._conn() as wrapped:
            return work(_pg_exec(wrapped.connection))


def _pg_exec(raw: Any) -> Exec:
    """Build a ``?``-placeholder runner for one psycopg connection.

    Args:
        raw: psycopg connection.
    """

    def exec_(sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run one statement, translating sqlite placeholders.

        Args:
            sql: SQL with ``?`` placeholders.
            params: Bind values.
        """
        return raw.execute(sql.replace("?", "%s"), params)

    return exec_
