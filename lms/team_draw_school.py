"""MCK-242: SchoolDB methods for Team Draw (registered on ``SchoolDB``)."""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from team_draw import (
    TEAM_DRAW_RESPONSE_MODE,
    gallery_row_title,
    load_inked_png,
    is_mystery_rule_item,
    is_team_draw_item,
    mystery_steps_for_item,
    resolved_prompt_text,
    should_snapshot_team_draw_run,
    status_label,
    strip_image_from_snapshot_answer,
    team_draw_answer_json,
)

if TYPE_CHECKING:
    from school_db import SchoolDB


def register_team_draw_methods(cls: type) -> None:
    """Attach Team Draw helpers to ``SchoolDB``."""

    cls._ensure_team_draw_schema = _ensure_team_draw_schema  # type: ignore[attr-defined]
    cls._initialize_team_draw = _initialize_team_draw  # type: ignore[attr-defined]
    cls._publish_team_draw_whiteboard = _publish_team_draw_whiteboard  # type: ignore[attr-defined]
    cls.team_draw_teacher_view = team_draw_teacher_view  # type: ignore[attr-defined]
    cls.student_team_draw_state = student_team_draw_state  # type: ignore[attr-defined]
    cls.submit_team_draw = submit_team_draw  # type: ignore[attr-defined]
    cls.submit_individual_draw = submit_individual_draw  # type: ignore[attr-defined]
    cls.individual_draw_teacher_view = individual_draw_teacher_view  # type: ignore[attr-defined]
    cls.team_draw_gallery = team_draw_gallery  # type: ignore[attr-defined]
    cls.team_draw_end_and_clear = team_draw_end_and_clear  # type: ignore[attr-defined]
    cls._purge_team_draw_images = _purge_team_draw_images  # type: ignore[attr-defined]
    cls._purge_team_board_key = _purge_team_board_key  # type: ignore[attr-defined]
    cls._store_team_draw_image = _store_team_draw_image  # type: ignore[attr-defined]
    cls._present_team_draw_member_labels = _present_team_draw_member_labels  # type: ignore[attr-defined]
    cls._team_draw_pass_turn = _team_draw_pass_turn  # type: ignore[attr-defined]


def _ensure_team_draw_schema(self: SchoolDB) -> None:
    """Session-only PNG storage for gallery (never snapshotted)."""
    self.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_team_draw_images (
            live_session_id INTEGER NOT NULL,
            live_item_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            image_png BLOB NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (live_session_id, live_item_id, team_id)
        )
        """
    )
    self.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_individual_draw_images (
            live_session_id INTEGER NOT NULL,
            live_item_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            image_png BLOB NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (live_session_id, live_item_id, student_id)
        )
        """
    )


def _initialize_team_draw(self: SchoolDB, item: dict[str, Any]) -> None:
    """Same team rows as group submit, without MC fields."""
    self._initialize_group_submit(item)


def _publish_team_draw_whiteboard(
    self: SchoolDB, session_id: int, *, publish_mode: str = "group_shared"
) -> None:
    """Publish the deck whiteboard with the draw question.

    Args:
        session_id: ``live_class_sessions.id``.
        publish_mode: ``group_shared`` for a team drawing, ``individual``
            for one board per student.
    """
    mode = "individual" if str(publish_mode or "") == "individual" else "group_shared"
    self.ensure_live_session_items(session_id)
    for row in self.list_live_session_items(session_id):
        if str(row.get("kind") or "") != "whiteboard":
            continue
        if str(row.get("status") or "") == "active":
            if str(row.get("publish_mode") or "") != mode:
                self.update_live_session_item_settings(
                    session_id,
                    int(row["id"]),
                    publish_mode=mode,
                )
            return
        self.publish_live_session_item(
            session_id, int(row["id"]), publish_mode=mode
        )
        return


def _purge_team_draw_images(
    self: SchoolDB, session_id: int, live_item_id: int | None = None
) -> None:
    """Delete ephemeral gallery blobs for one item or the whole session."""
    with self._lock:
        if live_item_id is None:
            self.conn.execute(
                "DELETE FROM live_team_draw_images WHERE live_session_id = ?",
                (int(session_id),),
            )
        else:
            self.conn.execute(
                """
                DELETE FROM live_team_draw_images
                WHERE live_session_id = ? AND live_item_id = ?
                """,
                (int(session_id), int(live_item_id)),
            )
            self.conn.execute(
                """
                DELETE FROM live_individual_draw_images
                WHERE live_session_id = ? AND live_item_id = ?
                """,
                (int(session_id), int(live_item_id)),
            )
        if live_item_id is None:
            self.conn.execute(
                "DELETE FROM live_individual_draw_images WHERE live_session_id = ?",
                (int(session_id),),
            )
        self.conn.commit()


def _purge_team_board_key(self: SchoolDB, session_id: int, board_key: str) -> None:
    """Remove ink for one team board without ending the run."""
    boards = getattr(self, "boards", None)
    if boards is None:
        return
    try:
        run_key, _gen = self._live_board_base_run_key(session_id)
    except KeyError:
        return
    boards.purge_board_key(run_key, str(board_key))


def _store_team_draw_image(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    team_id: int,
    png: bytes,
    *,
    now: str,
) -> None:
    """Upsert one team's gallery PNG."""
    with self._lock:
        self.conn.execute(
            """
            INSERT INTO live_team_draw_images (
                live_session_id, live_item_id, team_id, image_png, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(live_session_id, live_item_id, team_id) DO UPDATE SET
                image_png = excluded.image_png,
                updated_at = excluded.updated_at
            """,
            (int(session_id), int(live_item_id), int(team_id), png, now),
        )
        self.conn.commit()


def team_draw_teacher_view(
    self: SchoolDB, session_id: int, item: dict[str, Any]
) -> dict[str, Any]:
    """Staff grid: drawing / submitted plus title; gallery metadata."""
    from participation_real_students import exclude_participation_answer_student_ids

    session_row = self.get_live_session(session_id)
    class_id = int(session_row["class_id"]) if session_row else 0
    excluded = exclude_participation_answer_student_ids(class_id, self)
    status_board: list[dict[str, Any]] = []
    for team_id, team_name, state in self._iter_group_submit_teams(session_id, item):
        count = int(state.get("submit_count") or 0)
        submitted = count > 0
        title = gallery_row_title(state) if submitted else ""
        status_board.append(
            {
                "team_id": team_id,
                "team_name": team_name,
                "submitted": submitted,
                "status_label": status_label(submitted),
                "title": title,
            }
        )
    prompt = resolved_prompt_text(item)
    mystery = is_mystery_rule_item(item)
    view: dict[str, Any] = {
        "item": item,
        "response_mode": TEAM_DRAW_RESPONSE_MODE,
        "prompt": prompt,
        "mystery_rule": mystery,
        "mystery_steps": mystery_steps_for_item(item) if mystery else [],
        "status_board": status_board,
        "response_count": sum(1 for row in status_board if row["submitted"]),
        "eligible_count": len(status_board),
        "scoring": False,
        "excluded_student_ids": sorted(excluded),
    }
    seq = self._merge_teacher_quiet_dots_from_light(
        session_id, int(item["id"]), status_board=status_board
    )
    if seq:
        view["group_response_seq"] = seq
    return view


def _present_team_draw_member_labels(
    self: SchoolDB, session_id: int, team_id: int
) -> list[tuple[int, str]]:
    """Ordered (student_id, display label) for present members on one team."""
    present = {
        int(row["student_id"])
        for row in self.list_live_session_attendees(session_id, present_only=True)
        if row.get("student_id") not in (None, "")
    }
    packed: list[tuple[int, str]] = []
    for team in self._named_teams_for_live_session(session_id):
        if int(team.get("id") or 0) != int(team_id):
            continue
        for member in team.get("members") or []:
            try:
                sid = int(member["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if sid not in present:
                continue
            label = str(
                member.get("first_name") or member.get("codename") or "Student"
            ).strip()
            packed.append((sid, label[:40] or "Student"))
        break
    packed.sort(key=lambda row: row[0])
    return packed


def _team_draw_pass_turn(
    self: SchoolDB, session_id: int, team_id: int, student_id: int
) -> dict[str, Any] | None:
    """Pass-it-on pill: shared current holder among present teammates (mystery rule)."""
    members = self._present_team_draw_member_labels(session_id, int(team_id))
    if len(members) < 2:
        return None
    if not any(int(sid) == int(student_id) for sid, _name in members):
        return None
    # One holder for the whole team: first present member in roster order.
    index = 0
    _sid, holder_name = members[index]
    return {
        "holder_name": holder_name,
        "turn_index": int(index) + 1,
        "turn_total": len(members),
    }


def student_team_draw_state(
    self: SchoolDB, item: dict[str, Any], student_id: int | None
) -> dict[str, Any] | None:
    """Student card: prompt, mystery steps, submit eligibility."""
    if student_id in (None, "") or str(item.get("response_mode") or "") != TEAM_DRAW_RESPONSE_MODE:
        return None
    session_id = int(item["live_session_id"])
    try:
        team_id = self._group_submit_read_team(item, student_id)
    except ValueError:
        return None
    row = self._group_response_row(int(item["id"]), int(team_id)) or {}
    submitted = int(row.get("submit_count") or 0) > 0
    final = row.get("final_answer") if isinstance(row.get("final_answer"), dict) else {}
    teams = {
        int(team["id"]): team
        for team in self._named_teams_for_live_session(session_id)
    }
    active = str(item.get("status") or "") == "active"
    question = item.get("item") if isinstance(item.get("item"), dict) else {}
    prompt_id = str(question.get("team_draw_prompt_id") or "").strip()
    mystery = is_mystery_rule_item(item)
    pass_turn = (
        self._team_draw_pass_turn(session_id, int(team_id), int(student_id))
        if mystery
        else None
    )
    return {
        "team_id": int(team_id),
        "team_name": str((teams.get(int(team_id)) or {}).get("name") or ""),
        "prompt": resolved_prompt_text(item),
        "team_draw_prompt_id": prompt_id,
        "mystery_rule": mystery,
        "mystery_steps": mystery_steps_for_item(item) if mystery else [],
        "pass_turn": pass_turn,
        "submitted": submitted,
        "title": str(final.get("title") or "").strip(),
        "can_submit": active and not submitted,
        "phase": "done" if submitted else "draw",
    }


def submit_team_draw(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    student_id: int,
    *,
    title: str = "",
    image_b64: str,
) -> dict[str, Any]:
    """Store the team's drawing. A title on the request is ignored.

    Args:
        session_id: ``live_class_sessions.id``.
        live_item_id: ``live_session_items.id``.
        student_id: Roster id of the student sending the drawing.
        title: Ignored. Older callers may still pass one.
        image_b64: PNG data URL or raw base64. A blank canvas is refused.
    """
    from school_db import GroupAnswerLocked, _now

    del title

    item = self.get_live_session_item(session_id, live_item_id, sync="if_stale")
    if str(item.get("response_mode") or "") != TEAM_DRAW_RESPONSE_MODE:
        raise ValueError("Not a Team Draw question.")
    if str(item.get("status") or "") != "active":
        card = self.student_team_draw_state(item, student_id)
        raise GroupAnswerLocked("This question is closed.", card=card)
    team_id = self._require_group_submit_member(item, student_id)
    row = self._group_response_row(int(item["id"]), int(team_id)) or {}
    if int(row.get("submit_count") or 0) > 0:
        card = self.student_team_draw_state(item, student_id)
        raise GroupAnswerLocked("Your team already submitted.", card=card)
    png = load_inked_png(image_b64)
    answer = team_draw_answer_json("")
    now = _now()
    with self._lock:
        self.conn.execute(
            """
            UPDATE live_group_responses
            SET status = 'submitted',
                final_answer_json = ?,
                proposed_answer_json = ?,
                finalizer_student_id = ?,
                submit_count = 1,
                submitter_ids_json = ?,
                why_text = '',
                updated_at = ?,
                finalized_at = ?
            WHERE live_item_id = ? AND team_id = ?
            """,
            (
                json.dumps(answer),
                json.dumps(answer),
                int(student_id),
                json.dumps([int(student_id)]),
                now,
                now,
                int(item["id"]),
                int(team_id),
            ),
        )
        self.conn.commit()
    self._store_team_draw_image(session_id, int(item["id"]), int(team_id), png, now=now)
    self._purge_team_board_key(session_id, f"team:{int(team_id)}")
    card = self.student_team_draw_state(item, student_id) or {}
    return card


def _store_individual_draw_image(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    student_id: int,
    png: bytes,
    *,
    now: str,
) -> None:
    """Upsert one student's gallery PNG."""
    with self._lock:
        self.conn.execute(
            """
            INSERT INTO live_individual_draw_images (
                live_session_id, live_item_id, student_id, image_png, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(live_session_id, live_item_id, student_id) DO UPDATE SET
                image_png = excluded.image_png,
                updated_at = excluded.updated_at
            """,
            (int(session_id), int(live_item_id), int(student_id), png, now),
        )
        self.conn.commit()


def individual_draw_teacher_view(
    self: SchoolDB, session_id: int, item: dict[str, Any]
) -> dict[str, Any]:
    """Staff card for an individual draw: submitted status, count, prompt.

    Args:
        session_id: ``live_class_sessions.id``.
        item: Lifecycle row whose answer kind is team draw and whose
            response mode is individual.
    """
    prompt = self._prompt_for_live_item(item)
    responded: set[int] = set()
    if prompt is not None and prompt.get("id") not in (None, ""):
        for row in self.list_live_prompt_responses(int(prompt["id"])):
            sid = row.get("student_id")
            if sid not in (None, ""):
                responded.add(int(sid))
    status_board: list[dict[str, Any]] = []
    for attendee in self.list_live_session_attendees(session_id, present_only=True):
        sid = attendee.get("student_id")
        if sid in (None, ""):
            continue
        student_id = int(sid)
        submitted = student_id in responded
        label = str(attendee.get("codename") or "Student").strip() or "Student"
        status_board.append(
            {
                "team_id": student_id,
                "team_name": label,
                "submitted": submitted,
                "status_label": status_label(submitted),
                "title": "",
            }
        )
    mystery = is_mystery_rule_item(item)
    return {
        "item": item,
        "response_mode": "individual",
        "draw": True,
        "prompt": resolved_prompt_text(item),
        "mystery_rule": mystery,
        "mystery_steps": mystery_steps_for_item(item) if mystery else [],
        "status_board": status_board,
        "response_count": sum(1 for row in status_board if row["submitted"]),
        "eligible_count": len(status_board),
        "scoring": False,
    }


def submit_individual_draw(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    student_id: int,
    *,
    image_b64: str,
) -> dict[str, Any]:
    """Store one student's drawing. No title is required.

    Args:
        session_id: ``live_class_sessions.id``.
        live_item_id: ``live_session_items.id``.
        student_id: Roster id of the student sending the drawing.
        image_b64: PNG data URL or raw base64. A blank canvas is refused.

    Returns:
        Student card with ``submitted`` and ``can_submit``.
    """
    from school_db import GroupAnswerLocked, _now

    item = self.get_live_session_item(session_id, live_item_id, sync="if_stale")
    if self._question_answer_kind(item) != "team_draw":
        raise ValueError("Not a draw question.")
    if str(item.get("response_mode") or "") != "individual":
        raise ValueError("Not an individual draw question.")
    if str(item.get("status") or "") != "active":
        raise GroupAnswerLocked("This question is closed.", card=None)
    prompt = self._prompt_for_live_item(item)
    if prompt is None or prompt.get("id") in (None, ""):
        raise ValueError("This question is not open.")
    prior = self.get_live_prompt_response(int(prompt["id"]), int(student_id))
    if prior is not None:
        raise GroupAnswerLocked("You already submitted.", card={"submitted": True, "can_submit": False})
    png = load_inked_png(image_b64)
    answer = team_draw_answer_json("")
    self.submit_live_prompt_response(int(prompt["id"]), int(student_id), answer)
    now = _now()
    _store_individual_draw_image(
        self, session_id, int(item["id"]), int(student_id), png, now=now
    )
    try:
        board_key = self.student_live_board_key(session_id, int(student_id))
    except (KeyError, TypeError, ValueError):
        board_key = ""
    if board_key:
        self._purge_team_board_key(session_id, board_key)
    # Individual publish stores ink on solo:<id>, not the team bucket.
    self._purge_team_board_key(session_id, f"solo:{int(student_id)}")
    return {
        "submitted": True,
        "can_submit": False,
        "prompt": resolved_prompt_text(item),
        "title": "",
        "phase": "done",
    }


def team_draw_gallery(
    self: SchoolDB, session_id: int, live_item_id: int
) -> list[dict[str, Any]]:
    """Teacher gallery rows with data URLs (session only)."""
    import base64

    item = self.get_live_session_item(session_id, live_item_id)
    if not is_team_draw_item(item):
        raise ValueError("Not a Team Draw question.")
    if str(item.get("response_mode") or "") == "individual":
        return _individual_draw_gallery(self, session_id, live_item_id, item)
    rows: list[dict[str, Any]] = []
    for team_id, team_name, state in self._iter_group_submit_teams(session_id, item):
        blob_row = self.conn.execute(
            """
            SELECT image_png FROM live_team_draw_images
            WHERE live_session_id = ? AND live_item_id = ? AND team_id = ?
            """,
            (int(session_id), int(live_item_id), int(team_id)),
        ).fetchone()
        image_url = ""
        if blob_row and blob_row["image_png"]:
            encoded = base64.b64encode(bytes(blob_row["image_png"])).decode("ascii")
            image_url = f"data:image/png;base64,{encoded}"
        rows.append(
            {
                "team_id": team_id,
                "team_name": team_name,
                "title": gallery_row_title(state),
                "submitted": int(state.get("submit_count") or 0) > 0,
                "image_url": image_url,
            }
        )
    return rows


def team_draw_end_and_clear(self: SchoolDB, session_id: int, live_item_id: int) -> dict[str, Any]:
    """Drop all team boards and gallery images for this Team Draw item."""
    item = self.get_live_session_item(session_id, live_item_id)
    if not is_team_draw_item(item):
        raise ValueError("Not a Team Draw question.")
    session_row = self.get_live_session(session_id)
    if session_row is None:
        raise KeyError(f"live session {session_id}")
    if str(item.get("response_mode") or "") == "individual":
        for attendee in self.list_live_session_attendees(session_id, present_only=False):
            sid = attendee.get("student_id")
            if sid in (None, ""):
                continue
            self._purge_team_board_key(session_id, f"solo:{int(sid)}")
        self._purge_team_draw_images(session_id, int(live_item_id))
        return self.individual_draw_teacher_view(session_id, item)
    for team in self._named_teams_for_live_session(session_id):
        self._purge_team_board_key(session_id, f"team:{int(team['id'])}")
    self._purge_team_draw_images(session_id, int(live_item_id))
    return self.team_draw_teacher_view(session_id, item)


def _individual_draw_gallery(
    self: SchoolDB,
    session_id: int,
    live_item_id: int,
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    """Gallery rows for an individual draw question.

    Args:
        session_id: ``live_class_sessions.id``.
        live_item_id: ``live_session_items.id``.
        item: Lifecycle row.
    """
    import base64

    view = self.individual_draw_teacher_view(session_id, item)
    rows: list[dict[str, Any]] = []
    for board in view.get("status_board") or []:
        student_id = int(board.get("team_id") or 0)
        blob_row = self.conn.execute(
            """
            SELECT image_png FROM live_individual_draw_images
            WHERE live_session_id = ? AND live_item_id = ? AND student_id = ?
            """,
            (int(session_id), int(live_item_id), student_id),
        ).fetchone()
        image_url = ""
        if blob_row and blob_row["image_png"]:
            encoded = base64.b64encode(bytes(blob_row["image_png"])).decode("ascii")
            image_url = f"data:image/png;base64,{encoded}"
        rows.append(
            {
                "team_id": student_id,
                "team_name": str(board.get("team_name") or ""),
                "title": "",
                "submitted": bool(board.get("submitted")),
                "image_url": image_url,
            }
        )
    return rows
