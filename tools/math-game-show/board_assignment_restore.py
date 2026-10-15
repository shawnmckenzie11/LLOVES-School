"""Helpers for game-show team restore using ``live_board_assignments``."""

from __future__ import annotations

from typing import Any

from live_board_assignments import (
    active_live_run_key,
    assignment_sort_orders,
    record_board_assignment,
)


def apply_team_lock_with_board_assignments(
    db: Any,
    class_id: int,
    game_id: int,
    session_id: int,
    spec: dict[str, Any],
    *,
    student_is_test_locked: Any,
) -> bool:
    """Rebuild named teams and honor pinned board buckets.

    Args:
        db: ``GameShowDB`` instance (uses ``db.conn``).
        class_id: Classes primary key.
        game_id: Open game id.
        session_id: Attendance session id.
        spec: Saved team-lock payload.
        student_is_test_locked: Callable ``(student_id) -> bool``.

    Returns:
        True when at least one student was placed on a named team.
    """
    from teams import color_for_team, default_team_name

    conn = db.conn
    conn.execute("DELETE FROM game_memberships WHERE game_id = ?", (int(game_id),))
    conn.execute("DELETE FROM team_buckets WHERE game_id = ?", (int(game_id),))
    conn.execute("DELETE FROM game_teams WHERE game_id = ?", (int(game_id),))
    db._ensure_session_scores(int(session_id), int(class_id))
    team_by_order: dict[int, int] = {}
    spec_placements: dict[int, int] = {}
    for team in spec.get("teams") or []:
        if not isinstance(team, dict):
            continue
        try:
            order = int(team.get("sort_order") or 0)
        except (TypeError, ValueError):
            order = 0
        name = str(team.get("name") or "").strip() or default_team_name(order)
        if name == "Class":
            continue
        for raw in team.get("student_ids") or []:
            try:
                spec_placements[int(raw)] = int(order)
            except (TypeError, ValueError):
                continue
        color = str(team.get("color") or "").strip() or color_for_team(order)
        cur = conn.execute(
            """
            INSERT INTO game_teams (game_id, name, color, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (int(game_id), name, color, order),
        )
        team_id = int(cur.lastrowid)
        team_by_order[int(order)] = team_id
        conn.execute(
            """
            INSERT INTO team_buckets (game_id, team_id, points)
            VALUES (?, ?, 0)
            """,
            (int(game_id), team_id),
        )
    run_key = active_live_run_key(conn, int(class_id))
    pinned = assignment_sort_orders(conn, class_id=int(class_id), run_key=run_key or "")
    targets = dict(spec_placements)
    for sid, order in pinned.items():
        targets[int(sid)] = int(order)
    wrote = False
    for student_id, order in sorted(targets.items()):
        if student_is_test_locked(int(student_id)):
            continue
        roster = conn.execute(
            "SELECT id FROM students WHERE id = ? AND class_id = ?",
            (int(student_id), int(class_id)),
        ).fetchone()
        if roster is None:
            continue
        team_id = team_by_order.get(int(order))
        if team_id is None:
            continue
        conn.execute(
            """
            INSERT INTO game_memberships (game_id, team_id, student_id)
            VALUES (?, ?, ?)
            """,
            (int(game_id), int(team_id), int(student_id)),
        )
        conn.execute(
            """
            UPDATE session_scores
            SET present = 1
            WHERE session_id = ? AND student_id = ? AND present = 0
            """,
            (int(session_id), int(student_id)),
        )
        wrote = True
    return wrote


def record_assignments_from_state(
    conn: Any,
    *,
    class_id: int,
    run_key: str,
    game_state: dict[str, Any],
    student_is_test_locked: Any,
) -> None:
    """Write assignment rows from a ``game_state`` teams list.

    Args:
        conn: Shared sqlite connection.
        class_id: Classes primary key.
        run_key: Live board run key.
        game_state: Open game state payload.
        student_is_test_locked: Callable ``(student_id) -> bool``.
    """
    for team in game_state.get("teams") or []:
        if str(team.get("name") or "") == "Class":
            continue
        try:
            sort_order = int(team.get("sort_order") or 0)
        except (TypeError, ValueError):
            sort_order = 0
        for member in team.get("members") or []:
            try:
                sid = int(member.get("id"))
            except (TypeError, ValueError):
                continue
            if student_is_test_locked(sid):
                continue
            record_board_assignment(
                conn,
                class_id=int(class_id),
                run_key=run_key,
                student_id=sid,
                team_sort_order=sort_order,
            )
