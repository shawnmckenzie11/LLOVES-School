"""Whiteboard team pins stored on the shared LMS sqlite file (MCK-218).

The game database calls these helpers directly. They do not import ``lms``,
so the standalone game-show app still runs when that package is not on
``sys.path``. When the assignment table is absent (a game-show-only
database), writes and reads are no-ops.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def team_sort_order(raw: Any) -> int:
    """Return a team sort order, keeping ``0``.

    ``None`` and blank values become ``0``. A stored ``0`` is the first
    team (``team:0``), not a missing value.

    Args:
        raw: ``game_teams.sort_order`` or a missing field.
    """
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def board_key_for_sort_order(team_sort_order: int) -> str:
    """Canonical collab board key for one named team bucket.

    Args:
        team_sort_order: ``game_teams.sort_order`` for the team (0-based).
    """
    return f"team:{int(team_sort_order)}"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    """True when ``name`` is a table on this connection.

    Args:
        conn: Open sqlite connection.
        name: Table name to look up in ``sqlite_master``.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def active_live_run_key(conn: sqlite3.Connection, class_id: int) -> str | None:
    """Return the active session run key for a class, if any.

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
    """
    if not _has_table(conn, "live_class_sessions"):
        return None
    row = conn.execute(
        """
        SELECT run_key FROM live_class_sessions
        WHERE class_id = ? AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(class_id),),
    ).fetchone()
    if row is None:
        return None
    key = str(row["run_key"] or "").strip()
    return key or None


def record_board_assignment(
    conn: sqlite3.Connection,
    *,
    class_id: int,
    run_key: str,
    student_id: int,
    team_sort_order: int,
) -> None:
    """Persist one student's board bucket for this run (idempotent).

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
        run_key: ``live_class_sessions.run_key`` (may include ``~g<n>``).
        student_id: Roster id.
        team_sort_order: Named team sort order on the open game.
    """
    key = str(run_key or "").strip()
    if not key or not _has_table(conn, "live_board_assignments"):
        return
    board_key = board_key_for_sort_order(int(team_sort_order))
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(live_board_assignments)")
    }
    session_id = None
    if "live_session_id" in columns and _has_table(conn, "live_class_sessions"):
        found = conn.execute(
            """
            SELECT id FROM live_class_sessions
            WHERE class_id = ?
              AND (
                run_key = ?
                OR ? LIKE run_key || '~g%'
              )
            ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (int(class_id), key, key),
        ).fetchone()
        if found is not None:
            try:
                session_id = int(found["id"])
            except (TypeError, ValueError, KeyError):
                session_id = None
    if "live_session_id" in columns:
        conn.execute(
            """
            INSERT INTO live_board_assignments (
                class_id, run_key, board_key, student_id, team_sort_order,
                live_session_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(class_id, run_key, student_id) DO UPDATE SET
                live_session_id = COALESCE(
                    excluded.live_session_id,
                    live_board_assignments.live_session_id
                )
            """,
            (
                int(class_id),
                key,
                board_key,
                int(student_id),
                int(team_sort_order),
                session_id,
            ),
        )
        return
    conn.execute(
        """
        INSERT INTO live_board_assignments (
            class_id, run_key, board_key, student_id, team_sort_order
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(class_id, run_key, student_id) DO NOTHING
        """,
        (int(class_id), key, board_key, int(student_id), int(team_sort_order)),
    )


def delete_board_assignments_for_run(
    conn: sqlite3.Connection, *, class_id: int, run_key: str
) -> None:
    """Drop assignment rows when a participation run is discarded.

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
        run_key: Participation / live run key.
    """
    key = str(run_key or "").strip()
    if not key or not _has_table(conn, "live_board_assignments"):
        return
    conn.execute(
        """
        DELETE FROM live_board_assignments
        WHERE class_id = ? AND run_key = ?
        """,
        (int(class_id), key),
    )


def assignment_sort_orders(
    conn: sqlite3.Connection,
    *,
    class_id: int,
    run_key: str,
) -> dict[int, int]:
    """All saved team sort orders for one run (student id → sort_order).

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
        run_key: Board run key for the active live session.
    """
    key = str(run_key or "").strip()
    if not key or not _has_table(conn, "live_board_assignments"):
        return {}
    rows = conn.execute(
        """
        SELECT student_id, team_sort_order
        FROM live_board_assignments
        WHERE class_id = ? AND run_key = ?
        """,
        (int(class_id), key),
    ).fetchall()
    out: dict[int, int] = {}
    for row in rows:
        try:
            out[int(row["student_id"])] = int(row["team_sort_order"])
        except (TypeError, ValueError):
            continue
    return out


def assignment_sort_order(
    conn: sqlite3.Connection,
    *,
    class_id: int,
    run_key: str,
    student_id: int,
) -> int | None:
    """Saved team sort order for one student on this run, if any.

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
        run_key: Board run key for the active live session.
        student_id: Roster id.
    """
    if not _has_table(conn, "live_board_assignments"):
        return None
    row = conn.execute(
        """
        SELECT team_sort_order FROM live_board_assignments
        WHERE class_id = ? AND run_key = ? AND student_id = ?
        """,
        (int(class_id), str(run_key).strip(), int(student_id)),
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["team_sort_order"])
    except (TypeError, ValueError):
        return None


def assignment_board_key(
    conn: sqlite3.Connection,
    *,
    class_id: int,
    run_key: str,
    student_id: int,
) -> str | None:
    """Saved collab board key for one student on this run.

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
        run_key: Board run key for the active live session.
        student_id: Roster id.
    """
    if not _has_table(conn, "live_board_assignments"):
        return None
    row = conn.execute(
        """
        SELECT board_key FROM live_board_assignments
        WHERE class_id = ? AND run_key = ? AND student_id = ?
        """,
        (int(class_id), str(run_key).strip(), int(student_id)),
    ).fetchone()
    if row is None:
        return None
    key = str(row["board_key"] or "").strip()
    return key or None


def sync_assignments_from_game_state(
    conn: sqlite3.Connection,
    *,
    class_id: int,
    run_key: str,
    game_state: dict[str, Any],
    is_test_student: Any,
) -> None:
    """Write assignment rows from the open game's team membership.

    Args:
        conn: Shared sqlite connection.
        class_id: Game-show ``classes.id``.
        run_key: Live board run key.
        game_state: ``game_state`` payload with ``teams`` members.
        is_test_student: Callable ``(student_id) -> bool``.
    """
    key = str(run_key or "").strip()
    if not key:
        return
    for team in game_state.get("teams") or []:
        if not isinstance(team, dict):
            continue
        name = str(team.get("name") or "")
        if name == "Class":
            continue
        sort_order = team_sort_order(team.get("sort_order"))
        for member in team.get("members") or []:
            if not isinstance(member, dict):
                continue
            try:
                sid = int(member.get("id"))
            except (TypeError, ValueError):
                continue
            if is_test_student(sid):
                continue
            record_board_assignment(
                conn,
                class_id=int(class_id),
                run_key=key,
                student_id=sid,
                team_sort_order=sort_order,
            )
